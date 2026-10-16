"""Shared-character selection, migration, translations and active-client guards."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent

from src import config
from src.app import MainWindow
from src.core.db import Account, Character
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


def source_roster(root):
    accounts = [
        Account("account-a", 101, "player", False,
                [Character(501, "Aster"), Character(503, "Cedar")]),
        Account("account-b", 102, "player", False, [Character(502, "Boreal")]),
    ]
    source = {"username": "account-a", "account_id": 101,
              "character_id": 501, "evejs_root": str(root.resolve())}
    return accounts, source


def choose_source(page, tmp_path):
    root = tmp_path / "EveJS"
    accounts, source = source_roster(root)
    page.evejs_root_edit.setText(str(root))
    page.set_shared_profile_accounts(accounts, str(root))
    assert page.shared_profile_source_combo.currentData() == {}
    page.shared_profile_source_combo.setCurrentIndex(1)
    assert page.shared_profile_source_combo.currentData() == source
    return accounts, source


def test_default_is_separate_and_folder_controls_are_removed(qapp):
    page = SettingsPage()
    try:
        assert page.client_settings_mode_combo.currentData() == "separate"
        assert not hasattr(page, "shared_settings_folder_edit")
        assert not hasattr(page, "client_advanced_fields")
        assert not hasattr(page, "client_advanced_toggle")
        assert page.shared_profile_source_combo.isHidden()
        assert page.shared_profile_source_combo.currentData() == {}
        assert not page.is_dirty()
        assert page.client_path_edit.parent().parent().title() == "Client"
    finally:
        page.deleteLater()


def test_shared_selection_saves_and_restores_preserving_legacy_folder(qapp, tmp_path):
    folder = tmp_path / "Shared settings"
    folder.mkdir()
    (folder / "prefs.ini").write_text("keep=1\n")
    config.save({"shared_settings_folder": str(folder)})
    page = SettingsPage()
    try:
        _, source = choose_source(page, tmp_path)
        page.client_settings_mode_combo.setCurrentIndex(1)
        assert not page.shared_profile_source_combo.isHidden()
        assert page.is_dirty()
        page.save_settings()
        assert config.load()["client_settings_mode"] == "shared"
        assert config.load()["shared_settings_folder"] == str(folder.resolve())
        assert config.load()["shared_settings_source"] == source
        assert (folder / "prefs.ini").read_text() == "keep=1\n"
        page.load_settings()
        assert page.client_settings_mode_combo.currentData() == "shared"
        assert not hasattr(page, "client_advanced_toggle")
        page.client_settings_mode_combo.setCurrentIndex(0)
        page.save_settings()
        assert config.load()["client_settings_mode"] == "separate"
        assert config.load()["shared_settings_folder"] == str(folder.resolve())
        assert config.load()["shared_settings_source"] == source
    finally:
        page.deleteLater()


def test_shared_mode_saves_selected_profile_without_choosing_a_folder(qapp, tmp_path):
    page = SettingsPage()
    try:
        _, source = choose_source(page, tmp_path)
        page.client_settings_mode_combo.setCurrentIndex(1)
        assert page._legacy_shared_settings_folder == ""
        assert page.is_dirty()
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [True]
        assert config.load()["client_settings_mode"] == "shared"
        assert config.load()["shared_settings_folder"] == ""
        assert config.load()["shared_settings_source"] == source
        assert not (config.CONFIG_DIR / "SharedSettings").exists()
        page.load_settings()
        assert page.client_settings_mode_combo.currentData() == "shared"
        assert not page.is_dirty()
    finally:
        page.deleteLater()


@pytest.mark.parametrize("explicit", [False, True])
def test_legacy_unavailable_folder_is_preserved_without_blocking_save(qapp, tmp_path, explicit):
    legacy_folder = str(tmp_path / "Unavailable old import")
    config.save({"shared_settings_folder": legacy_folder})
    page = SettingsPage()
    try:
        if explicit:
            choose_source(page, tmp_path)
        page.client_settings_mode_combo.setCurrentIndex(1)
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [True]
        assert config.load()["client_settings_mode"] == "shared"
        assert config.load()["shared_settings_folder"] == legacy_folder
        assert not (tmp_path / "Unavailable old import").exists()
        assert not page.is_dirty()
    finally:
        page.deleteLater()


def test_managed_shared_folder_saves_automatic_first_character_source(qapp, tmp_path):
    page = SettingsPage()
    try:
        page.client_settings_mode_combo.setCurrentIndex(1)
        assert page.shared_profile_source_combo.currentData() == {}
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [True]
        saved = config.load()
        assert saved["client_settings_mode"] == "shared"
        assert saved["shared_settings_folder"] == ""
        assert saved["shared_settings_source"] == {}
        assert saved["auto_login_enabled"] is True
        page.load_settings()
        assert not page.is_dirty()
        accounts, source = source_roster(tmp_path / "EveJS")
        page.set_shared_profile_accounts(accounts, source["evejs_root"])
        assert page.shared_profile_source_combo.currentData() == {}
        assert not page.is_dirty()
        assert not (config.CONFIG_DIR / "SharedSettings").exists()
    finally:
        page.deleteLater()


def test_saved_source_is_visible_until_roster_loads_without_becoming_dirty(qapp, tmp_path):
    accounts, source = source_roster(tmp_path / "EveJS")
    config.save({"client_settings_mode": "shared", "shared_settings_source": source,
                 "evejs_root": source["evejs_root"]})
    page = SettingsPage()
    try:
        assert page.shared_profile_source_combo.currentData() == source
        assert page.shared_profile_source_combo.currentText() == "Saved character profile"
        assert not page.is_dirty()
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [False]
        page.set_shared_profile_accounts(accounts, source["evejs_root"])
        assert page.shared_profile_source_combo.currentText() == "Aster (account-a)"
        assert not page.is_dirty()
        page.save_settings()
        assert result == [False, True]
        assert config.load()["shared_settings_source"] == source
    finally:
        page.deleteLater()


@pytest.mark.parametrize("mismatch", ["username", "account_id", "character_id", "evejs_root"])
def test_saved_source_does_not_match_other_account_character_or_server(qapp, tmp_path, mismatch):
    accounts, source = source_roster(tmp_path / "EveJS")
    changed = dict(source)
    changed[mismatch] = {"username": "unrelated-account", "account_id": 999,
                         "character_id": 999, "evejs_root": str(tmp_path / "OtherEveJS")}[mismatch]
    config.save({"client_settings_mode": "shared", "shared_settings_source": changed,
                 "evejs_root": source["evejs_root"]})
    page = SettingsPage()
    try:
        page.set_shared_profile_accounts(accounts, source["evejs_root"])
        assert page.shared_profile_source_combo.currentData() == changed
        assert page.shared_profile_source_combo.currentText() == "Saved character profile"
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [False]
        assert config.load()["shared_settings_source"] == changed
    finally:
        page.deleteLater()


def test_source_choice_is_dirty_persists_and_retains_each_clients_auto_login(qapp, tmp_path):
    page = SettingsPage()
    try:
        accounts, source = choose_source(page, tmp_path)
        page.client_settings_mode_combo.setCurrentIndex(1)
        page.save_settings()
        assert not page.is_dirty()
        page.shared_profile_source_combo.setCurrentIndex(2)
        assert page.shared_profile_source_combo.currentText() == "Boreal (account-b)"
        assert page.is_dirty()
        page.save_settings()
        selected = config.load()["shared_settings_source"]
        assert selected["username"] == "account-b"
        assert selected["account_id"] == 102
        assert selected["character_id"] == 502
        assert config.load()["auto_login_enabled"] is True
        page.set_shared_profile_accounts(list(reversed(accounts)), source["evejs_root"])
        assert page.shared_profile_source_combo.currentData() == selected
        assert not page.is_dirty()
        page.load_settings()
        assert page.shared_profile_source_combo.currentData() == selected
    finally:
        page.deleteLater()


def test_server_root_change_requires_new_verified_roster(qapp, tmp_path):
    page = SettingsPage()
    try:
        accounts, source = choose_source(page, tmp_path)
        page.client_settings_mode_combo.setCurrentIndex(1)
        page.save_settings()
        changed_root = tmp_path / "OtherEveJS"
        page.evejs_root_edit.setText(str(changed_root))
        result = []
        page.save_finished.connect(result.append)
        page.save_settings()
        assert result == [False]
        page.set_shared_profile_accounts(accounts, str(changed_root))
        assert page.shared_profile_source_combo.currentData() == source
        page.save_settings()
        assert result == [False, False]
        page.shared_profile_source_combo.setCurrentIndex(1)
        page.save_settings()
        assert result == [False, False, True]
        assert config.load()["shared_settings_source"]["evejs_root"] == str(changed_root)
    finally:
        page.deleteLater()


def test_source_picker_placeholder_translates_but_character_names_do_not(qapp, tmp_path):
    set_language("nl")
    page = SettingsPage()
    try:
        accounts, source = source_roster(tmp_path / "EveJS")
        page.set_shared_profile_accounts(accounts, source["evejs_root"])
        assert page.shared_profile_source_combo.itemText(0) == "Eerst gestarte personage (automatisch)"
        assert page.shared_profile_source_combo.itemText(1) == "Aster (account-a)"
        assert page.shared_profile_source_label.text() == "Gedeeld profiel:"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("running,pending,worker", [(1, set(), None), (0, {"account-a"}, None), (0, set(), object())])
def test_settings_cannot_switch_during_client_use(running, pending, worker):
    window = SimpleNamespace(_cfg=deepcopy(config.DEFAULT_CONFIG),
        _tracker=SimpleNamespace(running_count=running), _pending_client_launches=pending,
        _client_launch_thread=worker)
    assert MainWindow._settings_save_rejection(window, {"client_settings_mode": "shared"}) == (
        "Close EVE clients before applying the selected shared character profile.")


def test_shared_feature_catalog_is_complete():
    from src.translations_shared_settings import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE
    for option in LANGUAGES:
        if option.code != "en":
            for phrase in SOURCE_PHRASES:
                assert UI_PHRASES_BY_LANGUAGE[option.code][phrase].strip()
                assert translate_ui_phrase(phrase, option.code) == UI_PHRASES_BY_LANGUAGE[option.code][phrase]
