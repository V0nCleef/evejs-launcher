"""Draft, host-confirmed saving, localization, and input behavior for mod forms."""
from copy import deepcopy
from dataclasses import replace
from string import Formatter
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel

from src import i18n
from src.core.mod_settings_schema import ModSetting, SettingChoice, validate_values
from src.translations_mod_settings import UI_PHRASES_BY_LANGUAGE
from src.widgets.mod_settings_dialog import ModSettingsDialog, QMessageBox


FIELDS = (
    ModSetting("enabled", "Enabled", "boolean", True, group="Mining"),
    ModSetting("count", "Cycles", "integer", 3, minimum=1, maximum=10, restart="game_server", group="Mining"),
    ModSetting("scale", {"en": "Yield", "nl": "Opbrengst"}, "number", 0.125, minimum=0.1, maximum=2, step=0.025, group="Mining"),
    ModSetting("name", "Name", "string", "Example", max_length=12, group="Display"),
    ModSetting("mode", "Mode", "choice", "normal", choices=(SettingChoice("normal", "Normal"), SettingChoice("extra", "Extra")), restart="client", group="Display"),
    ModSetting("detail", "Detail", "integer", 2, advanced=True, group="Display"),
)


@pytest.fixture
def make_dialog(qapp, monkeypatch):
    previous = i18n.current_language()
    i18n.set_language("en")
    dialogs = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Discard)

    def make(fields=FIELDS, values=None, **kwargs):
        dialog = ModSettingsDialog("Example Mod", fields, values or {}, **kwargs)
        dialogs.append(dialog)
        dialog.show()
        qapp.processEvents()
        return dialog

    yield make
    for dialog in dialogs:
        dialog.set_saving(False)
        dialog.reject()
        dialog.deleteLater()
    qapp.processEvents()
    i18n.set_language(previous)


def test_saving_waits_for_host_and_failures_keep_the_draft(make_dialog):
    initial = {field.id: deepcopy(field.default) for field in FIELDS}
    original = deepcopy(initial)
    dialog = make_dialog(values=initial, scope_label="Example profile")
    requests = []
    dialog.save_requested.connect(requests.append)
    assert not dialog.is_dirty()
    dialog._controls["count"].setValue(5)
    dialog._controls["mode"].setCurrentIndex(1)
    dialog.save_button.click()
    assert requests == [{**original, "count": 5, "mode": "extra"}]
    assert initial == original
    assert dialog.result() != dialog.DialogCode.Accepted
    assert dialog.isVisible()
    assert not dialog.scroll_area.isEnabled()
    dialog.reject()
    assert dialog.isVisible(), "A save in flight must retain its dialog"

    dialog.show_error("The host could not save this draft")
    assert dialog.is_dirty()
    assert dialog._controls["count"].value() == 5
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    confirmed = {**requests[-1], "count": 6}
    dialog.mark_saved(confirmed)
    assert dialog._controls["count"].value() == 6
    assert dialog.draft_values() == confirmed
    assert not dialog.is_dirty()
    assert not dialog.save_button.isEnabled()
    assert "game server" in dialog.restart_label.text()
    assert "EVE client" in dialog.restart_label.text()
    assert dialog.cancel_button.text() == "Close"


def test_validation_and_defaults_never_emit_or_write_implicitly(make_dialog):
    dialog = make_dialog()
    requests = []
    dialog.save_requested.connect(requests.append)
    dialog._controls["count"].setValue(0)
    dialog._controls["name"].setText("Much too long for this field")
    dialog.save_button.click()
    assert requests == []
    assert dialog._errors["count"].text() == "The minimum value is 1."
    assert dialog._errors["name"].text() == "Use at most 12 characters."
    assert dialog.isVisible()
    dialog.defaults_button.click()
    assert dialog.draft_values() == {field.id: field.default for field in FIELDS}
    assert requests == []
    assert not dialog.is_dirty()


def test_discard_confirmation_keeps_or_discards_without_saving(make_dialog, monkeypatch):
    dialog = make_dialog()
    requests = []
    dialog.save_requested.connect(requests.append)
    dialog._controls["count"].setValue(7)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    dialog.reject()
    assert dialog.isVisible()
    assert dialog.is_dirty()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Discard)
    dialog.reject()
    assert not dialog.isVisible()
    assert requests == []


def test_search_and_advanced_filtering_preserve_all_draft_values(make_dialog):
    fields = FIELDS + tuple(ModSetting(f"other{i}", f"Other {i}", "string", "", group="Other") for i in range(3))
    dialog = make_dialog(fields=fields)
    assert dialog.search_edit.isVisible()
    assert not dialog._rows["detail"].isVisible()
    dialog.advanced_toggle.setChecked(True)
    dialog._controls["detail"].setValue(8)
    dialog.search_edit.setText("Yield")
    assert dialog._rows["scale"].isVisible()
    assert not dialog._rows["detail"].isVisible()
    assert dialog.draft_values()["detail"] == 8
    dialog.search_edit.setText("No such setting")
    assert dialog.empty_label.isVisible()
    dialog.search_edit.clear()
    dialog.advanced_toggle.setChecked(False)
    assert dialog.draft_values()["detail"] == 8


