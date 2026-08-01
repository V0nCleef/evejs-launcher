"""Cancelable, read-only Docker Compose log follower."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
from typing import Callable, TextIO

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core import platform
from src.core.runtime.docker_cli import DockerCommandRunner
from src.core.runtime.docker_compose import ComposeTarget


_ALLOWED_SERVICES = frozenset({"server", "market"})
DEFAULT_TAIL = 200
MAX_TAIL = 500
MAX_LINE_CHARS = 8_192
READ_CHUNK_CHARS = 1_024
CANCEL_WAIT_SECONDS = 1.0


def build_log_argv(executable: str, target: ComposeTarget, service: str, tail: int) -> tuple[str, ...]:
    """Build the sole Phase 2B2 Docker command as a strict argv tuple."""
    if service not in _ALLOWED_SERVICES:
        raise ValueError("Docker logs are available only for server or market.")
    if not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= MAX_TAIL:
        raise ValueError("Docker log tail must be within the supported bound.")
    return (*target.base_argv(executable), "logs", "--follow", "--no-color", "--timestamps", "--tail", str(tail), service)


class DockerLogWorker(QObject):
    """Run one bounded local Compose follower in its dedicated QThread."""

    line = pyqtSignal(object, str)
    diagnostic = pyqtSignal(object, str)
    finished = pyqtSignal(object)
    terminal = pyqtSignal()

    def __init__(self, target_factory: Callable[[], ComposeTarget], *, service: str = "server",
                 tail: int = DEFAULT_TAIL, executable_factory: Callable[[], str] | None = None,
                 token: object | None = None) -> None:
        super().__init__()
        if service not in _ALLOWED_SERVICES:
            raise ValueError("Docker logs are available only for server or market.")
        if not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= MAX_TAIL:
            raise ValueError("Docker log tail must be within the supported bound.")
        self._target_factory = target_factory
        self._executable_factory = executable_factory or DockerCommandRunner.resolve_executable
        self._service, self._tail = service, tail
        self.token = token if token is not None else object()
        self._cancelled = threading.Event()
        self._terminate_sent = threading.Event()
        self._force_reap = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._job_handle: int | None = None
        self._reaper: threading.Thread | None = None
        self._reaped_process: object | None = None
        self._reap_result: int | None = None
        self._completed = False

    def request_cancel(self) -> None:
        """Cancel without waiting for a blocked follower or the GUI thread."""
        self._cancelled.set()
        with self._process_lock:
            process = self._process
            reaper = self._reaper
            if process is not None and reaper is None:
                reaper = threading.Thread(
                    target=self._reap_cancelled_process,
                    args=(process,),
                    name="docker-log-reaper",
                    daemon=True,
                )
                self._reaper = reaper
            else:
                reaper = None
        if reaper is not None:
            reaper.start()

    def _reap_cancelled_process(self, process) -> None:
        """Boundedly reap precisely one retained local logs-follower process."""
        result = self._terminate_wait_kill_wait(process)
        with self._process_lock:
            self._reaped_process = process
            self._reap_result = result

    def _terminate_wait_kill_wait(self, process) -> int:
        if not self._terminate_sent.is_set():
            tree_terminated = False
            with self._process_lock:
                job_handle = self._job_handle
            if job_handle is not None:
                try:
                    tree_terminated = platform.terminate_job(job_handle)
                except (OSError, ValueError):
                    tree_terminated = False
            pid = getattr(process, "pid", None)
            if not tree_terminated and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                try:
                    tree_terminated = platform.terminate_process_tree(pid)
                except (OSError, ValueError):
                    tree_terminated = False
            if not tree_terminated and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            self._terminate_sent.set()
        if process.poll() is not None:
            return process.poll()
        try:
            return process.wait(timeout=CANCEL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                return process.wait(timeout=CANCEL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                return process.poll() if process.poll() is not None else -1

    @pyqtSlot()
    def run(self) -> None:
        """Resolve in worker affinity, stream bounded physical lines, then reap."""
        try:
            target = self._target_factory()
            executable = self._executable_factory()
            argv = build_log_argv(executable, target, self._service, self._tail)
            if self._cancelled.is_set():
                return
            process = subprocess.Popen(
                argv, shell=False, cwd=os.fspath(target.project_directory),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **platform.get_suspended_hidden_process_flags(),
            )
            process_handle = getattr(process, "_handle", None)
            if isinstance(process_handle, int) and process_handle > 0:
                job_handle = platform.create_kill_on_close_job(process_handle)
                if job_handle is None:
                    process.kill()
                    process.wait(timeout=CANCEL_WAIT_SECONDS)
                    raise OSError("Could not assign Docker logs to a Windows Job Object.")
                self._job_handle = job_handle
                if not platform.resume_process(process_handle):
                    platform.terminate_job(job_handle)
                    process.wait(timeout=CANCEL_WAIT_SECONDS)
                    raise OSError("Could not resume Docker logs after Job assignment.")
            with self._process_lock:
                self._process = process
            if self._cancelled.is_set():
                self.request_cancel()
            assert process.stdout is not None
            self._stream_bounded(process.stdout)
            returncode = self._reap_once(process)
            if not self._cancelled.is_set() and returncode != 0:
                self.diagnostic.emit(self.token, "Docker log stream ended unexpectedly.")
        except FileNotFoundError:
            self.diagnostic.emit(self.token, "Docker CLI is unavailable.")
        except (ValueError, OSError):
            self._force_reap.set()
            self.diagnostic.emit(self.token, "Docker log stream could not be started.")
        finally:
            with self._process_lock:
                retained = self._process
                self._process = None
            if retained is not None:
                # A read exception skips the normal reap below.  Claiming is
                # idempotent by process identity, so normal paths are not
                # double-waited while exceptional paths cannot orphan a child.
                self._reap_once(retained)
                if retained.stdout is not None:
                    try:
                        retained.stdout.close()
                    except OSError:
                        pass
            with self._process_lock:
                job_handle = self._job_handle
                self._job_handle = None
            if job_handle is not None:
                platform.close_job(job_handle)
            if not self._completed:
                self._completed = True
                self.finished.emit(self.token)
                self.terminal.emit()

    def _stream_bounded(self, stdout: TextIO) -> None:
        """Bound every read and discard an overlong physical-line remainder."""
        partial = ""
        discarding = False
        while not self._cancelled.is_set():
            piece = stdout.readline(READ_CHUNK_CHARS)
            if not piece:
                if partial and not discarding and not self._cancelled.is_set():
                    self.line.emit(self.token, self._sanitize_line(partial))
                return
            newline = piece.endswith("\n") or piece.endswith("\r")
            content = piece.rstrip("\r\n") if newline else piece
            if discarding:
                if newline:
                    discarding = False
                continue
            remaining = MAX_LINE_CHARS - len(partial)
            if len(content) > remaining:
                self.line.emit(self.token, self._sanitize_line(partial + content[:remaining], truncated=True))
                partial = ""
                discarding = not newline
                continue
            partial += content
            if newline:
                self.line.emit(self.token, self._sanitize_line(partial))
                partial = ""

    def _reap_once(self, process) -> int:
        """Coordinate with the cancellation reaper or own one normal wait path."""
        with self._process_lock:
            reaper = self._reaper
        if reaper is not None:
            reaper.join()
            with self._process_lock:
                if self._reaped_process is process:
                    return self._reap_result if self._reap_result is not None else -1
        with self._process_lock:
            if self._reaped_process is process:
                return self._reap_result if self._reap_result is not None else -1
            # Reserve this exact Popen before waiting so finally cannot repeat
            # its ownership path after a stream exception.
            self._reaped_process = process
        if self._cancelled.is_set() or self._force_reap.is_set():
            result = self._terminate_wait_kill_wait(process)
            with self._process_lock:
                self._reap_result = result
            return result
        try:
            result = process.wait(timeout=CANCEL_WAIT_SECONDS)
        except TypeError:
            result = process.wait()
        except subprocess.TimeoutExpired:
            # EOF from the pipe is not proof that docker logs exited.  A live
            # local child is always completed by the bounded cancellation path.
            result = self._terminate_wait_kill_wait(process)
        with self._process_lock:
            self._reap_result = result
        return result

    @staticmethod
    def _sanitize_line(value: str, *, truncated: bool = False) -> str:
        suffix = " [line truncated]" if truncated else ""
        return DockerCommandRunner._redact(value.rstrip("\r\n")[:MAX_LINE_CHARS]) + suffix
