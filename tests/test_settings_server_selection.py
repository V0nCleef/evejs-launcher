"""Offscreen tests for the Settings server-start preference UI."""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from src import config
from src.core.server_selection import ASK_EVERY_TIME
from src.pages.settings_page import SettingsPage


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config"
    config_file = config_dir / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def test_settings_lists_always_ask_then_discovered_filenames(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    evejs_root = tmp_path / "evejs"
    evejs_root.mkdir()
    (evejs_root / "StartServerWithMods.bat").write_text("", encoding="utf-8")
    (evejs_root / "StartServer.bat").write_text("", encoding="utf-8")

    cfg = config.load()
    cfg["evejs_root"] = str(evejs_root)
    config.save(cfg)

    page = SettingsPage()
    try:
        assert page.server_script_combo.count() == 3
        assert page.server_script_combo.itemText(0) == "Always ask (default)"
        assert page.server_script_combo.itemData(0) == ASK_EVERY_TIME
        assert [
            page.server_script_combo.itemText(index)
            for index in range(1, page.server_script_combo.count())
        ] == ["StartServer.bat", "StartServerWithMods.bat"]
        assert [
            page.server_script_combo.itemData(index)
            for index in range(1, page.server_script_combo.count())
        ] == ["StartServer.bat", "StartServerWithMods.bat"]
    finally:
        page.deleteLater()


def _create_root(base: Path, name: str, *scripts: str) -> Path:
    root = base / name
    root.mkdir()
    for script in scripts:
        (root / script).write_text("", encoding="utf-8")
    return root


def _save_selector_config(root: Path, preference: str = ASK_EVERY_TIME) -> None:
    cfg = config.load()
    cfg["evejs_root"] = str(root)
    cfg["server_start_preference"] = preference
    config.save(cfg)


def test_settings_saves_and_reloads_relative_filename(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(
        tmp_path,
        "evejs",
        "StartServer.bat",
        "StartServerWithMods.bat",
    )
    _save_selector_config(root)
    page = SettingsPage()
    try:
        page.server_script_combo.setCurrentIndex(2)
        page.save_settings()

        loaded = config.load()
        assert loaded["server_start_preference"] == "StartServerWithMods.bat"
        assert "server_start_script" not in loaded
        assert str(root) not in loaded["server_start_preference"]

        page.load_settings()
        assert page.server_script_combo.currentData() == "StartServerWithMods.bat"
    finally:
        page.deleteLater()


def test_stale_preference_selects_ask_and_explains_why(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(
        tmp_path,
        "evejs",
        "StartServer.bat",
        "StartServerWithMods.bat",
    )
    _save_selector_config(root, "StartServerOld.bat")
    page = SettingsPage()
    try:
        assert page.server_script_combo.currentData() == ASK_EVERY_TIME
        assert "StartServerOld.bat" in page.server_script_info.text()
        assert "unavailable" in page.server_script_info.text().casefold()
    finally:
        page.deleteLater()


def test_changing_a_stale_selection_refreshes_the_explanation(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(
        tmp_path,
        "evejs",
        "StartServer.bat",
        "StartServerWithMods.bat",
    )
    _save_selector_config(root, "StartServerOld.bat")
    page = SettingsPage()
    try:
        page.server_script_combo.setCurrentIndex(1)
        assert "vanilla" in page.server_script_info.text().casefold()
        assert "startserverold.bat" not in page.server_script_info.text().casefold()
    finally:
        page.deleteLater()


def test_one_script_explains_that_launch_will_not_prompt(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(tmp_path, "evejs", "StartServerWithMods.bat")
    _save_selector_config(root)
    page = SettingsPage()
    try:
        assert "automatically" in page.server_script_info.text().casefold()
        assert "no prompt" in page.server_script_info.text().casefold()
    finally:
        page.deleteLater()


def test_one_unknown_script_is_explained_as_unsupported(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(tmp_path, "evejs", "StartServerCustom.bat")
    _save_selector_config(root)
    page = SettingsPage()
    try:
        assert "unsupported" in page.server_script_info.text().casefold()
        assert "automatically" not in page.server_script_info.text().casefold()
    finally:
        page.deleteLater()


def test_stale_preference_does_not_make_an_unknown_script_look_automatic(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(tmp_path, "evejs", "StartServerCustom.bat")
    _save_selector_config(root, "StartServerOld.bat")
    page = SettingsPage()
    try:
        assert "unsupported" in page.server_script_info.text().casefold()
        assert "automatically" not in page.server_script_info.text().casefold()
    finally:
        page.deleteLater()


def test_script_info_updates_when_selection_changes(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = _create_root(
        tmp_path,
        "evejs",
        "StartServer.bat",
        "StartServerWithMods.bat",
    )
    _save_selector_config(root)
    page = SettingsPage()
    try:
        page.server_script_combo.setCurrentIndex(2)
        assert "modded" in page.server_script_info.text().casefold()
        page.server_script_combo.setCurrentIndex(1)
        assert "vanilla" in page.server_script_info.text().casefold()
    finally:
        page.deleteLater()


def test_root_change_rescans_and_resets_missing_preference_to_ask(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root_a = _create_root(
        tmp_path,
        "root-a",
        "StartServer.bat",
        "StartServerWithMods.bat",
    )
    root_b = _create_root(tmp_path, "root-b", "StartServer.bat")
    _save_selector_config(root_a, "StartServerWithMods.bat")
    page = SettingsPage()
    try:
        assert page.server_script_combo.currentData() == "StartServerWithMods.bat"

        page.evejs_root_edit.setText(str(root_b))
        page.evejs_root_edit.editingFinished.emit()

        assert page.server_script_combo.count() == 2
        assert page.server_script_combo.itemData(1) == "StartServer.bat"
        assert page.server_script_combo.currentData() == ASK_EVERY_TIME

        page.save_settings()
        loaded = config.load()
        assert loaded["evejs_root"] == str(root_b)
        assert loaded["server_start_preference"] == ASK_EVERY_TIME
        assert str(root_a) not in isolated_config.read_text(encoding="utf-8")
    finally:
        page.deleteLater()


def test_server_selector_has_no_custom_script_registration(
    qapp: QApplication,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    try:
        button_texts = {button.text() for button in page.findChildren(QPushButton)}
        assert "Add Custom…" not in button_texts
        assert not hasattr(SettingsPage, "server_script_prompt")
    finally:
        page.deleteLater()
