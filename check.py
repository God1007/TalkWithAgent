#!/usr/bin/env python3
"""Run: python3 check.py. No network or model calls."""
import tempfile
from server import Store

with tempfile.TemporaryDirectory() as directory:
    store = Store(directory, 'main-task')
    sid = store.create()['id']
    request = {'type': 'forward', 'text': '仅回传已确认结论', 'request_id': 'retry-safe'}
    store.action(sid, request)
    store.action(sid, request)
    state = store.get(sid)
    assert len(state['outbox']) == 1, 'A transport retry must not duplicate the instruction'
    assert state['outbox'][0]['status'] == 'pending', 'Saving is not acknowledgement'
    store = Store(directory)
    assert store.get(sid)['outbox'][0]['text'] == request['text'], 'Restart must preserve the queue'
    store.bridge(sid, {'ack': ['retry-safe'], 'text': '已收到你的决定'})
    assert store.get(sid)['outbox'][0]['status'] == 'received'
    store.action(sid, {'type': 'card', 'card_id': 'summary', 'value': '采纳', 'request_id': 'suggestion'})
    state = store.get(sid)
    assert state['status'] == 'working', 'Non-blocking feedback must not pause the main task'
    assert state['cards'][0]['delivery'] == 'pending'
    store.bridge(sid, {'ack': ['suggestion']})
    assert store.get(sid)['cards'][0]['delivery'] == 'received'
    store.action(sid, {'type': 'card', 'card_id': 'workflow', 'value': '稍后', 'request_id': 'defer'})
    assert len(store.get(sid)['outbox']) == 2, 'Deferring must not send a made-up answer'
    store.action(sid, {'type': 'card', 'card_id': 'workflow', 'value': '2 个', 'request_id': 'answer'})
    store.action(sid, {'type': 'demo_decision', 'request_id': 'demo'})
    store.action(sid, {'type': 'card', 'card_id': 'demo', 'value': '项目目录', 'request_id': 'choose'})
    assert len(store.get(sid)['outbox']) == 3, 'Example decisions must never reach the actual main task'
    try:
        store.action(sid, {'type': 'card', 'card_id': 'summary', 'value': '不采纳', 'request_id': 'stale'})
        raise AssertionError('Stale answers must be rejected')
    except ValueError:
        pass
    second = store.create()['id']
    assert not store.get(second)['outbox'], 'Discussion histories must stay isolated'
    assert store.get(sid)['main_thread'] == 'main-task'
print('PASS: persistence, idempotency, acknowledgement, non-blocking replies, deferred answers, demo isolation, stale actions, session isolation')
