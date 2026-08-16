"""Focused layout and interaction contracts for the Deep Signal roster."""
from __future__ import annotations

from PyQt6.QtCore import QAbstractAnimation, QSize, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication

from src.constants import COLORS, Status
from src.core.db import Account, Character
from src.core.process_tracker import ProcessTracker
from src.pages.characters_page import _ROSTER_SUBTITLE, CharactersPage


def _accounts(count: int = 6) -> list[Account]:
    return [
        Account(
            username=f"fixture-account-{index}",
            account_id=100 + index,
            role="0",
            banned=False,
            characters=[
                Character(
                    char_id=9_000 + index,
                    name=f"Fixture Pilot {index}",
                    isk=index * 1_000_000,
                    skill_points=index * 25_000,
                    ship_name="Fixture Cruiser",
                    location="Fixture System",
                    security_status=2.5,
                )
            ],
        )
        for index in range(1, count + 1)
    ]


def _show_page(
    qapp: QApplication,
    size: QSize,
    *,
    count: int = 6,
) -> CharactersPage:
    page = CharactersPage()
    page.resize(size)
    page.show()
    page.refresh(_accounts(count), [], ProcessTracker())
    qapp.processEvents()
    QTest.qWait(20)
    qapp.processEvents()
    return page


def _close_page(qapp: QApplication, page: CharactersPage) -> None:
    page.cancel_portrait_loads(invalidate=True)
    page.close()
    page.deleteLater()
    qapp.processEvents()


def test_default_layout_is_static_six_column_deep_signal_roster(
    qapp: QApplication,
) -> None:
    page = _show_page(qapp, QSize(1146, 680))
    try:
        assert page.property("deepSignal") is True
        assert page.signal_background.is_animating() is False
        assert page.page_header.title_label.text() == "CHARACTERS"
        assert page._grid_columns == 6
        assert page._compact_controls is False
        assert page._new_character_card is page._grid.itemAtPosition(0, 0).widget()
        widths = {card.width() for card in page._cards.values()}
        assert len(widths) == 1
        assert 156 <= widths.pop() <= 196
    finally:
        _close_page(qapp, page)


def test_native_shell_control_rail_keeps_both_actions_inside_the_panel(
    qapp: QApplication,
) -> None:
    """The 1366 shell leaves a 1146 px Characters-page viewport."""
    page = _show_page(qapp, QSize(1146, 680))
    try:
        controls = (
            page.search_edit,
            page.status_combo,
            page.group_combo,
            page.launch_group_button,
            page.manage_groups_button,
        )
        margins = page._controls_grid.contentsMargins()
        usable_right = page.controls_panel.width() - margins.right()

        assert page._compact_controls is False
        assert all(
            left.geometry().right() < right.geometry().left()
            for left, right in zip(controls, controls[1:])
        )
        assert page.manage_groups_button.geometry().right() <= usable_right
        for button in (page.launch_group_button, page.manage_groups_button):
            assert button.width() >= button.sizeHint().width()
    finally:
        _close_page(qapp, page)


def test_selected_card_and_bottom_command_pane_have_strong_gold_hierarchy(
    qapp: QApplication,
) -> None:
    page = _show_page(qapp, QSize(1146, 680))
    key = ("fixture-account-1", 9_001)
    card = page._cards[key]
    try:
        page._on_card_selected(key[0], "Fixture Pilot 1", key[1])
        QTest.qWait(220)
        qapp.processEvents()

        assert card.property("selected") is True
        assert card._selection_marker.isVisible()
        assert page.detail_panel.isVisible()
        assert page.detail_panel.maximumHeight() == 150
        assert page.detail_panel.geometry().top() > page._scroll.geometry().top()
        assert COLORS["gold"] in card._launch_btn.styleSheet()
        assert COLORS["gold"] in page.detail_panel._launch_btn.styleSheet()
        assert page.detail_panel.get_character() == (
            "fixture-account-1",
            "Fixture Pilot 1",
            9_001,
        )
    finally:
        _close_page(qapp, page)


def test_reduce_motion_settles_character_detail_transitions_immediately(
    qapp: QApplication,
) -> None:
    page = _show_page(qapp, QSize(1146, 680))
    key = ("fixture-account-1", 9_001)
    try:
        page.set_animations_enabled(False)
        page._on_card_selected(key[0], "Fixture Pilot 1", key[1])

        assert page.animations_enabled is False
        assert page._animating is False
        assert page._transition_group is None
        assert page.detail_panel.maximumHeight() == 150
        assert page.detail_panel.isVisible()

        # Re-enabling motion permits a future transition, but never starts the
        # page's permanently-static raster background.
        page.set_animations_enabled(True)
        page.clear_selection()
        assert page._transition_group is not None
        assert (
            page._transition_group.state()
            == QAbstractAnimation.State.Running
        )

        # Toggling Reduce Motion during that close settles the requested end
        # state synchronously rather than leaving a half-height command pane.
        page.set_animations_enabled(False)
        assert page._animating is False
        assert page._transition_group is None
        assert page.detail_panel.maximumHeight() == 0
        assert page.detail_panel.isHidden()

        page.set_animations_enabled(True)
        assert page.signal_background.is_animating() is False
    finally:
        _close_page(qapp, page)


