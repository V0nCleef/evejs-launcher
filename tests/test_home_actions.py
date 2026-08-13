"""Behavior tests for the operational Home dashboard."""
from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from src.constants import COLORS
from src.core.service_status import RuntimeSnapshot, ServiceState
from src.pages.home_page import HomePage


def test_services_card_renders_game_and_market_independently(
    qapp: QApplication,
) -> None:
    page = HomePage()
    snapshot = RuntimeSnapshot(
        game=ServiceState.ONLINE,
        market=ServiceState.FAILED,
        running_clients=0,
        game_pid=1200,
        market_error="Market exited before readiness",
    )

    page.apply_runtime_snapshot(snapshot)

    assert page.services_card.game_row.state_text == "Online"
    assert page.services_card.game_row.detail_text == "PID 1200"
    assert page.services_card.market_row.state_text == "Failed"
    assert page.services_card.market_row.detail_text == "Market exited before readiness"
    assert page.services_card.game_row._ring.state == "online"
    assert page.services_card.market_row._ring.state == "failed"
    assert page.overall_status_label.text() == "ATTENTION REQUIRED"


def test_deep_signal_overall_status_uses_the_shared_snapshot(
    qapp: QApplication,
) -> None:
    page = HomePage()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.ONLINE,
            market=ServiceState.ONLINE,
            running_clients=2,
        )
    )

    assert page.overall_status_label.text() == "ALL SYSTEMS NOMINAL"
    assert page.overall_status_label.property("state") == "online"
    assert page.running_card.value_label.text() == "2"
    assert page.running_card._ring.value == "2"
    assert page.running_card._ring.state == "online"


def test_game_market_and_zero_clients_are_red_then_return_green_online(
    qapp: QApplication,
) -> None:
    page = HomePage()
    offline = RuntimeSnapshot(
        game=ServiceState.OFFLINE,
        market=ServiceState.OFFLINE,
        running_clients=0,
    )

    page.apply_runtime_snapshot(offline)

    game = page.services_card.game_row
    market = page.services_card.market_row
    clients = page.running_card

    assert game.property("statusState") == "offline"
    assert game._ring.signal_color == COLORS["red"]
    assert game._ring.progress == 0.72
    assert COLORS["red"] in game._dot.styleSheet()
    assert COLORS["red"] in game._state_label.styleSheet()

    assert market.property("statusState") == "offline"
    assert market._ring.signal_color == COLORS["red"]
    assert market._ring.progress == 0.72
    assert COLORS["red"] in market._dot.styleSheet()
    assert COLORS["red"] in market._state_label.styleSheet()

    assert clients.property("statusState") == "offline"
    assert clients._ring.value == "0"
    assert clients._ring.signal_color == COLORS["red"]
    assert clients._ring.progress == 0.72
    assert COLORS["red"] in clients._dot.styleSheet()
    assert COLORS["red"] in clients._state_label.styleSheet()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.ONLINE,
            market=ServiceState.ONLINE,
            running_clients=3,
        )
    )

    assert game.property("statusState") == "online"
    assert game._ring.signal_color == COLORS["green"]
    assert game._ring.progress == 1.0
    assert COLORS["green"] in game._dot.styleSheet()
    assert COLORS["green"] in game._state_label.styleSheet()

    assert market.property("statusState") == "online"
    assert market._ring.signal_color == COLORS["green"]
    assert market._ring.progress == 1.0
    assert COLORS["green"] in market._dot.styleSheet()
    assert COLORS["green"] in market._state_label.styleSheet()

    assert clients.property("statusState") == "online"
    assert clients._ring.value == "3"
    assert clients._ring.signal_color == COLORS["green"]
    assert clients._ring.progress == 1.0
    assert COLORS["green"] in clients._dot.styleSheet()
    assert COLORS["green"] in clients._state_label.styleSheet()


def test_recent_activity_records_only_truthful_snapshot_transitions(
    qapp: QApplication,
) -> None:
    page = HomePage()
    observed_at = datetime(2026, 8, 12, 12, 41, tzinfo=timezone.utc)
    initial = RuntimeSnapshot(
        game=ServiceState.OFFLINE,
        market=ServiceState.OFFLINE,
        running_clients=0,
        checked_at=observed_at,
    )
    page.apply_runtime_snapshot(initial)
    page.apply_runtime_snapshot(initial)

    assert len(page.recent_activity.messages) == 1
    assert page.recent_activity.messages[0] == "Game offline · Market offline · 0 clients"

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.ONLINE,
            market=ServiceState.STARTING,
            running_clients=2,
            checked_at=observed_at,
        )
    )

    assert page.recent_activity.messages == (
        "2 EVE clients running",
        "Market launch sequence started",
        "Game readiness signal online",
        "Game offline · Market offline · 0 clients",
    )
    assert len(page.recent_activity.activity_rows) == 4
    assert all(
        row.message_label.property("class") == "activityMessage"
        for row in page.recent_activity.activity_rows
    )

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.FAILED,
            market=ServiceState.ONLINE,
            running_clients=0,
            checked_at=observed_at,
        )
    )
    assert len(page.recent_activity.messages) == 4
    assert page.recent_activity.messages[0] == "0 EVE clients running"
    assert "Game service reported a failure" in page.recent_activity.messages


def test_service_row_opens_the_matching_console_from_keyboard(
    qapp: QApplication,
) -> None:
    page = HomePage()
    requested: list[str] = []
    page.console_requested.connect(requested.append)
    page.show()
    qapp.processEvents()

    page.services_card.market_row.setFocus()
    QTest.keyClick(page.services_card.market_row, Qt.Key.Key_Return)

    assert requested == ["market"]


