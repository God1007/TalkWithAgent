"""Read visible messages from the bound Codex thread without resuming it."""
import json
import queue
import subprocess
import threading
import time


def visible_context(thread):
    messages = []
    for turn in thread.get('turns', []):
        for item in turn.get('items', []):
            kind = item.get('type')
            if kind == 'userMessage':
                text = '\n'.join(c['text'] for c in item.get('content', []) if c.get('type') == 'text')
            elif kind == 'agentMessage':
                text = item.get('text', '')
            else:
                continue  # Exclude reasoning, tool payloads, and system instructions.
            if text.strip():
                messages.append({'id': item['id'], 'role': 'user' if kind == 'userMessage' else 'assistant',
                                 'text': text, 'phase': item.get('phase'), 'turn_id': turn['id'],
                                 'time': turn.get('startedAt')})
    # ponytail: bounded recent transcript; retrieval is needed beyond 120k characters.
    kept, size = [], 0
    for msg in reversed(messages):
        if kept and size + len(msg['text']) > 120000:
            break
        text = msg['text'][-120000:]
        kept.append({**msg, 'text': text})
        size += len(text)
    return {'thread_id': thread['id'], 'messages': list(reversed(kept)),
            'truncated': len(kept) < len(messages) or any(len(m['text']) > 120000 for m in messages)}


class ContextReader:
    def __init__(self, codex):
        self.codex, self.process, self.serial = codex, None, 0

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None

    def call(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.process.stdin.write(json.dumps({'id': request_id, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 15
        while True:
            try:
                reply = self.responses.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError('读取 Codex 会话超时') from None
            if reply is None:
                raise RuntimeError('Codex 会话读取连接已关闭')
            if reply.get('id') == request_id:
                if 'error' in reply:
                    raise RuntimeError(reply['error'].get('message', 'Codex 会话读取失败'))
                return reply['result']

    def read(self, thread_id):
        try:
            if self.process is None:
                self.process = subprocess.Popen([self.codex, 'app-server'], stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                self.responses = queue.Queue()
                def receive(output, responses):
                    try:
                        for line in output:
                            responses.put(json.loads(line))
                    finally:
                        responses.put(None)
                threading.Thread(target=receive, args=(self.process.stdout, self.responses), daemon=True).start()
                self.call('initialize', {'clientInfo': {'name': 'talkwithagent', 'version': '0.2.0'}})
                self.process.stdin.write('{"method":"initialized","params":{}}\n')
                self.process.stdin.flush()
            thread = self.call('thread/read', {'threadId': thread_id, 'includeTurns': True})['thread']
            if thread['id'] != thread_id:
                raise RuntimeError('Codex 返回了不匹配的会话')
            return visible_context(thread)
        except Exception:
            self.close()
            raise
