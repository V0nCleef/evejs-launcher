"""Runtime game-server selection tests for MainWindow."""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMainWindow

from src.app import MainWindow


@pytest.fixture
def bare_window(qapp: QApplication) -> MainWindow:
    """Create a MainWindow wrapper without constructing the complete launcher UI."""
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._server_proc = None
    window._market_proc = None
    yield window
    window.deleteLater()


def _write_script(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text("@echo off\n", encoding="utf-8")
    return path


def test_resolver_uses_the_only_discovered_script_without_prompt(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    script = _write_script(tmp_path, "StartServer.bat")
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "ask",
        "server_mode": "modded",
    }

    result = bare_window._resolve_server_start()

    assert result == ("vanilla", script)


def test_effective_mode_label_uses_the_only_script_instead_of_asking(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    _write_script(tmp_path, "StartServer.bat")
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "ask",
        "server_mode": "modded",
    }

    assert bare_window._effective_server_mode_label() == "Vanilla"


def test_resolver_uses_valid_saved_filename_without_prompt(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_script(tmp_path, "StartServer.bat")
    selected = _write_script(tmp_path, "StartServerWithMods.bat")
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "startserverwithmods.BAT",
        "server_mode": "vanilla",
    }

    def unexpected_prompt(*_args: object, **_kwargs: object) -> tuple[str, bool]:
        pytest.fail("a valid saved preference must not open the chooser")

    monkeypatch.setattr(QInputDialog, "getItem", unexpected_prompt)

    assert bare_window._resolve_server_start() == ("modded", selected)


def test_ask_preference_prompts_once_without_saving_one_off_choice(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vanilla = _write_script(tmp_path, "StartServer.bat")
    modded = _write_script(tmp_path, "StartServerWithMods.bat")
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "ask",
        "server_mode": "vanilla",
    }
    prompted: dict[str, object] = {}

    def choose_modded(
        _parent: object,
        title: str,
        _label: str,
        items: list[str],
        *_args: object,
    ) -> tuple[str, bool]:
        prompted["title"] = title
        prompted["items"] = items
        return modded.name, True

    monkeypatch.setattr(QInputDialog, "getItem", choose_modded)
    monkeypatch.setattr(
        "src.app.config.save",
        lambda _cfg: pytest.fail("one-off chooser selections must not be saved"),
    )

    assert bare_window._resolve_server_start() == ("modded", modded)
    assert prompted["items"] == [vanilla.name, modded.name]
    assert "server" in str(prompted["title"]).casefold()
    assert bare_window._cfg["server_start_preference"] == "ask"


def test_stale_preference_is_reset_before_prompting(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vanilla = _write_script(tmp_path, "StartServer.bat")
    _write_script(tmp_path, "StartServerWithMods.bat")
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "StartServerOldVersion.bat",
        "server_mode": "modded",
    }
    saved: list[dict] = []

    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *_args, **_kwargs: (vanilla.name, True),
    )
    monkeypatch.setattr(
        "src.app.config.save",
        lambda cfg: saved.append(dict(cfg)),
    )

    assert bare_window._resolve_server_start() == ("vanilla", vanilla)
    assert bare_window._cfg["server_start_preference"] == "ask"
    assert saved[-1]["server_start_preference"] == "ask"


@pytest.mark.parametrize("fallback_mode", ["vanilla", "modded"])
def test_zero_scripts_uses_legacy_mode_when_node_entry_exists(
    fallback_mode: str,
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir(parents=True)
    (server_dir / "index.js").write_text("", encoding="utf-8")
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "ask",
        "server_mode": fallback_mode,
    }

    assert bare_window._resolve_server_start() == (fallback_mode, None)


def test_zero_scripts_without_node_entry_shows_validation_error(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg = {
        "evejs_root": str(tmp_path),
        "server_start_preference": "ask",
        "server_mode": "modded",
    }
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.app.QMessageBox.critical",
        lambda _parent, title, message: errors.append((title, message)),
    )

    assert bare_window._resolve_server_start() is None
    assert errors
    assert "StartServer" in errors[-1][1]
    assert "server/index.js" in errors[-1][1]
