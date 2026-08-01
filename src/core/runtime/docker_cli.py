"""Bounded, argv-only Docker CLI execution for read-only inspection."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable

from src.core import platform


_SECRET_PATTERN = re.compile(r"(?i)(password|token|secret|apikey|api_key)\s*([=:])\s*[^\s,]+")
_BEARER_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,]+")
_URL_USERINFO_PATTERN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+(?::[^\s/@]*)?@")
_PROCESS_REAP_SECONDS = 1.0


def redact_docker_diagnostic(value: object, *, limit: int = 512) -> str:
    """Return a short user-safe Docker diagnostic at every async boundary."""
    bounded_limit = max(1, int(limit))
    text = str(value)[:bounded_limit]
    text = _SECRET_PATTERN.sub(r"\1\2[REDACTED]", text)
    text = _BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    text = _URL_USERINFO_PATTERN.sub(r"\1[REDACTED]@", text)
    # Redaction markers can be longer than the secret they replace.  Enforce
    # the public bound again after every substitution has completed.
    return text[:bounded_limit]


@dataclass(frozen=True)
class DockerCommandResult:
    """Sanitized bounded result from a Docker CLI operation."""

    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    truncated: bool
    timed_out: bool

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0


class DockerCommandError(RuntimeError):
    """A safe diagnostic; result content is bounded and redacted."""

    def __init__(self, operation: str, result: DockerCommandResult) -> None:
        self.result = result
        reason = "timed out" if result.timed_out else f"exited with code {result.returncode}"
        super().__init__(f"Docker {operation} {reason}.")


RawExecutor = Callable[[tuple[str, ...], Path, float], subprocess.CompletedProcess[str]]


class DockerCommandRunner:
    """Execute Docker commands without a shell or unbounded pipe capture."""

    DOCKER_DESKTOP_FALLBACK = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"

    def __init__(
        self,
        executable: str | None = None,
        *,
        execute: RawExecutor | None = None,
        output_limit: int = 64 * 1024,
    ) -> None:
        if output_limit <= 0:
            raise ValueError("output_limit must be positive")
        self.executable = executable or self.resolve_executable()
        self._execute = execute or self._execute_bounded
        self._output_limit = output_limit

    @classmethod
    def resolve_executable(cls) -> str:
        """Find Docker from PATH, then the standard Docker Desktop location."""
        discovered = shutil.which("docker")
        if discovered:
            return discovered
        fallback = Path(cls.DOCKER_DESKTOP_FALLBACK)
        if fallback.is_file():
            return cls.DOCKER_DESKTOP_FALLBACK
        raise FileNotFoundError("Docker CLI was not found on PATH or in Docker Desktop.")

    def run(self, args: tuple[str, ...], *, cwd: Path, timeout: float = 10.0) -> DockerCommandResult:
        """Run one Docker argv operation and raise safely on timeout/nonzero exit."""
        if not args:
            raise ValueError("Docker command arguments are required")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        command = (self.executable, *args)
        try:
            completed = self._execute(command, cwd, timeout)
        except subprocess.TimeoutExpired as exc:
            result = DockerCommandResult(command, None, "", "", False, True)
            raise DockerCommandError(args[0], result) from exc
        except OSError as exc:
            result = DockerCommandResult(command, None, "", self._redact(str(exc)), False, False)
            raise DockerCommandError(args[0], result) from exc

        stdout, stdout_truncated = self._bound(completed.stdout or "")
        stderr, stderr_truncated = self._bound(completed.stderr or "")
        result = DockerCommandResult(command, completed.returncode, stdout, stderr, stdout_truncated or stderr_truncated, False)
        if completed.returncode != 0:
            safe_result = DockerCommandResult(command, completed.returncode, "", "", result.truncated, False)
            raise DockerCommandError(args[0], safe_result)
        return result

    def _execute_bounded(self, argv: tuple[str, ...], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        """Capture through temporary files, avoiding an unbounded PIPE in memory."""
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            process = subprocess.Popen(
                argv,
                shell=False,
                cwd=os.fspath(cwd),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                text=False,
                **platform.get_suspended_hidden_process_flags(),
            )
            process_handle = getattr(process, "_handle", None)
            if not isinstance(process_handle, int) or process_handle <= 0:
                self._kill_and_reap_suspended_root(process)
                raise OSError("Docker command process handle is unavailable.")
            job_handle = platform.create_kill_on_close_job(process_handle)
            if job_handle is None:
                self._kill_and_reap_suspended_root(process)
                raise OSError("Could not contain the Docker command process tree.")
            try:
                if not platform.resume_process(process_handle):
                    platform.terminate_job(job_handle)
                    process.wait(timeout=_PROCESS_REAP_SECONDS)
                    raise OSError("Could not resume the contained Docker command.")
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                platform.terminate_job(job_handle)
                process.wait(timeout=_PROCESS_REAP_SECONDS)
                raise
            finally:
                platform.close_job(job_handle)
            stdout_file.seek(0)
            stderr_file.seek(0)
            return subprocess.CompletedProcess(
                argv,
                returncode,
                stdout_file.read(self._output_limit + 1).decode("utf-8", errors="replace"),
                stderr_file.read(self._output_limit + 1).decode("utf-8", errors="replace"),
            )

    @staticmethod
    def _kill_and_reap_suspended_root(process: subprocess.Popen[bytes]) -> None:
        """Fail closed before an uncontained child is ever allowed to run."""
        process.kill()
        process.wait(timeout=_PROCESS_REAP_SECONDS)

    def _bound(self, value: str) -> tuple[str, bool]:
        truncated = len(value) > self._output_limit
        bounded = value[: self._output_limit]
        return self._redact(bounded), truncated

    @staticmethod
    def _redact(value: str) -> str:
        return redact_docker_diagnostic(value, limit=max(1, len(value)))
