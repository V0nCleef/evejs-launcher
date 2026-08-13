"""Interaction tests for the character-group manager."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from src.core.db import Account, Character
from src.core.groups import CharacterGroup, GroupMember, TargetGroupState
from src.widgets.character_groups_dialog import CharacterGroupsDialog
from src.pages.characters_page import CharactersPage
from src.pages.home_page import HomePage
from src.core.process_tracker import ProcessTracker


@pytest.fixture(autouse=True)
def _flush_deferred_qt_deletes(qapp: QApplication):
    """Prevent page-owned timers from leaking into later UI regressions."""
    yield
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _accounts() -> list[Account]:
    return [
        Account(
            username="fixture-account-a",
            account_id=1,
            role="0",
            banned=False,
            characters=[
                Character(101, "Fixture Miner"),
                Character(102, "Fixture Hauler"),
            ],
        ),
        Account(
            username="fixture-account-b",
            account_id=2,
            role="0",
            banned=False,
            characters=[Character(201, "Fixture Support")],
        ),
    ]


def test_dialog_creates_group_and_enforces_one_character_per_account(
    qapp: QApplication,
) -> None:
    dialog = CharacterGroupsDialog(_accounts(), set(), TargetGroupState())

    QTest.mouseClick(dialog.new_button, Qt.MouseButton.LeftButton)
    dialog.name_edit.setText("Miners")
    first = dialog._member_items[GroupMember(1, 101)]
    second = dialog._member_items[GroupMember(1, 102)]
    support = dialog._member_items[GroupMember(2, 201)]

    first.setCheckState(0, Qt.CheckState.Checked)
    second.setCheckState(0, Qt.CheckState.Checked)
    support.setCheckState(0, Qt.CheckState.Checked)

    assert first.checkState(0) == Qt.CheckState.Unchecked
    assert second.checkState(0) == Qt.CheckState.Checked
    assert support.checkState(0) == Qt.CheckState.Checked

    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.group_state.groups[0].name == "Miners"
    assert dialog.group_state.groups[0].members == (
        GroupMember(1, 102),
        GroupMember(2, 201),
    )
    dialog.deleteLater()


def test_groups_dialog_uses_native_responsive_deep_signal_shell(
    qapp: QApplication,
) -> None:
    dialog = CharacterGroupsDialog(_accounts(), set(), TargetGroupState())

    assert not bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert dialog.objectName() == "characterGroupsDialog"
    assert dialog.minimumWidth() <= 760
    assert dialog.group_list.accessibleName() == "Character groups"
    assert dialog.save_button.minimumHeight() >= 32
    assert dialog.error_label.focusPolicy() == Qt.FocusPolicy.StrongFocus
    dialog.deleteLater()


def test_dialog_allows_same_character_in_multiple_groups(
    qapp: QApplication,
) -> None:
    member = GroupMember(1, 101)
    state = TargetGroupState(
        (
            CharacterGroup("miners", "Miners", members=(member,)),
            CharacterGroup("industry", "Industry", members=(member,)),
        ),
        "miners",
    )
    dialog = CharacterGroupsDialog(_accounts(), set(), state)

    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert all(group.members == (member,) for group in dialog.group_state.groups)
    dialog.deleteLater()


def test_hidden_member_is_explained_and_retained(qapp: QApplication) -> None:
    member = GroupMember(1, 101)
    state = TargetGroupState(
        (CharacterGroup("miners", "Miners", members=(member,)),),
        "miners",
    )
    dialog = CharacterGroupsDialog(
        _accounts(),
        {"Fixture Miner"},
        state,
    )

    item = dialog._member_items[member]
    assert "hidden" in item.text(0).lower()
    assert item.checkState(0) == Qt.CheckState.Checked
    dialog.deleteLater()


def test_dialog_lists_launchable_accounts_before_hidden_accounts(
    qapp: QApplication,
) -> None:
    accounts = [
        Account(
            username="aaa-hidden",
            account_id=3,
            role="0",
            banned=False,
            hidden=True,
            characters=[Character(301, "Hidden Pilot")],
        ),
        *_accounts(),
    ]
    dialog = CharacterGroupsDialog(
        accounts,
        set(),
        TargetGroupState((CharacterGroup("miners", "Miners"),), "miners"),
    )

    top_level_names = [
        dialog.character_tree.topLevelItem(index).text(0)
        for index in range(dialog.character_tree.topLevelItemCount())
    ]

    assert top_level_names == [
        "fixture-account-a",
        "fixture-account-b",
        "aaa-hidden",
    ]
    dialog.deleteLater()


def test_delete_group_confirms_metadata_only(
    qapp: QApplication,
    monkeypatch,
) -> None:
    state = TargetGroupState(
        (CharacterGroup("miners", "Miners"), CharacterGroup("scouts", "Scouts")),
        "miners",
    )
    messages: list[str] = []

    def answer(*args):
        messages.append(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    dialog = CharacterGroupsDialog(_accounts(), set(), state)

    QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    assert "No characters or accounts will be deleted" in messages[0]
    assert [group.name for group in dialog.group_state.groups] == ["Scouts"]
    dialog.deleteLater()


def test_characters_page_filters_selected_group_without_rebuilding_portraits(
    qapp: QApplication,
) -> None:
    accounts = _accounts()
    state = TargetGroupState(
        (
            CharacterGroup(
                "miners",
                "Miners",
                members=(GroupMember(1, 101), GroupMember(2, 201)),
            ),
        ),
        "miners",
    )
    page = CharactersPage()
    page.set_group_state(state)
    page.refresh(accounts, [], ProcessTracker())

    assert not page._cards[("fixture-account-a", 101)].isHidden()
    assert page._cards[("fixture-account-a", 102)].isHidden()
    assert not page._cards[("fixture-account-b", 201)].isHidden()
    assert page.count_label.text() == "(2 of 3)"
    assert page._new_character_card is not None
    assert not page._new_character_card.isHidden()
    page.deleteLater()


def test_character_group_controls_emit_selection_launch_and_cancel(
    qapp: QApplication,
) -> None:
    state = TargetGroupState(
        (CharacterGroup("miners", "Miners", members=(GroupMember(1, 101),)),),
        None,
    )
    page = CharactersPage()
    page.set_group_state(state)
    selection_spy = QSignalSpy(page.group_selection_changed)
    launch_spy = QSignalSpy(page.launch_group_requested)
    cancel_spy = QSignalSpy(page.cancel_group_launches_requested)

    page.group_combo.setCurrentIndex(1)
    page.set_group_state(TargetGroupState(state.groups, "miners"))
    page.set_group_launch_available(True, 1)
    QTest.mouseClick(page.launch_group_button, Qt.MouseButton.LeftButton)
    page.set_group_launch_progress(0, 1, 0, "Miners")
    QTest.mouseClick(page.launch_group_button, Qt.MouseButton.LeftButton)

    assert selection_spy[0][0] == "miners"
    assert len(launch_spy) == 1
    assert len(cancel_spy) == 1
    page.deleteLater()


def test_home_group_selector_emits_selection_and_manage_action(
    qapp: QApplication,
) -> None:
    state = TargetGroupState(
        (CharacterGroup("miners", "Miners", members=(GroupMember(1, 101),)),),
        None,
    )
    page = HomePage()
    selection_spy = QSignalSpy(page.group_selection_changed)
    manage_spy = QSignalSpy(page.manage_groups_requested)
    page.set_group_state(state)

    page.group_combo.setCurrentIndex(1)
    manage_index = page.group_combo.findData("__manage_groups__")
    page.group_combo.setCurrentIndex(manage_index)

    assert selection_spy[0][0] == "miners"
    assert len(manage_spy) == 1
    assert page.group_combo.currentData() is None
    page.deleteLater()


def test_dialog_restores_fully_verified_previous_group_set(
    qapp: QApplication,
) -> None:
    previous = TargetGroupState(
        (
            CharacterGroup(
                "miners",
                "Miners",
                members=(GroupMember(1, 101), GroupMember(2, 201)),
            ),
        ),
        "miners",
    )
    dialog = CharacterGroupsDialog(
        _accounts(),
        set(),
        TargetGroupState(),
        relink_candidates=(previous,),
    )

    QTest.mouseClick(dialog.restore_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    assert dialog.group_state == previous
    dialog.deleteLater()


def test_browsing_groups_in_manager_does_not_change_launch_selection(
    qapp: QApplication,
) -> None:
    state = TargetGroupState(
        (
            CharacterGroup("miners", "Miners"),
            CharacterGroup("scouts", "Scouts"),
        ),
        "miners",
    )
    dialog = CharacterGroupsDialog(_accounts(), set(), state)

    dialog.group_list.setCurrentRow(1)
    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)

    assert dialog.group_state.selected_group_id == "miners"
    dialog.deleteLater()
