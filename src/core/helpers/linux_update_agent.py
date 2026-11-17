"""Standalone Linux update transaction. Runs with system Python, outside the bundle."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time

EXECUTABLE = 'evejs-launcher'
OWNED = (EXECUTABLE, '_internal')


def private_directory(path: Path):
    if path.is_symlink():
        raise ValueError('The update directory must not be redirected.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('The update directory must be private to this user.')


def read_json(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        content = stream.read(1024 * 1024 + 1)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or len(content) > 1024 * 1024):
        raise ValueError('The update record is not a private bounded file.')
    return json.loads(content)


def publish(path: Path, value):
    temporary = path.with_name(path.name + '.new')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def identity(pid):
    folder = Path('/proc') / str(pid)
    info = folder.stat()
    record = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
    if info.st_uid != os.getuid() or record[0] == 'Z':
        raise ValueError('The launcher process identity is unavailable.')
    return {'pid': pid, 'started': int(record[19]), 'uid': info.st_uid,
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'image': os.readlink(folder / 'exe')}


def running(pin):
    try:
        return identity(pin['pid']) == pin
    except (OSError, ValueError, KeyError):
        return False


def tree_digest(root):
    """Hash only launcher-owned entries; validate all links before copying/deleting."""
    digest = hashlib.sha256()
    total = count = 0
    for base in OWNED:
        start = root / base
        if start.is_symlink() or not start.exists():
            raise ValueError('The launcher executable or runtime is missing or redirected.')
        paths = [start, *sorted(start.rglob('*'))] if start.is_dir() else [start]
        for path in paths:
            count += 1
            if count > 20000:
                raise ValueError('The launcher runtime has too many entries.')
            info = path.lstat()
            name = path.relative_to(root).as_posix()
            digest.update(name.encode() + b'\0' + str(info.st_mode & 0o777).encode() + b'\0')
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                if Path(target).is_absolute() or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError('The launcher runtime contains an escaping link.')
                digest.update(b'L' + target.encode() + b'\0')
            elif stat.S_ISDIR(info.st_mode):
                digest.update(b'D\0')
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > 2 * 1024**3:
                    raise ValueError('The launcher runtime exceeds its size limit.')
                digest.update(b'F' + str(info.st_size).encode() + b'\0')
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
            else:
                raise ValueError('The launcher runtime contains an unsupported file.')
    return digest.hexdigest()


def load_request(directory):
    private_directory(directory)
    request = read_json(directory / 'request.json')
    install = Path(request['install'])
    if (request.get('schema') != 1 or not install.is_absolute() or install.is_symlink()
            or install.resolve() != install or install.stat().st_uid != os.getuid()
            or install.stat().st_mode & 0o022 or len(request['token']) != 64
            or not 0 <= time.time() - request['created'] <= 3600
            or request['parent']['uid'] != os.getuid()):
        raise ValueError('The Linux update request is invalid or expired.')
    if request['parent']['image'] != str(install / EXECUTABLE):
        raise ValueError('The update request names a different launcher.')
    return request


def remove_owned_copy(path, install, *, incomplete=False):
    """Delete only our exact, attributed executable/runtime copy."""
    private_directory(path)
    receipt = read_json(path / 'owned.json')
    names = set(p.name for p in path.iterdir())
    if (receipt != {'schema': 1, 'install': str(install)}
            or not names.issubset({*OWNED, 'owned.json'})
            or (not incomplete and names != {*OWNED, 'owned.json'})):
        raise ValueError('The previous update copy contains unknown files; preserve it.')
    if not incomplete:
        tree_digest(path)  # No external symlinks, devices or redirected roots.
    # rmtree never traverses symlink entries; the attributed private root is real.
    shutil.rmtree(path)


def wait_until(predicate, timeout, child=None):
    deadline = time.monotonic() + timeout
    while not predicate():
        if child is not None and child.poll() is not None:
            raise RuntimeError('The new launcher exited before startup completed.')
        if time.monotonic() >= deadline:
            raise TimeoutError('The launcher update startup handshake timed out.')
        time.sleep(0.05)


def stop_candidate(child):
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def run_agent(directory, *, parent_timeout=60, startup_timeout=60):
    request = load_request(directory)
    install = Path(request['install'])
    candidate = directory / 'unpacked' / 'EveJS-Launcher-Linux-x86_64'
    stage = install / ('.evejs-launcher-stage-' + directory.name)
    backup = install / '.evejs-launcher-rollback'
    moved_old = []
    moved_new = []
    child = None
    committed = False
    # This file remains as a stable lock inode; unlinking a held flock permits races.
    lock_path = directory.parent / ('install-' + hashlib.sha256(str(install).encode()).hexdigest() + '.lock')
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    previous_signals = {}
    def canceled(signum, frame):
        raise RuntimeError('The Linux update agent was canceled.')
    for requested_signal in (signal.SIGINT, signal.SIGTERM):
        previous_signals[requested_signal] = signal.signal(requested_signal, canceled)
    try:
        if os.fstat(lock).st_uid != os.getuid() or os.fstat(lock).st_mode & 0o077:
            raise ValueError('The installation update lock is not private.')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if tree_digest(candidate) != request['digest']:
            raise ValueError('The staged Linux update changed.')
        if not running(request['parent']):
            raise ValueError('The original launcher exited before updater readiness.')
        if stage.exists() or stage.is_symlink():
            raise ValueError('The Linux update staging path already exists.')
        stage.mkdir(mode=0o700)
        publish(stage / 'owned.json', {'schema': 1, 'install': str(install)})
        shutil.copy2(candidate / EXECUTABLE, stage / EXECUTABLE)
        shutil.copytree(candidate / '_internal', stage / '_internal', symlinks=True)
        if tree_digest(stage) != request['digest']:
            raise ValueError('The copied Linux update failed verification.')
        if tree_digest(install) != request['originalDigest']:
            raise ValueError('The installed launcher changed during update preparation.')
        if backup.exists() or backup.is_symlink():
            remove_owned_copy(backup, install)
        publish(directory / 'ready.json', {'token': request['token'], 'agent': identity(os.getpid())})
        def parent_exited():
            if (directory / 'cancel.json').exists():
                if read_json(directory / 'cancel.json') == {'token': request['token']}:
                    raise RuntimeError('The running launcher canceled the update handoff.')
            return not running(request['parent'])

        wait_until(parent_exited, parent_timeout)
        backup.mkdir(mode=0o700)
        publish(backup / 'owned.json', {'schema': 1, 'install': str(install)})
        publish(directory / 'result.json', {'state': 'switching'})
        for name in OWNED:
            os.replace(install / name, backup / name)
            moved_old.append(name)
        for name in OWNED:
            os.replace(stage / name, install / name)
            moved_new.append(name)
        sync_directory(install)
        environment = dict(request['environment'])
        environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        with (directory / 'startup.log').open('ab') as output:
            child = subprocess.Popen([str(install / EXECUTABLE), '--linux-update-request', str(directory / 'request.json')],
                cwd=install, env=environment, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, start_new_session=True)
        child_pin = identity(child.pid)
        publish(directory / 'candidate.json', {'token': request['token'], 'process': child_pin})

        def acknowledged():
            path = directory / 'ack.json'
            if not path.exists():
                return False
            value = read_json(path)
            if value != {'token': request['token'], 'process': child_pin} or not running(child_pin):
                raise ValueError('The Linux update startup acknowledgement is invalid.')
            return True

        wait_until(acknowledged, startup_timeout, child)
        publish(directory / 'result.json', {'state': 'committed', 'version': request['version']})
        publish(directory / 'commit.json', {'token': request['token'], 'process': child_pin})
        committed = True
        publish(directory / 'request.json', dict(request, environment={}))
        # Keep one validated rollback; remove only this transaction's staged copy.
        (stage / 'owned.json').unlink()
        stage.rmdir()
        shutil.rmtree(directory / 'unpacked')
        (directory / 'package.tar.gz').unlink(missing_ok=True)
        return 0
    except BaseException as exc:
        if committed:
            return 0  # A cleanup error must never roll back a running accepted build.
        if child is not None:
            stop_candidate(child)
        for name in reversed(moved_new):
            os.replace(install / name, stage / name)
        for name in reversed(moved_old):
            os.replace(backup / name, install / name)
        if moved_old and set(p.name for p in backup.iterdir()) == {'owned.json'}:
            (backup / 'owned.json').unlink()
            backup.rmdir()
        sync_directory(install)
        publish(directory / 'result.json', {'state': 'rolled-back' if moved_old else 'failed', 'error': str(exc)[:2000]})
        if stage.is_dir() and not stage.is_symlink():
            try:
                remove_owned_copy(stage, install, incomplete=True)
            except (OSError, ValueError):
                pass  # Preserve an incomplete/unattributed copy for inspection.
        if moved_old:
            # Checkpoint was not consumed: the old build can recover its services/clients.
            environment = dict(request['environment'], PYINSTALLER_RESET_ENVIRONMENT='1')
            with (directory / 'rollback.log').open('ab') as output:
                subprocess.Popen([str(install / EXECUTABLE)], cwd=install, env=environment,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        publish(directory / 'request.json', dict(request, environment={}))
        return 1
    finally:
        os.close(lock)
        for requested_signal, previous in previous_signals.items():
            signal.signal(requested_signal, previous)


if __name__ == '__main__':
    raise SystemExit(run_agent(Path(sys.argv[1]).resolve()))
