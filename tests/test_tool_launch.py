"""Tests for launching curated Windows tool wrappers."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.core import platform, platform_win
from src.core.platform_win import build_tool_batch_command, launch_tool_wrapper


def test_batch_command_uses_explicit_cmd_with_safe_quoting_for_spaces(
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "EveJS Tools" / "Market Seed" / "Build Market.bat"

    command = build_tool_batch_command(wrapper)

    assert command == (
        'cmd.exe /d /v:off /s /c '
        '""%EVEJS_LAUNCHER_TOOL_WRAPPER%""'
    )


def test_batch_command_preserves_catalog_owned_arguments(tmp_path: Path) -> None:
    wrapper = tmp_path / "ResetLocalDatabases.bat"

    command = build_tool_batch_command(wrapper, ("/whatif",))

    assert command == (
        'cmd.exe /d /v:off /s /c '
        '""%EVEJS_LAUNCHER_TOOL_WRAPPER%" /whatif"'
    )


def test_batch_command_rejects_shell_metacharacters(tmp_path: Path) -> None:
    wrapper = tmp_path / "Tool.bat"

    with pytest.raises(ValueError, match="Unsupported tool argument"):
        build_tool_batch_command(wrapper, ("/whatif & whoami",))


def test_launch_uses_environment_indirection_for_cmd_sensitive_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = (
        tmp_path
        / "Literal%PATH% & ! ^ (safe)"
        / "tools"
        / "Run Tool.bat"
    )
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_popen(command: str, **kwargs: object) -> object:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(platform_win.subprocess, "Popen", fake_popen)

    launch_tool_wrapper(wrapper)

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, str)
    assert isinstance(kwargs, dict)
    assert str(wrapper) not in command
    assert command == (
        'cmd.exe /d /v:off /s /c '
        '""%EVEJS_LAUNCHER_TOOL_WRAPPER%""'
    )
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["EVEJS_LAUNCHER_TOOL_WRAPPER"] == str(wrapper)


def test_batch_command_executes_from_path_with_legal_cmd_sensitive_characters(
    tmp_path: Path,
) -> None:
    wrapper = tmp_path / "Literal%PATH% & ! ^ (safe)" / "Run Tool.bat"
    wrapper.parent.mkdir(parents=True)
    marker = wrapper.parent / "launched.txt"
    wrapper.write_text(
        '@echo off\r\n>"%~dp0launched.txt" echo launched\r\n',
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["EVEJS_LAUNCHER_TOOL_WRAPPER"] = str(wrapper)

    completed = subprocess.run(
        build_tool_batch_command(wrapper),
        cwd=str(wrapper.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8").strip() == "launched"


def test_launch_uses_visible_independent_console_and_wrapper_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "EveJS Root With Spaces" / "tools" / "Tool" / "Run.bat"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\n", encoding="utf-8")
    sentinel = object()
    captured: dict[str, object] = {}

    def fake_popen(command: str, **kwargs: object) -> object:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(platform_win.subprocess, "Popen", fake_popen)

    result = launch_tool_wrapper(wrapper, ("/whatif",))

    assert result is sentinel
    assert captured["command"] == build_tool_batch_command(wrapper, ("/whatif",))
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == str(wrapper.parent)
    assert kwargs["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    for forbidden in ("shell", "stdout", "stderr", "stdin", "text"):
        assert forbidden not in kwargs


def test_launch_returns_without_waiting_or_polling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "Run.bat"
    wrapper.write_text("@echo off\n", encoding="utf-8")

    class SpawnOnlyProcess:
        def wait(self) -> None:
            raise AssertionError("launch helper must not wait")

        def poll(self) -> None:
            raise AssertionError("launch helper must not poll")

    monkeypatch.setattr(
        platform_win.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SpawnOnlyProcess(),
    )

    assert isinstance(launch_tool_wrapper(wrapper), SpawnOnlyProcess)


def test_launch_rejects_missing_or_non_batch_entrypoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_called = False

    def fake_popen(*_args: object, **_kwargs: object) -> None:
        nonlocal popen_called
        popen_called = True

    monkeypatch.setattr(platform_win.subprocess, "Popen", fake_popen)

    with pytest.raises(FileNotFoundError, match="Tool wrapper not found"):
        launch_tool_wrapper(tmp_path / "missing.bat")

    script = tmp_path / "unsafe.ps1"
    script.write_text("# no\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a .bat"):
        launch_tool_wrapper(script)

    assert popen_called is False


def test_spawn_errors_include_the_wrapper_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "RunTool.bat"
    wrapper.write_text("@echo off\n", encoding="utf-8")

    def fail_spawn(*_args: object, **_kwargs: object) -> None:
        raise OSError("CreateProcess failed")

    monkeypatch.setattr(platform_win.subprocess, "Popen", fail_spawn)

    with pytest.raises(RuntimeError, match="Failed to launch.*RunTool.bat.*CreateProcess"):
        launch_tool_wrapper(wrapper)


def test_tool_launch_helpers_are_reexported_by_platform_module() -> None:
    assert platform.build_tool_batch_command is platform_win.build_tool_batch_command
    assert platform.launch_tool_wrapper is platform_win.launch_tool_wrapper
    assert (
        platform.center_tool_window_for_process_tree
        is platform_win.center_tool_window_for_process_tree
    )
