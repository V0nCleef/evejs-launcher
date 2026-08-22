"""Focused tests for the bounded, argv-only Docker CLI runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from src.core import platform
from src.core.runtime.docker_cli import (
    DockerCommandError,
    DockerCommandRunner,
    DockerStructuredOutputError,
)


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


def test_input_runner_preserves_paths_and_uses_separate_executor() -> None:
    payload = b'{"username":"Pilot One"}'
    calls: list[tuple[tuple[str, ...], Path, float, bytes]] = []

    def execute(_argv: tuple[str, ...], _cwd: Path, _timeout: float) -> subprocess.CompletedProcess[str]:
        pytest.fail("run_with_input must not use the legacy three-argument executor")

    def execute_input(
        argv: tuple[str, ...],
        cwd: Path,
        timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, cwd, timeout, input_bytes))
        return subprocess.CompletedProcess(argv, 0, "created", "")

    runner = DockerCommandRunner(
        executable="C:/Program Files/Docker/Docker/resources/bin/docker.exe",
        execute=execute,
        execute_input=execute_input,
    )
    result = runner.run_with_input(
        ("compose", "-f", "C:/Fixture Space/EveJS/compose.yaml", "run", "--rm", "server"),
        cwd=Path("C:/Fixture Space/EveJS"),
        input_bytes=payload,
        timeout=2.5,
    )

    assert result.ok
    assert calls == [(
        (
            "C:/Program Files/Docker/Docker/resources/bin/docker.exe",
            "compose",
            "-f",
            "C:/Fixture Space/EveJS/compose.yaml",
            "run",
            "--rm",
            "server",
        ),
        Path("C:/Fixture Space/EveJS"),
        2.5,
        payload,
    )]
    assert payload.decode() not in repr(result)


def test_input_runner_rejects_payload_over_16_kib_before_execution() -> None:
    payload = CANARY.encode() + (b"x" * (16 * 1024))

    def execute_input(
        _argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
        _input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        pytest.fail("oversize input must be rejected before execution")

    runner = DockerCommandRunner(executable="docker", execute_input=execute_input)

    with pytest.raises(ValueError, match="16 KiB") as raised:
        runner.run_with_input(("compose",), cwd=Path("C:/Fixture"), input_bytes=payload)

    assert CANARY not in str(raised.value)


def test_input_runner_scrubs_an_exact_payload_echo_from_success_output() -> None:
    payload = CANARY.encode()

    def execute_input(
        argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        echoed = input_bytes.decode()
        return subprocess.CompletedProcess(argv, 0, f"before {echoed} after", echoed)

    runner = DockerCommandRunner(executable="docker", execute_input=execute_input)
    result = runner.run_with_input(("compose",), cwd=Path("C:/Fixture"), input_bytes=payload)

    assert result.ok
    assert CANARY not in result.stdout
    assert CANARY not in result.stderr
    assert "[REDACTED]" in result.stdout


def test_input_runner_scrubs_before_truncating_payload_echo() -> None:
    payload = b'{"username":"PRIVATE_ACCOUNT"}'

    def execute_input(
        argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, input_bytes.decode() + " tail", "")

    runner = DockerCommandRunner(
        executable="docker",
        execute_input=execute_input,
        output_limit=8,
    )
    result = runner.run_with_input(
        ("compose",),
        cwd=Path("C:/Fixture"),
        input_bytes=payload,
    )

    assert "PRIVATE" not in result.stdout
    assert "PRIVATE_ACCOUNT" not in result.stdout
    assert result.truncated is True


def test_input_runner_timeout_drops_payload_and_originating_exception() -> None:
    payload = CANARY.encode()

    def execute_input(
        argv: tuple[str, ...],
        _cwd: Path,
        timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        echoed = input_bytes.decode()
        raise subprocess.TimeoutExpired(argv, timeout, output=echoed, stderr=echoed)

    runner = DockerCommandRunner(executable="docker", execute_input=execute_input)

    with pytest.raises(DockerCommandError) as raised:
        runner.run_with_input(("compose",), cwd=Path("C:/Fixture"), input_bytes=payload, timeout=0.1)

    error = raised.value
    assert error.result.timed_out is True
    assert error.__cause__ is None
    assert error.__context__ is None
    assert CANARY not in str(error)
    assert CANARY not in repr(error.result)


def test_input_runner_os_error_drops_payload_and_originating_exception() -> None:
    payload = CANARY.encode()

    def execute_input(
        _argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        raise OSError(input_bytes.decode())

    runner = DockerCommandRunner(executable="docker", execute_input=execute_input)

    with pytest.raises(DockerCommandError) as raised:
        runner.run_with_input(("compose",), cwd=Path("C:/Fixture"), input_bytes=payload)

    error = raised.value
    assert error.result.returncode is None
    assert error.result.timed_out is False
    assert error.__cause__ is None
    assert error.__context__ is None
    assert CANARY not in str(error)
    assert CANARY not in repr(error.result)


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


def test_structured_runner_hashes_unredacted_json_without_returning_it() -> None:
    raw = json.dumps(
        {
            "NODE_OPTIONS": f"--require /mods/loader.js password={CANARY}",
            "DATABASE_PASSWORD": "SECOND_PRIVATE_VALUE",
        },
        separators=(",", ":"),
    )

    def execute(
        argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, raw, "ignored stderr")

    runner = DockerCommandRunner(executable="docker", execute=execute)
    projection = runner.run_parsed(
        ("inspect",),
        cwd=Path("C:/Fixture"),
        parser=lambda stdout: hashlib.sha256(
            json.loads(stdout)["NODE_OPTIONS"].encode("utf-8")
        ).hexdigest(),
    )

    assert projection == hashlib.sha256(
        f"--require /mods/loader.js password={CANARY}".encode("utf-8")
    ).hexdigest()
    assert CANARY not in projection
    assert "SECOND_PRIVATE_VALUE" not in projection


def test_structured_runner_drops_raw_json_from_parser_failure_context() -> None:
    raw = f'{{"password":"{CANARY}"'

    def execute(
        argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, raw, CANARY)

    runner = DockerCommandRunner(executable="docker", execute=execute)
    with pytest.raises(DockerStructuredOutputError) as raised:
        runner.run_parsed(
            ("inspect",),
            cwd=Path("C:/Fixture"),
            parser=json.loads,
        )

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert CANARY not in str(raised.value)
    assert CANARY not in repr(raised.value)
    traceback = raised.value.__traceback__
    runner_frames = []
    while traceback is not None:
        name = traceback.tb_frame.f_code.co_name
        if name in {"run_parsed", "_consume_parsed_output"}:
            runner_frames.append((name, repr(traceback.tb_frame.f_locals)))
        traceback = traceback.tb_next
    assert [name for name, _locals in runner_frames] == ["run_parsed"]
    assert all(CANARY not in locals_repr for _name, locals_repr in runner_frames)


def test_structured_runner_rejects_truncation_before_parser() -> None:
    parser_called = False

    def execute(
        argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, CANARY * 4, "")

    def parser(_stdout: str) -> str:
        nonlocal parser_called
        parser_called = True
        return "unsafe"

    runner = DockerCommandRunner(
        executable="docker",
        execute=execute,
        output_limit=8,
    )
    with pytest.raises(DockerStructuredOutputError) as raised:
        runner.run_parsed(
            ("inspect",),
            cwd=Path("C:/Fixture"),
            parser=parser,
        )

    assert raised.value.code == "too_large"
    assert not parser_called
    assert CANARY not in str(raised.value)


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


def test_bounded_input_runner_uses_tempfile_stdin_and_same_job_containment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b'{"password":"not-on-the-command-line"}'
    observed: dict[str, object] = {}
    events: list[tuple[str, int | float]] = []

    class Process:
        _handle = 95

        def wait(self, timeout: float) -> int:
            events.append(("wait", timeout))
            return 0

        def kill(self) -> None:
            pytest.fail("successful command must not be killed")

    def popen(*args: object, **kwargs: object) -> Process:
        stdin = kwargs["stdin"]
        assert stdin != subprocess.PIPE
        assert stdin != subprocess.DEVNULL
        assert hasattr(stdin, "read")
        observed["argv"] = args[0]
        observed["cwd"] = kwargs["cwd"]
        observed["input"] = stdin.read()
        events.append(("popen", int(kwargs["creationflags"])))
        return Process()

    monkeypatch.setattr(platform, "get_suspended_hidden_process_flags", lambda: {"creationflags": 456})
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda handle: events.append(("create_job", handle)) or 80,
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

    result = runner._execute_bounded_input(
        ("docker", "compose", "-f", "C:/Fixture Space/compose.yaml", "run"),
        Path("C:/Fixture Space"),
        1.0,
        payload,
    )

    assert result.returncode == 0
    assert observed == {
        "argv": ("docker", "compose", "-f", "C:/Fixture Space/compose.yaml", "run"),
        "cwd": str(Path("C:/Fixture Space")),
        "input": payload,
    }
    assert events == [
        ("popen", 456),
        ("create_job", 95),
        ("resume", 95),
        ("wait", 1.0),
        ("close_job", 80),
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
