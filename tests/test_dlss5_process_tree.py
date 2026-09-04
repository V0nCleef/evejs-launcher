"""DLSS5 helper containment tests; real children are disposable Python only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time

import psutil
import pytest

from src.core import dlss5, platform


_SUSPENDED_HIDDEN_FLAGS = 0x08000004


class _Process:
    def __init__(
        self,
        events: list[tuple[str, object]],
        *,
        exit_code: int = 0,
        timeout_waits: tuple[int, ...] = (),
        with_handle: bool = True,
    ) -> None:
        self.events = events
        self.pid = 42_424
        self.returncode: int | None = None
        self.exit_code = exit_code
        self.timeout_waits = timeout_waits
        self.wait_count = 0
        if with_handle:
            self._handle = 91

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float) -> int:
        self.events.append(("wait", timeout))
        self.wait_count += 1
        if self.wait_count in self.timeout_waits:
            raise subprocess.TimeoutExpired("fixture-dlss5-helper", timeout)
        if self.returncode is None:
            self.returncode = self.exit_code
        return self.returncode

    def kill(self) -> None:
        self.events.append(("kill", self.pid))
        self.returncode = -9


def _mock_process_and_job(
    monkeypatch: pytest.MonkeyPatch,
    process: _Process,
    *,
    job_handle: int | None = 77,
    resume_ok: bool = True,
    terminate_ok: bool = True,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> dict[str, object]:
    observed: dict[str, object] = {}
    events = process.events

    def popen(command: list[str], **kwargs: object) -> _Process:
        observed.update(command=command, **kwargs)
        events.append(("popen", kwargs["creationflags"]))
        kwargs["stdout"].write(stdout)
        kwargs["stderr"].write(stderr)
        return process

    monkeypatch.setattr(dlss5.subprocess, "Popen", popen)
    monkeypatch.setattr(
        platform,
        "get_suspended_hidden_process_flags",
        lambda: {"creationflags": _SUSPENDED_HIDDEN_FLAGS},
    )
    monkeypatch.setattr(
        platform,
        "create_kill_on_close_job",
        lambda handle: events.append(("create_job", handle)) or job_handle,
    )
    monkeypatch.setattr(
        platform,
        "resume_process",
        lambda handle: events.append(("resume", handle)) or resume_ok,
    )
    monkeypatch.setattr(
        platform,
        "terminate_job",
        lambda handle: events.append(("terminate_job", handle)) or terminate_ok,
    )
    monkeypatch.setattr(
        platform,
        "close_job",
        lambda handle: events.append(("close_job", handle)),
    )
    return observed


def test_preparation_is_contained_before_resume_and_uses_null_input_and_temp_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    process = _Process(events, exit_code=7)
    observed = _mock_process_and_job(
        monkeypatch,
        process,
        stdout="verified \u2713\n".encode("utf-8"),
        stderr=b"fixture warning\n",
    )
    command = ["C:/Windows Test/powershell.exe", "-File", "C:/Package Space/manager.ps1"]
    environment = {"FIXTURE_DLSS5": "isolated"}

    result = dlss5._run_dlss5_preparation_process(
        command, cwd=tmp_path, environment=environment, timeout=3_600,
    )

    assert result.args == command
    assert result.returncode == 7
    assert result.stdout == "verified \u2713\n"
    assert result.stderr == "fixture warning\n"
    assert observed["command"] == command
    assert observed["cwd"] == str(tmp_path)
    assert observed["env"] == environment
    assert observed["shell"] is False
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["text"] is False
    assert observed["creationflags"] == _SUSPENDED_HIDDEN_FLAGS
    for name in ("stdout", "stderr"):
        stream = observed[name]
        assert stream not in (subprocess.PIPE, subprocess.DEVNULL)
        assert stream.closed
    assert observed["stdout"] is not observed["stderr"]
    assert events == [
        ("popen", _SUSPENDED_HIDDEN_FLAGS),
        ("create_job", 91),
        ("resume", 91),
        ("wait", 3_600),
        ("close_job", 77),
        ("wait", 5),
    ]


def test_preparation_output_is_tail_bounded_and_replaces_invalid_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    limit = 256 * 1024
    _mock_process_and_job(
        monkeypatch,
        _Process(events),
        stdout=b"discarded-prefix" + b"x" * limit + b"TAIL",
        stderr=b"diagnostic: \xff\n",
    )

    result = dlss5._run_dlss5_preparation_process(
        ["fixture"], cwd=tmp_path, environment={}, timeout=12,
    )

    assert result.stdout == "x" * (limit - 4) + "TAIL"
    assert result.stderr == "diagnostic: \ufffd\n"


@pytest.mark.parametrize("timeout", (0, -1))
def test_non_positive_timeout_never_spawns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout: float,
) -> None:
    monkeypatch.setattr(
        dlss5.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("invalid timeout must not spawn"),
    )

    with pytest.raises(ValueError, match="positive"):
        dlss5._run_dlss5_preparation_process(
            ["fixture"], cwd=tmp_path, environment={}, timeout=timeout,
        )


def test_missing_handle_kills_and_reaps_only_the_unstarted_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    _mock_process_and_job(monkeypatch, _Process(events, with_handle=False))

    with pytest.raises(OSError, match="handle"):
        dlss5._run_dlss5_preparation_process(
            ["fixture"], cwd=tmp_path, environment={}, timeout=12,
        )

    assert events == [
        ("popen", _SUSPENDED_HIDDEN_FLAGS),
        ("kill", 42_424),
        ("wait", 5),
    ]


def test_containment_failure_never_resumes_and_reaps_suspended_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    _mock_process_and_job(monkeypatch, _Process(events), job_handle=None)

    with pytest.raises(OSError, match="contain"):
        dlss5._run_dlss5_preparation_process(
            ["fixture"], cwd=tmp_path, environment={}, timeout=12,
        )

    assert events == [
        ("popen", _SUSPENDED_HIDDEN_FLAGS),
        ("create_job", 91),
        ("kill", 42_424),
        ("wait", 5),
    ]


def test_resume_failure_terminates_and_closes_job_before_bounded_reap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    _mock_process_and_job(monkeypatch, _Process(events, exit_code=-9), resume_ok=False)

    with pytest.raises(OSError, match="resume"):
        dlss5._run_dlss5_preparation_process(
            ["fixture"], cwd=tmp_path, environment={}, timeout=12,
        )

    assert events == [
        ("popen", _SUSPENDED_HIDDEN_FLAGS),
        ("create_job", 91),
        ("resume", 91),
        ("terminate_job", 77),
        ("close_job", 77),
        ("wait", 5),
    ]


@pytest.mark.parametrize("terminate_ok", (True, False))
def test_timeout_closes_kill_on_close_job_and_reaps_before_propagating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminate_ok: bool,
) -> None:
    events: list[tuple[str, object]] = []
    _mock_process_and_job(
        monkeypatch,
        _Process(events, exit_code=-9, timeout_waits=(1,)),
        terminate_ok=terminate_ok,
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        dlss5._run_dlss5_preparation_process(
            ["fixture"], cwd=tmp_path, environment={}, timeout=0.05,
        )

    assert raised.value.timeout == 0.05
    assert events == [
        ("popen", _SUSPENDED_HIDDEN_FLAGS),
        ("create_job", 91),
        ("resume", 91),
        ("wait", 0.05),
        ("terminate_job", 77),
        ("close_job", 77),
        ("wait", 5),
    ]


def test_unconfirmed_cleanup_reports_failure_without_an_unbounded_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    _mock_process_and_job(
        monkeypatch,
        _Process(events, timeout_waits=(1, 2)),
    )

    with pytest.raises(OSError, match="cleanup could not confirm process exit"):
        dlss5._run_dlss5_preparation_process(
            ["fixture"], cwd=tmp_path, environment={}, timeout=0.05,
        )

    assert events[-3:] == [("terminate_job", 77), ("close_job", 77), ("wait", 5)]
    assert [value for name, value in events if name == "wait"] == [0.05, 5]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_real_python_preparation_returns_output_environment_and_working_directory(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment["EVEJS_DLSS5_TEST_ONLY"] = "isolated-success"
    helper = (
        "import os, sys; "
        "sys.stdout.buffer.write((os.environ['EVEJS_DLSS5_TEST_ONLY'] + '|' + "
        "os.getcwd() + '\\n').encode('utf-8')); "
        "sys.stderr.buffer.write(b'fixture diagnostic\\n')"
    )
    command = [sys.executable, "-c", helper]

    result = dlss5._run_dlss5_preparation_process(
        command, cwd=tmp_path, environment=environment, timeout=10,
    )

    assert result.args == command
    assert result.returncode == 0
    assert result.stdout == f"isolated-success|{tmp_path}\n"
    assert result.stderr == "fixture diagnostic\n"


def _recorded_process_is_alive(record: dict[str, object]) -> bool:
    try:
        process = psutil.Process(int(record["pid"]))
        return abs(process.create_time() - float(record["created"])) < 0.01
    except psutil.NoSuchProcess:
        return False


def _read_process_records(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            # A force-stop may interrupt the last line; earlier flushed records
            # still identify the exact test parent for the cleanup safety net.
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_real_timeout_reaps_self_spawned_python_parent_and_child(tmp_path: Path) -> None:
    """Only this test's recorded Python tree may be inspected or cleaned up."""
    pid_file = tmp_path / "dlss5-test-processes.jsonl"
    helper = textwrap.dedent(
        """
        import json, os, pathlib, subprocess, sys, time
        import psutil

        with pathlib.Path(sys.argv[1]).open('w', encoding='utf-8') as stream:
            def record(role, pid):
                stream.write(json.dumps({
                    'role': role, 'pid': pid,
                    'created': psutil.Process(pid).create_time(),
                }) + '\\n')
                stream.flush()
                os.fsync(stream.fileno())

            record('parent', os.getpid())
            child = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(60)'],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            record('child', child.pid)
            time.sleep(60)
        """
    )
    records: list[dict[str, object]] = []

    try:
        with pytest.raises(subprocess.TimeoutExpired):
            dlss5._run_dlss5_preparation_process(
                [sys.executable, "-c", helper, str(pid_file)],
                cwd=tmp_path,
                environment=dict(os.environ),
                timeout=2.0,
            )

        records = _read_process_records(pid_file)
        assert {record["role"] for record in records} == {"parent", "child"}
        deadline = time.monotonic() + 2.0
        while any(_recorded_process_is_alive(record) for record in records) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert all(not _recorded_process_is_alive(record) for record in records)
    finally:
        # Reload even when an earlier assertion failed. PID plus creation time
        # prevents the safety net from touching an unrelated reused PID.
        records = records or _read_process_records(pid_file)
        for record in records:
            if _recorded_process_is_alive(record):
                platform.terminate_process_tree(int(record["pid"]))
