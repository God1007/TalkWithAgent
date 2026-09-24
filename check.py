#!/usr/bin/env python3
"""Run: python3 check.py. No network or model calls."""
import tempfile
from unittest.mock import patch
from server import Store, validate_result, discussion_context, event, message, source

def proposal(key='topic', kind='question'):
    return {'reply': '', 'topics': [{'key': key, 'kind': kind, 'title': key,
            'description': '关于刚同步的进展，你更倾向哪种做法？', 'options': ['A', 'B']}]}

def claim(store, sid):
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

    start = {'type': 'start_topic', 'text': '我也可以开话题', 'request_id': 'user-start'}
    store.action(sid, start)
    store.action(sid, start)
    state = store.get(sid)
    user_topic = state['receipts']['user-start']['topic_id']
    assert len(state['cards']) == 2 and len(state['jobs']) == 1
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
    assert len(store.get(sid)['cards']) == 2, 'Already answered topics must not repeat'
    assert not store.claim(sid)

    store.bridge(sid, {'status': 'waiting', 'text': '执行端需要用户在 A、B 中决定'})
    job = claim(store, sid)
    store.finish(sid, job, proposal('real-decision', 'decision'))
    assert store.get(sid)['cards'][-1]['kind'] == 'decision'
    second = store.create()['id']
    assert not store.get(second)['messages'] and not store.get(second)['outbox']
    store.bridge(sid, {'status': 'completed', 'text': '完成'})
    assert store.get(second)['status'] == 'completed'
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

    clean = store.create()
    clean['status'] = 'working'
    event(clean, '正在实现订单导出，需要确定分文件方式')
    latest = source(clean)
    clean['cards'].append({'id': 'removed', 'title': '旧演示', 'status': 'pending', 'archived': True})
    message(clean, 'user', '旧联调内容', 'removed')
    event(clean, '旧演示进展')
    clean['events'][-1]['archived'] = True
    store.save(clean)
    snapshot = store.claim(clean['id'])
    context = discussion_context(snapshot)
    assert snapshot['active_job']['source_id'] == latest['id']
    assert not any(c['id'] == 'removed' for c in context['topics'])
    assert not context['messages'], 'Archived topic messages must not leak into model context'
    assert context['updates'][-1]['id'] == latest['id'], 'Archived progress must not seed new topics'
    store.finish(clean['id'], snapshot['active_job'], {'reply': '', 'topics': []})
    store.bridge(clean['id'], {'status': 'completed', 'text': '任务已完成'})
    with patch('server.time.time', return_value=9999999999):
        assert not store.claim(clean['id']), 'Completion must not trigger unsolicited next-iteration questions'
    store.action(clean['id'], {'type': 'start_topic', 'text': '解释一下导出结果', 'request_id': 'after-completion'})
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
print('PASS: two-way topics, proactive triggers, completion silence, archived context isolation, deduplication, handoff, retries and persistence')
