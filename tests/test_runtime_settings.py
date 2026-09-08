"""Offscreen tests for runtime Home animation preferences."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PyQt6.QtWidgets import QApplication

from src import app as app_module
from src import config
from src.app import MainWindow


def _window_config() -> dict:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "",
            "client_path": "",
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    return cfg


@pytest.fixture
def animation_window(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> MainWindow:
    cfg = _window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    yield window
    window.deleteLater()


def test_main_window_applies_animation_config_during_construction(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _window_config()
    cfg.update({"animations_enabled": False, "hero_rotation_interval_sec": 17})
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])

    window = MainWindow()
    try:
        assert window.property("animationsEnabled") is False
        assert window._home_page.signal_background.motion_enabled is False
        assert window._home_page._motion.animations_enabled is False
        assert window._characters_page.animations_enabled is False
        assert window._status_bar.animations_enabled is False
    finally:
        window._status_timer.stop()
        window._prune_timer.stop()
        window.deleteLater()


def test_settings_save_reapplies_animation_preferences_immediately(
    animation_window: MainWindow,
) -> None:

    animation_window._on_settings_saved(
        {"animations_enabled": False, "hero_rotation_interval_sec": 9}
    )

    assert animation_window.property("animationsEnabled") is False
    assert animation_window._home_page.signal_background.motion_enabled is False
    assert animation_window._home_page._motion.animations_enabled is False
    assert animation_window._characters_page.animations_enabled is False
    assert animation_window._status_bar.animations_enabled is False
