"""Bounded, argv-only Docker CLI execution for allowlisted operations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import BinaryIO, Callable, TypeVar, cast

from src.core import platform


_SECRET_PATTERN = re.compile(r"(?i)(password|token|secret|apikey|api_key)\s*([=:])\s*[^\s,]+")
_BEARER_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,]+")
_URL_USERINFO_PATTERN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+(?::[^\s/@]*)?@")
_PROCESS_REAP_SECONDS = 1.0
_MAX_INPUT_BYTES = 16 * 1024


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
RawInputExecutor = Callable[[tuple[str, ...], Path, float, bytes], subprocess.CompletedProcess[str]]
ParsedOutput = TypeVar("ParsedOutput")


class DockerStructuredOutputError(ValueError):
    """Static diagnostic for rejected structured stdout.

    The originating parser exception is deliberately discarded because JSON
    exceptions retain the complete source document on their ``doc`` field.
    """

    _MESSAGES = {
        "invalid": "Docker structured output could not be validated.",
        "too_large": (
            "Docker structured output was invalid or exceeded its bounded limit."
        ),
    }

    def __init__(self, code: str) -> None:
        if code not in self._MESSAGES:
            raise ValueError("Unknown Docker structured-output failure code.")
        self.code = code
        super().__init__(self._MESSAGES[code])


class DockerCommandRunner:
    """Execute Docker commands without a shell or unbounded pipe capture."""

    DOCKER_DESKTOP_FALLBACK = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"

    def __init__(
        self,
        executable: str | None = None,
        *,
        execute: RawExecutor | None = None,
        execute_input: RawInputExecutor | None = None,
        output_limit: int = 64 * 1024,
    ) -> None:
        if output_limit <= 0:
            raise ValueError("output_limit must be positive")
        self.executable = executable or self.resolve_executable()
        self._execute = execute or self._execute_bounded
        self._execute_input = execute_input or self._execute_bounded_input
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

    def run_parsed(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        parser: Callable[[str], ParsedOutput],
        timeout: float = 10.0,
    ) -> ParsedOutput:
        """Consume bounded raw stdout inside one trusted parser callback.

        Ordinary :meth:`run` redacts text before returning it.  Redacting JSON
        before parsing can silently change effective configuration values, so
        secret-bearing structured commands use this narrower boundary instead.
        The callback must return only a privacy-safe projection; raw stdout,
        stderr, and parser exceptions never cross this method boundary.
        """

        if not args:
            raise ValueError("Docker command arguments are required")
        if not callable(parser):
            raise TypeError("parser must be callable")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        command = (self.executable, *args)
        (
            status,
            parsed,
            returncode,
            truncated,
            timed_out,
        ) = self._consume_parsed_output(command, cwd, timeout, parser)
        if status == "command_failed":
            safe_result = DockerCommandResult(
                command,
                returncode,
                "",
                "",
                truncated,
                timed_out,
            )
            raise DockerCommandError(args[0], safe_result)
        if status != "ok":
            raise DockerStructuredOutputError(status) from None
        return cast(ParsedOutput, parsed)

    def _consume_parsed_output(
        self,
        command: tuple[str, ...],
        cwd: Path,
        timeout: float,
        parser: Callable[[str], ParsedOutput],
    ) -> tuple[str, ParsedOutput | None, int | None, bool, bool]:
        """Own all secret-bearing objects until only a safe outcome remains.

        No exception is allowed to leave this frame: an exception traceback
        retains its frame locals, which would otherwise keep raw stdout alive
        even when the public exception message was static.
        """

        try:
            completed = self._execute(command, cwd, timeout)
        except subprocess.TimeoutExpired:
            return "command_failed", None, None, False, True
        except OSError:
            return "command_failed", None, None, False, False

        raw_stdout = completed.stdout
        raw_stderr = completed.stderr
        stdout_valid = isinstance(raw_stdout, str)
        stderr_valid = raw_stderr is None or isinstance(raw_stderr, str)
        stdout_truncated = stdout_valid and len(raw_stdout) > self._output_limit
        stderr_truncated = (
            isinstance(raw_stderr, str)
            and len(raw_stderr) > self._output_limit
        )
        truncated = bool(stdout_truncated or stderr_truncated)
        if completed.returncode != 0:
            return (
                "command_failed",
                None,
                completed.returncode,
                truncated,
                False,
            )
        if not stdout_valid or not stderr_valid:
            return "invalid", None, completed.returncode, truncated, False
        if truncated:
            return "too_large", None, completed.returncode, True, False
        try:
            parsed = parser(raw_stdout)
        except Exception:
            # JSONDecodeError retains its full input in ``doc``.  Collapse it
            # to a safe status before this frame returns.
            return "invalid", None, completed.returncode, False, False
        return "ok", parsed, completed.returncode, False, False

    def run_with_input(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        input_bytes: bytes,
        timeout: float = 10.0,
    ) -> DockerCommandResult:
        """Run one Docker operation with a small, non-command-line stdin payload."""
        if not args:
            raise ValueError("Docker command arguments are required")
        if not isinstance(input_bytes, bytes):
            raise TypeError("input_bytes must be bytes")
        if len(input_bytes) > _MAX_INPUT_BYTES:
            raise ValueError("input_bytes must not exceed 16 KiB")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        command = (self.executable, *args)
        failure: DockerCommandResult | None = None
        try:
            completed = self._execute_input(command, cwd, timeout, input_bytes)
        except subprocess.TimeoutExpired:
            failure = DockerCommandResult(command, None, "", "", False, True)
        except OSError:
            # Input-bearing failures deliberately omit the originating
            # diagnostic so a payload echoed by the OS/executor cannot cross
            # this public boundary.
            failure = DockerCommandResult(command, None, "", "", False, False)
        if failure is not None:
            # Raise after leaving the except block so the original exception,
            # which may itself retain input/output, is not chained or exposed.
            raise DockerCommandError(args[0], failure)

        stdout, stdout_truncated = self._bound_without_input(completed.stdout or "", input_bytes)
        stderr, stderr_truncated = self._bound_without_input(completed.stderr or "", input_bytes)
        result = DockerCommandResult(
            command,
            completed.returncode,
            stdout,
            stderr,
            stdout_truncated or stderr_truncated,
            False,
        )
        if completed.returncode != 0:
            safe_result = DockerCommandResult(command, completed.returncode, "", "", result.truncated, False)
            raise DockerCommandError(args[0], safe_result)
        return result

    def _execute_bounded(self, argv: tuple[str, ...], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
        """Capture through temporary files, avoiding an unbounded PIPE in memory."""
        return self._execute_bounded_process(argv, cwd, timeout, stdin=subprocess.DEVNULL)

    def _execute_bounded_input(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        """Feed bounded bytes through a temporary file, never an in-memory PIPE."""
        with tempfile.TemporaryFile(mode="w+b") as stdin_file:
            stdin_file.write(input_bytes)
            stdin_file.flush()
            stdin_file.seek(0)
            return self._execute_bounded_process(argv, cwd, timeout, stdin=stdin_file)

    def _execute_bounded_process(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        timeout: float,
        *,
        stdin: int | BinaryIO,
    ) -> subprocess.CompletedProcess[str]:
        """Run with the shared bounded-output and Windows Job containment path."""
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            process = subprocess.Popen(
                argv,
                shell=False,
                cwd=os.fspath(cwd),
                stdin=stdin,
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

    def _bound_without_input(self, value: str, input_bytes: bytes) -> tuple[str, bool]:
        """Bound/redact output and remove an exact echo of the stdin payload."""
        raw_truncated = len(value) > self._output_limit
        payload = input_bytes.decode("utf-8", errors="replace")
        scrubbed = value
        secrets = {payload} if payload else set()
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None

        def collect(candidate: object) -> None:
            if isinstance(candidate, str) and candidate:
                secrets.add(candidate)
            elif isinstance(candidate, list):
                for item in candidate:
                    collect(item)
            elif isinstance(candidate, dict):
                for item in candidate.values():
                    collect(item)

        collect(parsed)
        for secret in sorted(secrets, key=len, reverse=True):
            scrubbed = scrubbed.replace(secret, "[REDACTED]")
        bounded, scrubbed_truncated = self._bound(scrubbed)
        return bounded, raw_truncated or scrubbed_truncated

    @staticmethod
    def _redact(value: str) -> str:
        return redact_docker_diagnostic(value, limit=max(1, len(value)))
