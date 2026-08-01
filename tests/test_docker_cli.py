"""Focused tests for the bounded, argv-only Docker CLI runner."""
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from src.core import platform
from src.core.runtime.docker_cli import DockerCommandError, DockerCommandRunner


CANARY = "CANARY_SECRET_VALUE"


def test_runner_preserves_windows_path_as_one_argv_element_and_explicit_cwd() -> None:
    calls: list[tuple[tuple[str, ...], Path, float]] = []

    def execute(argv: tuple[str, ...], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append((argv, cwd, timeout))
        return subprocess.CompletedProcess(argv, 0, "client-ok", "")

    runner = DockerCommandRunner(executable="C:/Program Files/Docker/Docker/resources/bin/docker.exe", execute=execute)
    result = runner.run(("compose", "-f", "C:/Fixture Space/EveJS/compose.yaml", "version"), cwd=Path("C:/Fixture Space/EveJS"))

    assert result.ok
    assert calls == [(
        ("C:/Program Files/Docker/Docker/resources/bin/docker.exe", "compose", "-f", "C:/Fixture Space/EveJS/compose.yaml", "version"),
        Path("C:/Fixture Space/EveJS"),
        10.0,
    )]


def test_runner_nonzero_diagnostic_is_bounded_and_redacted() -> None:
    def execute(argv: tuple[str, ...], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 17, CANARY + "\n" + ("x" * 500), CANARY + "\n" + ("y" * 500))

    runner = DockerCommandRunner(executable="docker", execute=execute, output_limit=64)

    with pytest.raises(DockerCommandError) as raised:
        runner.run(("version",), cwd=Path("C:/Fixture"))

    result = raised.value.result
    assert result.returncode == 17
    assert result.truncated is True
    assert CANARY not in str(raised.value)
    assert CANARY not in result.stdout
    assert CANARY not in result.stderr
    assert len(result.stdout) <= 64
    assert len(result.stderr) <= 64


def test_runner_timeout_is_structured_and_does_not_expose_output() -> None:
    def execute(argv: tuple[str, ...], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, timeout, output=CANARY, stderr=CANARY)

    runner = DockerCommandRunner(executable="docker", execute=execute)

    with pytest.raises(DockerCommandError) as raised:
        runner.run(("version",), cwd=Path("C:/Fixture"), timeout=0.1)

    assert raised.value.result.timed_out is True
    assert CANARY not in str(raised.value)
    assert CANARY not in raised.value.result.stdout
    assert CANARY not in raised.value.result.stderr


def test_bounded_runner_assigns_suspended_process_to_job_before_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    events: list[tuple[str, int | float]] = []

    class Process:
        _handle = 91
        pid = 42_424

        def wait(self, timeout: float) -> int:
            events.append(("wait", timeout))
            return 0

        def kill(self) -> None:
            raise AssertionError("successful command must not be killed")

    def popen(*args: object, **kwargs: object) -> Process:
        observed.update(kwargs)
        events.append(("popen", int(kwargs["creationflags"])))
        return Process()

    monkeypatch.setattr(
        platform,
        "get_suspended_hidden_process_flags",
        lambda: {"creationflags": 123},
    )
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda handle: events.append(("create_job", handle)) or 77,
    )
    monkeypatch.setattr(
        platform,
        "resume_process",
        lambda handle: events.append(("resume", handle)) or True,
    )
    monkeypatch.setattr(
        platform,
        "close_job",
        lambda handle: events.append(("close_job", handle)),
    )
    monkeypatch.setattr(
        platform,
        "terminate_job",
        lambda _handle: pytest.fail("normal completion must not terminate the Job"),
    )
    monkeypatch.setattr("src.core.runtime.docker_cli.subprocess.Popen", popen)
    runner = DockerCommandRunner(executable="docker")

    runner._execute_bounded(("docker", "version"), Path("."), 1.0)

    assert observed["creationflags"] == 123
    assert events == [
        ("popen", 123),
        ("create_job", 91),
        ("resume", 91),
        ("wait", 1.0),
        ("close_job", 77),
    ]


def test_missing_process_handle_kills_and_boundedly_reaps_suspended_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | float]] = []

    class Process:
        pid = 42_428

        def kill(self) -> None:
            events.append(("kill", self.pid))

        def wait(self, timeout: float) -> int:
            events.append(("wait", timeout))
            return -9

    monkeypatch.setattr(
        "src.core.runtime.docker_cli.subprocess.Popen",
        lambda *_args, **_kwargs: events.append(("popen", 1)) or Process(),
    )
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda _handle: pytest.fail("a missing process handle cannot be assigned"),
    )

    runner = DockerCommandRunner(executable="docker")
    with pytest.raises(OSError, match="handle"):
        runner._execute_bounded(("docker", "version"), Path("."), 1.0)

    assert events[:2] == [("popen", 1), ("kill", 42_428)]
    assert len(events) == 3
    assert events[2][0] == "wait" and events[2][1] > 0


