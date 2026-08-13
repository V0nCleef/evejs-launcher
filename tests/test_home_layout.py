"""Geometry contracts for the Deep Signal Operations surface."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from src.core.service_status import ServiceState
from src.pages.home_page import HomePage, extract_latest_release
from src.theme import build_qss


def test_latest_release_parser_uses_the_first_release_and_caps_highlights() -> None:
    changelog = """# EveJS Launcher V1

## Changelog

## v9.9.9 — 2026-07-28

### Added
- First highlight
- Second highlight
- Third highlight
- Fourth highlight

## v9.9.8 — 2026-07-27
- Older highlight
"""

    version, highlights = extract_latest_release(changelog, limit=3)

    assert version == "v9.9.9 — 2026-07-28"
    assert highlights == ["First highlight", "Second highlight", "Third highlight"]


def test_home_uses_a_cinematic_command_column_and_hidden_compatibility_store(
    qapp: QApplication,
) -> None:
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})
    )
    page = HomePage()
    page.resize(1122, 696)
    page.show()
    qapp.processEvents()

    try:
        assert page.command_column.width() == 760
        assert page.command_column.geometry().right() < page.contentsRect().right()
        assert page.recent_activity.property("class") == "recentActivity"
        assert len(page.recent_activity.activity_rows) == 4

        cards = (
            page.services_card.game_row,
            page.services_card.market_row,
            page.running_card,
        )
        assert all(card.property("class") == "signalInstrument" for card in cards)
        assert all(card.height() == 132 for card in cards)
        assert max(card.width() for card in cards) - min(card.width() for card in cards) <= 1
        assert page.services_card.game_row._ring.size().width() == 80
        assert page.services_card.market_row._ring.size().width() == 80
        assert page.running_card._ring.size().width() == 80

        # Removed primary-canvas widgets remain available to controller code.
        assert page.hero.isVisible() is False
        assert page.accounts_card.isVisible() is False
        assert page.characters_card.isVisible() is False
        assert page.release_card.isVisible() is False
        assert page.resources_card.isVisible() is False
        assert page.release_card.version_label.text()
        assert page.release_card.highlights_label.text()
    finally:
        page.close()
        page.deleteLater()
        qapp.setStyleSheet(original_style)


def test_primary_actions_are_equal_strength_and_fit_the_minimum_viewport(
    qapp: QApplication,
) -> None:
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})
    )
    page = HomePage()
    # The current shell leaves 756 px for Home at its 1000 px minimum width.
    page.resize(756, 568)
    page.show()
    qapp.processEvents()

    try:
        assert page.command_column.width() == 708
        assert page.btn_start_servers.height() == 70
        assert page.btn_launch_all.height() == 70
        assert abs(page.btn_start_servers.width() - page.btn_launch_all.width()) <= 1
        assert page.btn_start_servers.geometry().right() < page.group_combo.geometry().left()
        assert page.group_combo.geometry().right() < page.btn_launch_all.geometry().left()
        assert page.group_combo.width() == 130
        assert page.btn_start_servers.property("deepRole") == "launchStack"
        assert page.btn_launch_all.property("deepRole") == "launchGroup"
        assert page.btn_kill_all.height() == 30
        assert page.btn_kill_all.width() <= 148

        cards = (
            page.services_card.game_row,
            page.services_card.market_row,
            page.running_card,
        )
        assert max(card.width() for card in cards) - min(card.width() for card in cards) <= 1
        assert page.recent_activity.geometry().bottom() <= page.command_column.contentsRect().bottom()
        assert all(
            row.geometry().right() <= page.recent_activity.contentsRect().right()
            for row in page.recent_activity.activity_rows
        )
    finally:
        page.close()
        page.deleteLater()
        qapp.setStyleSheet(original_style)


def test_home_exposes_deep_signal_operations_instruments(
    qapp: QApplication,
) -> None:
    page = HomePage()

    assert page.property("deepSignal") is True
    assert page.page_header.title_label.text() == "OPERATIONS"
    assert page.signal_background.motion_enabled is True
    assert page.services_card.game_row._ring.accessibleName() == "Game status"
    assert page.services_card.market_row._ring.accessibleName() == "Market status"

    page.show()
    qapp.processEvents()
    assert page.signal_background.is_animating() is False
    page.set_animations_enabled(False)
    assert page.hero.animations_enabled is False
    assert page.signal_background.motion_enabled is False
    assert page.signal_background.is_animating() is False
    page.set_animations_enabled(True)
    assert page.hero.animations_enabled is True
    assert page.signal_background.motion_enabled is True
    assert page.signal_background.is_animating() is False


def test_home_reduce_motion_controls_every_live_status_instrument(
    qapp: QApplication,
) -> None:
    page = HomePage()
    page.resize(1122, 696)
    page.show()
    page.services_card.game_row.set_state(ServiceState.STARTING)
    page.services_card.market_row.set_state(ServiceState.STOPPING)
    qapp.processEvents()

    game_ring = page.services_card.game_row._ring
    market_ring = page.services_card.market_row._ring
    client_ring = page.running_card._ring
    try:
        assert game_ring._motion is page._motion
        assert market_ring._motion is page._motion
        assert client_ring._motion is page._motion
        assert game_ring.is_animating() is True
        assert market_ring.is_animating() is True

        page.set_animations_enabled(False)
        assert page._motion.animations_enabled is False
        assert game_ring.is_animating() is False
        assert market_ring.is_animating() is False
        assert game_ring._phase == 0.0
        assert market_ring._phase == 0.0

        page.set_animations_enabled(True)
        qapp.processEvents()
        assert game_ring.is_animating() is True
        assert market_ring.is_animating() is True
        assert page.signal_background.is_animating() is False
    finally:
        page.close()
        page.deleteLater()


def test_resources_card_routes_each_console_action_through_home(
    qapp: QApplication,
) -> None:
    page = HomePage()
    requested: list[str] = []
    page.console_requested.connect(requested.append)

    page.resources_card.btn_game_console.click()
    page.resources_card.btn_market_console.click()

    assert requested == ["server", "market"]
