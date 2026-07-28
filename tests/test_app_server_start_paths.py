"""Tests for routing every application server-start path through one resolver."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.db import Account, Character
from src.workers.server_worker import ServiceStartResult, ServiceStopResult


def _minimal_window_config() -> dict:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "",
            "client_path": "",
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    return cfg


@pytest.fixture
def bare_window(qapp: QApplication) -> MainWindow:
    """Create a MainWindow wrapper without constructing unrelated pages/timers."""
    class FakeHero:
        @staticmethod
        def set_rotation_interval(_seconds: int) -> None:
            return None

        @staticmethod
        def set_animations_enabled(_enabled: bool) -> None:
            return None

    class FakeHomePage:
        def __init__(self) -> None:
            self.hero = FakeHero()

        @staticmethod
        def set_server_mode(_mode: str) -> None:
            return None

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = _minimal_window_config()
    window._server_proc = None
    window._market_proc = None
    window._service_thread = None
    window._service_monitor = None
    window._service_monitor_start_pending = False
    window._active_update_checkers = []
    window._update_install_worker = None
    window._close_in_progress = False
    window._home_page = FakeHomePage()
    yield window
    window.deleteLater()


def test_main_window_constructs_without_legacy_settings_prompt(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)

    window = MainWindow()
    try:
        assert window._settings_page is not None
    finally:
        window.deleteLater()


def test_main_window_connects_mods_apply_to_central_restart(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    restarted: list[MainWindow] = []

    def record_restart(window: MainWindow) -> None:
        restarted.append(window)

    monkeypatch.setattr(MainWindow, "_restart_server", record_restart)

    window = MainWindow()
    try:
        window._mods_page.apply_restart_clicked.emit()
        assert restarted == [window]
    finally:
        window.deleteLater()


def test_manual_start_delegates_resolved_mode_to_background_sequence(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "StartServer.bat"
    bare_window._cfg["evejs_root"] = str(tmp_path)
    events: list[object] = []

    class FakeProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("vanilla", script)
    )
    bare_window._update_status_bar = lambda: events.append("status")
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda root, *, mode: events.append(("start", root, mode)) or FakeProcess(),
    )
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: events.append(("sequence", kwargs)) or True,
        raising=False,
    )

    bare_window._start_server()

    assert events == [
        "resolve",
        (
            "sequence",
            {
                "start_market": False,
                "start_game": True,
                "mode": "vanilla",
                "on_ready": None,
                "error_title": "Game Server Error",
            },
        ),
    ]


def test_lifecycle_remains_active_until_gui_thread_teardown(
    bare_window: MainWindow,
) -> None:
    class FinishedThread:
        @staticmethod
        def isRunning() -> bool:
            return False

    bare_window._lifecycle_thread = FinishedThread()

    assert bare_window._lifecycle_active() is True


def test_ready_callback_waits_for_service_result_and_thread_teardown(
    bare_window: MainWindow,
) -> None:
    class FinishedThread:
        deleted = False

        @classmethod
        def deleteLater(cls) -> None:
            cls.deleted = True

    callbacks: list[str] = []
    bare_window._lifecycle_thread = FinishedThread()
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    bare_window._lifecycle_start_scope = (True, True)
    bare_window._lifecycle_ready_callback = lambda: callbacks.append("ready")
    bare_window._service_reachability = (False, False)
    bare_window._publish_cached_runtime = lambda: None

    bare_window._on_service_start_completed(
        ServiceStartResult(market_ready=True, game_ready=True)
    )

    assert callbacks == []
    assert bare_window._lifecycle_thread is not None

    bare_window._on_lifecycle_thread_finished()

    assert callbacks == ["ready"]
    assert FinishedThread.deleted is True
    assert bare_window._lifecycle_thread is None


def test_ready_callback_survives_thread_finish_arriving_before_result(
    bare_window: MainWindow,
) -> None:
    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            return None

    callbacks: list[str] = []
    bare_window._lifecycle_thread = FinishedThread()
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    bare_window._lifecycle_start_scope = (True, False)
    bare_window._lifecycle_ready_callback = lambda: callbacks.append("ready")
    bare_window._service_reachability = (False, False)
    bare_window._publish_cached_runtime = lambda: None

    bare_window._on_lifecycle_thread_finished()
    bare_window._on_service_start_completed(ServiceStartResult(game_ready=True))

    assert callbacks == ["ready"]
    assert bare_window._lifecycle_thread is None


def test_manual_market_start_delegates_to_background_sequence(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr("src.app.start_market_server", lambda _root: FakeProcess())
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: calls.append(kwargs) or True,
        raising=False,
    )

    bare_window._start_market()

    assert calls == [
        {
            "start_market": True,
            "start_game": False,
            "mode": None,
            "on_ready": None,
            "error_title": "Market Server Error",
        }
    ]


def test_manual_start_does_not_duplicate_alive_starting_process(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AliveProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = AliveProcess()
    resolver_calls: list[str] = []
    launch_calls: list[str] = []
    bare_window._resolve_server_start = lambda: (
        resolver_calls.append("resolve") or ("modded", None)
    )
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda *_args, **_kwargs: launch_calls.append("start"),
    )
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._start_server()

    assert resolver_calls == []
    assert launch_calls == []
    assert bare_window._server_proc.pid == 4321


def test_start_all_delegates_market_then_game_to_background_sequence(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    events: list[object] = []
    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("modded", tmp_path / "StartServerWithMods.bat")
    )
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr("src.app.start_market_server", lambda _root: object())
    monkeypatch.setattr("src.app.start_game_server", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)
    monkeypatch.setattr("src.app.QMessageBox.information", lambda *_args: None)
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: events.append(("sequence", kwargs)) or True,
        raising=False,
    )

    bare_window._start_all_servers()

    assert events == [
        "resolve",
        (
            "sequence",
            {
                "start_market": True,
                "start_game": True,
                "mode": "modded",
                "on_ready": None,
                "error_title": "Service Startup Failed",
            },
        ),
    ]


def test_owned_game_stop_delegates_wait_and_kill_to_background_sequence(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 1234

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.return_code = 0

    calls: list[dict[str, object]] = []
    bare_window._server_proc = LiveProcess()
    bare_window._service_reachability = (True, False)
    bare_window._update_status_bar = lambda: None
    bare_window._run_stop_sequence = lambda **kwargs: calls.append(kwargs) or True

    bare_window._stop_server()

    assert calls == [
        {
            "stop_game": True,
            "stop_market": False,
            "on_complete": None,
        }
    ]


def test_owned_market_stop_delegates_wait_and_kill_to_background_sequence(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 4321

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.return_code = 0

    calls: list[dict[str, object]] = []
    bare_window._market_proc = LiveProcess()
    bare_window._service_reachability = (False, True)
    bare_window._update_status_bar = lambda: None
    bare_window._run_stop_sequence = lambda **kwargs: calls.append(kwargs) or True

    bare_window._stop_market()

    assert calls == [
        {
            "stop_game": False,
            "stop_market": True,
            "on_complete": None,
        }
    ]


def test_stop_all_delegates_one_ordered_background_sequence(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 4321

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.return_code = 0

    calls: list[dict[str, object]] = []
    bare_window._server_proc = LiveProcess()
    bare_window._market_proc = LiveProcess()
    bare_window._run_stop_sequence = lambda **kwargs: calls.append(kwargs) or True

    bare_window._stop_all_servers()

    assert calls == [
        {
            "stop_game": True,
            "stop_market": True,
            "on_complete": None,
        }
    ]


def test_start_all_cancellation_occurs_before_market_side_effects(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._resolve_server_start = lambda: None
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    starts: list[str] = []

    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_market_server",
        lambda _root: starts.append("market") or object(),
    )
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda *_args, **_kwargs: starts.append("game") or object(),
    )

    bare_window._start_all_servers()

    assert starts == []
    assert bare_window._market_proc is None
    assert bare_window._server_proc is None


def test_auto_start_cancellation_occurs_before_market_side_effects(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "auto_start_market": True,
            "auto_start_server": True,
        }
    )
    bare_window._resolve_server_start = lambda: None
    bare_window._is_market_running = lambda: False
    starts: list[str] = []

    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_market_server",
        lambda _root: starts.append("market") or object(),
    )
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda *_args, **_kwargs: starts.append("game") or object(),
    )

    ready: list[str] = []

    assert bare_window._ensure_server_if_needed(lambda: ready.append("ready")) is False
    assert starts == []
    assert ready == []


def test_auto_start_defers_the_ready_callback_until_the_sequence_finishes(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "auto_start_market": True,
            "auto_start_server": True,
        }
    )
    events: list[str] = []
    sequence_calls: list[dict[str, object]] = []
    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("modded", tmp_path / "StartServerWithMods.bat")
    )
    bare_window._is_market_running = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: sequence_calls.append(kwargs) or True,
        raising=False,
    )

    assert bare_window._ensure_server_if_needed(lambda: events.append("ready")) is True

    assert events == ["resolve"]
    assert len(sequence_calls) == 1
    assert sequence_calls[0]["start_market"] is True
    assert sequence_calls[0]["start_game"] is True
    assert sequence_calls[0]["mode"] == "modded"
    assert sequence_calls[0]["error_title"] == "Auto-start Services Failed"
    callback = sequence_calls[0]["on_ready"]
    assert callable(callback)
    callback()
    assert events == ["resolve", "ready"]


def test_single_client_launch_aborts_when_auto_start_is_cancelled(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IdleTracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "client_path": str(tmp_path / "tq"),
        }
    )
    bare_window._tracker = IdleTracker()
    bare_window._ensure_server_if_needed = lambda _on_ready: False
    downstream_calls: list[str] = []
    monkeypatch.setattr(
        "src.app.profile_exists",
        lambda _username: downstream_calls.append("profile") or False,
    )
    monkeypatch.setattr(
        "src.app.create_profile",
        lambda *_args: downstream_calls.append("create-profile"),
    )
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._on_character_launch("account", "character")

    assert downstream_calls == []


def test_single_client_launch_waits_for_the_auto_start_continuation(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    class IdleTracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

    callbacks: list[object] = []
    launches: list[tuple[str, str, bool]] = []
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "client_path": str(tmp_path / "tq"),
        }
    )
    bare_window._tracker = IdleTracker()
    bare_window._ensure_server_if_needed = lambda on_ready: callbacks.append(on_ready) or True
    bare_window._launch_account = lambda username, character, *, show_errors: (
        launches.append((username, character, show_errors)) or True
    )
    bare_window._refresh_characters = lambda: None
    bare_window._update_status_bar = lambda: None

    bare_window._on_character_launch("account", "character")

    assert launches == []
    assert len(callbacks) == 1
    callback = callbacks[0]
    assert callable(callback)
    callback()
    assert launches == [("account", "character", True)]


def test_launch_all_aborts_when_auto_start_is_cancelled(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "client_path": str(tmp_path / "tq"),
        }
    )
    bare_window._accounts = [
        Account(
            username="account-a",
            account_id=1,
            role="player",
            banned=False,
            characters=[Character(char_id=1, name="Character A")],
        )
    ]

    class FakeTracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

    callbacks: list[object] = []
    bare_window._tracker = FakeTracker()
    bare_window._effective_hidden_characters = lambda: set()
    bare_window._ensure_server_if_needed = (
        lambda on_ready: callbacks.append(on_ready) or False
    )
    bare_window._refresh_characters = lambda: None
    bare_window._update_status_bar = lambda: None
    messages: list[str] = []
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, _title, message: messages.append(message),
    )

    bare_window._launch_all()

    assert len(callbacks) == 1
    assert messages == []


def test_restart_cancellation_preserves_the_running_server(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    class AliveProcess:
        pid = 8765

        @staticmethod
        def poll() -> None:
            return None

    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = AliveProcess()
    bare_window._resolve_server_start = lambda: None
    stop_calls: list[str] = []
    start_calls: list[str] = []
    bare_window._stop_server = lambda: stop_calls.append("stop")
    bare_window._start_resolved_server = (
        lambda *_args, **_kwargs: start_calls.append("start") or True
    )

    bare_window._restart_server()

    assert stop_calls == []
    assert start_calls == []
    assert bare_window._server_proc.pid == 8765


def test_settings_root_change_refreshes_the_mods_page(
    bare_window: MainWindow,
) -> None:
    class FakeModsPage:
        refresh_count = 0

        def refresh_mods(self) -> None:
            self.refresh_count += 1

    mods_page = FakeModsPage()
    bare_window._cfg["evejs_root"] = "old-root"
    bare_window._mods_page = mods_page
    bare_window._refresh_characters = lambda: None

    bare_window._on_settings_saved({"evejs_root": "new-root"})

    assert mods_page.refresh_count == 1


def test_settings_root_change_clears_root_dependent_caches(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeModsPage:
        @staticmethod
        def refresh_mods() -> None:
            return None

    class FakePortraitCache:
        @staticmethod
        def clear() -> None:
            cleared.append("portraits")

    cleared: list[str] = []
    bare_window._cfg["evejs_root"] = "old-root"
    bare_window._mods_page = FakeModsPage()
    bare_window._refresh_characters = lambda: None
    monkeypatch.setattr(
        app_module,
        "clear_solar_system_name_cache",
        lambda: cleared.append("locations"),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "PortraitCache",
        FakePortraitCache,
        raising=False,
    )

    bare_window._on_settings_saved({"evejs_root": "new-root"})

    assert cleared == ["locations", "portraits"]


def test_stop_server_does_not_force_kill_an_external_game_process(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._service_reachability = (True, False)
    bare_window._update_status_bar = lambda: None
    forced_ports: list[int] = []
    messages: list[str] = []
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port, **_kwargs: port == int(app_module.Ports.GAME_TCP),
    )
    monkeypatch.setattr(
        bare_window,
        "_kill_process_on_port",
        lambda port: forced_ports.append(port),
        raising=False,
    )
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, _title, message: messages.append(message),
    )

    bare_window._stop_server()

    assert forced_ports == []
    assert messages
    assert "outside this launcher" in messages[-1].casefold()


def test_close_defers_window_acceptance_until_owned_service_stop_completes(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    class IdleTracker:
        running_count = 0

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = CloseEvent()
    stop_requests: list[dict[str, object]] = []
    monitor_stops: list[str] = []
    bare_window._tracker = IdleTracker()
    bare_window._server_proc = LiveProcess()
    bare_window._market_proc = None
    bare_window._service_thread = None
    bare_window._stop_service_monitor = lambda: monitor_stops.append("monitor")
    bare_window._run_stop_sequence = (
        lambda **kwargs: stop_requests.append(kwargs) or True
    )

    bare_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert monitor_stops == []
    assert len(stop_requests) == 1
    assert stop_requests[0]["stop_game"] is True
    assert stop_requests[0]["stop_market"] is True
    assert callable(stop_requests[0]["on_complete"])


def test_failed_deferred_shutdown_keeps_the_window_open(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._lifecycle_stop_scope = (True, False)
    bare_window._lifecycle_stop_callback = lambda: None
    bare_window._service_reachability = (True, False)
    bare_window._close_after_lifecycle = True
    bare_window._publish_cached_runtime = lambda: None
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._on_service_stop_completed(
        ServiceStopResult(
            game_stopped=False,
            market_stopped=True,
            game_error="Game service did not exit.",
        )
    )

    assert bare_window._close_after_lifecycle is False


def test_service_monitor_shutdown_intent_precedes_thread_release(
    bare_window: MainWindow,
) -> None:
    """The GUI records shutdown intent before releasing the monitor thread."""
    events: list[str] = []

    class FakeMonitor:
        @staticmethod
        def request_shutdown() -> None:
            events.append("shutdown")

        @staticmethod
        def stop() -> None:
            events.append("stop")

    class FakeThread:
        @staticmethod
        def requestInterruption() -> None:
            events.append("interrupt")

        @staticmethod
        def quit() -> None:
            events.append("quit")

        @staticmethod
        def wait(_timeout_ms: int) -> bool:
            events.append("wait")
            return True

    monitor = FakeMonitor()
    bare_window._service_monitor = monitor
    bare_window._service_thread = FakeThread()
    bare_window._service_monitor_stop_requested.connect(monitor.stop)

    bare_window._stop_service_monitor()

    assert events == ["shutdown", "interrupt", "stop", "quit", "wait"]
    assert bare_window._service_monitor is None
    assert bare_window._service_thread is None


def test_service_monitor_keeps_references_when_thread_remains_alive(
    bare_window: MainWindow,
) -> None:
    """A timed-out monitor thread must not be released for widget teardown."""
    class FakeMonitor:
        @staticmethod
        def request_shutdown() -> None:
            return None

        @staticmethod
        def stop() -> None:
            return None

    class StuckThread:
        @staticmethod
        def requestInterruption() -> None:
            return None

        @staticmethod
        def quit() -> None:
            return None

        @staticmethod
        def wait(_timeout_ms: int) -> bool:
            return False

    monitor = FakeMonitor()
    thread = StuckThread()
    bare_window._service_monitor = monitor
    bare_window._service_thread = thread
    bare_window._service_monitor_stop_requested.connect(monitor.stop)

    bare_window._stop_service_monitor()

    assert bare_window._service_monitor is monitor
    assert bare_window._service_thread is thread


def test_show_event_defers_service_monitor_start_until_the_gui_event_loop(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible window must yield to the GUI loop before it creates the worker."""
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    starts: list[str] = []
    window = MainWindow()
    window._start_service_monitor = lambda: starts.append("start")

    window.show()
    try:
        assert starts == []

        qapp.processEvents()

        assert starts == ["start"]
    finally:
        window.hide()
        window.deleteLater()
        qapp.processEvents()


