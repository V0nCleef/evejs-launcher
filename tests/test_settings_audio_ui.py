"""Focused Audio & Voice Settings UI, persistence, and layout coverage."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QLabel

from src import config
from src.pages.settings_page import SettingsPage
from src.theme import build_qss


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def test_visible_audio_settings_persist_without_retired_interface_cues(
    qapp,
    isolated_config: Path,
) -> None:
    cfg = config.load()
    cfg.update(
        {
            "audio_master_muted": False,
            "audio_music_enabled": False,
            "audio_music_volume": 47,
            "audio_ui_sounds_enabled": True,
            "audio_ui_sounds_volume": 16,
            "audio_voice_enabled": True,
            "audio_voice_volume": 73,
            "audio_voice_engine": "local-engine",
            "audio_voice_locale": "en-GB",
            "audio_voice_name": "Local Example",
            "audio_voice_rate": -0.2,
            "audio_voice_pitch": 0.15,
            "audio_announce_character_names": False,
            "audio_announce_results": True,
            "audio_ducking_enabled": False,
            "audio_ducking_level": 18,
        }
    )
    config.save(cfg)
    page = SettingsPage()

    try:
        assert not hasattr(page, "master_audio_toggle")
        assert page.music_enabled_toggle.isChecked() is False
        assert page.music_volume_slider.value() == 47
        assert page.voice_volume_slider.value() == 73
        assert page.announce_results_toggle.isChecked() is True
        assert page.ducking_enabled_toggle.isChecked() is False
        assert page.ducking_level_slider.value() == 18
        assert page.voice_pack_source_value.text() == "LYRA PRERECORDED VOICE"
        assert page.voice_pack_language_value.text() == "English (UK)"
        assert page.voice_pack_profile_value.text() == "Balanced Lift"
        settings_copy = " ".join(
            label.text()
            for label in page.findChildren(QLabel)
        ).casefold()
        assert "speak character names" not in settings_copy
        assert "voice rate" not in settings_copy
        assert "voice pitch" not in settings_copy
        assert "system speech" not in settings_copy
        assert "background music" in settings_copy
        assert "interface cues" not in settings_copy
        assert "music while\nlyra speaks" in settings_copy
        assert not hasattr(page, "ui_sounds_enabled_toggle")
        assert not hasattr(page, "ui_sounds_volume_slider")
        assert not hasattr(page, "announce_character_names_toggle")
        assert not hasattr(page, "voice_rate_slider")
        assert not hasattr(page, "voice_pitch_slider")
        assert page.is_dirty() is False

        page.music_enabled_toggle.setChecked(True)
        page.music_volume_slider.setValue(62)
        page.voice_volume_slider.setValue(88)
        page.announce_results_toggle.setChecked(False)
        page.ducking_enabled_toggle.setChecked(True)
        page.ducking_level_slider.setValue(24)
        assert page.is_dirty() is True

        page.save_settings()
        saved = config.load()
        assert saved["audio_master_muted"] is False
        assert saved["audio_music_enabled"] is True
        assert saved["audio_music_volume"] == 62
        assert "audio_ui_sounds_enabled" not in saved
        assert "audio_ui_sounds_volume" not in saved
        persisted = json.loads(isolated_config.read_text(encoding="utf-8"))
        assert "audio_ui_sounds_enabled" not in persisted
        assert "audio_ui_sounds_volume" not in persisted
        assert saved["audio_voice_volume"] == 88
        assert saved["audio_announce_results"] is False
        assert saved["audio_ducking_enabled"] is True
        assert saved["audio_ducking_level"] == 24
        # Obsolete system-TTS/name controls remain readable for downgrade
        # compatibility, but this fixed prerecorded UI never rewrites them.
        assert saved["audio_voice_engine"] == "local-engine"
        assert saved["audio_voice_locale"] == "en-GB"
        assert saved["audio_voice_name"] == "Local Example"
        assert saved["audio_voice_rate"] == -0.2
        assert saved["audio_voice_pitch"] == 0.15
        assert saved["audio_announce_character_names"] is False
        assert page.is_dirty() is False
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_voice_preview_is_truthful_and_emits_only_when_capable(
    qapp,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    requested: list[bool] = []
    page.voice_preview_requested.connect(lambda: requested.append(True))

    try:
        assert page.preview_voice_btn.isEnabled() is False
        assert "not been verified" in page.voice_preview_status_label.text().lower()
        page.preview_voice_btn.click()
        assert requested == []

        page.set_voice_preview_available(True, "Bundled LYRA voice pack is ready.")
        assert page.preview_voice_btn.isEnabled() is True
        assert (
            page.voice_preview_status_label.text()
            == "Bundled LYRA voice pack is ready."
        )
        page.voice_volume_slider.setValue(64)
        preview = page.audio_preview_settings()
        assert preview == {
            "audio_voice_enabled": True,
            "audio_voice_volume": 64,
        }
        assert "audio_voice_rate" not in preview
        assert "audio_voice_pitch" not in preview
        assert "audio_voice_engine" not in preview
        assert "audio_announce_character_names" not in preview
        page.preview_voice_btn.click()
        assert requested == [True]

        page.voice_enabled_toggle.setChecked(False)
        assert page.preview_voice_btn.isEnabled() is False
        assert "disabled" in page.voice_preview_status_label.text().lower()
        page._request_voice_preview()
        assert requested == [True]

        page.voice_enabled_toggle.setChecked(True)
        page.set_voice_preview_available(False, "Bundled voice asset unavailable.")
        assert page.preview_voice_btn.isEnabled() is False
        assert (
            page.voice_preview_status_label.text()
            == "Bundled voice asset unavailable."
        )
        assert (
            page.preview_voice_btn.accessibleDescription()
            == "Bundled voice asset unavailable."
        )
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_custom_music_ui_is_removed_while_legacy_and_unknown_keys_are_preserved(
    qapp,
    isolated_config: Path,
) -> None:
    cfg = config.load()
    cfg["audio_music_library"] = ["C:/Legacy/Personal Track.mp3"]
    cfg["future_audio_option"] = {"mode": "preserve-me"}
    config.save(cfg)
    page = SettingsPage()

    try:
        assert not hasattr(page, "audio_music_library_panel")
        assert not hasattr(page, "music_library_list")
        assert not hasattr(page, "add_music_tracks_btn")
        assert not hasattr(page, "remove_music_tracks_btn")
        settings_copy = " ".join(
            label.text() for label in page.findChildren(QLabel)
        ).casefold()
        assert "music rotation" not in settings_copy
        assert "personal files" not in settings_copy
        assert "local tracks" not in settings_copy

        page.music_volume_slider.setValue(61)
        assert page.is_dirty() is True
        page.save_settings()

        saved = config.load()
        assert saved["audio_music_library"] == [
            "C:/Legacy/Personal Track.mp3"
        ]
        assert saved["audio_music_volume"] == 61
        assert saved["future_audio_option"] == {"mode": "preserve-me"}
        assert page.is_dirty() is False
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_audio_dependencies_and_reduce_motion_keep_runtime_setting_semantics(
    qapp,
    isolated_config: Path,
) -> None:
    page = SettingsPage()

    try:
        assert page.animations_toggle.isChecked() is True
        assert page.reduce_motion_toggle.isChecked() is False
        assert page.hero_interval_spin.isEnabled() is True

        page.reduce_motion_toggle.setChecked(True)
        assert page.animations_toggle.isChecked() is False
        assert page.hero_interval_spin.isEnabled() is False
        assert page._form_state()["animations_enabled"] is False

        page.music_enabled_toggle.setChecked(False)
        assert page.music_volume_slider.isEnabled() is False
        assert page.ducking_enabled_toggle.isEnabled() is False
        assert page.ducking_level_slider.isEnabled() is False

        page.music_enabled_toggle.setChecked(True)
        page.voice_enabled_toggle.setChecked(False)
        assert page.voice_volume_slider.isEnabled() is False
        assert page.announce_results_toggle.isEnabled() is False
        assert page.voice_event_status_label.text() == "VOICE EVENTS DISABLED"

        page.animations_toggle.setChecked(True)
        assert page.reduce_motion_toggle.isChecked() is False
        assert page.hero_interval_spin.isEnabled() is True
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_audio_panels_reflow_at_16_by_9_and_minimum_window_content_width(
    qapp,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    page.show()

    try:
        page.resize(1122, 696)  # 1366x768 shell after nav/title/status chrome
        qapp.processEvents()
        assert page.audio_layout_mode() == "wide"
        assert page.audio_panel_grid.getItemPosition(
            page.audio_panel_grid.indexOf(page.audio_mix_panel)
        ) == (0, 0, 1, 1)
        assert page.audio_panel_grid.getItemPosition(
            page.audio_panel_grid.indexOf(page.audio_events_panel)
        ) == (0, 1, 1, 1)
        assert page.audio_panel_grid.getItemPosition(
            page.audio_panel_grid.indexOf(page.audio_identity_panel)
        ) == (0, 2, 1, 1)
        assert page.audio_panel_grid.count() == 3
        audio_heading = page.findChild(type(page.voice_event_status_label), "audioVoiceSectionTitle")
        assert audio_heading is not None
        assert audio_heading.mapTo(page.settings_scroll.viewport(), audio_heading.rect().topLeft()).y() >= 0
        assert audio_heading.isVisibleTo(page.settings_scroll.viewport())

        page.resize(756, 568)  # 1000x640 minimum shell content area
        qapp.processEvents()
        assert page.audio_layout_mode() == "compact"
        assert page.audio_panel_grid.getItemPosition(
            page.audio_panel_grid.indexOf(page.audio_identity_panel)
        ) == (1, 0, 1, 2)
        assert page.audio_panel_grid.count() == 3
        assert (
            page.settings_container.minimumSizeHint().width()
            <= page.settings_scroll.viewport().width()
        )

        page.resize(620, 568)
        qapp.processEvents()
        assert page.audio_layout_mode() == "single"
        assert page.audio_panel_grid.getItemPosition(
            page.audio_panel_grid.indexOf(page.audio_identity_panel)
        ) == (2, 0, 1, 1)
        assert page.audio_panel_grid.count() == 3
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_settings_save_preserves_newer_title_bar_music_mute(
    qapp,
    isolated_config: Path,
) -> None:
    config.save(config.load())
    page = SettingsPage()

    try:
        page.proxy_url_edit.setText("http://127.0.0.1:29999")
        externally_updated = config.load()
        externally_updated["audio_music_muted"] = True
        config.save(externally_updated)

        page.save_settings()

        saved = config.load()
        assert saved["audio_music_muted"] is True
        assert saved["audio_master_muted"] is False
        assert saved["proxy_url"] == "http://127.0.0.1:29999"
        assert not hasattr(page, "master_audio_toggle")
        assert page.is_dirty() is False
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_audio_theme_is_scoped_to_semantic_settings_properties() -> None:
    qss = build_qss(
        {"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"}
    )

    assert 'QFrame[class="audioSettingsPanel"]' in qss
    assert 'QSlider[audioControl="true"]::groove:horizontal' in qss
    assert 'QLabel[class="audioEventIndicator"][state="off"]' in qss
