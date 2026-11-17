#!/usr/bin/env python3
"""Install /delete without replacing unrelated Claude Code settings."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.claude-delete-', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def is_ours(hook, target):
    if hook.get('type') != 'command':
        return False
    try:
        parts = shlex.split(hook.get('command', ''))
        return len(parts) == 2 and Path(parts[1]) == target
    except ValueError:
        return False


def install(root, force=False, uninstall=False):
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    settings = root / 'settings.json'
    config = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
    if not isinstance(config, dict):
        raise ValueError('settings.json 必须是 JSON 对象。')
    target = root / 'hooks' / 'delete_session.py'
    files = {target: SOURCE / 'delete_session.py', root / 'commands' / 'delete.md': SOURCE / 'delete.md'}
    for dest, src in files.items():
        if dest.is_symlink() or dest.parent.is_symlink() or settings.is_symlink():
            raise ValueError('安装目标不能是符号链接。')
        if dest.exists() and dest.read_bytes() != src.read_bytes() and not force:
            raise ValueError(f'{dest} 已存在且内容不同；请先检查，或使用 --force（会备份）。')
    hooks = config.setdefault('hooks', {})
    entries = hooks.get('UserPromptSubmit', [])
    remaining = []
    for entry in entries:
        filtered = [h for h in entry.get('hooks', []) if not is_ours(h, target)]
        if filtered:
            remaining.append(dict(entry, hooks=filtered))
        elif not entry.get('hooks'):
            remaining.append(entry)
    if not uninstall:
        remaining.append({'hooks': [{'type': 'command', 'command':
            shlex.quote(sys.executable) + ' ' + shlex.quote(str(target)), 'timeout': 10}]})
    if remaining:
        hooks['UserPromptSubmit'] = remaining
    else:
        hooks.pop('UserPromptSubmit', None)
    if not hooks:
        config.pop('hooks', None)
    changes = {settings: (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode()}
    changes.update({p: None if uninstall else src.read_bytes() for p, src in files.items()})
    changes = {p: data for p, data in changes.items()
               if (p.exists() and (data is None or p.read_bytes() != data))
               or (not p.exists() and data is not None)}
    if not changes:
        print('配置已是目标状态，无需修改。')
        return
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = root / 'claude-delete-backups' / stamp
    backup.mkdir(parents=True, mode=0o700)
    originals = {p: p.read_bytes() if p.exists() else None for p in changes}
    for path, data in originals.items():
        if data is not None:
            dest = backup / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(0o600)
    try:
        # Publish configuration last so it only references installed files.
        for path in sorted(changes, key=lambda p: p == settings):
            data = changes[path]
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)
    except Exception:
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)
        raise
    print(('已卸载' if uninstall else '已安装') + ' /delete：' + str(root))
    print('原文件备份：' + str(backup))
    print('请退出并重新启动 Claude Code。')


def main():
    parser = argparse.ArgumentParser(description='安装或卸载 Claude Code /delete 命令')
    parser.add_argument('--config-dir', type=Path, default=Path(os.environ.get('CLAUDE_CONFIG_DIR', '~/.claude')))
    parser.add_argument('--force', action='store_true', help='备份并覆盖内容不同的目标文件')
    parser.add_argument('--uninstall', action='store_true', help='卸载本工具，保留其他 Hook 和配置')
    args = parser.parse_args()
    try:
        install(args.config_dir, args.force, args.uninstall)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        parser.exit(1, f'安装失败：{error}\n')


if __name__ == '__main__':
    main()
