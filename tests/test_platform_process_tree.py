"""Contracts for exact-PID Windows process-tree termination."""
from __future__ import annotations

import subprocess

import pytest

from src.core import platform_win


def test_terminate_process_tree_uses_exact_pid_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("SystemRoot", r"C:\Windows Test")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert platform_win.terminate_process_tree(42_424)
    assert captured["argv"] == [
        r"C:\Windows Test\System32\taskkill.exe",
        "/F",
        "/T",
        "/PID",
        "42424",
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["check"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 5
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("pid", [0, -1, True, 1.5, "42"])
def test_terminate_process_tree_rejects_non_positive_integer_pids(pid) -> None:
    with pytest.raises(ValueError):
        platform_win.terminate_process_tree(pid)  # type: ignore[arg-type]
