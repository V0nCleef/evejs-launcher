"""New-character tile and dialog interaction tests."""
from __future__ import annotations

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMenu

from src.core.db import Account, Character
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus
from src.core.process_tracker import ProcessTracker
from src.i18n import format_ui_phrase, set_language, translate_ui_phrase
from src.pages.characters_page import CharactersPage
from src.widgets.new_character_card import NewCharacterCard
from src.widgets.new_character_dialog import NewCharacterDialog, NewCharacterDraft


def _account() -> Account:
    return Account(
        username="fixture-account",
        account_id=7,
        role="0",
        banned=False,
        characters=[Character(char_id=140000007, name="Fixture Source")],
    )


def test_new_character_tile_is_first_and_emits_request(qapp: QApplication) -> None:
    page = CharactersPage()
    page.refresh([_account()], [], ProcessTracker())
    spy = QSignalSpy(page.new_character_requested)

    tile = page._grid.itemAtPosition(0, 0).widget()
    assert isinstance(tile, NewCharacterCard)
    QTest.mouseClick(tile._button, Qt.MouseButton.LeftButton)

    assert len(spy) == 1
    page.deleteLater()


def test_new_character_tile_supports_keyboard_and_unavailable_guard(
    qapp: QApplication,
) -> None:
    tile = NewCharacterCard()
    spy = QSignalSpy(tile.requested)

    tile.setFocus()
    QTest.keyClick(tile, Qt.Key.Key_Return)

    assert len(spy) == 1
    assert tile.accessibleName() == "Create a new local character"
    assert tile.minimumWidth() <= 148
    assert tile.maximumWidth() == 196

    tile.set_available(False, "Native runtime required")
    QTest.keyClick(tile, Qt.Key.Key_Space)

    assert len(spy) == 1
    assert tile._button.text() == "NATIVE ONLY"
    assert "Native runtime" in tile.accessibleDescription()

    tile.set_available(False, "Managed Docker mode is required")
    assert tile._button.text() == "MANAGED ONLY"
    tile.deleteLater()


def test_character_overflow_exposes_character_and_account_deletion(
    qapp: QApplication,
    monkeypatch,
) -> None:
    page = CharactersPage()
    page.refresh([_account()], [], ProcessTracker())
    card = page._cards[("fixture-account", 140000007)]
    character_spy = QSignalSpy(page.delete_character_requested)
    account_spy = QSignalSpy(page.delete_account_requested)
    group_spy = QSignalSpy(page.manage_groups_requested)
    action_texts: list[str] = []

    def fake_exec(menu: QMenu, *_args):
        action_texts.extend(action.text() for action in menu.actions())
        next(
            action
            for action in menu.actions()
            if action.text() == "Manage Groups..."
        ).trigger()
        next(
            action
            for action in menu.actions()
            if action.text() == "Delete Character..."
        ).trigger()
        next(
            action
            for action in menu.actions()
            if action.text() == "Delete Account..."
        ).trigger()

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    card._on_overflow_clicked()

    assert "Delete Character..." in action_texts
    assert "Delete Account..." in action_texts
    assert "Manage Groups..." in action_texts
    assert group_spy[0][0] == 140000007
    assert list(character_spy[0]) == ["fixture-account", "Fixture Source", 140000007]
    assert list(account_spy[0]) == ["fixture-account", "Fixture Source", 140000007]
    page.deleteLater()


def test_dialog_requires_patched_ready_snapshot_for_overview_copy(
    qapp: QApplication,
) -> None:
    status = OverviewPatchStatus(
        OverviewPatchState.PATCHED,
        "Overview copy bridge installed; original backup verified.",
        3396210,
    )
    dialog = NewCharacterDialog([_account()], status, {140000007})
    spy = QSignalSpy(dialog.create_requested)
    dialog.account_edit.setText("fixture-new")
    dialog.character_edit.setText("Fixture Pilot")
    dialog.overview_combo.setCurrentIndex(1)

    assert dialog.create_button.isEnabled()
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

    assert len(spy) == 1
    draft = spy[0][0]
    assert isinstance(draft, NewCharacterDraft)
    assert draft.overview_source_character_id == 140000007
    assert draft.is_gm is False
    dialog.deleteLater()


def test_dialog_uses_javascript_character_name_semantics(
    qapp: QApplication,
) -> None:
    status = OverviewPatchStatus(
        OverviewPatchState.PATCHED,
        "Overview copy bridge installed; original backup verified.",
        3396210,
    )
    dialog = NewCharacterDialog([], status, set())
    spy = QSignalSpy(dialog.create_requested)
    dialog.account_edit.setText("fixture-new")

    dialog.character_edit.setText("Fixture\x7fPilot")
    assert not dialog.create_button.isEnabled()

    dialog.character_edit.setText("🚀" * 19)
    assert not dialog.create_button.isEnabled()

    dialog.character_edit.setText("\ufeff Étoile\ufeff\u200b🚀 \ufeff")
    assert dialog.create_button.isEnabled()
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

    assert len(spy) == 1
    draft = spy[0][0]
    assert isinstance(draft, NewCharacterDraft)
    assert draft.character_name == "Étoile \u200b🚀"
    dialog.deleteLater()


