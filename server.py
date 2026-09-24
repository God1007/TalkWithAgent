#!/usr/bin/env python3
"""TalkWithAgent: persistent, two-way topic discussions for a native Codex task."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from settings import DEFAULTS, load_config

ROOT = Path(__file__).resolve().parent
CODEX = shutil.which('codex')


def message(state, role, text, topic_id=None):
    state['messages'].append({'id': str(uuid.uuid4()), 'role': role, 'text': text,
                              'topic_id': topic_id, 'time': time.time()})
    return state['messages'][-1]['id']


def event(state, text, kind='progress'):
    state['events'].append({'id': str(uuid.uuid4()), 'text': text, 'kind': kind, 'time': time.time()})


def source(state):
    return next((e for e in reversed(state['events']) if e['kind'] == 'progress' and not e.get('archived')), None)


def fresh(main_thread=''):
    state = {'id': str(uuid.uuid4()), 'version': 0, 'schema_version': 2,
             'title': '当前任务', 'created': time.time(), 'status': 'working',
             'main_thread': main_thread, 'codex_thread': None, 'busy': False, 'active_job': None,
             'messages': [], 'events': [], 'receipts': {}, 'outbox': [], 'cards': [], 'jobs': [],
             'input_revision': 0, 'reviewed_revision': -1, 'review_after': 0,
             'agent_error': '', 'last_review': None}
    event(state, '等待 Codex 同步任务进展。' if main_thread else '等待关联 Codex 任务。')
    return state


def validate_result(result):
    """Validate model output before it can become actionable UI."""
    if not isinstance(result, dict) or not isinstance(result.get('reply'), str):
        raise ValueError('讨论输出格式无效')
    cards = result.get('topics')
    if not isinstance(cards, list) or len(cards) > 3 or len(result['reply']) > 12000:
        raise ValueError('讨论输出长度无效')
    for card in cards:
        if not isinstance(card, dict) or card.get('kind') not in ('question', 'suggestion', 'decision'):
            raise ValueError('话题类型无效')
        for key, limit in [('key', 100), ('title', 100), ('description', 1800)]:
            if not isinstance(card.get(key), str) or not 1 <= len(card[key].strip()) <= limit:
                raise ValueError('话题内容无效')
        if not re.fullmatch(r'[a-z0-9_-]+', card['key']):
            raise ValueError('话题标识无效')
        options = card.get('options')
        if not isinstance(options, list) or len(options) > 4 or any(
                not isinstance(x, str) or not x.strip() or len(x) > 160 for x in options):
            raise ValueError('话题选项无效')
        if card['kind'] == 'decision' and len(options) < 2:
            raise ValueError('决策需要至少两个选项')
    return result


class Store:
    def __init__(self, directory, main_thread='', settings=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'sessions.sqlite3'
        self.lock = threading.RLock()
        self.main_thread = main_thread
        self.settings = {**DEFAULTS, **(settings or {})}
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def save(self, state):
        state['version'] += 1
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?, ?)',
                       (state['id'], json.dumps(state, ensure_ascii=False)))

    def get(self, sid):
        if not isinstance(sid, str):
            raise ValueError('会话编号无效')
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT body FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise ValueError('找不到这个会话')
        return json.loads(row[0])

    def all(self):
        with sqlite3.connect(self.path) as db:
            states = [json.loads(row[0]) for row in db.execute('SELECT body FROM sessions')]
        return sorted(states, key=lambda s: s['created'], reverse=True)

    def create(self, main_thread=None, title=None):
        with self.lock:
            state = fresh(self.main_thread if main_thread is None else main_thread)
            previous = next((s for s in self.all() if s['main_thread'] == state['main_thread']), None)
            if previous:
                state['title'] = previous['title']
                state['status'] = previous['status']
                state['events'] = [e for e in previous['events'] if e['kind'] == 'progress' and not e.get('archived')]
            if title:
                state['title'] = title
            self.save(state)
            return state

    def attach(self, main_thread, title):
        if not isinstance(main_thread, str) or not main_thread.strip() or len(main_thread) > 200:
            raise ValueError('请提供有效的 Codex 任务 ID')
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 100):
            raise ValueError('任务名称需要 1–100 字')
        with self.lock:
            existing = next((s for s in self.all() if s['main_thread'] == main_thread), None)
            if existing:
                if title:
                    existing['title'] = title.strip()
                    self.save(existing)
                return existing
            return self.create(main_thread, title.strip() if title else None)

    def recover(self):
        with self.lock:
            for state in self.all():
                if state.get('schema_version') != 2:
                    for card in state['cards']:
                        card['archived'] = True
                    old_id = 'previous-discussion'
                    if state['messages']:
                        state['cards'].append({'id': old_id, 'key': old_id, 'kind': 'discussion',
                                               'owner': 'user', 'title': '先前的讨论', 'description': '',
                                               'status': 'resolved', 'answer': '', 'created': state['created']})
                        for msg in state['messages']:
                            msg['topic_id'] = old_id
                    defaults = fresh()
                    for key in ('jobs', 'active_job', 'input_revision', 'reviewed_revision',
                                'review_after', 'agent_error', 'last_review'):
                        state[key] = defaults[key]
                    state['schema_version'] = 2
                state['busy'] = False
                state['active_job'] = None
                self.save(state)

    def action(self, sid, data):
        with self.lock:
            state = self.get(sid)
            rid = data.get('request_id')
            if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
                raise ValueError('缺少有效消息编号')
            if rid in state['receipts']:
                return state
            kind = data.get('type')
            value = data.get('text', '')
            if kind in ('chat', 'start_topic', 'forward'):
                if not isinstance(value, str) or not value.strip() or len(value) > 6000:
                    raise ValueError('请输入 1–6000 字的内容')
                value = value.strip()
            topic_id = data.get('topic_id')
            topic = next((c for c in state['cards'] if c['id'] == topic_id and not c.get('archived')), None)
            if kind == 'start_topic':
                if not CODEX:
                    raise ValueError('没有找到 Codex CLI')
                topic_id = str(uuid.uuid4())
                title = data.get('title') or value.splitlines()[0][:60]
                if not isinstance(title, str) or len(title) > 100:
                    raise ValueError('话题标题过长')
                state['cards'].append({'id': topic_id, 'key': topic_id, 'kind': 'discussion', 'owner': 'user',
                                       'title': title, 'description': '', 'status': 'pending',
                                       'answer': '', 'created': time.time()})
                mid = message(state, 'user', value, topic_id)
                state['jobs'].append({'id': rid, 'topic_id': topic_id, 'message_id': mid, 'reason': 'message'})
            elif kind == 'chat':
                if not topic or not CODEX:
                    raise ValueError('请选择一个话题，并确认 Codex CLI 可用')
                mid = message(state, 'user', value, topic_id)
                state['jobs'].append({'id': rid, 'topic_id': topic_id, 'message_id': mid, 'reason': 'message'})
            elif kind == 'retry':
                if not CODEX:
                    raise ValueError('没有找到 Codex CLI')
                failed = state.pop('failed_job', None)
                if failed and failed.get('topic_id'):
                    state['jobs'].append({**failed, 'id': rid})
                else:
                    state['input_revision'] += 1
                state['review_after'] = 0
                state['agent_error'] = ''
            elif kind == 'forward':
                if not topic:
                    raise ValueError('请选择要回传的话题')
                state['outbox'].append({'id': rid, 'topic_id': topic_id, 'text': value,
                                        'time': time.time(), 'status': 'pending'})
                message(state, 'system', '已提交结论，等待 Codex 读取。', topic_id)
            elif kind == 'card':
                card = next((c for c in state['cards'] if c['id'] == data.get('card_id')), None)
                if not card or card.get('archived') or card['status'] not in ('pending', 'deferred'):
                    raise ValueError('这个话题已处理，请刷新后查看')
                topic_id = card['id']
                value = data.get('value', '')
                if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                    raise ValueError('请填写有效回复')
                if value == '稍后':
                    card['status'] = 'deferred'
                else:
                    if card['kind'] == 'decision' and value not in card.get('options', []) and not value.startswith('补充：'):
                        raise ValueError('请选择一种方案，或补充你的方案')
                    card.update(status='resolved', answer=value, delivery='pending', receipt_id=rid)
                    state['outbox'].append({'id': rid, 'topic_id': card['id'],
                                            'text': f'{card["title"]} → {value}',
                                            'time': time.time(), 'status': 'pending'})
                    mid = message(state, 'user', value, card['id'])
                    message(state, 'system', '已提交回复，等待 Codex 读取。', card['id'])
                    state['jobs'].append({'id': rid, 'topic_id': card['id'], 'message_id': mid, 'reason': 'feedback'})
            else:
                raise ValueError('不支持的操作')
            state['receipts'][rid] = {'time': time.time(), 'type': kind, 'topic_id': topic_id}
            self.save(state)
            return state

    def bridge(self, sid, data):
        with self.lock:
            state = self.get(sid)
            text = data.get('text', '')
            if not isinstance(text, str) or len(text) > 6000:
                raise ValueError('状态消息无效')
            status = data.get('status', state['status'])
            if status not in ('working', 'waiting', 'completed'):
                raise ValueError('无效任务状态')
            ack = data.get('ack', [])
            if not isinstance(ack, list) or any(not isinstance(x, str) for x in ack):
                raise ValueError('无效回执')
            for item in state['outbox']:
                if item['id'] in ack:
                    item['status'] = 'received'
            for card in state['cards']:
                if card.get('receipt_id') in ack:
                    card['delivery'] = 'received'
            self.save(state)
            for sibling in self.all():
                if sibling['main_thread'] != state['main_thread']:
                    continue
                old_source = source(sibling)
                changed = (text and (not old_source or old_source['text'] != text)) or status != sibling['status']
                if not changed:
                    continue
                sibling['status'] = status
                if text:
                    event(sibling, text)
                sibling['input_revision'] += 1
                sibling['review_after'] = time.time() + 2
                self.save(sibling)
            return self.get(sid)

    def claim(self, sid):
        with self.lock:
            state = self.get(sid)
            if state['busy']:
                return None
            if state['jobs']:
                job = dict(state['jobs'][0])
            elif (self.settings['auto_discuss'] and state['main_thread'] and state['status'] != 'completed'
                  and state['input_revision'] > state['reviewed_revision']
                  and time.time() >= state['review_after']):
                job = {'id': str(uuid.uuid4()), 'topic_id': None, 'reason': 'progress'}
            else:
                return None
            job.update(revision=state['input_revision'], source_id=(source(state) or {}).get('id'))
            state.update(busy=True, active_job=job, agent_error='')
            self.save(state)
            return state

    def finish(self, sid, job, result=None, thread_id=None, error=None):
        if result is not None:
            validate_result(result)
        with self.lock:
            state = self.get(sid)
            if not state['active_job'] or state['active_job']['id'] != job['id']:
                return
            state.update(busy=False, active_job=None, last_review=time.time())
            if thread_id:
                state['codex_thread'] = thread_id
            state['jobs'] = [j for j in state['jobs'] if j['id'] != job['id']]
            if job['reason'] == 'progress':
                state['reviewed_revision'] = job['revision']
            if error:
                state['agent_error'] = error
                state['failed_job'] = job
                if job.get('topic_id'):
                    message(state, 'error', error, job['topic_id'])
            else:
                state.pop('failed_job', None)
                if job.get('topic_id') and result['reply'].strip():
                    message(state, 'assistant', result['reply'], job['topic_id'])
                if (self.settings['auto_discuss'] and job['revision'] == state['input_revision']
                        and (job['reason'] != 'progress' or state['status'] != 'completed')):
                    known_keys = {c.get('key') for c in state['cards']}
                    known_titles = {re.sub(r'\W+', '', c['title']) for c in state['cards']}
                    open_count = sum(c['status'] == 'pending' and c.get('owner') == 'agent'
                                     and not c.get('archived') for c in state['cards'])
                    for card in result['topics']:
                        title_key = re.sub(r'\W+', '', card['title'])
                        if (card['key'] in known_keys or title_key in known_titles
                                or open_count >= self.settings['max_open_topics']):
                            continue
                        new = dict(card)
                        # Only the executor can declare it is waiting for a decision.
                        if new['kind'] == 'decision' and state['status'] != 'waiting':
                            new['kind'] = 'question'
                        new.update(id=str(uuid.uuid4()), owner='agent', status='pending', answer='',
                                   created=time.time(), source_id=job['source_id'],
                                   source_text=(source(state) or {}).get('text', ''))
                        state['cards'].append(new)
                        known_keys.add(card['key'])
                        known_titles.add(title_key)
                        open_count += 1
            self.save(state)


def discussion_context(snapshot):
    topics = [c for c in snapshot['cards'] if not c.get('archived')]
    topic_ids = {c['id'] for c in topics}
    messages = [m for m in snapshot['messages'] if m.get('topic_id') in topic_ids]
    return {'task': snapshot['title'], 'main_status': snapshot['status'], 'trigger': snapshot['active_job'],
            'request': next((m for m in messages if m['id'] == snapshot['active_job'].get('message_id')), None),
            'topics': topics, 'updates': [e for e in snapshot['events'] if not e.get('archived')][-10:],
            'messages': messages[-30:]}


def discuss(store, snapshot):
    sid, job = snapshot['id'], snapshot['active_job']
    thread_id = snapshot['codex_thread']
    try:
        context = {**discussion_context(snapshot), 'auto_discuss': store.settings['auto_discuss']}
        prompt = (
            '你是 TalkWithAgent 的讨论 agent，和用户是平等的讨论伙伴，双方都能主动发起话题。'
            '你只讨论，不执行代码、不调用工具、不访问文件或网络。执行留在原生 Codex，'
            '你只能依据提供的同步进展，不假装掌握完整主会话，也不能声称已经执行或暂停主任务。'
            '输出符合给定 JSON schema。reply 是你对当前话题的简短中文回复；topics 是你主动发起的新话题。'
            'auto_discuss=false 时 topics 必须为空，仍然正常回复用户，也可在当前话题内追问。'
            'trigger.reason=progress 时，根据最新进展主动发现值得用户回应的取舍、建议或缺失信息，'
            '生成 0–2 个具体、简短的新话题，不等用户先说话；没有新的有价值问题就返回空 topics，避免骚扰。'
            '每个主动话题必须对应当前任务中具体、尚未解决且用户回答能影响结果的问题。'
            '进展只是报告完成、测试通过或提交代码时，不需要追问。不要据此让用户安排下一轮、'
            '排列产品路线图，或验证 TalkWithAgent 自身是否可用；用户主动提出这些任务时才讨论。'
            '不要为了展示双向对话能力而制造话题，也不要把开发联调、演示记录当作用户的真实需求。'
            '直接聊任务本身，不用演示、验收、功能介绍的口吻，不反复附加“这里只讨论”、'
            '“不会自动执行”、“不会启动下一轮”等说明；用户询问执行状态或存在具体误解时再澄清。'
            '提供的上下文是当前有效记录；历史中已移除的话题和联调内容不再是待办，不要重新提出。'
            '用户明确要求已确定的事不要反问；已存在、已回答或稍后的话题不要重提。'
            'key 是稳定英文语义标识，同一问题始终用同一个 key。每个话题说明为何此时值得讨论，'
            'description 用自然对话表达，问题须明确提问，options 给 0–3 个短回答候选。'
            'decision 仅用于 main_status=waiting 且执行端确实声明需要决定的情况，至少给两个选项。'
            '其余用 question 或 suggestion。不得自行将执行端改成等待状态。'
            'trigger.reason=message/feedback 时，回应 trigger.topic_id 内 request 指定的消息，'
            '必要时追问，或基于新发现开新话题；不要重复用户已确认的答案。'
            '不要在 reply 中替代可交互的新话题：主动新问题必须放入 topics。'
            '用户回答、采纳或明确提交的结论才回传执行端；聊天本身不等于执行授权。'
            '全部用中文纯文本，reply 通常 2–4 句。上下文：\n' + json.dumps(context, ensure_ascii=False))
        cmd = [CODEX, 'exec']
        if thread_id:
            cmd += ['resume', thread_id]
        if store.settings['model']:
            cmd += ['--model', store.settings['model']]
        cmd += ['--ignore-user-config', '--skip-git-repo-check', '--json',
                '--output-schema', str(ROOT / 'discussion.schema.json'),
                '-c', 'sandbox_mode="read-only"', '-c', 'approval_policy="never"', '-']
        # ponytail: one CLI process per turn; a resident app-server can reduce startup cost later.
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, cwd=store.directory, start_new_session=True) as process:
            try:
                output, _ = process.communicate(prompt, timeout=store.settings['discussion_timeout'])
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()
                raise RuntimeError('这次思考超时了，内容已保留。可以点击重新思考。')
        answer = ''
        for line in output.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get('type') == 'thread.started':
                thread_id = item.get('thread_id') or thread_id
            if item.get('type') == 'item.completed' and item.get('item', {}).get('type') == 'agent_message':
                answer = item['item'].get('text', '')
        if process.returncode or not answer:
            raise RuntimeError('讨论 agent 暂时没有回复，请检查 Codex 登录与额度后重试。')
        store.finish(sid, job, json.loads(answer), thread_id)
    except Exception as error:
        text = str(error) if isinstance(error, RuntimeError) else '这次讨论没有完成，已有内容已保留。请重新思考或继续回复。'
        store.finish(sid, job, thread_id=thread_id, error=text)


def run_worker(store, stop):
    # ponytail: scan local sessions; use a work queue if session count makes polling costly.
    while not stop.wait(0.5):
        if not CODEX:
            continue
        for state in store.all():
            claimed = store.claim(state['id'])
            if claimed:
                threading.Thread(target=discuss, args=(store, claimed), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, code, value, content_type='application/json; charset=utf-8'):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def safe_origin(self):
        host = self.headers.get('Host', '')
        return host in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'} and self.headers.get('Origin', f'http://{host}') == f'http://{host}'

    def do_GET(self):
        if not self.safe_origin():
            return self.send(403, {'error': '仅允许本机同源访问'})
        parsed = urlparse(self.path)
        if parsed.path in ('/', '/app.js', '/style.css'):
            name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                          '/style.css': ('style.css', 'text/css')}[parsed.path]
            return self.send(200, (ROOT / name).read_bytes(), mime + '; charset=utf-8')
        if parsed.path == '/api/state':
            try:
                state = self.server.store.get(parse_qs(parsed.query).get('session', [''])[0])
                state['codex_available'] = bool(CODEX)
                state['auto_discuss'] = self.server.store.settings['auto_discuss']
                return self.send(200, state)
            except ValueError as error:
                return self.send(404, {'error': str(error)})
        if parsed.path == '/api/sessions':
            return self.send(200, [{'id': s['id'], 'title': s['title'], 'created': s['created']}
                                  for s in self.server.store.all()])
        self.send(404, {'error': '找不到页面'})

    def do_POST(self):
        if not self.safe_origin() or self.headers.get('X-TalkWithAgent') != '1' or self.headers.get('Content-Type') != 'application/json':
            return self.send(403, {'error': '请求来源无效'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 1 <= length <= 32000:
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求格式无效')
            if self.path == '/api/sessions':
                return self.send(201, self.server.store.create())
            if self.path == '/api/attach':
                return self.send(200, self.server.store.attach(data.get('main_thread'), data.get('title')))
            if self.path == '/api/action':
                return self.send(200, self.server.store.action(data.get('session'), data))
            if self.path == '/api/bridge':
                return self.send(200, self.server.store.bridge(data.get('session'), data))
            self.send(404, {'error': '找不到接口'})
        except (ValueError, TypeError) as error:
            self.send(400, {'error': str(error)})


def main(argv=None):
    parser = argparse.ArgumentParser(description='TalkWithAgent local discussion service')
    parser.add_argument('--config', help='JSON config file (or TALKWITHAGENT_CONFIG)')
    parser.add_argument('--port', type=int)
    parser.add_argument('--data-dir')
    parser.add_argument('--model')
    parser.add_argument('--auto-discuss', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--max-open-topics', type=int)
    parser.add_argument('--discussion-timeout', type=int)
    parser.add_argument('--main-thread', default='')
    args = parser.parse_args(argv)
    try:
        settings = load_config(args.config, vars(args))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    try:
        server = ThreadingHTTPServer(('127.0.0.1', settings['port']), Handler)
    except OSError as error:
        parser.exit(1, f'无法启动 TalkWithAgent: {error}\n')
    store = Store(settings['data_dir'], args.main_thread, settings)
    store.recover()
    if not store.all() or (args.main_thread and not any(s['main_thread'] == args.main_thread for s in store.all())):
        store.create()
    server.store = store
    stop = threading.Event()
    threading.Thread(target=run_worker, args=(store, stop), daemon=True).start()
    print(f'TalkWithAgent ready: http://127.0.0.1:{settings["port"]}', flush=True)
    print(f'Data: {settings["data_dir"]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
