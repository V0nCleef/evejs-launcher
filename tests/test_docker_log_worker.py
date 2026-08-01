"""Contracts for bounded, read-only Docker Compose log following."""
from __future__ import annotations

from pathlib import Path
import subprocess
import threading

import pytest
from PyQt6.QtCore import QThread, Qt

from src.core.runtime.docker_compose import ComposeTarget
from src.workers.docker_log_worker import (
    DockerLogWorker, MAX_LINE_CHARS, READ_CHUNK_CHARS, build_log_argv,
)


TARGET = lambda: ComposeTarget(Path("C:/Eve Root/compose.yaml"), Path("C:/Eve Root"), "eve.project")


def test_log_argv_is_exact_and_rejects_noncanonical_inputs() -> None:
    assert build_log_argv("C:/Program Files/Docker/docker.exe", TARGET(), "server", 123)[-7:] == (
        "logs", "--follow", "--no-color", "--timestamps", "--tail", "123", "server",
    )
    for service, tail in (("Server", 1), ("-server", 1), ("server ", 1), ("server", True),
                          ("server", 1.0), ("server", "1"), ("server", 0), ("server", 501)):
        with pytest.raises(ValueError):
            build_log_argv("docker", TARGET(), service, tail)  # type: ignore[arg-type]


class _ChunkPipe:
    def __init__(self, chunks: list[str]) -> None:
        self.chunks = list(chunks)
        self.limits: list[int] = []
        self.closed = False

    def readline(self, limit: int) -> str:
        self.limits.append(limit)
        if not self.chunks:
            return ""
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(self, pipe: _ChunkPipe, returncode: int = 0) -> None:
        self.stdout, self.returncode = pipe, returncode
        self.terminated = self.killed = 0
        self.waits: list[float | None] = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.returncode

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1


def test_bounded_chunks_normal_final_partial_truncation_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    pipe = _ChunkPipe(["normal\n", "password=topsecret\n", "final", "", "x" * READ_CHUNK_CHARS] * 0)
    # A physical line crosses several bounded reads then has a newline; its rest is discarded.
    pipe.chunks = ["x" * READ_CHUNK_CHARS] * 9 + ["ignored\n", "after\n", "final"]
    process = _Process(pipe)
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    lines: list[str] = []
    worker.line.connect(lambda _token, line: lines.append(line))
    worker.run()
    assert pipe.limits and set(pipe.limits) == {READ_CHUNK_CHARS}
    assert lines[0].endswith("[line truncated]")
    assert len(lines[0]) <= MAX_LINE_CHARS + len(" [line truncated]")
    assert lines[1:] == ["after", "final"]
    assert pipe.closed


def test_popen_contract_and_safe_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    process = _Process(_ChunkPipe(["password=topsecret\n"]))
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: captured.update(argv=argv, kwargs=kwargs) or process)
    lines: list[str] = []
    worker.line.connect(lambda _token, line: lines.append(line))
    worker.run()
    assert captured["kwargs"]["shell"] is False  # type: ignore[index]
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL  # type: ignore[index]
    assert captured["kwargs"]["stderr"] is subprocess.STDOUT  # type: ignore[index]
    assert "topsecret" not in lines[0]


@pytest.mark.parametrize(
    "raw, secret",
    [
        ("Authorization: Bearer private-token-value\n", "private-token-value"),
        ("https://user:password@example.invalid/logs\n", "user:password"),
        ("assignment_secret=assigned-value\n", "assigned-value"),
    ],
)
def test_stream_lines_redact_assignment_bearer_and_url_userinfo(
    monkeypatch: pytest.MonkeyPatch, raw: str, secret: str,
) -> None:
    process = _Process(_ChunkPipe([raw]))
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    lines: list[str] = []
    worker.line.connect(lambda _token, line: lines.append(line))
    worker.run()
    assert len(lines) == 1
    assert secret not in lines[0]


@pytest.mark.parametrize("read_raises", [True, False])
def test_stream_exception_or_lingering_eof_reaps_live_process_once(
    monkeypatch: pytest.MonkeyPatch, read_raises: bool,
) -> None:
    class Pipe(_ChunkPipe):
        def readline(self, limit: int) -> str:
            self.limits.append(limit)
            if read_raises:
                raise OSError("read failure")
            return ""

    class LiveProcess(_Process):
        def __init__(self) -> None:
            super().__init__(Pipe([]))
            self.returncode = None
        def poll(self): return self.returncode
        def wait(self, timeout=None):
            self.waits.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired("docker", timeout)
            return self.returncode
        def terminate(self): self.terminated += 1
        def kill(self):
            self.killed += 1
            self.returncode = -9

    process = LiveProcess()
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    finished: list[object] = []
    terminal: list[bool] = []
    diagnostics: list[str] = []
    worker.finished.connect(finished.append)
    worker.terminal.connect(lambda: terminal.append(True))
    worker.diagnostic.connect(lambda _token, message: diagnostics.append(message))
    worker.run()
    assert process.terminated == process.killed == 1
    expected_waits = 2 if read_raises else 3
    assert len(process.waits) == expected_waits and all(timeout > 0 for timeout in process.waits)
    assert process.stdout.closed
    assert len(finished) == len(terminal) == 1
    assert diagnostics == ["Docker log stream could not be started."] if read_raises else ["Docker log stream ended unexpectedly."]


