"""The approved Mods-page section, editing, root scope and translations."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent

from src import config
from src.core.startup_scripts import ScriptEntry, ScriptStore, StartupScriptController
from src.i18n import LANGUAGES, current_language, set_language, translate_ui_phrase
from src.pages import startup_scripts_panel as ui


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher")
    prior = current_language()
    set_language("en")
    yield
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    set_language(prior)


def test_panel_add_edit_toggle_remove_and_root_isolation(qapp, tmp_path, monkeypatch):
    root, other = tmp_path / "EveJS", tmp_path / "Other"
    root.mkdir()
    other.mkdir()
    filename = tmp_path / "Start.bat"
    filename.write_text("@echo off\n")
    original = ScriptEntry.create("Example Tool", str(filename))
    panel = ui.StartupScriptsPanel()
    panel.set_root(str(root))
    monkeypatch.setattr(panel, "_edit_dialog", lambda _: original)
    panel.add_button.click()
    assert panel.controller.store.list(root) == [original]
    assert panel.rows[original.id].name_label.text() == "Example Tool"
    panel.rows[original.id].auto_toggle.setChecked(False)
    assert panel.controller.store.list(root)[0].auto_start is False
    edited = replace(original, name="Edited", arguments="start", auto_start=False)
    monkeypatch.setattr(panel, "_edit_dialog", lambda _: edited)
    panel.rows[original.id].edit_button.click()
    assert panel.controller.store.list(root) == [edited]
    panel.set_root(str(other))
    assert panel.rows == {}
    panel.set_root(str(root))
    assert original.id in panel.rows
    panel.rows[original.id].remove_button.click()
    assert panel.controller.store.list(root) == []
    assert filename.is_file()
    panel.deleteLater()


def test_script_names_and_paths_do_not_get_translated(qapp, tmp_path):
    root = tmp_path / "EveJS"
    root.mkdir()
    filename = tmp_path / "Start.bat"
    filename.write_text("@echo off\n")
    item = ScriptEntry.create("Run now", str(filename))
    store = ScriptStore(tmp_path / "store.json")
    store.save(root, item)
    controller = StartupScriptController(store)
    set_language("nl")
    panel = ui.StartupScriptsPanel(controller=controller)
    panel.set_root(str(root))
    row = panel.rows[item.id]
    from src.widgets.ui_translation import register_translatable_widget_tree, retranslate_widget_tree
    register_translatable_widget_tree(panel)
    retranslate_widget_tree(panel, "nl")
    assert row.name_label.text() == "Run now"
    assert row.path_label.text() == str(filename)
    assert row.run_button.text() == translate_ui_phrase("Run now", "nl")
    panel.deleteLater()
    controller.deleteLater()


def test_mods_page_keeps_scripts_below_inventory_and_separate_from_mod_count(qapp, tmp_path, monkeypatch):
    from src.pages.mods_page import ModsPage
    root = tmp_path / "EveJS"
    root.mkdir()
    monkeypatch.setattr(config, "get_setting", lambda *_: "")
    page = ModsPage()
    page.set_evejs_root(str(root))
    assert page.startup_scripts.parent() is page.manifest_panel
    layout = page.manifest_panel.layout()
    assert layout.indexOf(page.startup_scripts) > layout.indexOf(page._scroll)
    assert len(page.mods()) == 0
    page.deleteLater()


def test_entry_dialog_saves_windows_arguments(qapp, tmp_path):
    filename = tmp_path / "Start.bat"
    filename.write_text("@echo off\n")
    dialog = ui.ScriptDialog(filename=str(filename))
    dialog.name_edit.setText("My Tool")
    dialog.arguments_edit.setText('start --name "two words"')
    dialog._save()
    assert dialog.entry.name == "My Tool"
    assert dialog.entry.arguments == 'start --name "two words"'
    assert dialog.entry.auto_start is True
    dialog.deleteLater()


def test_every_startup_script_phrase_has_all_supported_translations():
    from src.translations_startup_scripts import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE
    for option in LANGUAGES:
        if option.code == "en":
            continue
        for phrase in SOURCE_PHRASES:
            assert UI_PHRASES_BY_LANGUAGE[option.code][phrase].strip()
            assert translate_ui_phrase(phrase, option.code) == UI_PHRASES_BY_LANGUAGE[option.code][phrase]
