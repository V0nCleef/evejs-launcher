"""Shared-folder selection, persistence, translations and active-client guards."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent

from src import config
from src.app import MainWindow
from src.i18n import LANGUAGES, current_language, set_language, translate_ui_phrase
from src.pages.settings_page import SettingsPage


@pytest.fixture(autouse=True)
def isolated(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "launcher/config.json")
    language = current_language()
    set_language("en")
    yield
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    set_language(language)


def test_default_is_separate_and_shared_picker_is_disabled(qapp):
    page = SettingsPage()
    try:
        assert page.client_settings_mode_combo.currentData() == "separate"
        assert not page.shared_settings_folder_row.isEnabled()
        assert page.client_advanced_fields.isHidden()
        assert not page.client_advanced_toggle.isChecked()
        assert not page.is_dirty()
        assert page.client_path_edit.parent().parent().title() == "Client"
    finally:
        page.deleteLater()


def test_shared_selection_saves_and_restores_without_touching_folder(qapp, tmp_path):
    folder = tmp_path / "Shared settings"
    folder.mkdir()
    (folder / "prefs.ini").write_text("keep=1\n")
    page = SettingsPage()
    try:
        page.client_settings_mode_combo.setCurrentIndex(1)
        page.shared_settings_folder_edit.setText(str(folder))
        assert page.shared_settings_folder_row.isEnabled()
        assert page.is_dirty()
        page.save_settings()
        assert config.load()["client_settings_mode"] == "shared"
        assert config.load()["shared_settings_folder"] == str(folder.resolve())
        assert (folder / "prefs.ini").read_text() == "keep=1\n"
        page.load_settings()
        assert page.client_settings_mode_combo.currentData() == "shared"
        assert page.client_advanced_toggle.isChecked()
        assert not page.client_advanced_fields.isHidden()
        page.client_settings_mode_combo.setCurrentIndex(0)
        page.save_settings()
        assert config.load()["client_settings_mode"] == "separate"
        assert config.load()["shared_settings_folder"] == str(folder.resolve())
    finally:
        page.deleteLater()


def test_shared_mode_saves_without_choosing_a_folder(qapp):
    page = SettingsPage()
    try:
        page.client_settings_mode_combo.setCurrentIndex(1)
        assert page.shared_settings_folder_edit.text() == ""
        assert page.client_advanced_fields.isHidden()
        assert page.is_dirty()
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [True]
        assert config.load()["client_settings_mode"] == "shared"
        assert config.load()["shared_settings_folder"] == ""
        assert not (config.CONFIG_DIR / "SharedSettings").exists()
        page.load_settings()
        assert page.client_settings_mode_combo.currentData() == "shared"
        assert not page.client_advanced_toggle.isChecked()
        assert not page.is_dirty()
    finally:
        page.deleteLater()


def test_custom_override_is_optional_and_can_return_to_automatic(qapp, tmp_path):
    folder = tmp_path / "Custom shared settings"
    folder.mkdir()
    page = SettingsPage()
    try:
        page.client_settings_mode_combo.setCurrentIndex(1)
        page.client_advanced_toggle.setChecked(True)
        assert not page.client_advanced_fields.isHidden()
        page.shared_settings_folder_edit.setText(str(folder))
        page.save_settings()
        assert config.load()["shared_settings_folder"] == str(folder)
        page.shared_settings_folder_edit.clear()
        assert page.is_dirty()
        page.save_settings()
        assert config.load()["client_settings_mode"] == "shared"
        assert config.load()["shared_settings_folder"] == ""
    finally:
        page.deleteLater()


def test_invalid_shared_folder_does_not_save(qapp, tmp_path):
    page = SettingsPage()
    try:
        page.client_settings_mode_combo.setCurrentIndex(1)
        page.shared_settings_folder_edit.setText(str(tmp_path / "missing"))
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [False]
        assert config.load()["client_settings_mode"] == "separate"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("running,pending,worker", [(1, set(), None), (0, {"account-a"}, None), (0, set(), object())])
def test_settings_cannot_switch_during_client_use(running, pending, worker):
    window = SimpleNamespace(_cfg=deepcopy(config.DEFAULT_CONFIG),
        _tracker=SimpleNamespace(running_count=running), _pending_client_launches=pending,
        _client_launch_thread=worker)
    assert MainWindow._settings_save_rejection(window, {"client_settings_mode": "shared"}) == (
        "Close EVE clients before changing the shared settings folder.")


def test_shared_feature_catalog_is_complete():
    from src.translations_shared_settings import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE
    for option in LANGUAGES:
        if option.code != "en":
            for phrase in SOURCE_PHRASES:
                assert UI_PHRASES_BY_LANGUAGE[option.code][phrase].strip()
                assert translate_ui_phrase(phrase, option.code) == UI_PHRASES_BY_LANGUAGE[option.code][phrase]