def test_service_row_opens_the_matching_console_from_pointer(
    qapp: QApplication,
) -> None:
    page = HomePage()
    requested: list[str] = []
    page.console_requested.connect(requested.append)
    page.show()
    qapp.processEvents()

    QTest.mouseClick(page.services_card.game_row, Qt.MouseButton.LeftButton)

    assert requested == ["server"]


def test_kill_all_clients_is_only_actionable_when_clients_are_running(
    qapp: QApplication,
) -> None:
    page = HomePage()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.OFFLINE,
            market=ServiceState.OFFLINE,
            running_clients=0,
        )
    )
    assert page.btn_kill_all.text() == "Kill All Clients"
    assert page.btn_kill_all.isEnabled() is False
    assert page.btn_kill_all.property("class") == "dangerOutline"

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.OFFLINE,
            market=ServiceState.OFFLINE,
            running_clients=2,
        )
    )
    assert page.btn_kill_all.isEnabled() is True


@pytest.mark.parametrize(
    ("game", "market", "game_owned", "market_owned", "label", "enabled"),
    [
        (ServiceState.OFFLINE, ServiceState.OFFLINE, False, False, "Start Stack", True),
        (ServiceState.STARTING, ServiceState.OFFLINE, True, False, "Starting…", False),
        (ServiceState.ONLINE, ServiceState.OFFLINE, True, False, "Stop Stack", True),
        (ServiceState.FAILED, ServiceState.OFFLINE, False, False, "Retry Stack", True),
        (ServiceState.OFFLINE, ServiceState.STOPPING, False, True, "Stopping…", False),
    ],
)
def test_stack_action_reflects_service_lifecycle(
    qapp: QApplication,
    game: ServiceState,
    market: ServiceState,
    game_owned: bool,
    market_owned: bool,
    label: str,
    enabled: bool,
) -> None:
    page = HomePage()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=game,
            market=market,
            running_clients=0,
            game_owned=game_owned,
            market_owned=market_owned,
        )
    )

    assert page.btn_start_servers.text() == label
    assert page.btn_start_servers.isEnabled() is enabled
    assert page.btn_start_servers.property("class") == "secondary"


def test_stack_action_emits_start_or_stop_for_the_rendered_operation(
    qapp: QApplication,
) -> None:
    page = HomePage()
    emitted: list[str] = []
    page.start_servers_clicked.connect(lambda: emitted.append("start"))
    page.stop_servers_clicked.connect(lambda: emitted.append("stop"))

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.OFFLINE,
            market=ServiceState.OFFLINE,
            running_clients=0,
        )
    )
    page.btn_start_servers.click()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.ONLINE,
            market=ServiceState.ONLINE,
            running_clients=0,
            game_owned=True,
            market_owned=True,
        )
    )
    page.btn_start_servers.click()

    assert emitted == ["start", "stop"]


def test_stack_action_does_not_promise_shutdown_of_external_services(
    qapp: QApplication,
) -> None:
    page = HomePage()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.ONLINE,
            market=ServiceState.ONLINE,
            running_clients=0,
            game_owned=False,
            market_owned=False,
        )
    )

    assert page.btn_start_servers.text() == "Managed Externally"
    assert page.btn_start_servers.isEnabled() is False
    assert "outside this launcher" in page.btn_start_servers.toolTip().casefold()


def test_stack_action_only_offers_to_stop_launcher_owned_services(
    qapp: QApplication,
) -> None:
    page = HomePage()

    page.apply_runtime_snapshot(
        RuntimeSnapshot(
            game=ServiceState.ONLINE,
            market=ServiceState.ONLINE,
            running_clients=0,
            game_owned=False,
            market_owned=True,
        )
    )

    assert page.btn_start_servers.text() == "Stop Managed Services"
    assert page.btn_start_servers.isEnabled() is True
    assert "original console" in page.btn_start_servers.toolTip().casefold()


def test_launch_all_explains_when_no_account_is_eligible(
    qapp: QApplication,
) -> None:
    page = HomePage()

    page.set_launch_available(False, "No visible accounts available")

    assert page.btn_launch_all.isEnabled() is False
    assert page.btn_launch_all.toolTip() == "No visible accounts available"

    page.set_launch_available(True)

    assert page.btn_launch_all.isEnabled() is True
    assert page.btn_launch_all.toolTip() == "Launch every eligible visible account"


def test_launch_action_exposes_progress_and_cancels_remaining_queue_items(
    qapp: QApplication,
) -> None:
    page = HomePage()
    cancelled: list[str] = []
    page.cancel_launches_clicked.connect(lambda: cancelled.append("cancel"))

    page.set_launch_progress(attempted=1, total=3, succeeded=1)

    assert page.btn_launch_all.text() == "Launching 1 of 3…"
    assert page.btn_launch_all.isEnabled() is True
    assert "Cancel" in page.btn_launch_all.toolTip()

    page.btn_launch_all.click()

    assert cancelled == ["cancel"]

    page.finish_launch_progress(attempted=1, succeeded=1, cancelled=True)

    assert page.btn_launch_all.text() == "Launch All"
    assert page.btn_launch_all.isEnabled() is True


def test_services_card_shows_server_mode_without_a_file_path(
    qapp: QApplication,
) -> None:
    page = HomePage()

    page.set_server_mode("Modded")

    assert page.services_card.mode_label.text() == "MODDED"