def test_new_character_dialog_uses_native_responsive_deep_signal_shell(
    qapp: QApplication,
) -> None:
    status = OverviewPatchStatus(
        OverviewPatchState.READY,
        "Supported client; ready to patch.",
        3396210,
    )
    dialog = NewCharacterDialog([_account()], status, set())

    assert not bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert dialog.objectName() == "newCharacterDialog"
    assert dialog.minimumWidth() <= 560
    assert dialog.create_button.minimumHeight() >= 34
    assert dialog.account_edit.accessibleName() == "Local account name"
    dialog.deleteLater()


def test_new_character_dialog_is_translated_when_created_after_language_switch(
    qapp: QApplication,
) -> None:
    set_language("zh_CN")
    status = OverviewPatchStatus(
        OverviewPatchState.READY,
        "Supported client; ready to patch.",
        3396210,
    )
    dialog = NewCharacterDialog([_account()], status, set())

    try:
        title = dialog.findChild(QLabel, "dialogTitle")
        assert dialog.windowTitle() == "创建新角色"
        assert title is not None
        assert title.text() == "创建新角色"

        dialog.account_edit.setText("Settings")
        dialog.character_edit.setText("Market")
        assert dialog.account_edit.text() == "Settings"
        assert dialog.character_edit.text() == "Market"
    finally:
        set_language("en")
        dialog.deleteLater()


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_new_character_header_and_initial_readiness_use_active_language(
    qapp: QApplication,
    language: str,
) -> None:
    set_language(language)
    status = OverviewPatchStatus(
        OverviewPatchState.READY,
        "Supported client; ready to patch.",
        3396210,
    )
    dialog = NewCharacterDialog(
        [_account()],
        status,
        {140000007},
        runtime_label="NATIVE RUNTIME",
    )

    try:
        eyebrow = dialog.findChild(QLabel, "dialogEyebrow")
        runtime_label = translate_ui_phrase("NATIVE RUNTIME")
        expected_header = format_ui_phrase(
            "CHARACTER PROVISIONING  /  {runtime_label}",
            runtime_label=runtime_label,
        )
        assert eyebrow is not None
        assert eyebrow.text() == expected_header
        assert eyebrow.text() != "CHARACTER PROVISIONING  /  NATIVE RUNTIME"

        source_row = dialog.overview_combo.itemText(1)
        readiness = translate_ui_phrase("snapshot ready")
        assert "Fixture Source" in source_row
        assert "fixture-account" in source_row
        assert readiness in source_row
        assert readiness != "snapshot ready"
    finally:
        set_language("en")
        dialog.deleteLater()


def test_dialog_allows_creation_with_an_uncaptured_overview_source(
    qapp: QApplication,
) -> None:
    status = OverviewPatchStatus(
        OverviewPatchState.PATCHED,
        "Overview copy bridge installed; original backup verified.",
        3396210,
    )
    dialog = NewCharacterDialog([_account()], status, set())
    dialog.account_edit.setText("fixture-new")
    dialog.character_edit.setText("Fixture Pilot")
    dialog.overview_combo.setCurrentIndex(1)
    spy = QSignalSpy(dialog.create_requested)

    assert dialog.create_button.isEnabled()
    assert dialog.create_button.text() == "CREATE CHARACTER"
    assert "you can create the character now" in dialog.overview_hint.text().lower()
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

    assert len(spy) == 1
    assert spy[0][0].overview_source_character_id == 140000007
    dialog.deleteLater()


def test_dialog_revalidates_first_time_source_after_patch_and_capture(
    qapp: QApplication,
) -> None:
    ready = OverviewPatchStatus(
        OverviewPatchState.READY,
        "Supported client; ready to patch.",
        3396210,
    )
    patched = OverviewPatchStatus(
        OverviewPatchState.PATCHED,
        "Overview bridge installed.",
        3396210,
    )
    dialog = NewCharacterDialog([_account()], ready, set())
    dialog.account_edit.setText("fixture-new")
    dialog.character_edit.setText("Fixture Pilot")
    dialog.overview_combo.setCurrentIndex(1)

    dialog.set_busy(True, "PATCHING CLIENT…")
    dialog.set_busy(False)
    dialog.set_patch_status(patched)

    assert dialog.create_button.isEnabled()
    assert dialog.create_button.text() == "CREATE CHARACTER"
    assert "you can create the character now" in dialog.overview_hint.text().lower()

    dialog.set_snapshot_ready_ids({140000007})

    assert dialog.create_button.isEnabled()
    assert dialog.create_button.text() == "CREATE CHARACTER"
    assert "will be imported" in dialog.overview_hint.text().lower()
    dialog.deleteLater()