def test_factories_execute_off_gui_thread_and_cancellation_reaps_once(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    retained = threading.Event()
    released = threading.Event()
    factory_threads: list[int] = []

    class BlockingPipe(_ChunkPipe):
        def readline(self, limit: int) -> str:
            self.limits.append(limit)
            retained.set()
            released.wait(2)
            return ""

    class BlockingProcess(_Process):
        def __init__(self) -> None:
            super().__init__(BlockingPipe([]))
            self.returncode = None
        def poll(self): return self.returncode
        def terminate(self):
            self.terminated += 1
            released.set()
        def wait(self, timeout=None):
            self.waits.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired("docker", timeout)
            return self.returncode
        def kill(self):
            self.killed += 1
            self.returncode = -9
            released.set()

    process = BlockingProcess()
    worker = DockerLogWorker(
        lambda: factory_threads.append(threading.get_ident()) or TARGET(),
        executable_factory=lambda: factory_threads.append(threading.get_ident()) or "docker",
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    thread.finished.connect(worker.deleteLater)
    done = threading.Event()
    worker.finished.connect(lambda _token: done.set(), Qt.ConnectionType.DirectConnection)
    thread.start()
    assert retained.wait(2)
    worker.request_cancel()
    assert done.wait(2)
    thread.wait(2000)
    assert not thread.isRunning()
    assert process.terminated == 1 and process.killed == 1
    assert factory_threads and all(ident != threading.get_ident() for ident in factory_threads)


def test_cancel_reaper_kills_a_follower_that_ignores_terminate(
    qapp, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = threading.Event()
    released = threading.Event()

    class IgnoringPipe(_ChunkPipe):
        def readline(self, limit: int) -> str:
            self.limits.append(limit)
            retained.set()
            released.wait(2)
            return ""

    class IgnoringProcess(_Process):
        def __init__(self) -> None:
            super().__init__(IgnoringPipe([]))
            self.returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.terminated += 1
        def kill(self):
            self.killed += 1
            self.returncode = -9
            released.set()
        def wait(self, timeout=None):
            self.waits.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired("docker", timeout)
            return self.returncode

    process = IgnoringProcess()
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    completed: list[object] = []
    worker.finished.connect(completed.append)
    runner = threading.Thread(target=worker.run)
    runner.start()
    assert retained.wait(2)
    worker.request_cancel()
    worker.request_cancel()
    runner.join(2)
    assert not runner.is_alive()
    assert process.terminated == 1 and process.killed == 1
    assert len(process.waits) == 2 and all(timeout > 0.05 for timeout in process.waits)
    qapp.processEvents()
    assert len(completed) == 1


def test_cancel_reaper_terminates_the_retained_windows_process_tree(
    qapp, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Docker Compose plugin child must not survive its docker.exe parent."""
    retained = threading.Event()
    released = threading.Event()
    tree_pids: list[int] = []

    class BlockingPipe(_ChunkPipe):
        def readline(self, limit: int) -> str:
            self.limits.append(limit)
            retained.set()
            released.wait(2)
            return ""

    class TreeProcess(_Process):
        pid = 42_424

        def __init__(self) -> None:
            super().__init__(BlockingPipe([]))
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.waits.append(timeout)
            if self.returncode is None:
                raise subprocess.TimeoutExpired("docker", timeout)
            return self.returncode

    process = TreeProcess()

    def terminate_tree(pid: int) -> bool:
        tree_pids.append(pid)
        process.returncode = -15
        released.set()
        return True

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        "src.workers.docker_log_worker.platform.terminate_process_tree",
        terminate_tree,
        raising=False,
    )
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    runner = threading.Thread(target=worker.run)
    runner.start()
    assert retained.wait(2)

    worker.request_cancel()
    runner.join(2)

    assert not runner.is_alive()
    assert tree_pids == [process.pid]
    assert process.terminated == process.killed == 0
    qapp.processEvents()


def test_log_follower_is_assigned_to_a_kill_on_close_job_before_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(_ChunkPipe([]))
    process.pid = 42_425  # type: ignore[attr-defined]
    process._handle = 91  # type: ignore[attr-defined]
    captured: dict[str, object] = {}
    job_calls: list[tuple[str, int]] = []

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda argv, **kwargs: captured.update(argv=argv, kwargs=kwargs) or process,
    )
    monkeypatch.setattr(
        "src.workers.docker_log_worker.platform.create_kill_on_close_job",
        lambda handle: job_calls.append(("create", int(handle))) or 77,
        raising=False,
    )
    monkeypatch.setattr(
        "src.workers.docker_log_worker.platform.resume_process",
        lambda handle: job_calls.append(("resume", int(handle))) or True,
        raising=False,
    )
    monkeypatch.setattr(
        "src.workers.docker_log_worker.platform.close_job",
        lambda handle: job_calls.append(("close", int(handle))),
        raising=False,
    )

    DockerLogWorker(TARGET, executable_factory=lambda: "docker").run()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["creationflags"] & 0x00000004  # CREATE_SUSPENDED
    assert job_calls == [("create", 91), ("resume", 91), ("close", 77)]


@pytest.mark.parametrize("returncode", [0, 7])
def test_each_completed_process_has_one_reap_wait(monkeypatch: pytest.MonkeyPatch, returncode: int) -> None:
    process = _Process(_ChunkPipe([]), returncode)
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker")
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    completed: list[object] = []
    worker.finished.connect(completed.append)
    worker.run()
    assert len(process.waits) == 1
    assert process.terminated == process.killed == 0
    assert len(completed) == 1


@pytest.mark.parametrize("cycle", range(3))
def test_terminal_signal_releases_each_real_qthread_cycle(
    qapp, monkeypatch: pytest.MonkeyPatch, cycle: int,
) -> None:
    process = _Process(_ChunkPipe([]))
    worker = DockerLogWorker(TARGET, executable_factory=lambda: "docker", token=cycle)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.terminal.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
    worker.terminal.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    thread.start()
    assert thread.wait(2_000)
    qapp.processEvents()
    assert not thread.isRunning()
