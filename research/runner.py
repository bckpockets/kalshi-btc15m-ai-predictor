"""User-level process lock, heartbeat, and bounded restart.

This does not install a system service, raise privileges, or keep a host or
virtualenv alive. A missing interpreter, a full disk, or a stopped machine
ends the process. The lock is released when the holding process exits.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
import platform
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

LOCK_BUSY_EXIT = 2

class LockBusy(RuntimeError):
    pass

class WriterLock:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / '.writer.lock'
        self.fd = open(self.path, 'a+')
        self.held = False
    def acquire(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(f'Another writer holds {self.path}') from exc
        self.held = True
        self.fd.seek(0)
        self.fd.truncate()
        self.fd.write(str(os.getpid()))
        self.fd.flush()
        return self
    def release(self):
        if self.held:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.held = False
        if not self.fd.closed:
            self.fd.close()

def dependency_manifest(extra=None):
    def package(name):
        try:
            return version(name)
        except PackageNotFoundError:
            return None
    commit = None
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[1], text=True, stderr=subprocess.DEVNULL).strip() or None
    except (OSError, subprocess.CalledProcessError):
        commit = None
    payload = {
        'schema': 'runner-manifest-v1',
        'event': 'manifest',
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'httpx': package('httpx'),
        'git_commit': commit,
        'execution_model': 'taker-confirm-v1',
        'pid': os.getpid(),
        'note': 'Records this process only. It does not make the host, disk, or virtualenv durable.',
    }
    if extra:
        payload.update(extra)
    return payload

def write_heartbeat(directory, payload):
    directory = Path(directory)
    temporary = directory / 'heartbeat.json.tmp'
    temporary.write_text(json.dumps(payload, indent=2))
    os.replace(temporary, directory / 'heartbeat.json')

def hold_once(directory, argv, spawn=None):
    """Hold the writer lock and run one child. Does not restart it."""
    spawn = spawn or subprocess.Popen
    lock = WriterLock(directory)
    lock.acquire()
    try:
        child = spawn(argv)
        return child.wait()
    finally:
        lock.release()

def main(argv=None):
    import argparse
    from research.depth_recorder import parse_until
    parser = argparse.ArgumentParser(description='Restart a depth recorder, or hold a lock for one paper process.')
    parser.add_argument('--until', help='Required unless --once. UTC stop for restarts.')
    parser.add_argument('--once', action='store_true', help='Run one child and do not restart it.')
    parser.add_argument('--lock-dir', help='Directory locked for --once. The child must not lock it too.')
    parser.add_argument('--max-backoff', type=float, default=30.0)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        raise SystemExit('A command is required')
    if args.once:
        if not args.lock_dir:
            raise SystemExit('--once requires --lock-dir')
        try:
            raise SystemExit(hold_once(args.lock_dir, command))
        except LockBusy:
            raise SystemExit(LOCK_BUSY_EXIT)
    if not args.until:
        raise SystemExit('--until is required when restarting')
    result = supervise(command, parse_until(args.until), max_backoff=args.max_backoff)
    print(json.dumps(result))
    raise SystemExit(0 if result['reason'] == 'child_exit_0' else 1)

if __name__ == '__main__':
    main()

def supervise(argv, until, sleep=time.sleep, spawn=None, now=None, max_backoff=30.0, initial_backoff=1.0):
    """Restart a child until `until`, except a clean exit or a busy lock.

    `spawn` returns an object with `wait()`. Exit code 0 stops. Exit code 2
    means another writer holds the lock; this supervisor does not replace it.
    Other codes wait with exponential backoff capped at `max_backoff`, then
    start again if the clock is still before `until`. Nothing here survives
    a missing executable or a dead machine.
    """
    spawn = spawn or subprocess.Popen
    clock = now or (lambda: datetime.now(timezone.utc))
    until = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
    backoff = float(initial_backoff)
    restarts = []
    while clock() < until:
        child = spawn(argv)
        code = child.wait()
        if code == 0:
            return {'reason': 'child_exit_0', 'restarts': restarts}
        if code == LOCK_BUSY_EXIT:
            return {'reason': 'lock_busy', 'restarts': restarts}
        restarts.append({'code': code, 'at': clock().isoformat()})
        delay = min(float(max_backoff), backoff)
        backoff = min(float(max_backoff), max(float(initial_backoff), backoff * 2))
        remaining = (until - clock()).total_seconds()
        if remaining <= 0:
            break
        sleep(min(delay, remaining))
    return {'reason': 'until', 'restarts': restarts}
