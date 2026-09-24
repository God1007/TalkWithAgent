#!/usr/bin/env node
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const candidates = process.env.TALKWITHAGENT_PYTHON
  ? [[process.env.TALKWITHAGENT_PYTHON]]
  : process.platform === 'win32' ? [['py', '-3'], ['python'], ['python3']] : [['python3'], ['python']];
const python = candidates.find(([command, ...args]) =>
  spawnSync(command, [...args, '-c', 'import sys; sys.exit(sys.version_info < (3, 10))'], {stdio:'ignore'}).status === 0);
if (!python) {
  console.error('TalkWithAgent requires Python 3.10+. Install Python or set TALKWITHAGENT_PYTHON to its executable.');
  process.exit(1);
}
const child = spawn(python[0], [...python.slice(1), fileURLToPath(new URL('../cli.py', import.meta.url)), ...process.argv.slice(2)], {stdio:'inherit'});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('error', error => {console.error(error.message); process.exitCode = 1;});
child.on('exit', (code, signal) => {process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1);});
