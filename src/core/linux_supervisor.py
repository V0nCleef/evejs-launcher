"""Authenticated service control that survives replacing the launcher GUI."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid

from .host import external_process_environment, state_directory
from .linux_process import start_ticks

_FIELDS = {'schema', 'supervisorPid', 'supervisorStarted', 'pid', 'started', 'uid',
           'boot', 'address', 'key', 'image', 'directory'}


def _private_directory(path: Path):
    if path.is_symlink():
        raise OSError('The Linux supervisor directory must not be redirected.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('The Linux supervisor directory must be private to this user.')


class SupervisedServiceProcess:
    """Popen-compatible lifecycle with real wait status held by a stable parent."""

    def __init__(self, descriptor: dict, *, termination_signal=signal.SIGTERM):
        if not isinstance(descriptor, dict) or set(descriptor) != _FIELDS or descriptor.get('schema') != 1:
            raise ValueError('Invalid Linux supervisor identity.')
        for name in ('pid', 'supervisorPid', 'uid'):
            if type(descriptor[name]) is not int or descriptor[name] < (0 if name == 'uid' else 1):
                raise ValueError('Invalid Linux supervisor process identity.')
        if (descriptor['uid'] != os.getuid()
                or descriptor['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                or not isinstance(descriptor['key'], str) or len(descriptor['key']) != 64
                or not isinstance(descriptor['address'], str) or not descriptor['address'].startswith('\0evejs-launcher-')
                or len(descriptor['address']) > 100
                or not Path(descriptor['image']).is_absolute()
                or not Path(descriptor['directory']).is_absolute()
                or start_ticks(descriptor['supervisorPid']) != descriptor['supervisorStarted']):
            raise ValueError('The Linux service supervisor identity changed.')
        self.descriptor = dict(descriptor)
        self.pid = descriptor['pid']
        self.started = descriptor['started']
        self.termination_signal = termination_signal
        self.returncode = None
        self._released = False
        self._stop_observer = threading.Event()
        self._last_error = None
        self._query_lock = threading.Lock()
        self._release_lock = threading.Lock()
        self._query('status')  # Authenticate before claiming an owned service.
        self._observer = threading.Thread(target=self._observe, daemon=True, name='linux-service-status')
        self._observer.start()

    def _query(self, action: str, **values):
        with self._query_lock:
            return self._query_serial(action, **values)

    def _query_serial(self, action: str, **values):
        if action == 'status' and self.returncode is not None:
            return {'ok': True, 'pid': self.pid, 'code': self.returncode}
        descriptor = self.descriptor
        if start_ticks(descriptor['supervisorPid']) != descriptor['supervisorStarted']:
            raise OSError('The Linux service supervisor is no longer available; exit status is unknown.')
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(1)
            connection.connect(descriptor['address'])
            pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid() or pid != descriptor['supervisorPid']:
                raise OSError('The Linux service control endpoint changed owner.')
            connection.sendall(json.dumps({'key': descriptor['key'], 'action': action, **values}).encode() + b'\n')
            content = bytearray()
            while len(content) <= 16384:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                content.extend(chunk)
                if b'\n' in chunk:
                    break
            if len(content) > 16384 or not content.endswith(b'\n'):
                raise OSError('The Linux supervisor returned an invalid response.')
            response = json.loads(content)
            if (response.get('ok') is not True or response.get('pid') != self.pid
                    or (response.get('code') is not None and type(response['code']) is not int)):
                raise OSError('The Linux supervisor response does not match this service.')
            self.returncode = response['code']
            return response

    def _observe(self):
        while not self._stop_observer.wait(0.2):
            try:
                self._query('status')
                if self.returncode is not None:
                    self.release()
                    break
            except (OSError, ValueError) as exc:
                self._last_error = exc
                break  # Unknown never becomes a successful exit status.

    def poll(self):
        # GUI timers read a cached value; IPC and /proc scans stay off the GUI.
        return self.returncode

    def send_group_signal(self, value):
        if value not in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            raise ValueError('Unsupported service signal.')
        if self.returncode is None:
            self._query('signal', signal=int(value))
        return True

    def terminate(self):
        self.send_group_signal(self.termination_signal)

    def kill(self):
        self.send_group_signal(signal.SIGKILL)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.returncode is None:
            self._query('status')
            if self.returncode is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('Linux supervised service', timeout)
            time.sleep(0.05)
        return self.returncode

    def release(self):
        """Reap only a completed supervisor; a live service remains owned."""
        with self._release_lock:
            if self._released:
                return
            self.wait(timeout=0)
            self._stop_observer.set()
            # The observer may be the caller. A public release can also race
            # its final status delivery; neither path waits for the other lock.
            self._query('release')
            raw = getattr(self, '_supervisor_popen', None)
            if raw is not None:
                raw.wait(timeout=5)
            self._released = True
            from .linux_process import _OWNED
            if _OWNED.get(self.pid) is self:
                _OWNED.pop(self.pid, None)

    def detach(self):
        """An outgoing GUI releases its observer, preserving live wait ownership."""
        self._stop_observer.set()
        self._observer.join(timeout=2)


def start_supervised_service(command: list[str], *, cwd: Path, env: dict, log: Path,
                             termination_signal=signal.SIGTERM, retain_seconds=3600) -> SupervisedServiceProcess:
    python = shutil.which('python3')
    if not python:
        raise OSError('Linux service handoff requires the installed python3 runtime.')
    parent = state_directory() / 'service-supervisors'
    _private_directory(parent)
    source = Path(__file__).parent / 'helpers/linux_service_supervisor.py'
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    script = parent / ('supervisor-' + digest + '.py')
    if script.is_symlink():
        raise OSError('The Linux supervisor script must not be redirected.')
    if not script.exists():
        descriptor = os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    if not hmac.compare_digest(hashlib.sha256(script.read_bytes()).hexdigest(), digest):
        raise OSError('The retained Linux service supervisor changed.')
    directory = Path(tempfile.mkdtemp(prefix='service-', dir=parent))
    bootstrap = {'command': command, 'cwd': str(cwd), 'environment': external_process_environment(env),
        'log': str(log), 'key': secrets.token_hex(32),
        'address': '\0evejs-launcher-' + str(os.getuid()) + '-' + uuid.uuid4().hex,
        'retainSeconds': retain_seconds}
    descriptor = os.open(directory / 'bootstrap.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(bootstrap, stream)
    raw = subprocess.Popen([python, '-I', str(script), str(directory)], cwd=str(cwd),
        env=external_process_environment(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 10
    try:
        while True:
            identity = directory / 'identity.json'
            if identity.exists():
                process = SupervisedServiceProcess(json.loads(identity.read_bytes()), termination_signal=termination_signal)
                process._supervisor_popen = raw
                from .linux_process import _OWNED
                _OWNED[process.pid] = process
                return process
            if raw.poll() is not None:
                raise OSError('The Linux service supervisor could not start. No service ownership was granted.')
            if time.monotonic() >= deadline:
                raise OSError('The Linux service supervisor startup timed out.')
            time.sleep(0.02)
    except BaseException:
        if raw.poll() is None:
            raw.terminate()  # The supervisor kills/reaps its owned service group.
        raw.wait(timeout=10)
        raise
