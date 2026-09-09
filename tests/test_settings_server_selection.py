"""Settings no longer selects a batch file or overrides mod activation."""
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QLabel
from src import config
from src.pages.settings_page import SettingsPage

@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config/config.json")

def test_no_batch_or_mode_selector(qapp, isolated_config):
    page = SettingsPage()
    try:
        assert not hasattr(page, "server_script_combo")
        assert not hasattr(page, "server_mode_combo")
        assert page.scripts_box.title() == "Game Server Startup"
        assert "automatically" in page.scripts_box.findChild(QLabel).text()
    finally:
        page.deleteLater()

def test_save_preserves_unrelated_legacy_settings_but_does_not_edit_them(qapp, isolated_config, tmp_path):
    cfg = config.load()
    cfg.update(evejs_root=str(tmp_path), server_start_preference="StartServer.bat", server_mode="vanilla")
    config.save(cfg)
    page = SettingsPage()
    try:
        state = page._form_state()
        assert "server_mode" not in state
        assert "server_start_preference" not in state
        page.save_settings()
        assert config.load()["server_start_preference"] == "StartServer.bat"
        page.evejs_root_edit.editingFinished.emit()
    finally:
        page.deleteLater()