def test_three_array_controls_reflow_without_recreating_the_editors(make_dialog, qapp):
    fields = tuple(
        ModSetting(f"array{i}", f"Array {i} escalation chance (%)", "number", i * 5,
                   description="Percentage chance after depletion for this Array.",
                   minimum=0, maximum=100, step=0.5, group="Escalation")
        for i in range(1, 4)
    )
    dialog = make_dialog(fields=fields)
    controls = tuple(dialog._controls.values())
    controls[1].setValue(17.5)
    controls[1].setFocus()

    def row_positions(width):
        dialog.resize(width, 575)
        qapp.processEvents()
        qapp.processEvents()
        return [dialog._rows[field.id].y() for field in fields]

    wide = row_positions(720)
    assert wide[0] == wide[1] == wide[2]
    medium = row_positions(550)
    assert medium[0] == medium[1] < medium[2]
    narrow = row_positions(420)
    assert narrow[0] < narrow[1] < narrow[2]
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert tuple(dialog._controls.values()) == controls
    assert controls[1].hasFocus()
    assert dialog.draft_values() == {"array1": 5, "array2": 17.5, "array3": 15}
    assert row_positions(720) == wide


def test_authored_rows_and_full_width_fields_preserve_unlisted_settings(make_dialog, qapp):
    layout = (
        SimpleNamespace(title={"en": "Array presets"}, rows=(("count", "enabled"), ("scale",))),
    )
    dialog = make_dialog(fields=FIELDS, layout=layout)
    qapp.processEvents()
    assert tuple(section.title for section in dialog._sections) == ("Array presets", "Display")
    rows = dialog._rows
    assert rows["count"].y() == rows["enabled"].y()
    assert rows["count"].x() < rows["enabled"].x()
    assert rows["scale"].width() > rows["count"].width()
    assert rows["name"].isVisible()
    assert rows["name"].width() > rows["mode"].width()
    dialog._controls["count"].setFocus()
    QTest.keyClick(dialog._controls["count"], Qt.Key.Key_Tab)
    assert dialog._controls["enabled"].hasFocus()
    dialog._controls["scale"].setValue(0.75)
    assert dialog.draft_values()["scale"] == 0.75
    fallback = make_dialog(fields=FIELDS, layout_error=True)
    assert any("Using automatic layout." in label.text() for label in fallback.findChildren(QLabel))


def test_long_labels_and_editors_do_not_force_horizontal_scrolling(make_dialog, qapp):
    fields = (
        FIELDS[1],
        replace(FIELDS[2], label={"en": "Scale", "nl": "y" * 256}),
        replace(FIELDS[4], group="Mining", choices=(SettingChoice("normal", "x" * 512),)),
        replace(FIELDS[3], group="Mining", default="First line\nSecond line", max_length=40),
    )
    i18n.set_language("nl")
    dialog = make_dialog(fields=fields)
    qapp.processEvents()
    rows = dialog._rows
    assert rows["scale"].width() == rows["mode"].width() == rows["name"].width() > rows["count"].width()
    assert dialog._controls["scale"].accessibleName() == "y" * 256
    dialog.resize(420, 575)
    qapp.processEvents()
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert dialog.draft_values() == {field.id: field.default for field in fields}


def test_section_navigation_filters_and_reveals_errors_in_a_large_form(make_dialog, qapp):
    fields = tuple(
        ModSetting(f"setting{i}", f"Setting {i}", "integer", 1, minimum=1,
                   group=f"Panel {i // 20 + 1}", advanced=(i == 98))
        for i in range(100)
    )
    dialog = make_dialog(fields=fields)
    scrollbar = dialog.scroll_area.verticalScrollBar()
    assert scrollbar.maximum() > 0
    assert dialog.section_jump.count() == 5
    dialog.section_jump.activated.emit(4)
    assert scrollbar.value() > 0
    dialog.advanced_toggle.setChecked(True)
    dialog._controls["setting98"].setValue(0)
    dialog.advanced_toggle.setChecked(False)
    dialog.search_edit.setText("Panel 1")
    assert not dialog._rows["setting98"].isVisible()
    assert not dialog.section_jump.isVisible()
    dialog.save_button.click()
    qapp.processEvents()
    assert dialog.search_edit.text() == ""
    assert dialog.advanced_toggle.isChecked()
    assert dialog._rows["setting98"].isVisible()
    assert dialog._controls["setting98"].hasFocus()
    assert scrollbar.value() > 0
    assert len(dialog.draft_values()) == 100


