"""Phase 2B2 application routing contracts for Docker log presentation."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import config
from src import app as app_module
from src.app import MainWindow


@pytest.fixture
def docker_window(qapp):
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._cfg.update({"runtime_backend": "docker_compose", "evejs_root": "C:/EveJS"})
    window._docker_log_worker = None
    window._docker_log_thread = None
    window._log_generation = 0
    window._close_in_progress = False
    yield window
    window.deleteLater()


def test_connect_only_routes_server_and_market_to_read_only_stream(docker_window: MainWindow) -> None:
    docker_window._cfg["docker_control_policy"] = "connect_only"
    seen: list[str] = []
    docker_window._console_panel = type("Panel", (), {"isVisible": lambda _self: False})()
    docker_window._start_docker_log_stream = seen.append

    docker_window._on_console_toggled("server")
    docker_window._on_console_toggled("market")

    assert seen == ["server", "market"]


def test_stale_worker_delivery_is_suppressed(docker_window: MainWindow) -> None:
    appended: list[str] = []
    docker_window._console_panel = type("Panel", (), {"append_stream_line": appended.append, "finish_stream": appended.append})()
    current, stale = object(), object()
    docker_window._docker_log_worker = current
    docker_window._docker_log_token = current
    docker_window._close_in_progress = False

    docker_window._on_docker_log_line(stale, "old")
    docker_window._on_docker_log_diagnostic(stale, "old error")
    docker_window._on_docker_log_line(current, "current")

    assert appended == ["current"]


def test_stop_invalidates_current_delivery_without_touching_monitor_generation(
    docker_window: MainWindow,
) -> None:
    class Worker:
        def request_cancel(self): pass

    token = object()
    docker_window._docker_log_worker = Worker()
    docker_window._docker_log_thread = object()
    docker_window._docker_log_token = token
    docker_window._console_panel = type("Panel", (), {"append_stream_line": lambda *_args: None})()
    docker_window._monitor_generation = 19
    docker_window._stop_docker_log_stream()
    docker_window._on_docker_log_line(token, "queued")
    assert docker_window._docker_log_token is None
    assert docker_window._monitor_generation == 19


def test_settings_identity_change_cancels_log_before_monitor_restart(docker_window: MainWindow) -> None:
    calls: list[str] = []
    docker_window._service_thread = None
    docker_window._service_monitor = None
    docker_window._service_monitor_start_pending = False
    docker_window._service_monitor_restart_pending = False
    docker_window._monitor_generation = 0
    docker_window._close_in_progress = False
    docker_window._stop_docker_log_stream = lambda: calls.append("logs") or True
    docker_window._schedule_service_monitor_start = lambda: calls.append("monitor")
    docker_window._docker_unknown_snapshot = lambda: None
    docker_window._apply_runtime_snapshot = lambda _snapshot: None
    docker_window._apply_runtime_settings = lambda: None
    docker_window._home_page = type("Home", (), {"set_server_mode": lambda *_args: None})()
    docker_window._refresh_characters = lambda: None

    docker_window._on_settings_saved({**docker_window._cfg, "docker_project_name": "other"})

    assert calls == ["logs", "monitor"]


def test_cross_service_and_rapid_replacement_keep_only_the_last_intent(
    docker_window: MainWindow,
) -> None:
    class Worker:
        cancelled = 0
        def request_cancel(self): self.cancelled += 1

    worker = Worker()
    docker_window._docker_log_worker = worker
    docker_window._docker_log_thread = object()
    docker_window._pending_docker_log_service = None

    docker_window._start_docker_log_stream("market")
    docker_window._start_docker_log_stream("server")

    assert worker.cancelled == 2
    assert docker_window._pending_docker_log_service == "server"


def test_panel_close_clears_pending_and_cancellation_is_idempotent(
    docker_window: MainWindow,
) -> None:
    class Worker:
        cancelled = 0
        def request_cancel(self): self.cancelled += 1

    worker = Worker()
    docker_window._docker_log_worker = worker
    docker_window._docker_log_thread = object()
    docker_window._pending_docker_log_service = "market"

    docker_window._on_console_panel_closed()
    docker_window._on_console_panel_closed()

    assert worker.cancelled == 2
    assert docker_window._pending_docker_log_service is None


def test_policy_only_settings_change_does_not_cancel_an_active_log(docker_window: MainWindow) -> None:
    calls: list[str] = []
    docker_window._stop_docker_log_stream = lambda: calls.append("logs") or True
    docker_window._apply_runtime_settings = lambda: None
    docker_window._home_page = type("Home", (), {"set_server_mode": lambda *_args: None})()
    docker_window._refresh_characters = lambda: None
    docker_window._publish_cached_runtime = lambda: None

    docker_window._on_settings_saved({**docker_window._cfg, "docker_control_policy": "managed"})

    assert calls == []


def test_pending_replacement_starts_only_after_the_finalizer_releases_it(
    docker_window: MainWindow,
) -> None:
    seen: list[str] = []
    docker_window._pending_docker_log_service = "market"
    docker_window._start_docker_log_stream = seen.append

    docker_window._start_pending_docker_log_stream()

    assert seen == ["market"]
    assert docker_window._pending_docker_log_service is None


def test_native_empty_root_returns_before_resolving_logs_or_changing_panel(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker_window._cfg.update({"runtime_backend": "native", "evejs_root": ""})
    calls: list[str] = []
    docker_window._console_panel = type("Panel", (), {
        "isVisible": lambda _self: False,
        "tail": lambda *_args: calls.append("tail"),
        "show": lambda *_args: calls.append("show"),
        "raise_": lambda *_args: calls.append("raise"),
        "clear_content": lambda *_args: calls.append("clear"),
        "set_title": lambda *_args: calls.append("title"),
    })()
    monkeypatch.setattr(app_module, "get_server_console_log", lambda: pytest.fail("server resolver"))
    monkeypatch.setattr(app_module, "get_market_console_log", lambda: pytest.fail("market resolver"))

    docker_window._on_console_toggled("server")
    docker_window._on_console_toggled("market")

    assert calls == []


def test_docker_routing_precedes_native_log_resolvers(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch,
) -> None:
    routed: list[str] = []
    docker_window._console_panel = type("Panel", (), {"isVisible": lambda _self: False})()
    docker_window._start_docker_log_stream = routed.append
    monkeypatch.setattr(app_module, "get_server_console_log", lambda: pytest.fail("native server resolver"))
    monkeypatch.setattr(app_module, "get_market_console_log", lambda: pytest.fail("native market resolver"))

    docker_window._on_console_toggled("server")
    docker_window._on_console_toggled("market")

    assert routed == ["server", "market"]


@pytest.mark.parametrize("name", ["server", "market"])
def test_native_existing_log_routes_tail_exact_path(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path, name: str,
) -> None:
    log_path = tmp_path / f"{name}.log"
    log_path.write_text("line\n", encoding="utf-8")
    docker_window._cfg.update({"runtime_backend": "native", "evejs_root": "C:/EveJS"})
    tailed: list[str] = []
    docker_window._console_panel = type("Panel", (), {
        "isVisible": lambda _self: False,
        "tail": tailed.append,
    })()
    monkeypatch.setattr(app_module, "get_server_console_log", lambda: log_path)
    monkeypatch.setattr(app_module, "get_market_console_log", lambda: log_path)

    docker_window._on_console_toggled(name)

    assert tailed == [str(log_path)]


def test_native_missing_log_presentations_remain_service_specific(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    docker_window._cfg.update({"runtime_backend": "native", "evejs_root": "C:/EveJS"})
    calls: list[str] = []
    docker_window._console_panel = type("Panel", (), {
        "isVisible": lambda _self: False,
        "show": lambda _self: calls.append("show"),
        "raise_": lambda _self: calls.append("raise"),
        "clear_content": lambda _self: calls.append("clear"),
        "set_title": lambda _self, title: calls.append(title),
    })()
    missing = tmp_path / "missing.log"
    monkeypatch.setattr(app_module, "get_server_console_log", lambda: missing)
    monkeypatch.setattr(app_module, "get_market_console_log", lambda: missing)

    docker_window._on_console_toggled("server")
    assert calls == ["show", "raise"]
    calls.clear()
    docker_window._on_console_toggled("market")

    assert calls == ["clear", "Market Server — not started yet", "show", "raise"]


def test_same_service_docker_toggle_stops_panel_clears_pending_and_does_not_replace(
    docker_window: MainWindow,
) -> None:
    class Worker:
        cancelled = 0
        def request_cancel(self): self.cancelled += 1

    worker = Worker()
    docker_window._docker_log_worker = worker
    docker_window._docker_log_thread = object()
    docker_window._docker_log_service = "server"
    docker_window._docker_log_token = object()
    docker_window._pending_docker_log_service = None
    stops: list[bool] = []
    docker_window._console_panel = type("Panel", (), {
        "isVisible": lambda _self: True,
        "stop": lambda _self: stops.append(True) or docker_window._on_console_panel_closed(),
    })()

    docker_window._on_console_toggled("server")

    assert stops == [True]
    assert worker.cancelled == 1
    assert docker_window._pending_docker_log_service is None


def test_first_close_cancels_active_log_without_waiting_and_ignores(
    docker_window: MainWindow,
) -> None:
    class Worker:
        cancelled = 0
        def request_cancel(self): self.cancelled += 1
    class Thread:
        def wait(self, *_args): pytest.fail("GUI close must not wait for a log thread")
    class Event:
        ignored = False
        def ignore(self): self.ignored = True

    worker, event = Worker(), Event()
    docker_window._update_install_worker = None
    docker_window._docker_log_worker = worker
    docker_window._docker_log_thread = Thread()
    docker_window._docker_log_token = object()
    docker_window._pending_docker_log_service = "market"

    docker_window.closeEvent(event)

    assert event.ignored and docker_window._close_in_progress
    assert worker.cancelled == 1
    assert docker_window._docker_log_token is None
    assert docker_window._pending_docker_log_service is None


def test_second_deferred_close_with_client_prompt_no_restores_close_state(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Tracker: running_count = 1
    class Event:
        ignored = False
        def ignore(self): self.ignored = True

    docker_window._update_install_worker = None
    docker_window._docker_log_thread = None
    docker_window._close_in_progress = True
    docker_window._close_after_lifecycle = False
    docker_window._tracker = Tracker()
    monkeypatch.setattr(app_module.QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    event = Event()

    docker_window.closeEvent(event)

    assert event.ignored and docker_window._close_in_progress is False


def test_stale_real_qthread_finish_cannot_consume_current_log_session(
    docker_window: MainWindow, qapp,
) -> None:
    stale, current = QThread(), QThread()
    token = object()
    docker_window._docker_log_thread = current
    docker_window._docker_log_worker = object()
    docker_window._docker_log_token = token
    docker_window._docker_log_service = "server"
    docker_window._pending_docker_log_service = "market"
    exited: list[bool] = []
    stale.finished.connect(lambda: exited.append(not stale.isRunning()))
    stale.finished.connect(docker_window._on_docker_log_thread_finished)
    finished = QSignalSpy(stale.finished)

    stale.start()
    stale.quit()
    assert finished.wait(2_000)
    qapp.processEvents()

    assert exited == [True]
    assert docker_window._docker_log_thread is current
    assert docker_window._docker_log_worker is not None
    assert docker_window._docker_log_token is token
    assert docker_window._docker_log_service == "server"
    assert docker_window._pending_docker_log_service == "market"


def test_current_real_qthread_finish_releases_exact_session_and_schedules_pending(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch, qapp,
) -> None:
    thread, token = QThread(), object()
    docker_window._docker_log_thread = thread
    docker_window._docker_log_worker = object()
    docker_window._docker_log_token = token
    docker_window._docker_log_service = "server"
    docker_window._pending_docker_log_service = "market"
    scheduled: list[object] = []
    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda _delay, callback: scheduled.append(callback))
    exited: list[bool] = []
    thread.finished.connect(lambda: exited.append(not thread.isRunning()))
    thread.finished.connect(docker_window._on_docker_log_thread_finished)
    finished = QSignalSpy(thread.finished)

    thread.start()
    thread.quit()
    assert finished.wait(2_000)
    qapp.processEvents()

    assert exited == [True]
    assert docker_window._docker_log_worker is None
    assert docker_window._docker_log_thread is None
    assert docker_window._docker_log_token is None
    assert docker_window._docker_log_service is None
    assert docker_window._pending_docker_log_service == "market"
    assert scheduled == [docker_window._start_pending_docker_log_stream]
