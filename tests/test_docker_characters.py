"""Phase 3 Docker Characters availability and read-only launch controls."""
from __future__ import annotations

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow

from src.app import MainWindow
from src.core import process_tracker as process_tracker_module
from src.core.db import Account, Character
from src.core.process_tracker import ProcessTracker
from src.core.runtime.data import docker_settings_identity
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.pages.characters_page import CharactersPage


class _Button:
    def __init__(self) -> None:
        self.enabled = True
        self.tooltip = ""
        self.text = ""

    def setEnabled(self, value: bool) -> None:
        self.enabled = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def setText(self, value: str) -> None:
        self.text = value


class _Nav:
    btn_server = _Button()
    btn_market = _Button()
    btn_characters = _Button()
    btn_mods = _Button()
    btn_tools = _Button()
    btn_kill_all = _Button()

    def set_badge_count(self, *_args) -> None:
        pass


class _Status:
    def set_server_state(self, *_args, **_kwargs) -> None:
        pass

    def set_market_state(self, *_args, **_kwargs) -> None:
        pass

    def set_client_count(self, *_args) -> None:
        pass


class _Home:
    btn_start_servers = _Button()
    btn_stop_servers = _Button()

    def apply_runtime_snapshot(self, *_args) -> None:
        pass


class _Characters:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []

    def set_launch_available(self, enabled: bool, reason: str = "") -> None:
        self.calls.append((enabled, reason))


class _AliveProcess:
    pid = 4242

    @staticmethod
    def poll() -> int | None:
        return None


class _MutableProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.alive = True

    def poll(self) -> int | None:
        return None if self.alive else 0


def _endpoints() -> RuntimeEndpoints:
    def endpoint(target: int, port: int) -> Endpoint:
        return Endpoint("server", "127.0.0.1", port, target, "tcp")

    return RuntimeEndpoints(
        game=endpoint(26000, 32600),
        image=endpoint(26001, 32601),
        proxy=endpoint(26002, 32602),
        assets=endpoint(26003, 32603),
        xmpp=endpoint(5222, 35222),
        market=Endpoint("market", "127.0.0.1", 44110, 40110, "tcp"),
    )


def _settings_identity(window: MainWindow) -> str:
    return docker_settings_identity(
        str(window._cfg.get("evejs_root", "")),
        str(window._cfg.get("docker_compose_file", "")),
        str(window._cfg.get("docker_project_name", "")),
    )


def _account() -> Account:
    return Account(
        username="fixture-account",
        account_id=501,
        role="0",
        banned=False,
        characters=[Character(char_id=9001, name="Fixture Character")],
    )


def test_characters_page_view_only_mode_disables_launch_but_keeps_details(
    qapp: QApplication,
) -> None:
    page = CharactersPage()
    launches: list[tuple[str, str]] = []
    selections: list[tuple[str, str, int]] = []
    page.launch_character.connect(lambda *args: launches.append(args))
    page.character_selected.connect(lambda *args: selections.append(args))

    try:
        page.set_launch_available(
            False,
            "Client launch is unavailable for this Docker target.",
        )
        page.refresh([_account()], [], ProcessTracker())
        card = page._cards[("fixture-account", 9001)]

        assert card._launch_btn.isEnabled() is False
        assert card._launch_btn.text() == "VIEW ONLY"
        assert "Docker" in card._launch_btn.toolTip()
        card._launch_btn.click()
        assert launches == []

        page._on_card_selected("fixture-account", "Fixture Character", 9001)
        assert selections == [("fixture-account", "Fixture Character", 9001)]
        assert page.detail_panel._launch_btn.isEnabled() is False
        assert page.detail_panel._launch_btn.text() == "VIEW ONLY"
        assert page.detail_panel._hide_btn.isEnabled() is True

        page.set_launch_available(True)
        assert card._launch_btn.isEnabled() is True
        assert card._launch_btn.text() == "LAUNCH"
        assert page.detail_panel._launch_btn.isEnabled() is True
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_characters_page_shows_launching_immediately_and_preserves_it_on_refresh(
    qapp: QApplication,
) -> None:
    page = CharactersPage()
    tracker = ProcessTracker()
    try:
        page.refresh([_account()], [], tracker)
        page._on_card_selected(
            "fixture-account",
            "Fixture Character",
            9001,
        )
        page.set_account_launching(
            "fixture-account",
            "Fixture Character",
            True,
        )

        card = page._cards[("fixture-account", 9001)]
        assert card._launch_btn.text() == "LAUNCHING..."
        assert card._launch_btn.isEnabled() is False
        assert page.detail_panel._launch_btn.text() == "LAUNCHING..."
        assert page.detail_panel._launch_btn.isEnabled() is False

        page.refresh([_account()], [], tracker)
        assert card._launch_btn.text() == "LAUNCHING..."
        assert card._launch_btn.isEnabled() is False

        page.set_account_launching(
            "fixture-account",
            "Fixture Character",
            False,
        )
        assert card._launch_btn.text() == "LAUNCH"
        assert card._launch_btn.isEnabled() is True
        assert page.detail_panel._launch_btn.text() == "LAUNCH"
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_process_tracker_launch_grace_does_not_use_a_global_eve_window(
    monkeypatch,
) -> None:
    tracker = ProcessTracker()
    tracker.add("fixture-account", "Fixture Character", _AliveProcess())
    monkeypatch.setattr(
        process_tracker_module,
        "_eve_window_exists",
        lambda: False,
        raising=False,
    )

    assert tracker.is_account_launching("fixture-account", 1.0) is True


