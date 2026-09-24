"""Shared configuration for the service and command line."""
import json
import os
from pathlib import Path

DEFAULTS = {'port': 8765, 'data_dir': './data', 'model': None,
            'auto_discuss': True, 'max_open_topics': 4, 'discussion_timeout': 150}


def config_path(value=None):
    return Path(value or os.environ.get('TALKWITHAGENT_CONFIG')
                or Path.home() / '.config' / 'talkwithagent' / 'config.json').expanduser().resolve()


def load_config(path=None, overrides=None):
    explicit = path or os.environ.get('TALKWITHAGENT_CONFIG')
    path = config_path(path)
    values = {}
    if explicit and not path.exists():
        raise ValueError(f'找不到配置文件 {path}，请先运行 talkwithagent init --config PATH')
    if path.exists():
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, dict):
            raise ValueError('配置必须是 JSON 对象')
    if set(values) - set(DEFAULTS):
        raise ValueError('未知配置项: ' + ', '.join(sorted(set(values) - set(DEFAULTS))))
    result = {**DEFAULTS, **values}
    for key, low, high in [('port', 1, 65535), ('max_open_topics', 1, 20), ('discussion_timeout', 10, 600)]:
        value = (overrides or {}).get(key)
        if value is not None:
            result[key] = value
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key} 必须是 {low}–{high} 之间的整数')
    for key in ('model', 'auto_discuss', 'data_dir'):
        if (overrides or {}).get(key) is not None:
            result[key] = overrides[key]
    if type(result['auto_discuss']) is not bool:
        raise ValueError('auto_discuss 必须是 true 或 false')
    if result['model'] is not None and (not isinstance(result['model'], str)
                                       or not result['model'].strip() or len(result['model']) > 100):
        raise ValueError('model 必须是模型名称或 null')
    if not isinstance(result['data_dir'], str) or not result['data_dir'].strip():
        raise ValueError('data_dir 必须是非空路径')
    directory = Path(result['data_dir']).expanduser()
    base = Path.cwd() if (overrides or {}).get('data_dir') is not None else path.parent
    result['data_dir'] = str((base / directory).resolve())
    return result


def init_config(path=None):
    path = config_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as file:
        json.dump(DEFAULTS, file, ensure_ascii=False, indent=2)
        file.write('\n')
    return path
