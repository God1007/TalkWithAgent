#!/usr/bin/env python3
"""Run: python3 check.py. No network or model calls."""
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from server import Store, validate_result, discussion_context, discuss, event, message, source
from settings import init_config, load_config
from codex_context import visible_context

def proposal(key='topic', kind='question'):
    return {'reply': '', 'topics': [{'key': key, 'kind': kind, 'title': key,
            'description': '关于刚同步的进展，你更倾向哪种做法？', 'options': ['A', 'B']}]}

def claim(store, sid):
    state = store.get(sid)
    if not state.get('context_synced_at'):
        store.sync_context(sid, {'thread_id': state['main_thread'], 'messages': [], 'truncated': False})
    with patch('server.time.time', return_value=9999999999):
        result = store.claim(sid)
    assert result, 'Expected work to be scheduled'
    return result['active_job']

with tempfile.TemporaryDirectory() as directory, patch('server.CODEX', 'codex'):
    store = Store(directory, 'main-task')
    sid = store.create()['id']
    assert not store.get(sid)['cards'], 'No canned discussion cards'
    job = claim(store, sid)
    assert job['reason'] == 'progress' and not store.claim(sid)
    store.finish(sid, job, proposal(kind='decision'), 'persistent-thread')
    state = store.get(sid)
    agent_topic = state['cards'][0]['id']
    assert state['cards'][0]['owner'] == 'agent'
    assert state['cards'][0]['kind'] == 'question', 'Agent cannot invent a blocking decision'
    assert not state['outbox'] and state['status'] == 'working'
    assert not store.claim(sid), 'No repeated model calls without new input'

    start = {'type': 'chat', 'text': '我直接补充一下', 'request_id': 'user-start'}
    store.action(sid, start)
    store.action(sid, start)
    state = store.get(sid)
    user_topic = state['receipts']['user-start']['topic_id']
    assert len(state['cards']) == 1 and len(state['jobs']) == 1
    assert user_topic is None, 'Direct discussion must not create or require a topic'
    job = claim(store, sid)
    assert job['topic_id'] == user_topic
    assert job['message_id'] == state['messages'][-1]['id']
    store.finish(sid, job, {'reply': '当然，双方都可以发起。', 'topics': []})
    assert store.get(sid)['messages'][-1]['topic_id'] == user_topic
    assert not store.get(sid)['outbox'], 'Discussion is not authorization'

    store.action(sid, {'type': 'card', 'card_id': agent_topic, 'value': '稍后', 'request_id': 'later'})
    assert not store.get(sid)['outbox']
    answer = {'type': 'card', 'card_id': agent_topic, 'value': '我的补充', 'request_id': 'answer'}
    store.action(sid, answer)
    store.action(sid, answer)
    state = store.get(sid)
    assert len(state['outbox']) == 1 and state['outbox'][0]['status'] == 'pending'
    assert state['status'] == 'working', 'Non-blocking replies cannot pause execution'
    store.bridge(sid, {'ack': ['answer']})
    assert store.get(sid)['cards'][0]['delivery'] == 'received'
    job = claim(store, sid)
    store.finish(sid, job, error='temporary failure')
    store.action(sid, {'type': 'retry', 'request_id': 'retry'})
    job = claim(store, sid)
    assert job['topic_id'] == agent_topic, 'Retry must answer the failed topic'
    store.finish(sid, job, {'reply': '收到补充', 'topics': []})

    store.bridge(sid, {'text': '新的进展'})
    revision = store.get(sid)['input_revision']
    store.bridge(sid, {'text': '新的进展'})
    assert store.get(sid)['input_revision'] == revision, 'Duplicate checkpoints do not trigger again'
    job = claim(store, sid)
    store.bridge(sid, {'text': '更新的进展'})
    store.finish(sid, job, proposal('stale'))
    assert not any(c.get('key') == 'stale' for c in store.get(sid)['cards'])
    job = claim(store, sid)
    store.finish(sid, job, proposal())
    assert len(store.get(sid)['cards']) == 1, 'Already answered questions must not repeat'
    assert not store.claim(sid)

    store.bridge(sid, {'status': 'waiting', 'text': '执行端需要用户在 A、B 中决定'})
    job = claim(store, sid)
    store.finish(sid, job, proposal('real-decision', 'decision'))
    assert store.get(sid)['cards'][-1]['kind'] == 'decision'
    assert store.create()['id'] == sid, 'One discussion per native session'
    second = store.create('another-task')['id']
    assert not store.get(second)['messages'] and not store.get(second)['outbox']
    store.bridge(sid, {'status': 'completed', 'text': '完成'})
    assert store.get(second)['status'] == 'working', 'Task state must not cross sessions'
    store.action(sid, {'type': 'chat', 'topic_id': user_topic, 'text': '重启前排队', 'request_id': 'queued'})
    claim(store, sid)
    store = Store(directory, 'main-task')
    store.recover()
    assert store.get(sid)['codex_thread'] == 'persistent-thread'
    assert not store.get(sid)['busy'] and store.get(sid)['jobs'][0]['id'] == 'queued'

    legacy = store.get(second)
    legacy.pop('schema_version')
    legacy['messages'] = [{'id': 'old', 'role': 'user', 'text': '旧对话', 'time': 1}]
    store.save(legacy)
    store.recover()
    assert store.get(second)['messages'][0]['topic_id'] == 'previous-discussion'
    assert store.get(second)['messages'][0]['text'] == '旧对话'

    clean = store.create('clean-task')
    clean['status'] = 'working'
    event(clean, '正在实现订单导出，需要确定分文件方式')
    latest = source(clean)
    clean['cards'].append({'id': 'removed', 'title': '旧演示', 'status': 'pending', 'archived': True})
    message(clean, 'user', '旧联调内容', 'removed')
    event(clean, '旧演示进展')
    clean['events'][-1]['archived'] = True
    store.save(clean)
    claim(store, clean['id'])
    snapshot = store.get(clean['id'])
    context = discussion_context(snapshot)
    assert snapshot['active_job']['source_id'] == latest['id']
    assert not any(c['id'] == 'removed' for c in context['topics'])
    assert not context['messages'], 'Archived topic messages must not leak into model context'
    assert context['updates'][-1]['id'] == latest['id'], 'Archived progress must not seed new topics'
    store.finish(clean['id'], snapshot['active_job'], {'reply': '', 'topics': []})
    store.bridge(clean['id'], {'status': 'completed', 'text': '任务已完成'})
    with patch('server.time.time', return_value=9999999999):
        assert not store.claim(clean['id']), 'Completion must not trigger unsolicited next-iteration questions'
    store.action(clean['id'], {'type': 'chat', 'text': '解释一下导出结果', 'request_id': 'after-completion'})
    job = claim(store, clean['id'])
    store.finish(clean['id'], job, {'reply': '可以继续讨论已经完成的工作。', 'topics': []})
    assert store.get(clean['id'])['messages'][-1]['role'] == 'assistant', 'User discussions must still work after completion'

    for bad in [None, {'reply': '', 'topics': [{'kind': 'execute'}]}, proposal('../invalid')]:
        try:
            validate_result(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed model output must be rejected')

with tempfile.TemporaryDirectory() as directory, patch('server.CODEX', 'codex'):
    path = Path(directory) / 'config.json'
    init_config(path)
    assert load_config(path)['data_dir'] == str((Path(directory) / 'data').resolve())
    path.write_text(json.dumps({'port': 9001, 'auto_discuss': False, 'max_open_topics': 1}), encoding='utf-8')
    options = load_config(path, {'port': 9002})
    assert options['port'] == 9002 and options['auto_discuss'] is False
    try:
        init_config(path)
    except FileExistsError:
        pass
    else:
        raise AssertionError('init must never overwrite existing configuration')
    for bad in [{'port': True}, {'auto_discuss': 'false'}, {'max_open_topics': 0}, {'typo': 1}]:
        path.write_text(json.dumps(bad), encoding='utf-8')
        try:
            load_config(path)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid config must fail clearly')
    store = Store(Path(directory) / 'sessions', settings=options)
    unbound = store.create()
    store.settings['auto_discuss'] = True
    assert store.claim(unbound['id']) is None, 'Starting an unbound service must not spend model calls'
    store.settings['auto_discuss'] = False
    first = store.attach('task-a', 'Task A')
    second = store.attach('task-b', 'Task B')
    assert store.attach('task-a', 'Updated title')['id'] == first['id']
    assert first['id'] != second['id']
    assert store.claim(first['id']) is None, 'Disabled proactivity must not start model calls'
    store.action(first['id'], {'type': 'chat', 'request_id': 'input', 'text': 'A question'})
    job = claim(store, first['id'])
    result = proposal('should-not-appear')
    result['reply'] = 'A useful reply'
    store.finish(first['id'], job, result)
    assert not store.get(first['id'])['cards'], 'Auto-discuss off still allows direct replies'
    assert store.get(first['id'])['messages'][-1]['text'] == 'A useful reply'
    store.settings['auto_discuss'] = True
    job = claim(store, first['id'])
    result = proposal('first')
    result['topics'] += proposal('second')['topics']
    store.finish(first['id'], job, result)
    assert sum(c.get('owner') == 'agent' for c in store.get(first['id'])['cards']) == 1
    assert not store.get(second['id'])['cards'], 'Separate native tasks must not share topics'
    store.settings.update(model='configured-model', discussion_timeout=23)
    store.action(first['id'], {'type': 'chat', 'topic_id': store.get(first['id'])['cards'][0]['id'],
                              'request_id': 'model-config', 'text': 'Another question'})
    with patch('server.subprocess.Popen') as run:
        process = run.return_value.__enter__.return_value
        process.returncode = 0
        process.communicate.return_value = (json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': json.dumps({'reply': 'Configured reply', 'topics': []})}}), '')
        discuss(store, store.claim(first['id']))
        command = run.call_args.args[0]
        assert command[command.index('--model') + 1] == 'configured-model'
        assert process.communicate.call_args.kwargs['timeout'] == 23
        assert store.get(first['id'])['messages'][-1]['text'] == 'Configured reply'
with tempfile.TemporaryDirectory() as directory, patch('server.CODEX', 'codex'):
    store = Store(directory)
    a, b = store.attach('a', None), store.attach('b', None)
    store.action(a['id'], {'type': 'chat', 'request_id': 'direct', 'text': '主任务的约束是什么？'})
    assert store.claim(a['id']) is None, 'Wait for main context before answering'
    context = visible_context({'id': 'a', 'turns': [{'id': 't', 'items': [
        {'id': 'u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': '离线运行，不使用云服务'}]},
        {'id': 'r', 'type': 'reasoning', 'text': 'must not share'},
        {'id': 'x', 'type': 'commandExecution', 'aggregatedOutput': 'raw tool output'},
        {'id': 'm', 'type': 'agentMessage', 'text': '正在实现本地数据库'}]}]})
    assert len(context['messages']) == 2 and not context['truncated']
    store.sync_context(a['id'], context)
    revision = store.get(a['id'])['input_revision']
    store.sync_context(a['id'], context)
    assert store.get(a['id'])['input_revision'] == revision, 'Unchanged history must not retrigger'
    snapshot = store.claim(a['id'])
    prompt = discussion_context(snapshot)
    assert prompt['main_session']['messages'][0]['text'] == '离线运行，不使用云服务'
    assert prompt['request']['text'] == '主任务的约束是什么？'
    assert store.get(b['id'])['main_context'] is None, 'Never share another task history'
    store.finish(a['id'], snapshot['active_job'], error='temporary')
    store.action(a['id'], {'type': 'retry', 'request_id': 'retry-direct'})
    job = claim(store, a['id'])
    assert job['message_id'] == prompt['request']['id'], 'Retry direct session messages'
    store.finish(a['id'], job, {'reply': '离线运行。', 'topics': []})
    store.sync_context(a['id'], error='unavailable')
    assert store.claim(a['id']) is None
    assert store.get(a['id'])['main_context'] == context, 'A read failure must preserve existing context'
    context['messages'].append({'id': 'u2', 'role': 'user', 'text': '只支持 macOS'})
    store.sync_context(a['id'], context)
    assert store.get(a['id'])['input_revision'] > revision
    assert store.get(a['id'])['context_error'] == ''
    try:
        store.sync_context(b['id'], context)
    except ValueError:
        pass
    else:
        raise AssertionError('Mismatched source session must be rejected')
    store.action(a['id'], {'type': 'forward', 'text': '使用本地数据库', 'request_id': 'forward-direct'})
    assert store.get(a['id'])['outbox'][-1]['text'] == '使用本地数据库'
    assert not store.get(b['id'])['outbox']
print('PASS: shared session context, direct discussion, isolation, proactive questions, handoff, retry and persistence')