def test_close_before_deferred_start_cancels_service_monitor_creation(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close request must cancel a monitor callback that has not run yet."""
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    starts: list[str] = []
    window = MainWindow()
    window._start_service_monitor = lambda: starts.append("start")

    window.show()
    try:
        assert starts == []

        window.close()
        qapp.processEvents()

        assert starts == []
    finally:
        if window.isVisible():
            window.hide()
        window.deleteLater()
        qapp.processEvents()


def test_close_keeps_window_open_until_retained_monitor_thread_exits(
    bare_window: MainWindow,
) -> None:
    """A live monitor thread must not be destroyed by an accepted window close."""
    class IdleTracker:
        running_count = 0

    class FakeMonitor:
        @staticmethod
        def request_shutdown() -> None:
            return None

        @staticmethod
        def stop() -> None:
            return None

    class StuckThread:
        @staticmethod
        def requestInterruption() -> None:
            return None

        @staticmethod
        def quit() -> None:
            return None

        @staticmethod
        def wait(_timeout_ms: int) -> bool:
            return False

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    monitor = FakeMonitor()
    thread = StuckThread()
    event = CloseEvent()
    bare_window._tracker = IdleTracker()
    bare_window._service_monitor = monitor
    bare_window._service_thread = thread
    bare_window._service_monitor_stop_requested.connect(monitor.stop)

    bare_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert bare_window._service_monitor is monitor
    assert bare_window._service_thread is thread


def test_close_defers_window_acceptance_until_running_update_check_finishes(
    bare_window: MainWindow,
) -> None:
    """A live updater QThread must finish before its parent window is destroyed."""
    class IdleTracker:
        running_count = 0

    class RunningChecker:
        @staticmethod
        def isRunning() -> bool:
            return True

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = CloseEvent()
    bare_window._tracker = IdleTracker()
    bare_window._active_update_checkers = [RunningChecker()]

    bare_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert bare_window._close_in_progress is True
