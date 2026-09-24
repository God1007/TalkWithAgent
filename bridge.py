#!/usr/bin/env python3
"""Explicit checkpoints between the native Codex task and the companion."""
import argparse
import json
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--session', required=True, help='Discussion id from the page URL')
    sub = parser.add_subparsers(dest='command', required=True)
    publish = sub.add_parser('publish')
    publish.add_argument('text')
    publish.add_argument('--status', choices=['working', 'waiting', 'completed'], default='working')
    sub.add_parser('pending')
    ack = sub.add_parser('ack')
    ack.add_argument('ids', nargs='+')
    args = parser.parse_args()
    if args.command == 'pending':
        with urlopen(args.url + '/api/discussion?session=' + args.session, timeout=10) as response:
            state = json.load(response)
        print(json.dumps(state['pending'], ensure_ascii=False, indent=2))
    else:
        data = {'session': args.session}
        data.update({'ack': args.ids} if args.command == 'ack' else {'text': args.text, 'status': args.status})
        request = Request(args.url + '/api/bridge', json.dumps(data).encode(), {'Content-Type': 'application/json', 'X-TalkWithAgent': '1'})
        with urlopen(request, timeout=10) as response:
            state = json.load(response)
        print(json.dumps({'session': state['id'], 'version': state['version'], 'status': state['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
