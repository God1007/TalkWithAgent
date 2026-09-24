"""npm entry point; each command also works with python3 cli.py."""
import argparse
import json
from pathlib import Path
import os
import subprocess
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from settings import config_path, init_config, load_config

ROOT = Path(__file__).resolve().parent


def request(config, route, data=None):
    url = f'http://127.0.0.1:{config["port"]}'
    req = Request(url + route, json.dumps(data).encode() if data is not None else None,
                  {'Content-Type': 'application/json', 'X-TalkWithAgent': '1'})
    try:
        with urlopen(req, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(json.load(error).get('error', str(error))) from error


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'start':
        from server import main as serve
        return serve(argv[1:])
    parser = argparse.ArgumentParser(prog='talkwithagent',
        description='Persistent task discussions for Codex. Start service: talkwithagent start --help')
    parser.add_argument('--version', action='version', version=json.loads((ROOT / 'package.json').read_text())['version'])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('start', help='Start the local service (start --help for options)')
    for name in ('init', 'config', 'attach', 'publish', 'discussion', 'pending', 'ack'):
        command = commands.add_parser(name)
        command.add_argument('--config', help='Config file (or TALKWITHAGENT_CONFIG)')
        if name == 'attach':
            command.add_argument('--main-thread', default=os.environ.get('CODEX_THREAD_ID'))
            command.add_argument('--title')
        if name in ('publish', 'discussion', 'pending', 'ack'):
            command.add_argument('--session', required=True)
        if name == 'publish':
            command.add_argument('text')
            command.add_argument('--status', choices=['working', 'waiting', 'completed'], default='working')
        if name == 'ack':
            command.add_argument('ids', nargs='+')
    commands.add_parser('install-codex', help='Register and install the bundled Codex plugin')
    args = parser.parse_args(argv)
    try:
        if args.command == 'install-codex':
            subprocess.run(['codex', 'plugin', 'marketplace', 'add', str(ROOT)], check=True)
            subprocess.run(['codex', 'plugin', 'add', 'talkwithagent@talkwithagent'], check=True)
            print('插件已安装。请在新的 Codex 任务中使用 $talkwithagent。')
            return
        if args.command == 'init':
            print(init_config(args.config))
            return
        config = load_config(args.config)
        if args.command == 'config':
            result = {'config_file': str(config_path(args.config)), **config}
        elif args.command == 'attach':
            if not args.main_thread:
                raise ValueError('请通过 --main-thread 提供当前 Codex 任务 ID')
            state = request(config, '/api/attach', {'main_thread': args.main_thread, 'title': args.title})
            result = {'session': state['id'], 'url': f'http://127.0.0.1:{config["port"]}/?session={state["id"]}'}
        elif args.command in ('discussion', 'pending'):
            from urllib.parse import urlencode
            state = request(config, '/api/state?' + urlencode({'session': args.session}))
            if args.command == 'pending':
                result = [x for x in state['outbox'] if x['status'] == 'pending']
            else:
                cards = [c for c in state['cards'] if not c.get('archived')]
                ids = {c['id'] for c in cards}
                result = {'cards': cards, 'messages': [m for m in state['messages'] if not m.get('archived')
                          and (not m.get('topic_id') or m['topic_id'] in ids)][-30:]}
        else:
            data = {'session': args.session}
            data.update({'ack': args.ids} if args.command == 'ack' else {'text': args.text, 'status': args.status})
            state = request(config, '/api/bridge', data)
            result = {'session': state['id'], 'status': state['status'], 'version': state['version']}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, URLError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'TalkWithAgent: {error}\n')


if __name__ == '__main__':
    main()