def test_minimum_window_reflows_controls_and_cards_without_losing_actions(
    qapp: QApplication,
) -> None:
    page = _show_page(qapp, QSize(780, 548))
    try:
        assert page._compact_controls is True
        assert page._grid_columns == 4
        assert page.group_combo.geometry().top() > page.search_edit.geometry().top()
        assert page.launch_group_button.geometry().top() == page.group_combo.geometry().top()
        second_row = (
            page.group_combo,
            page.launch_group_button,
            page.manage_groups_button,
        )
        assert all(
            left.geometry().right() < right.geometry().left()
            for left, right in zip(second_row, second_row[1:])
        )
        assert page.manage_groups_button.geometry().right() <= page.controls_panel.width()
        assert (
            page.launch_group_button.width()
            >= page.launch_group_button.sizeHint().width()
        )
        assert (
            page.manage_groups_button.width()
            >= page.manage_groups_button.sizeHint().width()
        )
        assert page.search_edit.accessibleName() == "Search characters"
        assert page.status_combo.accessibleName() == "Character status filter"
    finally:
        _close_page(qapp, page)


def test_status_filter_and_keyboard_selection_preserve_public_contracts(
    qapp: QApplication,
) -> None:
    page = _show_page(qapp, QSize(780, 548), count=3)
    ready_key = ("fixture-account-1", 9_001)
    running_key = ("fixture-account-2", 9_002)
    ready_card = page._cards[ready_key]
    running_card = page._cards[running_key]
    running_card.set_status(Status.RUNNING)
    selection_spy = QSignalSpy(page.character_selected)
    try:
        page.status_combo.setCurrentIndex(page.status_combo.findData(Status.RUNNING))
        qapp.processEvents()
        assert ready_card.isHidden()
        assert not running_card.isHidden()
        assert page.count_label.text() == "(1 / 3)"

        page.status_combo.setCurrentIndex(0)
        qapp.processEvents()
        ready_card.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(ready_card, Qt.Key.Key_Return)
        qapp.processEvents()

        assert len(selection_spy) == 1
        assert list(selection_spy[0]) == [
            "fixture-account-1",
            "Fixture Pilot 1",
            9_001,
        ]
        assert "Selected" in ready_card.accessibleDescription()

        # Repeating the same selection toggles it off and does not emit a
        # second selection event (therefore no duplicate UI cue upstream).
        QTest.keyClick(ready_card, Qt.Key.Key_Return)
        qapp.processEvents()
        assert len(selection_spy) == 1
        assert ready_card.property("selected") is False
    finally:
        _close_page(qapp, page)


def test_unreadable_roster_is_distinguished_from_an_empty_game_store(
    qapp: QApplication,
) -> None:
    """A failed EveJS read must not render as a store with no characters."""
    reason = "Docker character export returned malformed JSON."
    page = _show_page(qapp, QSize(1146, 680), count=0)
    try:
        assert page.count_label.text() == "(0)"

        page.set_data_error(reason)
        qapp.processEvents()

        assert page.count_label.text() == "DATA UNAVAILABLE"
        assert page.count_label.toolTip() == reason
        assert page.count_label.property("state") == "error"
        assert page.page_header.subtitle_label.text() == reason

        page.set_data_error("")
        qapp.processEvents()

        assert page.count_label.text() == "(0)"
        assert page.count_label.property("state") == ""
        assert page.page_header.subtitle_label.text() == _ROSTER_SUBTITLE
    finally:
        _close_page(qapp, page)


def test_recovered_roster_reports_counts_instead_of_the_stale_failure(
    qapp: QApplication,
) -> None:
    """Cards arriving after a failure retire the unavailable state."""
    page = _show_page(qapp, QSize(1146, 680), count=0)
    try:
        page.set_data_error("Docker character export returned malformed JSON.")
        qapp.processEvents()
        assert page.count_label.text() == "DATA UNAVAILABLE"

        page.refresh(_accounts(3), [], ProcessTracker())
        qapp.processEvents()

        assert page.count_label.text() == "(3)"
    finally:
        _close_page(qapp, page)
