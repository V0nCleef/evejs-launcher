"""Standalone stdlib supervisor, retained outside replaceable launcher files.

The child wait status belongs to this parent across GUI updates. Abstract Unix
sockets avoid pathname limits; peer credentials and a private capability key
limit control to the owning user and its launcher instances.
"""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

MAX_MESSAGE = 16384


def ticks(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def members(pid, started):
    result = {}
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
            if (folder.stat().st_uid == os.getuid() and fields[0] != 'Z'
                    and fields[2] == str(pid) and fields[3] == str(pid)
                    and int(fields[19]) >= int(started)):
                result[int(folder.name)] = fields[19]
        except (OSError, ValueError, IndexError):
            continue
    return result


def signal_child(process, started, value):
    if value not in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        raise ValueError('Unsupported service signal')
    if started is None:
        if process.poll() is not None:
            return
        raise ValueError('The service start identity is unavailable')
    if ticks(process.pid) == started and os.getpgid(process.pid) == process.pid:
        try:
            os.killpg(process.pid, value)
        except ProcessLookupError:
            pass
        return
    for pid, identity in members(process.pid, started).items():
        if ticks(pid) == identity:
            try:
                os.kill(pid, value)
            except ProcessLookupError:
                pass


def atomic_json(path, value):
    temporary = path.with_suffix('.pending')
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def receive(connection):
    content = bytearray()
    while len(content) <= MAX_MESSAGE:
        chunk = connection.recv(min(4096, MAX_MESSAGE + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if b'\n' in chunk:
            break
    if len(content) > MAX_MESSAGE or not content.endswith(b'\n'):
        raise ValueError('Invalid supervisor request')
    return json.loads(content)


def main():
    directory = Path(sys.argv[1])
    if not directory.is_absolute() or directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError('Invalid supervisor directory')
    bootstrap_path = directory / 'bootstrap.json'
    if bootstrap_path.is_symlink() or bootstrap_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Invalid supervisor bootstrap')
    bootstrap = json.loads(bootstrap_path.read_bytes())
    bootstrap_path.unlink()
    command = bootstrap['command']
    environment = bootstrap['environment']
    if (not isinstance(command, list) or not 1 <= len(command) <= 128
            or any(not isinstance(arg, str) or '\0' in arg or len(arg) > 32768 for arg in command)
            or not Path(command[0]).is_absolute() or not Path(bootstrap['cwd']).is_absolute()
            or not isinstance(environment, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items())
            or not isinstance(bootstrap['key'], str) or len(bootstrap['key']) != 64
            or not bootstrap['address'].startswith('\0evejs-launcher-')):
        raise ValueError('Invalid service bootstrap')
    retain = bootstrap.get('retainSeconds', 3600)
    if type(retain) not in (float, int) or not 0.2 <= retain <= 3600:
        raise ValueError('Invalid supervisor retention')
    server = socket.socket(socket.AF_UNIX)
    process = None
    started = None
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.bind(bootstrap['address'])
        server.listen(8)
        server.settimeout(0.1)
        log_descriptor = os.open(bootstrap['log'], os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(log_descriptor, 'ab', buffering=0) as output:
            process = subprocess.Popen(command, cwd=bootstrap['cwd'], env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        started = ticks(process.pid)
        # An immediately exited child still has an authentic Popen wait status.
        if started is None:
            process.wait(timeout=5)
        identity = {'schema': 1, 'supervisorPid': os.getpid(), 'supervisorStarted': ticks(os.getpid()),
            'pid': process.pid, 'started': started, 'uid': os.getuid(),
            'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'address': bootstrap['address'], 'key': bootstrap['key'],
            'image': str(Path(command[0]).resolve()), 'directory': str(directory)}
        atomic_json(directory / 'identity.json', identity)
        ended = None
        while not stopping:
            code = process.poll()
            live_members = members(process.pid, started) if started is not None and code is not None else {}
            done = code is not None and not live_members
            if done:
                ended = ended or time.monotonic()
                if time.monotonic() - ended > retain:
                    break
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(1)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.getuid():
                        continue
                    request = receive(connection)
                    if not hmac.compare_digest(str(request.get('key', '')), identity['key']):
                        continue
                    action = request.get('action')
                    if action == 'signal':
                        if not done:
                            signal_child(process, started, request['signal'])
                    elif action == 'release':
                        if not done:
                            raise ValueError('A live service cannot release its supervisor')
                        stopping = True
                    elif action != 'status':
                        raise ValueError('Unknown supervisor action')
                    response = {'ok': True, 'pid': process.pid, 'code': code if done else None}
                    connection.sendall(json.dumps(response).encode() + b'\n')
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # A malformed client never changes process ownership.
        if process.poll() is None or (started is not None and members(process.pid, started)):
            signal_child(process, started, signal.SIGKILL)
            process.wait(timeout=5)
    except BaseException as exc:
        if process is not None:
            try:
                signal_child(process, started, signal.SIGKILL)
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired, TypeError):
                pass
        atomic_json(directory / 'error.json', {'error': type(exc).__name__})
        raise
    finally:
        server.close()
        # Remove only this supervisor's exact protocol files. Unknown content
        # is preserved; no recursive deletion or traversal is used here.
        for name in ('identity.json', 'identity.pending'):
            (directory / name).unlink(missing_ok=True)
        try:
            directory.rmdir()
        except OSError:
            pass


if __name__ == '__main__':
    main()
