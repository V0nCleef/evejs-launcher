"""Transfer only proven EVE processes; pidfds prevent PID-reuse signalling."""
from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import subprocess
import time

from .linux_process import ProtonClientProcess, start_ticks, retire_settings_alias

# A reconnected non-child has no waitpid exit status. This impossible Linux
# signal value is explicitly unknown, never a fabricated successful exit.
UNKNOWN_EXIT_STATUS = -255


def _boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def _identity(pid):
    if type(pid) is not int or pid <= 1:
        raise ValueError('Invalid Linux client PID.')
    folder = Path(f'/proc/{pid}')
    if folder.stat().st_uid != os.getuid():
        raise ValueError('The EVE process belongs to another user.')
    started = start_ticks(pid)
    if started is None:
        raise ValueError('The EVE process has exited.')
    return {'pid': pid, 'started': started, 'image': os.readlink(folder / 'exe')}


def capture_client(process) -> dict:
    if isinstance(process, PinnedClientProcess):
        return process.export()
    if not isinstance(process, ProtonClientProcess):
        raise ValueError('The Linux client is not owned by a Proton launch.')
    pins = []
    for pid, started in process.snapshot_clients().items():
        try:
            identity = _identity(pid)
        except (OSError, ValueError):
            continue
        if identity['started'] != started:
            continue
        # The Proton Python wrapper also carries the arguments. Its eventual
        # exec/exit cannot substitute for an identified EVE client process.
        if pid == process.process.pid and 'wine' not in Path(identity['image']).name.casefold():
            continue
        pins.append(identity)
    if not pins:
        raise ValueError('Wait for the EVE client to finish starting before updating the launcher.')
    return {'schema': 1, 'boot': _boot(), 'uid': os.getuid(), 'processes': pins}


class PinnedClientProcess:
    """The tracker surface for exact EVE processes after the old GUI exits."""
    exit_status_known = False
    def __init__(self, descriptor):
        if (not isinstance(descriptor, dict) or set(descriptor) != {'schema', 'boot', 'uid', 'processes'}
                or type(descriptor['schema']) is not int or descriptor['schema'] != 1
                or type(descriptor['uid']) is not int or descriptor['boot'] != _boot() or descriptor['uid'] != os.getuid()
                or not isinstance(descriptor['processes'], list) or not 1 <= len(descriptor['processes']) <= 64):
            raise ValueError('Invalid Linux client ownership record.')
        self._pins = []
        self.returncode = None
        self.settings_alias = None
        seen = set()
        try:
            for identity in descriptor['processes']:
                if (not isinstance(identity, dict) or set(identity) != {'pid', 'started', 'image'}
                        or type(identity['pid']) is not int or identity['pid'] in seen
                        or not isinstance(identity['image'], str) or not Path(identity['image']).is_absolute()
                        or not isinstance(identity['started'], str) or not identity['started'].isdigit()):
                    raise ValueError('Invalid EVE process identity.')
                seen.add(identity['pid'])
                descriptor_fd = None
                try:
                    descriptor_fd = os.pidfd_open(identity['pid'])
                    if _identity(identity['pid']) != identity:
                        raise ValueError('The EVE process identity changed.')
                except (OSError, ValueError):
                    if descriptor_fd is not None:
                        os.close(descriptor_fd)
                    continue
                self._pins.append((dict(identity), descriptor_fd))
            if not self._pins:
                raise ValueError('The recorded EVE processes have exited or changed identity.')
            self.pid = self._pins[0][0]['pid']
        except BaseException:
            self.close()
            raise

    def export(self):
        if self.poll() is not None:
            raise ValueError('The EVE process has exited.')
        return {'schema': 1, 'boot': _boot(), 'uid': os.getuid(),
                'processes': [identity for identity, descriptor in self._pins if not self._exited(descriptor)]}

    @staticmethod
    def _exited(descriptor):
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        return bool(poller.poll(0))

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if all(self._exited(descriptor) for _, descriptor in self._pins):
            self.returncode = UNKNOWN_EXIT_STATUS
            self.close()
            if self.settings_alias is not None:
                retire_settings_alias(self.settings_alias)
                self.settings_alias = None
        return self.returncode

    def _signal(self, value):
        for _, descriptor in self._pins:
            if not self._exited(descriptor):
                try:
                    signal.pidfd_send_signal(descriptor, value)
                except ProcessLookupError:
                    pass

    def terminate(self):
        self._signal(signal.SIGTERM)

    def kill(self):
        self._signal(signal.SIGKILL)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('Reconnected Linux EVE client', timeout)
            time.sleep(0.05)
        return self.returncode

    def close(self):
        for _, descriptor in getattr(self, '_pins', ()):
            os.close(descriptor)
        self._pins = []

    def __del__(self):
        self.close()
