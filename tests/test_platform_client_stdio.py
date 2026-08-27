"""Contracts for the standard handles the EVE client is spawned with."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.core import platform_win


def test_launch_eve_client_gives_the_client_valid_standard_handles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The client must never inherit the windowed launcher's invalid fd 0/1/2.

    Inheriting them makes every ``print >> sys.stderr`` in the client's
    logmodule raise IOError EBADF, which unwinds out of the tasklet that logged
    and silently kills in-flight UI construction.
    """
    captured: dict[str, object] = {}

    def fake_popen(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    exe = tmp_path / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    platform_win.launch_eve_client(exe, {"EVEJS": "1"}, exe.parent)

    assert captured["argv"] == [str(exe)]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["creationflags"] == (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    )


def test_launch_eve_client_keeps_handles_when_arguments_are_passed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    exe = tmp_path / "bin64" / "ExeFile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")

    platform_win.launch_eve_client(
        exe,
        {},
        exe.parent,
        arguments=("/noconsole", "/autoSelectCharacter"),
    )

    assert captured["argv"] == [str(exe), "/noconsole", "/autoSelectCharacter"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["stderr"] is subprocess.DEVNULL
