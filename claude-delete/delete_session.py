#!/usr/bin/env python3
"""Intercept /delete; terminate only the invoking Claude, then remove its transcript."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid


def process(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'ppid=', '-o', 'stat=',
                             '-o', 'lstart=', '-o', 'comm='], capture_output=True, text=True)
    fields = result.stdout.strip().split(None, 7)
    if result.returncode or len(fields) != 8:
        return None
    return int(fields[0]), fields[1], ' '.join(fields[2:7]), fields[7]


def claude_parent():
    pid = os.getppid()
    for _ in range(12):
        info = process(pid)
        if not info or pid <= 1:
            break
        name = Path(info[3]).name
        # Native installs expose their versioned executable to ps on macOS.
        native = '/claude/versions/' in info[3] and Path(info[3]).is_file()
        if name == 'claude' or native:
            return pid, info[2]
        pid = info[0]
    raise ValueError('无法确认当前 Claude Code 进程，未执行删除。')


def validate(root, transcript, sid):
    uuid.UUID(sid)
    path = Path(transcript)
    if path.is_symlink():
        raise ValueError('会话文件不能是符号链接。')
    path = path.resolve()
    projects = (root / 'projects').resolve()
    if path.name != sid + '.jsonl' or path.parent.parent != projects:
        raise ValueError('会话路径与 ID 不匹配，未执行删除。')
    return path


def rewrite(path, transform):
    """Optimistic replacement: retry if another process wrote while reading."""
    for _ in range(10):
        if not path.exists():
            return
        if path.is_symlink():
            raise ValueError('拒绝修改符号链接：' + str(path))
        before = path.stat()
        original = path.read_bytes()
        result = transform(original)
        if result == original:
            return
        fd, temp = tempfile.mkstemp(prefix='.delete-session-', dir=path.parent)
        try:
            os.fchmod(fd, before.st_mode & 0o777)
            with os.fdopen(fd, 'wb') as out:
                out.write(result)
                out.flush()
                os.fsync(out.fileno())
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                continue
            os.replace(temp, path)
            return
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    raise RuntimeError('历史文件正在被其它进程持续写入，未改写该文件。')


def remove_path(path):
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def cleanup(root, transcript, sid):
    path = validate(root, transcript, sid)
    remove_path(path)
    remove_path(path.with_suffix(''))
    # Only exact session ID paths, never substring/glob matches.
    for directory in ('file-history', 'session-env', 'tasks'):
        parent = root / directory
        if parent.exists() and parent.resolve() != parent:
            raise ValueError('拒绝清理符号链接目录：' + str(parent))
        remove_path(parent / sid)

    def history(data):
        kept = []
        for line in data.splitlines(keepends=True):
            try:
                row = json.loads(line)
            except ValueError:
                kept.append(line)
                continue
            if not isinstance(row, dict) or row.get('sessionId') != sid:
                kept.append(line)
        return b''.join(kept)
    rewrite(root / 'history.jsonl', history)

    def index(data):
        obj = json.loads(data)
        entries = obj.get('entries')
        if not isinstance(entries, list):
            raise ValueError('未知会话索引格式，未改写。')
        filtered = [e for e in entries if not isinstance(e, dict) or e.get('sessionId') != sid]
        if filtered == entries:
            return data
        obj['entries'] = filtered
        return (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode()
    rewrite(path.parent / 'sessions-index.json', index)


def worker(job):
    pid, start = job['pid'], job['start']
    # Let the hook return and Claude display the message before requesting exit.
    time.sleep(0.5)
    info = process(pid)
    if info and info[2] == start and not info[1].startswith('Z'):
        os.kill(pid, signal.SIGTERM)
    for _ in range(300):
        info = process(pid)
        if not info or info[2] != start or info[1].startswith('Z'):
            cleanup(Path(job['root']), job['transcript'], job['sid'])
            print('已删除会话 ' + job['sid'], flush=True)
            return
        time.sleep(0.2)
    raise RuntimeError('Claude 60 秒内未退出；未删除会话文件。')


def hook():
    data = json.load(sys.stdin)
    if data.get('prompt', '').strip() != '/delete':
        return
    try:
        if data.get('agent_id'):
            raise ValueError('只支持主会话。')
        root = Path(os.environ.get('CLAUDE_CONFIG_DIR', '~/.claude')).expanduser().resolve()
        sid = data['session_id']
        transcript = validate(root, data['transcript_path'], sid)
        pid, start = claude_parent()
        job = dict(pid=pid, start=start, root=str(root), transcript=str(transcript), sid=sid)
        logdir = root / 'delete-session-logs'
        logdir.mkdir(mode=0o700, exist_ok=True)
        with (logdir / (sid + '.log')).open('ab') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                     stdin=subprocess.PIPE, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True)
            child.stdin.write(json.dumps(job).encode())
            child.stdin.close()
        print(json.dumps({'decision': 'block', 'reason': '正在退出并删除当前会话：' + sid}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'decision': 'block', 'reason': '删除失败：' + str(error)}, ensure_ascii=False))


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        worker(json.load(sys.stdin))
    else:
        hook()
