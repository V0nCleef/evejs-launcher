"""Native mode comes from enabled loaders, never batch-file preferences."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from PyQt6.QtWidgets import QMainWindow
from src.app import MainWindow
from src.core.server_launcher import build_game_server_command

@pytest.fixture
def window(qapp, tmp_path):
    w = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(w)
    w._server_proc = None
    w._cfg = {"evejs_root": str(tmp_path), "server_mode": "modded",
              "server_start_preference": "StartServer.bat"}
    (tmp_path / "server").mkdir()
    (tmp_path / "server/index.js").write_text("")
    yield w
    w.deleteLater()

@pytest.mark.parametrize("script", [None, "StartServer.bat", "StartServerWithMods.bat", "StartServerCustom.bat"])
@pytest.mark.parametrize("enabled", [True, False])
def test_enabled_mods_are_authoritative(window, tmp_path, script, enabled):
    if script:
        (tmp_path / script).write_text("@echo off")
    folder = tmp_path / "mods/AutoMiningDrones"
    folder.mkdir(parents=True)
    (folder / ("loader.js" if enabled else "loader.js.disabled")).write_text("")
    mode = "modded" if enabled else "vanilla"
    assert window._resolve_server_start() == (mode, None)
    command = build_game_server_command(tmp_path, mode)
    assert ("--require" in command) is enabled
    assert window._effective_server_mode_label() == f"Next start: {mode.title()}"

def test_no_mods_ignores_old_modded_preference(window):
    assert window._resolve_server_start() == ("vanilla", None)

def test_running_label_does_not_claim_pending_disabled_state(window, tmp_path):
    window._server_proc = SimpleNamespace(pid=123, poll=lambda: None)
    window._current_mod_runtime_snapshot = SimpleNamespace(
        backend="native", pid=123, root=tmp_path.resolve(), mode="modded",
        selected_loader_ids=("AutoMiningDrones",))
    assert window._effective_server_mode_label() == "Running: Modded · 1 loader mod(s)"
    window._server_proc = None
    assert window._effective_server_mode_label() == "Next start: Vanilla"

def test_invalid_mod_prevents_start(window, tmp_path, monkeypatch):
    folder = tmp_path / "mods/broken"
    folder.mkdir(parents=True)
    (folder / "loader.js").write_text("")
    (folder / "loader.js.disabled").write_text("")
    errors = []
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *args: errors.append(args))
    assert window._resolve_server_start() is None
    assert errors

def test_missing_entry_rejected_even_with_batch_file(window, tmp_path, monkeypatch):
    (tmp_path / "server/index.js").unlink()
    (tmp_path / "StartServer.bat").write_text("")
    errors = []
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *args: errors.append(args))
    assert window._resolve_server_start() is None
    assert "server/index.js" in errors[0][-1]