def test_job_assignment_failure_kills_and_boundedly_reaps_suspended_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | float]] = []

    class Process:
        _handle = 92
        pid = 42_425

        def kill(self) -> None:
            events.append(("kill", self.pid))

        def wait(self, timeout: float) -> int:
            events.append(("wait", timeout))
            return -9

    monkeypatch.setattr(
        "src.core.runtime.docker_cli.subprocess.Popen",
        lambda *_args, **_kwargs: events.append(("popen", 1)) or Process(),
    )
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda handle: events.append(("create_job", handle)) or None,
    )
    monkeypatch.setattr(
        platform,
        "resume_process",
        lambda _handle: pytest.fail("an uncontained process must never be resumed"),
    )
    monkeypatch.setattr(
        platform,
        "close_job",
        lambda _handle: pytest.fail("there is no Job handle to close"),
    )

    runner = DockerCommandRunner(executable="docker")
    with pytest.raises(OSError, match="contain"):
        runner._execute_bounded(("docker", "version"), Path("."), 1.0)

    assert events[:3] == [("popen", 1), ("create_job", 92), ("kill", 42_425)]
    assert len(events) == 4
    assert events[3][0] == "wait" and events[3][1] > 0


def test_resume_failure_terminates_and_boundedly_reaps_assigned_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | float]] = []

    class Process:
        _handle = 93
        pid = 42_426

        def wait(self, timeout: float) -> int:
            events.append(("wait", timeout))
            return -1

        def kill(self) -> None:
            pytest.fail("the assigned Job must own resume-failure termination")

    monkeypatch.setattr(
        "src.core.runtime.docker_cli.subprocess.Popen",
        lambda *_args, **_kwargs: events.append(("popen", 1)) or Process(),
    )
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda handle: events.append(("create_job", handle)) or 78,
    )
    monkeypatch.setattr(
        platform,
        "resume_process",
        lambda handle: events.append(("resume", handle)) or False,
    )
    monkeypatch.setattr(
        platform,
        "terminate_job",
        lambda handle: events.append(("terminate_job", handle)) or True,
    )
    monkeypatch.setattr(
        platform,
        "close_job",
        lambda handle: events.append(("close_job", handle)),
    )

    runner = DockerCommandRunner(executable="docker")
    with pytest.raises(OSError, match="resume"):
        runner._execute_bounded(("docker", "version"), Path("."), 1.0)

    assert [name for name, _value in events] == [
        "popen",
        "create_job",
        "resume",
        "terminate_job",
        "wait",
        "close_job",
    ]
    wait_timeout = next(value for name, value in events if name == "wait")
    assert wait_timeout > 0


def test_timeout_terminates_job_and_uses_only_bounded_reap_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int | float]] = []

    class Process:
        _handle = 94
        pid = 42_427

        def wait(self, timeout: float) -> int:
            events.append(("wait", timeout))
            if sum(name == "wait" for name, _value in events) == 1:
                raise subprocess.TimeoutExpired("docker", timeout)
            return -1

        def kill(self) -> None:
            pytest.fail("timeout cleanup must terminate the containing Job")

    monkeypatch.setattr(
        "src.core.runtime.docker_cli.subprocess.Popen",
        lambda *_args, **_kwargs: events.append(("popen", 1)) or Process(),
    )
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda handle: events.append(("create_job", handle)) or 79,
    )
    monkeypatch.setattr(
        platform,
        "resume_process",
        lambda handle: events.append(("resume", handle)) or True,
    )
    monkeypatch.setattr(
        platform,
        "terminate_job",
        lambda handle: events.append(("terminate_job", handle)) or True,
    )
    monkeypatch.setattr(
        platform,
        "close_job",
        lambda handle: events.append(("close_job", handle)),
    )

    runner = DockerCommandRunner(executable="docker")
    with pytest.raises(subprocess.TimeoutExpired):
        runner._execute_bounded(("docker", "version"), Path("."), 0.05)

    assert [name for name, _value in events] == [
        "popen",
        "create_job",
        "resume",
        "wait",
        "terminate_job",
        "wait",
        "close_job",
    ]
    wait_timeouts = [value for name, value in events if name == "wait"]
    assert wait_timeouts and all(timeout > 0 for timeout in wait_timeouts)


def test_docker_resolution_uses_which_then_desktop_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.core.runtime.docker_cli.shutil.which", lambda _: None)
    monkeypatch.setattr("src.core.runtime.docker_cli.Path.is_file", lambda _: True)

    assert DockerCommandRunner.resolve_executable() == DockerCommandRunner.DOCKER_DESKTOP_FALLBACK
