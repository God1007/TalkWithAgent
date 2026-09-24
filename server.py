#!/usr/bin/env python3
"""Local discussion companion. Python stdlib + the installed Codex CLI."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
CODEX = shutil.which('codex')


def message(state, role, text):
    state['messages'].append({'id': str(uuid.uuid4()), 'role': role, 'text': text, 'time': time.time()})


def event(state, text, kind='progress'):
    state['events'].append({'id': str(uuid.uuid4()), 'text': text, 'kind': kind, 'time': time.time()})


def fresh(main_thread=''):
    state = {'id': str(uuid.uuid4()), 'version': 0, 'title': '讨论 agent 插件 · 交互 Demo',
             'created': time.time(), 'status': 'working', 'main_thread': main_thread,
             'codex_thread': None, 'busy': False, 'messages': [], 'events': [], 'receipts': {}, 'outbox': [],
             'cards': [
                 {'id': 'summary', 'kind': 'suggestion', 'title': '只把确认后的结论交给执行 agent',
                  'description': '取舍和追问留在这里。你明确采纳的建议、决定和补充说明，再回传到 Codex。',
                  'status': 'pending', 'answer': ''},
                 {'id': 'workflow', 'kind': 'question', 'title': '你通常会同时推进几个任务？',
                  'description': '这会影响后续是否需要任务切换。现在的演示只绑定当前 Codex 任务，可以稍后回答。',
                  'status': 'pending', 'answer': ''}]}
    event(state, '执行留在 Codex，网页负责讨论。')
    message(state, 'welcome', '我们在这里把思路聊清楚。\n\n我会结合 Codex 同步过来的任务进展，帮你比较方案、提出建议、补充问题。讨论不会打断执行；需要回传的内容，由你明确提交。')
    return state


class Store:
    def __init__(self, directory, main_thread=''):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'sessions.sqlite3'
        self.lock = threading.RLock()
        self.main_thread = main_thread
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def save(self, state):
        state['version'] += 1
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?, ?)', (state['id'], json.dumps(state, ensure_ascii=False)))

    def get(self, sid):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT body FROM sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise ValueError('找不到这个会话')
        return json.loads(row[0])

    def all(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM sessions ORDER BY rowid DESC')]

    def create(self):
        with self.lock:
            state = fresh(self.main_thread)
            previous = next((s for s in self.all() if s['main_thread'] == self.main_thread), None)
            if previous:
                state['status'] = previous['status']
                state['events'] = [e for e in previous['events'] if e['kind'] == 'progress']
            self.save(state)
            return state

    def recover(self):
        with self.lock:
            for state in self.all():
                if state['busy']:
                    state['busy'] = False
                    message(state, 'error', '服务重启，上一条回复未完成。已有讨论已保留，请重新发送你的问题。')
                    self.save(state)

    def action(self, sid, data):
        with self.lock:
            state = self.get(sid)
            rid = data.get('request_id')
            if not isinstance(rid, str) or not 1 <= len(rid) <= 100:
                raise ValueError('缺少有效消息编号')
            if rid in state['receipts']:
                return state, False
            kind = data.get('type')
            if kind == 'demo_decision':
                if not any(c['id'] == 'demo' for c in state['cards']):
                    state['cards'].insert(0, {'id': 'demo', 'kind': 'decision', 'title': '示例：配置保存在哪里？',
                                            'description': '体验决策卡。此示例只演示确认流程，不会暂停或修改当前 Codex 任务。',
                                            'status': 'pending', 'answer': '', 'demo': True})
            elif kind == 'forward':
                value = data.get('text', '')
                if not isinstance(value, str) or not value.strip() or len(value) > 6000:
                    raise ValueError('请输入需要回传的结论')
                state['outbox'].append({'id': rid, 'text': value.strip(), 'time': time.time(), 'status': 'pending'})
                message(state, 'system', '结论已保存到回传队列，等待 Codex 在检查点读取。')
            elif kind == 'chat':
                value = data.get('text', '')
                if not isinstance(value, str) or not value.strip() or len(value) > 6000:
                    raise ValueError('请输入 1–6000 字的内容')
                if state['busy']:
                    raise ValueError('讨论 agent 正在回复，请等它回复后再发送')
                if not CODEX:
                    raise ValueError('没有找到 Codex CLI，卡片演示仍可使用')
                message(state, 'user', value.strip())
                state['busy'] = True
            elif kind == 'card':
                card = next((c for c in state['cards'] if c['id'] == data.get('card_id')), None)
                if not card or card['status'] not in ('pending', 'deferred'):
                    raise ValueError('这条事项已处理，请刷新后查看')
                value = data.get('value', '')
                if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                    raise ValueError('请填写有效回复')
                if card['kind'] == 'decision' and value not in ('项目目录', '用户目录'):
                    raise ValueError('请选择一种存储方案')
                if card['kind'] == 'suggestion' and value not in ('采纳', '不采纳') and not value.startswith('补充：'):
                    raise ValueError('无效的建议回复')
                if value == '稍后' and card['kind'] == 'question':
                    card['status'] = 'deferred'
                    event(state, '问题已暂存：' + card['title'], 'discussion')
                else:
                    card['status'] = 'resolved'
                    card['answer'] = value
                    card['delivery'] = 'demo' if card.get('demo') else 'pending'
                    if card.get('demo'):
                        message(state, 'system', f'示例决策已确认：{value}。当前 Codex 任务未受影响。')
                    else:
                        card['receipt_id'] = rid
                        state['outbox'].append({'id': rid, 'text': f'{card["title"]} → {value}', 'time': time.time(), 'status': 'pending'})
                        message(state, 'system', f'{card["title"]} · {value} · 等待 Codex 读取')
            else:
                raise ValueError('不支持的操作')
            state['receipts'][rid] = {'time': time.time(), 'type': kind}
            self.save(state)
            return state, kind == 'chat'

    def bridge(self, sid, data):
        with self.lock:
            state = self.get(sid)
            if data.get('text'):
                if not isinstance(data['text'], str) or len(data['text']) > 6000:
                    raise ValueError('状态消息无效')
                event(state, data['text'])
            if 'status' in data:
                if data['status'] not in ('working', 'waiting', 'completed'):
                    raise ValueError('无效任务状态')
                state['status'] = data['status']
            ack = data.get('ack', [])
            if not isinstance(ack, list) or any(not isinstance(x, str) for x in ack):
                raise ValueError('无效回执')
            for item in state['outbox']:
                if item['id'] in ack and item['status'] == 'pending':
                    item['status'] = 'received'
                    event(state, 'Codex 已读取：' + item['text'], 'received')
            for card in state['cards']:
                if card.get('receipt_id') in ack:
                    card['delivery'] = 'received'
            self.save(state)
            if data.get('text') or 'status' in data:
                for sibling in self.all():
                    if sibling['id'] == sid or sibling['main_thread'] != state['main_thread']:
                        continue
                    sibling['status'] = state['status']
                    if data.get('text'):
                        event(sibling, data['text'])
                    self.save(sibling)
            return state


def discuss(store, sid):
    """One active CLI turn per discussion, always resume its own stored thread."""
    try:
        state = store.get(sid)
        context = {'任务': state['title'], '执行状态': state['status'], '事项': state['cards'],
                   '最近执行事件': state['events'][-12:], '讨论记录': state['messages'][-20:]}
        prompt = ('你是 Sidecar 的讨论 agent，用简洁自然的中文和用户讨论任务。你不执行代码，不调用工具，不访问文件或网络。'
                  '任务执行在原生 Codex 中，网页只负责讨论。你只能看到通过检查点同步到的执行状态，不是自动获取完整主会话。不要声称修改过文件或实时知道未同步进展。'
                  '你是真实 Codex 讨论会话。依据下方上下文回答最后一条用户消息，不复述协议。'
                  '选择方案必须通过网页决策卡提交，你不能自行把聊天意向视为批准。'
                  '项目目录便于版本控制与团队共享；用户目录适合个人偏好。建议可不采纳，问题可稍后回答。'
                  '你可以正常自由讨论。需要执行的结论让用户用“回传 Codex”提交。已保存到回传队列不等于执行端已读取，也不代表已执行。'
                  '避免长篇，通常 2–4 句即可。\n当前上下文：\n' + json.dumps(context, ensure_ascii=False))
        cmd = [CODEX, 'exec']
        if state['codex_thread']:
            cmd += ['resume', state['codex_thread']]
        cmd += ['--ignore-user-config', '--skip-git-repo-check', '--json',
                '-c', 'sandbox_mode="read-only"', '-c', 'approval_policy="never"', '-']
        # ponytail: one subprocess per reply; keep app-server alive if startup latency matters.
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, cwd=store.directory, start_new_session=True) as process:
            try:
                output, errors = process.communicate(prompt, timeout=150)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise RuntimeError('Codex 回复超时。讨论已保存，可以稍后重试。')
        answer, thread_id = '', state['codex_thread']
        for line in output.splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get('type') == 'thread.started':
                thread_id = item.get('thread_id') or thread_id
            if item.get('type') == 'item.completed' and item.get('item', {}).get('type') == 'agent_message':
                answer = item['item'].get('text', answer)
        with store.lock:
            state = store.get(sid)
            state['codex_thread'] = thread_id
            state['busy'] = False
            if process.returncode != 0 or not answer:
                message(state, 'error', 'Codex 这次没有成功回复。请检查本机 Codex 登录与可用额度后重试；你的消息已经保存。')
            else:
                message(state, 'assistant', answer)
            store.save(state)
    except Exception as error:
        with store.lock:
            state = store.get(sid)
            state['busy'] = False
            message(state, 'error', str(error) if isinstance(error, RuntimeError) else '讨论服务暂时不可用，你的消息已经保存。')
            store.save(state)


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
        self.wfile.write(body)

    def safe_origin(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        return host in hosts and self.headers.get('Origin', f'http://{host}') == f'http://{host}'

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
                sid = parse_qs(parsed.query).get('session', [''])[0]
                state = self.server.store.get(sid)
                state['codex_available'] = bool(CODEX)
                return self.send(200, state)
            except ValueError as error:
                return self.send(404, {'error': str(error)})
        if parsed.path == '/api/sessions':
            states = self.server.store.all()
            return self.send(200, [{'id': s['id'], 'title': s['title'], 'created': s['created']} for s in states])
        return self.send(404, {'error': '找不到页面'})

    def do_POST(self):
        if not self.safe_origin() or self.headers.get('X-Sidecar') != '1' or self.headers.get('Content-Type') != 'application/json':
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
            if self.path == '/api/action':
                sid = data.get('session', '')
                if not isinstance(sid, str):
                    raise ValueError('会话编号无效')
                state, run = self.server.store.action(sid, data)
                self.send(200, state)
                if run:
                    threading.Thread(target=discuss, args=(self.server.store, sid), daemon=True).start()
                return
            if self.path == '/api/bridge':
                return self.send(200, self.server.store.bridge(data.get('session', ''), data))
            self.send(404, {'error': '找不到接口'})
        except (ValueError, TypeError) as error:
            self.send(400, {'error': str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default=str(ROOT / '.runtime'))
    parser.add_argument('--main-thread', default='')
    args = parser.parse_args()
    store = Store(args.data_dir, args.main_thread)
    store.recover()
    if not store.all():
        store.create()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.store = store
    print(f'Sidecar ready: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