def test_choice_type_changes_are_real_changes(make_dialog):
    field = ModSetting("choice", "Choice", "choice", True, choices=(SettingChoice(True, "Boolean"), SettingChoice(1, "Integer")))
    dialog = make_dialog(fields=(field,))
    requests = []
    dialog.save_requested.connect(requests.append)
    dialog._controls["choice"].setCurrentIndex(1)
    assert dialog.is_dirty()
    dialog.save_button.click()
    assert type(requests[0]["choice"]) is int


def test_invalid_saved_values_stay_visible_and_untouched_until_edited(make_dialog):
    fields = (replace(FIELDS[1], maximum=10), replace(FIELDS[2], default=0.125))
    original = {"count": 99, "scale": "invalid"}
    dialog = make_dialog(fields=fields, values=original)
    assert dialog.draft_values() == original
    assert dialog._controls["count"].value() == 99
    assert dialog._controls["scale"].text() == "invalid"
    assert not dialog.is_dirty()
    assert dialog._errors["count"].text() == "The maximum value is 10."
    dialog.defaults_button.click()
    assert dialog.draft_values() == {"count": 3, "scale": 0.125}


@pytest.mark.parametrize("focused", [False, True])
def test_decimal_control_and_editor_scroll_the_form_without_changing_it(make_dialog, qapp, focused):
    fields = FIELDS + tuple(ModSetting(f"padding{i}", f"Padding {i}", "string", "") for i in range(8))
    dialog = make_dialog(fields=fields)
    spin = dialog._controls["scale"]
    if focused:
        spin.setFocus()
    else:
        dialog.search_edit.setFocus()
    dialog.scroll_area.ensureWidgetVisible(spin)
    qapp.processEvents()
    assert spin.hasFocus() is focused
    before = dialog.draft_values()
    bar = dialog.scroll_area.verticalScrollBar()
    for target in (spin, spin.lineEdit()):
        position = bar.value()
        amount = -120 if position < bar.maximum() else 120
        point = target.rect().center()
        event = QWheelEvent(QPointF(point), QPointF(target.mapToGlobal(point)), QPoint(), QPoint(0, amount), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(target, event)
        assert bar.value() != position
        assert dialog.draft_values() == before


@pytest.mark.parametrize("language", [option.code for option in i18n.LANGUAGES])
def test_dialog_uses_selected_language_and_preserves_author_text(make_dialog, language):
    i18n.set_language(language)
    dialog = make_dialog(fields=FIELDS + (ModSetting("literal", "Home", "string", "Settings"),))
    assert dialog.windowTitle() == i18n.format_ui_phrase("Configure {mod}", mod="Example Mod")
    assert dialog.save_button.text() == i18n.translate_ui_phrase("Save")
    assert dialog.defaults_button.text() == i18n.translate_ui_phrase("Restore Defaults")
    assert dialog._controls["scale"].accessibleName() == ("Opbrengst" if language == "nl" else "Yield")
    assert dialog._controls["literal"].accessibleName() == "Home"
    assert dialog._controls["literal"].text() == "Settings"
    dialog._controls["count"].setValue(0)
    assert dialog._errors["count"].text() == i18n.format_ui_phrase("The minimum value is {value}.", value=1)
    if language != "en":
        assert dialog.defaults_button.text() != "Restore Defaults"
        assert dialog._errors["count"].text() != "The minimum value is 1."


def test_every_new_builtin_phrase_has_all_locales_and_matching_placeholders():
    expected = {option.code for option in i18n.LANGUAGES if option.code != "en"}
    assert set(UI_PHRASES_BY_LANGUAGE) == expected
    same_spelling = {("fr", "Actions"), ("nl", "bytes")}
    for language, phrases in UI_PHRASES_BY_LANGUAGE.items():
        for source, translated in phrases.items():
            fields = lambda text: sorted(name for _, name, _, _ in Formatter().parse(text) if name is not None)
            assert translated and (translated != source or (language, source) in same_spelling), (language, source)
            assert fields(translated) == fields(source), (language, source)


@pytest.mark.parametrize("language", [option.code for option in i18n.LANGUAGES if option.code != "en"])
def test_shared_validation_messages_translate_before_formatting(language):
    previous = i18n.current_language()
    i18n.set_language(language)
    try:
        cases = (
            (replace(FIELDS[0]), 1, "Choose on or off.", {}),
            (replace(FIELDS[1]), 1.5, "Enter a whole number.", {}),
            (replace(FIELDS[2]), float("inf"), "Enter a finite number.", {}),
            (replace(FIELDS[3]), 3, "Enter text.", {}),
            (replace(FIELDS[4]), "missing", "Choose one of the available options.", {}),
            (replace(FIELDS[1]), 0, "The minimum value is {value}.", {"value": 1}),
            (replace(FIELDS[1]), 11, "The maximum value is {value}.", {"value": 10}),
            (replace(FIELDS[3]), "x" * 13, "Use at most {count} characters.", {"count": 12}),
        )
        for field, value, source, params in cases:
            error = validate_values((field,), {field.id: value})[field.id]
            assert error == i18n.format_ui_phrase(source, **params)
            assert error != source.format(**params)
    finally:
        i18n.set_language(previous)
