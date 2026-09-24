"""Pack, install and exercise the distributed CLI without model calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen, Request
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


with tempfile.TemporaryDirectory(prefix='talkwithagent-package-') as temporary:
    directory = Path(temporary)
    package = json.loads(run('npm', 'pack', '--json', '--pack-destination', temporary, cwd=ROOT))[0]
    files = {f['path'] for f in package['files']}
    assert '.agents/plugins/marketplace.json' in files
    assert 'plugins/talkwithagent/.codex-plugin/plugin.json' in files
    assert 'settings.py' in files and 'discussion.schema.json' in files and 'codex_context.py' in files
    assert not any('.sqlite' in p or '.runtime/' in p for p in files)
    run('npm', 'install', '--prefix', str(directory / 'installed'), '--ignore-scripts',
        '--no-audit', '--no-fund', str(directory / package['filename']))
    command = ['node', str(directory / 'installed/node_modules/talkwithagent/bin/talkwithagent.mjs')]
    assert run(*command, '--version') == json.loads((ROOT / 'package.json').read_text())['version']
    config = directory / 'settings.json'
    run(*command, 'init', '--config', str(config))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config.write_text(json.dumps({'port': port, 'auto_discuss': False, 'data_dir': './records'}))
    options = json.loads(run(*command, 'config', '--config', str(config)))
    assert options['data_dir'] == str((directory / 'records').resolve())
    env = {**os.environ, 'TALKWITHAGENT_CONFIG': str(config)}
    with (directory / 'service.log').open('w') as log:
        service = subprocess.Popen([*command, 'start'], env=env, stdout=log, stderr=log)
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(80):
                try:
                    with urlopen(base + '/api/sessions', timeout=1):
                        break
                except OSError:
                    assert service.poll() is None, (directory / 'service.log').read_text()
                    time.sleep(0.1)
            else:
                raise AssertionError('Service did not become ready')
            attached = json.loads(run(*command, 'attach', '--main-thread', 'first-task', '--title', 'First', env=env))
            again = json.loads(run(*command, 'attach', '--main-thread', 'first-task', env=env))
            other = json.loads(run(*command, 'attach', '--main-thread', 'second-task', env=env))
            assert attached['session'] == again['session'] != other['session']
            assert f':{port}/?session=' in attached['url']
            run(*command, 'publish', '--session', attached['session'], 'Done', '--status', 'completed', env=env)
            assert json.loads(run(*command, 'pending', '--session', attached['session'], env=env)) == []
            assert json.loads(run(*command, 'discussion', '--session', attached['session'], env=env)) == {'cards': [], 'messages': []}
            with urlopen(base + '/api/state?session=' + attached['session']) as response:
                state = json.load(response)
            assert state['status'] == 'completed' and state['auto_discuss'] is False
            assert state['title'] == 'First', 'Reattachment without a title must preserve it'
            assert not state['busy'] and state['codex_thread'] is None
            data = {'session': attached['session'], 'request_id': 'new-agent', 'name': 'Second agent', 'focus': ''}
            with urlopen(Request(base + '/api/agents', json.dumps(data).encode(),
                                 {'Content-Type': 'application/json', 'X-TalkWithAgent': '1'})) as response:
                agent = json.load(response)
            assert agent['id'] != attached['session'] and len(agent['agents']) == 2
            with urlopen(base + '/api/state?session=' + attached['session'] + '&agent=' + agent['id']) as response:
                assert json.load(response)['agent_name'] == 'Second agent'
            try:
                urlopen(base + '/api/state?session=' + other['session'] + '&agent=' + agent['id'])
            except HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError('Agent selection must stay inside its bound session')
            with urlopen(base + '/api/state?session=' + other['session']) as response:
                assert json.load(response)['status'] == 'working'
            for route in ('/', '/app.js', '/style.css'):
                with urlopen(base + route) as response:
                    assert response.status == 200 and response.read()
        finally:
            service.terminate()
            service.wait(timeout=10)
    assert (directory / 'records/sessions.sqlite3').exists()
print('PASS: packed contents, isolated npm install, configuration, service, attachment, bridge and task isolation')
