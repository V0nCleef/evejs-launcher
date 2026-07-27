"""Tests for explicit game-server command construction."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core import server_launcher
from src.core.server_launcher import build_game_server_command


def test_vanilla_command_has_no_mod_preloads(tmp_path: Path) -> None:
    command = build_game_server_command(tmp_path, "vanilla")

    assert command[0] == "node"
    assert command[-1] == "."
    assert "--require" not in command
    assert not any(argument.casefold().endswith(".bat") for argument in command)


def test_modded_command_includes_only_active_loader_preloads(tmp_path: Path) -> None:
    active_b = tmp_path / "mods" / "b-mod" / "loader.js"
    active_a = tmp_path / "mods" / "a-mod" / "loader.js"
    disabled = tmp_path / "mods" / "disabled-mod" / "loader.js.disabled"
    active_b.parent.mkdir(parents=True)
    active_a.parent.mkdir(parents=True)
    disabled.parent.mkdir(parents=True)
    active_b.write_text("", encoding="utf-8")
    active_a.write_text("", encoding="utf-8")
    disabled.write_text("", encoding="utf-8")

    command = build_game_server_command(tmp_path, "modded")

    assert command[-1] == "."
    assert command.count("--require") == 2
    assert command.index(str(active_a)) < command.index(str(active_b))
    assert str(disabled) not in command
    assert not any(argument.casefold().endswith(".bat") for argument in command)


def test_unknown_server_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported server mode"):
        build_game_server_command(tmp_path, "automatic")


def test_start_game_server_requires_an_explicit_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "index.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        server_launcher,
        "build_game_server_command",
        lambda *_args, **_kwargs: pytest.fail("implicit mode reached command building"),
    )

    with pytest.raises(TypeError):
        server_launcher.start_game_server(str(tmp_path))


@pytest.mark.parametrize("mode", ["vanilla", "modded"])
def test_start_game_server_honors_explicit_mode(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "index.js").write_text("", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_build(root: str | Path, selected_mode: str) -> list[str]:
        observed["root"] = Path(root)
        observed["mode"] = selected_mode
        return ["node", f"mode={selected_mode}", "."]

    class FakeProcess:
        pid = 1234
        stdout = object()

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        observed["command"] = command
        observed["popen_kwargs"] = kwargs
        return FakeProcess()

    class FakeThread:
        def __init__(self, **kwargs: object) -> None:
            observed["thread_kwargs"] = kwargs

        def start(self) -> None:
            observed["thread_started"] = True

    monkeypatch.setattr(server_launcher, "build_game_server_command", fake_build)
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "server.log")
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})

    process = server_launcher.start_game_server(str(tmp_path), mode=mode)

    assert process.pid == 1234
    assert observed["root"] == tmp_path
    assert observed["mode"] == mode
    assert observed["command"] == ["node", f"mode={mode}", "."]
    assert observed["popen_kwargs"]["cwd"] == str(server_dir)
    assert observed["thread_started"] is True
