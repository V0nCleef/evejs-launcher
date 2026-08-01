"""Regression tests for non-blocking single-client launch preparation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading
import time

import pytest
from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.launcher import ClientLaunchContext
from src.workers.client_launch_worker import (
    ClientLaunchRequest,
    ClientLaunchWorker,
)


class _FakeProcess:
    pid = 4242

    @staticmethod
    def poll() -> int | None:
        return None


def _request(tmp_path: Path) -> ClientLaunchRequest:
    return ClientLaunchRequest(
        username="fixture-account",
        character_name="Fixture Character",
        evejs_root=str(tmp_path / "evejs"),
        client_path=str(tmp_path / "client" / "tq"),
        profiles_root=tmp_path / "profiles",
        launch_context=ClientLaunchContext.native(),
    )


def _wait_for_launch_teardown(
    qapp: QApplication,
    window: MainWindow,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if window._client_launch_thread is None:
            qapp.processEvents()
            return
        QTest.qWait(5)
    assert window._client_launch_thread is None


def test_worker_keeps_qt_event_loop_responsive_during_blocking_launch(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    release = threading.Event()
    worker_thread_ids: list[int] = []
    timer_fired: list[bool] = []

    def blocking_operation(_request: ClientLaunchRequest) -> _FakeProcess:
        worker_thread_ids.append(threading.get_ident())
        assert release.wait(1.0)
        return _FakeProcess()

    thread = QThread()
    worker = ClientLaunchWorker(_request(tmp_path), blocking_operation)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.cleanup.connect(
        worker.deleteLater,
        Qt.ConnectionType.DirectConnection,
    )
    worker.destroyed.connect(thread.quit)
    thread_finished = QSignalSpy(thread.finished)
    completed = QSignalSpy(worker.completed)
    gui_thread_id = threading.get_ident()

    thread.start()
    QTimer.singleShot(10, lambda: timer_fired.append(True))
    QTest.qWait(40)

    assert timer_fired == [True]
    assert len(completed) == 0
    assert worker_thread_ids and worker_thread_ids[0] != gui_thread_id

    release.set()
    assert thread_finished.wait(1_000)
    assert len(completed) == 1
    thread.deleteLater()


class _Tracker:
    def __init__(self) -> None:
        self.running: dict[str, tuple[str, _FakeProcess]] = {}

    def is_account_running(self, username: str) -> bool:
        return username in self.running

    def add(
        self,
        username: str,
        character_name: str,
        process: _FakeProcess,
    ) -> None:
        self.running[username] = (character_name, process)


class _CharactersPage:
    def __init__(self) -> None:
        self.states: list[tuple[str, str, bool]] = []

    def set_account_launching(
        self,
        username: str,
        character_name: str,
        pending: bool,
    ) -> None:
        self.states.append((username, character_name, pending))


def _bare_launch_window(qapp: QApplication, tmp_path: Path) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "runtime_backend": "native",
            "evejs_root": str(tmp_path / "evejs"),
            "client_path": str(tmp_path / "client" / "tq"),
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    window._cfg = cfg
    window._tracker = _Tracker()
    window._characters_page = _CharactersPage()
    window._pending_client_launches = set()
    window._client_launch_thread = None
    window._client_launch_worker = None
    window._client_launch_request = None
    window._client_launch_show_errors = False
    window._client_launch_from_queue = False
    window._client_launch_result_received = False
    window._client_launch_thread_finished = False
    window._client_launch_succeeded = False
    window._launch_queue = None
    window._close_in_progress = False
    window._refresh_character_views = lambda: None
    window._update_status_bar = lambda: None
    return window


def test_main_window_launch_is_immediately_pending_rejects_duplicates_and_stays_responsive(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _bare_launch_window(qapp, tmp_path)
    release = threading.Event()
    calls: list[ClientLaunchRequest] = []
    timer_fired: list[bool] = []

    def blocking_operation(request: ClientLaunchRequest) -> _FakeProcess:
        calls.append(request)
        assert release.wait(1.0)
        return _FakeProcess()

    monkeypatch.setattr(app_module, "_perform_client_launch", blocking_operation)
    monkeypatch.setattr(app_module, "_restore_eve_window", lambda *_args: None)

    try:
        assert window._start_client_launch(
            "fixture-account",
            "Fixture Character",
            show_errors=False,
        )
        assert window._characters_page.states[-1] == (
            "fixture-account",
            "Fixture Character",
            True,
        )
        assert "fixture-account" in window._pending_client_launches
        assert not window._start_client_launch(
            "fixture-account",
            "Fixture Character",
            show_errors=False,
        )

        QTimer.singleShot(10, lambda: timer_fired.append(True))
        QTest.qWait(40)
        assert timer_fired == [True]
        assert len(calls) == 1

        release.set()
        _wait_for_launch_teardown(qapp, window)

        assert "fixture-account" not in window._pending_client_launches
        assert window._characters_page.states[-1] == (
            "fixture-account",
            "Fixture Character",
            False,
        )
        assert window._tracker.is_account_running("fixture-account")
    finally:
        window.deleteLater()


def test_failed_async_launch_clears_pending_and_allows_retry(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _bare_launch_window(qapp, tmp_path)
    attempts = 0

    def fail_then_succeed(_request: ClientLaunchRequest) -> _FakeProcess:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("fixture spawn failure")
        return _FakeProcess()

    monkeypatch.setattr(app_module, "_perform_client_launch", fail_then_succeed)
    monkeypatch.setattr(app_module, "_restore_eve_window", lambda *_args: None)

    try:
        assert window._start_client_launch(
            "fixture-account",
            "Fixture Character",
        )
        _wait_for_launch_teardown(qapp, window)
        assert "fixture-account" not in window._pending_client_launches
        assert not window._tracker.is_account_running("fixture-account")

        assert window._start_client_launch(
            "fixture-account",
            "Fixture Character",
        )
        _wait_for_launch_teardown(qapp, window)
        assert attempts == 2
        assert window._tracker.is_account_running("fixture-account")
    finally:
        window.deleteLater()
