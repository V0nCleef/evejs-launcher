"""Offscreen tests for runtime Home animation preferences."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PyQt6.QtWidgets import QApplication

from src import app as app_module
from src import config
from src.app import MainWindow
from src.widgets.hero_banner import HeroBanner


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


def test_hero_applies_animation_enablement_and_rotation_interval(
    qapp: QApplication,
) -> None:
    hero = HeroBanner()

    hero.set_rotation_interval(17)
    hero.set_animations_enabled(False)

    assert hero.rotation_interval_ms == 17_000
    assert hero._rotate_timer.interval() == 17_000
    assert hero._zoom_anim.duration() == 17_000
    assert hero.animations_enabled is False
    assert hero.is_running() is False


def test_hero_restarts_when_it_becomes_visible_again(
    qapp: QApplication,
) -> None:
    hero = HeroBanner()
    hero.show()
    qapp.processEvents()
    assert hero.is_running() is True

    hero.hide()
    qapp.processEvents()
    assert hero.is_running() is False

    hero.show()
    qapp.processEvents()
    assert hero.is_running() is True


def test_stopping_hero_restores_a_stable_front_frame(
    qapp: QApplication,
) -> None:
    hero = HeroBanner()
    hero._opacity_back.setOpacity(0.5)

    hero.stop()

    assert hero._opacity_back.opacity() == 0.0


def test_hero_zoom_scales_a_stable_centered_image_without_raster_jitter(
    qapp: QApplication,
) -> None:
    hero = HeroBanner()
    hero.resize(800, HeroBanner.HEIGHT)
    hero.show()
    qapp.processEvents()
    hero._zoom_anim.stop()

    try:
        hero._on_zoom_frame(1.01)
        first_rect = hero._label_front.geometry()
        first_pixmap_key = hero._label_front.pixmap().cacheKey()

        hero._on_zoom_frame(1.04)
        second_rect = hero._label_front.geometry()

        assert first_rect.width() > hero.width()
        assert first_rect.height() > hero.height()
        assert first_rect.center() == hero.rect().center()
        assert second_rect.width() > first_rect.width()
        assert second_rect.height() > first_rect.height()
        assert second_rect.center() == hero.rect().center()
        assert hero._label_front.pixmap().cacheKey() == first_pixmap_key
    finally:
        hero.close()
        hero.deleteLater()


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
        hero = window._home_page.hero
        assert hero.animations_enabled is False
        assert hero.rotation_interval_ms == 17_000
        assert hero.is_running() is False
    finally:
        window._status_timer.stop()
        window._prune_timer.stop()
        window.deleteLater()


def test_settings_save_reapplies_animation_preferences_immediately(
    animation_window: MainWindow,
) -> None:
    hero = animation_window._home_page.hero

    animation_window._on_settings_saved(
        {"animations_enabled": False, "hero_rotation_interval_sec": 9}
    )

    assert hero.animations_enabled is False
    assert hero.rotation_interval_ms == 9_000