def test_process_tracker_status_reads_do_not_consume_exit_event() -> None:
    process = _MutableProcess(4242)
    tracker = ProcessTracker(window_probe=lambda _pid: False)
    tracker.add("fixture-account", "Fixture Character", process)

    process.alive = False

    assert tracker.running_count == 0
    assert tracker.get_running_character("fixture-account") is None
    assert tracker.prune_dead() == 1


def test_process_tracker_keeps_live_pids_when_a_seen_window_disappears() -> None:
    visible = {4242: False, 4343: True}
    tracker = ProcessTracker(
        window_probe=lambda pid: visible.get(pid, False),
        window_close_grace_seconds=0,
    )
    first = _MutableProcess(4242)
    second = _MutableProcess(4343)
    tracker.add("first-account", "First Character", first)
    tracker.add("second-account", "Second Character", second)

    assert tracker.prune_dead() == 0
    visible[4242] = True
    assert tracker.prune_dead() == 0

    visible[4242] = False

    assert tracker.prune_dead() == 0
    assert tracker.get_running_character("first-account") == "First Character"
    assert tracker.get_running_character("second-account") == "Second Character"


def test_characters_page_refreshes_when_process_launch_grace_expires(
    qapp: QApplication,
) -> None:
    page = CharactersPage()
    page._launch_grace_seconds = 0.05
    tracker = ProcessTracker()
    tracker.add("fixture-account", "Fixture Character", _AliveProcess())
    try:
        page.refresh([_account()], [], tracker)
        card = page._cards[("fixture-account", 9001)]
        assert card._launch_btn.text() == "LAUNCHING..."

        QTest.qWait(120)

        assert card._launch_btn.text() == "RUNNING"
        assert card._launch_btn.isEnabled() is False
        assert page._launch_status_timer.isActive() is False
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_prune_repairs_card_and_detail_after_an_earlier_status_read(
    qapp: QApplication,
) -> None:
    page = CharactersPage()
    page._launch_grace_seconds = 0
    process = _MutableProcess(4242)
    tracker = ProcessTracker(window_probe=lambda _pid: False)
    tracker.add("fixture-account", "Fixture Character", process)
    try:
        page.refresh([_account()], [], tracker)
        page._on_card_selected(
            "fixture-account",
            "Fixture Character",
            9001,
        )
        card = page._cards[("fixture-account", 9001)]
        assert card._launch_btn.text() == "RUNNING"
        assert not card._launch_btn.isEnabled()
        assert page.detail_panel._launch_btn.text() == "RUNNING"
        assert not page.detail_panel._launch_btn.isEnabled()

        process.alive = False
        # This used to delete the tracker entry before the prune timer could
        # observe it, permanently stranding the existing card as RUNNING.
        assert tracker.running_count == 0

        window = MainWindow.__new__(MainWindow)
        window._tracker = tracker
        window._characters_page = page
        events: list[str] = []

        def refresh_views() -> None:
            events.append("views")
            page.refresh_process_states()

        window._refresh_character_views = refresh_views
        window._refresh_characters = lambda: events.append("reload")
        window._update_status_bar = lambda: events.append("status")

        window._prune_and_update()

        assert events == ["views", "reload", "status"]
        assert card._launch_btn.text() == "LAUNCH"
        assert card._launch_btn.isEnabled()
        assert page.detail_panel._launch_btn.text() == "LAUNCH"
        assert page.detail_panel._launch_btn.isEnabled()
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_runtime_snapshot_keeps_docker_characters_open_but_marks_launch_view_only(
    qapp: QApplication,
) -> None:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._nav = _Nav()
    window._status_bar = _Status()
    window._home_page = _Home()
    window._characters_page = _Characters()
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "connect_only",
        "evejs_root": "C:/Fixture",
        "client_path": "C:/FixtureClient/tq",
    }
    window._monitor_generation = 7

    try:
        window._apply_runtime_snapshot(
            RuntimeSnapshot(
                ServiceState.ONLINE,
                ServiceState.ONLINE,
                0,
                backend=RuntimeBackend.DOCKER_COMPOSE,
                docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
                target_identity="docker:fixture-target",
                settings_identity=_settings_identity(window),
                monitor_generation=7,
            )
        )

        assert window._nav.btn_characters.enabled is True
        assert window._nav.btn_mods.enabled is True
        assert window._nav.btn_tools.enabled is True
        assert window._characters_page.calls[-1][0] is False
        assert "Docker" in window._characters_page.calls[-1][1]

        window._apply_runtime_snapshot(
            RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.OFFLINE, 0)
        )
        assert window._characters_page.calls[-1] == (True, "")
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_runtime_snapshot_enables_docker_launch_for_ready_complete_context(
    qapp: QApplication,
) -> None:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._nav = _Nav()
    window._status_bar = _Status()
    window._home_page = _Home()
    window._characters_page = _Characters()
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "connect_only",
        "evejs_root": "C:/Fixture",
        "client_path": "C:/FixtureClient/tq",
    }
    window._monitor_generation = 7

    try:
        window._apply_runtime_snapshot(
            RuntimeSnapshot(
                ServiceState.ONLINE,
                ServiceState.ONLINE,
                0,
                backend=RuntimeBackend.DOCKER_COMPOSE,
                docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
                endpoints=_endpoints(),
                target_identity="docker:fixture-target",
                settings_identity=_settings_identity(window),
                monitor_generation=7,
            )
        )

        assert window._characters_page.calls[-1] == (True, "")
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        qapp.processEvents()
