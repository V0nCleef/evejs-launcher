"""Linux process ownership: isolated service groups, exact client identities."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

_OWNED: dict[int, "OwnedServiceProcess"] = {}


def retire_settings_alias(value):
    if value is None:
        return
    alias, target = value
    try:
        if alias.is_symlink() and alias.resolve() == target:
            alias.unlink()
    except OSError:
        import logging
        logging.getLogger(__name__).warning('Could not remove a completed EVE settings alias.')


def signal_pinned_client(pid, started, requested_signal):
    """Pin the kernel process before the final identity check and signal."""
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        if start_ticks(pid) == started:
            try:
                signal.pidfd_send_signal(descriptor, requested_signal)
            except ProcessLookupError:
                pass
    finally:
        os.close(descriptor)


def start_ticks(pid: int) -> str | None:
    try:
        # comm may contain spaces and parentheses. Fields after its final ')'
        # start at field 3; starttime is field 22.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


class OwnedServiceProcess:
    """Retain the Popen and start identity of a group we actually created."""
    def __init__(self, process: subprocess.Popen, *, termination_signal: int = signal.SIGTERM):
        self.process = process
        self.termination_signal = termination_signal
        self.pid = process.pid
        self.started = start_ticks(self.pid)
        if self.started is None and process.poll() is not None:
            return
        if self.started is None or os.getpgid(self.pid) != self.pid:
            process.terminate()
            process.wait(timeout=5)
            raise OSError("The Linux service did not enter its owned process group.")
        _OWNED[self.pid] = self

    def poll(self):
        result = self.process.poll()
        if result is not None:
            if self._members():
                return None
            self._retire()
        return result

    def _retire(self):
        if self.process.poll() is None or self._members():
            return  # Retain shutdown authority until the entire group is gone.
        if _OWNED.get(self.pid) is self:
            _OWNED.pop(self.pid, None)

    def __getattr__(self, name):
        return getattr(self.process, name)

    def _members(self) -> dict[int, str]:
        if self.started is None:
            return {}
        members = {}
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                if (path.stat().st_uid == os.getuid() and fields[0] != 'Z'
                        and fields[2] == str(self.pid) and fields[3] == str(self.pid)
                        and int(fields[19]) >= int(self.started)):
                    members[int(path.name)] = fields[19]
            except (OSError, ValueError, IndexError):
                continue
        return members

    def send_group_signal(self, value: int) -> bool:
        # Never adopt by PID/port, and never signal a reused process identity.
        if self.poll() is not None:
            return True
        if start_ticks(self.pid) is None:
            # A delegated child can outlive its wrapper. Only the pinned live
            # members of the original session/group retain shutdown authority.
            for pid, tick in self._members().items():
                if start_ticks(pid) == tick:
                    try:
                        os.kill(pid, value)
                    except ProcessLookupError:
                        pass
            return True
        if start_ticks(self.pid) != self.started or os.getpgid(self.pid) != self.pid:
            raise OSError("The Linux service ownership identity changed.")
        try:
            os.killpg(self.pid, value)
        except ProcessLookupError:
            pass
        return True

    def terminate(self):
        self.send_group_signal(self.termination_signal)

    def kill(self):
        self.send_group_signal(signal.SIGKILL)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        code = self.process.wait(timeout=timeout)
        while self._members():
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired('Linux process group', timeout)
            time.sleep(0.02)
        self._retire()
        return code


def signal_owned_service(pid: int, value: int) -> bool:
    owned = _OWNED.get(pid)
    if owned is None:
        raise OSError("This launcher does not own that Linux process group.")
    return owned.send_group_signal(value)


def run_bounded(command: list[str], *, cwd: Path, env: dict | None = None,
                timeout: float = 30, limit: int = 65536) -> str:
    """Bound output on disk and reap a timed-out owned helper's whole group."""
    from .host import external_process_environment
    with tempfile.TemporaryFile() as output:
        raw = subprocess.Popen(command, cwd=str(cwd), env=external_process_environment(env), stdin=subprocess.DEVNULL,
                               stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        process = OwnedServiceProcess(raw)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if os.fstat(output.fileno()).st_size > limit:
                    process.kill()
                    process.wait(timeout=5)
                    raise RuntimeError('Linux runtime check exceeded its output limit.')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    code = process.wait(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("The Linux runtime check timed out; its owned process group was stopped.") from None
        finally:
            process._retire()
        output.seek(0)
        data = output.read(limit + 1)
        text = data.decode("utf-8", errors="replace")
        if code != 0:
            raise RuntimeError("Linux runtime preparation failed: " + text[-2048:])
        if len(data) > limit:
            raise RuntimeError("Linux runtime check exceeded its output limit.")
        return text


class ProtonClientProcess:
    """A Proton wrapper plus EVE identities bearing one unique profile option.

    A shared prefix's wineserver is deliberately outside termination authority.
    Every discovered EVE PID is pinned to its /proc start time before use.
    """
    def __init__(self, process: subprocess.Popen, marker: str, executable: Path):
        self.process = process
        self.pid = process.pid
        self.marker = marker.encode()
        self.executable = executable
        self._clients: dict[int, str] = {}
        self._observation_lock = threading.RLock()
        self._started = time.monotonic()
        self.settings_alias = None

    def _observe(self):
        with self._observation_lock:
            self._observe_locked()

    def snapshot_clients(self):
        with self._observation_lock:
            self._observe_locked()
            return dict(self._clients)

    def launch_ready(self, pid, *, grace_seconds=20):
        """Wayland-safe queue gate: an exact EVE/Wine process must survive startup.

        This is process readiness, not a claim that a compositor exposes a
        visible window. A Proton Python wrapper alone never opens this gate.
        """
        if pid != self.pid or time.monotonic() - self._started < grace_seconds:
            return False
        for client_pid, started in self.snapshot_clients().items():
            try:
                image = Path(os.readlink(f'/proc/{client_pid}/exe')).name.casefold()
                if 'wine' in image and start_ticks(client_pid) == started:
                    return True
            except OSError:
                pass
        return False

    def _observe_locked(self):
        expected = {str(self.executable).encode().lower(), ("z:" + str(self.executable).replace("/", "\\")).encode().lower()}
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                args = (path / "cmdline").read_bytes().split(b"\0")
                if self.marker not in args or not any(arg.lower() in expected for arg in args):
                    continue
                pid = int(path.name)
                tick = start_ticks(pid)
                if tick is not None:
                    self._clients.setdefault(pid, tick)
            except (OSError, ValueError):
                continue
        self._clients = {pid: tick for pid, tick in self._clients.items() if start_ticks(pid) == tick}

    def poll(self):
        clients = self.snapshot_clients()
        code = self.process.poll()
        if clients or code is None:
            return None
        if self.settings_alias is not None:
            retire_settings_alias(self.settings_alias)
            self.settings_alias = None
        return code

    def terminate(self):
        for pid, tick in self.snapshot_clients().items():
            signal_pinned_client(pid, tick, signal.SIGTERM)
        if self.process.poll() is None:
            self.process.terminate()

    def kill(self):
        for pid, tick in self.snapshot_clients().items():
            signal_pinned_client(pid, tick, signal.SIGKILL)
        if self.process.poll() is None:
            self.process.kill()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("Proton client", timeout)
            time.sleep(0.05)
        return self.poll()
