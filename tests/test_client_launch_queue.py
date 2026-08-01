"""Behavior tests for the serial, non-blocking client-launch queue."""
from __future__ import annotations

from copy import deepcopy
import time

import pytest
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.client_launch_queue import AsyncClientLaunchQueue, ClientLaunchQueue
from src.core.db import Account, Character
from src.core.launcher import ClientLaunchContext


def _wait_for_data(qapp: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not window._data_load_active():
            qapp.processEvents()
            return
        QTest.qWait(5)
    assert not window._data_load_active()


def test_queue_launches_items_in_order_with_qt_staggers(
    qapp: QApplication,
) -> None:
    launched: list[str] = []
    progress: list[tuple[int, int, int]] = []
    finished: list[tuple[int, int, bool]] = []
    queue = ClientLaunchQueue(
        ["first", "second", "third"],
        lambda item: launched.append(item) is None,
        stagger_ms=15,
    )
    queue.progress.connect(lambda attempted, total, succeeded: progress.append(
        (attempted, total, succeeded)
    ))
    queue.finished.connect(lambda attempted, succeeded, cancelled: finished.append(
        (attempted, succeeded, cancelled)
    ))
    completion = QSignalSpy(queue.finished)

    queue.start()

    assert launched == ["first"]
    assert progress == [(1, 3, 1)]
    assert queue.is_active is True

    assert completion.wait(500)

    assert launched == ["first", "second", "third"]
    assert progress == [(1, 3, 1), (2, 3, 2), (3, 3, 3)]
    assert finished == [(3, 3, False)]
    assert queue.is_active is False


def test_queue_cancellation_leaves_already_launched_items_alone(
    qapp: QApplication,
) -> None:
    launched: list[str] = []
    finished: list[tuple[int, int, bool]] = []
    queue = ClientLaunchQueue(
        ["first", "second", "third"],
        lambda item: launched.append(item) is None,
        stagger_ms=15,
    )
    queue.finished.connect(lambda attempted, succeeded, cancelled: finished.append(
        (attempted, succeeded, cancelled)
    ))

    queue.start()
    queue.cancel()
    QTest.qWait(50)

    assert launched == ["first"]
    assert finished == [(1, 1, True)]
    assert queue.is_active is False


def test_queue_continues_after_one_launch_callback_reports_failure(
    qapp: QApplication,
) -> None:
    attempted: list[str] = []
    finished: list[tuple[int, int, bool]] = []

    def launch(item: str) -> bool:
        attempted.append(item)
        return item != "second"

    queue = ClientLaunchQueue(
        ["first", "second", "third"],
        launch,
        stagger_ms=0,
    )
    queue.finished.connect(lambda count, succeeded, cancelled: finished.append(
        (count, succeeded, cancelled)
    ))

    queue.start()
    QTest.qWait(50)

    assert attempted == ["first", "second", "third"]
    assert finished == [(3, 2, False)]


def test_async_queue_waits_for_completion_before_starting_next_item(
    qapp: QApplication,
) -> None:
    started: list[str] = []
    progress: list[tuple[int, int, int]] = []
    queue = AsyncClientLaunchQueue(
        ["first", "second"],
        lambda item: started.append(item) is None,
        stagger_ms=0,
    )
    queue.progress.connect(
        lambda attempted, total, succeeded: progress.append(
            (attempted, total, succeeded)
        )
    )

    queue.start()
    QTest.qWait(20)
    assert started == ["first"]
    assert progress == []

    queue.item_finished(True)
    QTest.qWait(20)
    assert started == ["first", "second"]
    assert progress == [(1, 2, 1)]

    queue.item_finished(False)
    QTest.qWait(20)
    assert progress == [(1, 2, 1), (2, 2, 1)]
    assert queue.is_active is False


def test_async_queue_cancellation_waits_for_in_flight_item_and_skips_future_items(
    qapp: QApplication,
) -> None:
    started: list[str] = []
    finished: list[tuple[int, int, bool]] = []
    queue = AsyncClientLaunchQueue(
        ["first", "second"],
        lambda item: started.append(item) is None,
        stagger_ms=0,
    )
    queue.finished.connect(
        lambda attempted, succeeded, cancelled: finished.append(
            (attempted, succeeded, cancelled)
        )
    )

    queue.start()
    queue.cancel()
    QTest.qWait(20)

    assert started == ["first"]
    assert finished == []
    assert queue.is_active is True

    queue.item_finished(True)
    QTest.qWait(20)

    assert started == ["first"]
    assert finished == [(1, 1, True)]
    assert queue.is_active is False


def test_launch_all_uses_shared_queue_and_cancellation_preserves_started_clients(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [
        Account(
            username="account-a",
            account_id=1,
            role="0",
            banned=False,
            characters=[Character(char_id=101, name="Pilot One")],
        ),
        Account(
            username="account-b",
            account_id=2,
            role="0",
            banned=False,
            characters=[Character(char_id=102, name="Pilot Two")],
        ),
    ]
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "C:/Games/EveJS",
            "client_path": "C:/Games/EVE/tq",
            "hide_test_characters": False,
            "stagger_delay_sec": 0,
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    load_calls: list[str] = []

    def load_accounts(evejs_root: str) -> list[Account]:
        load_calls.append(evejs_root)
        return accounts

    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", load_accounts)
    monkeypatch.setattr(app_module.CharactersPage, "refresh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(app_module, "profile_exists", lambda _username: True)
    monkeypatch.setattr(app_module, "prefill_username", lambda _username: None)
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(
        app_module,
        "launch_client",
        lambda *_args, **_kwargs: type(
            "FakeProcess",
            (),
            {"pid": 1234, "poll": staticmethod(lambda: None)},
        )(),
    )

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    try:
        _wait_for_data(qapp, window)
        ready_callbacks: list[object] = []
        window._ensure_server_if_needed = (
            lambda on_ready: ready_callbacks.append(on_ready) or True
        )
        window._refresh_characters = lambda: None
        window._update_status_bar = lambda: None
        shared_launches: list[tuple[str, str, object]] = []
        window._start_client_launch = lambda username, character, **kwargs: (
            shared_launches.append(
                (username, character, kwargs.get("launch_context"))
            )
            or True
        )

        window._launch_all()

        assert shared_launches == []
        assert len(ready_callbacks) == 1
        callback = ready_callbacks[0]
        assert callable(callback)
        callback()
        assert len(shared_launches) == 1
        assert shared_launches[0][:2] == ("account-a", "Pilot One")
        assert isinstance(shared_launches[0][2], ClientLaunchContext)

        window._cancel_launch_queue()
        assert window._launch_queue is not None
        window._launch_queue.item_finished(True)
        QTest.qWait(25)

        assert len(shared_launches) == 1
        assert window._home_page.btn_launch_all.text() == "Launch All"
        assert load_calls == ["C:/Games/EveJS"]
    finally:
        window.close()
        _wait_for_data(qapp, window)
        window.deleteLater()
