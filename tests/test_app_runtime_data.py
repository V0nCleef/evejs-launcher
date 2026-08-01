"""Phase 3 application integration tests for runtime-selected character data."""
from __future__ import annotations

from copy import deepcopy
import threading
import time

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.db import Account, Character
from src.core.runtime.data import (
    RuntimeDataSelection,
    docker_settings_identity,
    native_data_selection,
)
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import RuntimeBackend, RuntimeSnapshot, ServiceState
from src.workers.db_worker import AccountLoadResult


def _account(label: str, char_id: int = 101) -> Account:
    return Account(
        username=f"fixture-{label}",
        account_id=char_id,
        role="0",
        banned=False,
        characters=[Character(char_id=char_id, name=f"Fixture {label}")],
    )


def _config() -> dict:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "C:/Synthetic/EveJS",
            "client_path": "",
            "hide_test_characters": False,
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    return cfg


def _wait_until(qapp: QApplication, predicate, timeout_ms: int = 2_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    assert predicate()


def _close_window(qapp: QApplication, window: MainWindow) -> None:
    window.close()
    _wait_until(qapp, lambda: not window._data_load_active())
    window.deleteLater()
    qapp.processEvents()


def test_native_account_refresh_uses_existing_loader_seam_off_gui_thread(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    expected = _account("One")
    calls: list[tuple[str, QThread]] = []
    gui_thread = QThread.currentThread()

    def load_accounts(root: str) -> list[Account]:
        calls.append((root, QThread.currentThread()))
        return [expected]

    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", load_accounts)
    monkeypatch.setattr(app_module.CharactersPage, "_load_portrait_for_card", lambda *_args: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_until(qapp, lambda: window._accounts == [expected])

        assert calls == [("C:/Synthetic/EveJS", calls[0][1])]
        assert calls[0][1] is not gui_thread
        assert window._home_page.accounts_card.value_label.text() == "1"
    finally:
        _close_window(qapp, window)


def test_overlapping_account_refresh_is_cancelled_then_serially_replaced(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[int] = []

    def load_accounts(_root: str) -> list[Account]:
        call_number = len(calls) + 1
        calls.append(call_number)
        if call_number == 1:
            first_started.set()
            assert release_first.wait(2.0)
            return [_account("Stale")]
        return [_account("Current", 202)]

    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", load_accounts)
    monkeypatch.setattr(app_module.CharactersPage, "_load_portrait_for_card", lambda *_args: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_until(qapp, first_started.is_set)

        window._refresh_characters()
        release_first.set()
        _wait_until(
            qapp,
            lambda: window._accounts == [_account("Current", 202)]
            and not window._data_load_active(),
        )

        assert calls == [1, 2]
        assert all(account.username != "fixture-Stale" for account in window._accounts)
    finally:
        release_first.set()
        _close_window(qapp, window)


def test_stale_account_result_cannot_replace_current_accounts(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    cfg["evejs_root"] = ""
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        current = _account("Current")
        stale = _account("Stale", 202)
        current_token = object()
        window._accounts = [current]
        window._account_request_token = current_token

        window._on_account_load_completed(
            AccountLoadResult(
                RuntimeDataSelection(
                    native_data_selection("C:/Synthetic/Old").data_source,
                    None,
                    "native:stale",
                ),
                (stale,),
                object(),
            )
        )

        assert window._accounts == [current]
    finally:
        _close_window(qapp, window)


def test_selected_character_detail_is_loaded_off_thread_and_applied(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    account = _account("Detail")
    detail_threads: list[QThread] = []
    gui_thread = QThread.currentThread()

    def get_character_detail(_root: str, _char_id: int) -> dict[str, object]:
        detail_threads.append(QThread.currentThread())
        return {
            "balance": 2_500_000,
            "skillPoints": 12_500,
            "shipName": "Fixture Cruiser",
            "solarSystemName": "Fixture System",
            "securityStatus": 2.5,
        }

    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [account])
    monkeypatch.setattr(app_module, "get_character_detail", get_character_detail, raising=False)
    monkeypatch.setattr(app_module.CharactersPage, "_load_portrait_for_card", lambda *_args: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_until(qapp, lambda: bool(window._characters_page._cards))

        window._characters_page._on_card_selected(
            account.username,
            account.characters[0].name,
            account.characters[0].char_id,
        )
        ship_value = window._characters_page.detail_panel._stat_rows["Ship"][1]
        _wait_until(qapp, lambda: ship_value.text() == "Fixture Cruiser")

        assert detail_threads and detail_threads[0] is not gui_thread
        assert window._characters_page.detail_panel._stat_rows["ISK"][1].text() == "2.5M"
        assert window._characters_page.detail_panel._stat_rows["SP"][1].text() == "12k"
    finally:
        _close_window(qapp, window)


def test_close_cancels_data_load_without_waiting_on_gui_thread(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    load_started = threading.Event()
    release_load = threading.Event()

    def load_accounts(_root: str) -> list[Account]:
        load_started.set()
        assert release_load.wait(2.0)
        return [_account("Late")]

    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", load_accounts)
    monkeypatch.setattr(app_module.CharactersPage, "_load_portrait_for_card", lambda *_args: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_until(qapp, load_started.is_set)

        started_at = time.monotonic()
        window.close()
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.2
        assert window._close_in_progress is True
        assert window._data_load_active() is True
        release_load.set()
        _wait_until(qapp, lambda: not window._data_load_active())
        assert window._accounts == []
    finally:
        release_load.set()
        _close_window(qapp, window)


def test_docker_portrait_target_requires_matching_runtime_data_and_observation(
    qapp: QApplication,
) -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "evejs_root": "C:/Fixture",
        "docker_compose_file": "C:/Fixture/compose.yaml",
        "docker_project_name": "fixture",
    }
    window._monitor_generation = 7
    settings_identity = docker_settings_identity(
        "C:/Fixture",
        "C:/Fixture/compose.yaml",
        "fixture",
    )

    def endpoints(image_port: int) -> RuntimeEndpoints:
        def endpoint(name: str, port: int, target: int) -> Endpoint:
            return Endpoint(
                "market" if name == "market" else "server",
                "127.0.0.1",
                port,
                target,
                "tcp",
            )

        return RuntimeEndpoints(
            game=endpoint("game", 32600, 26000),
            image=endpoint("image", image_port, 26001),
            proxy=endpoint("proxy", 32602, 26002),
            assets=endpoint("assets", 32603, 26003),
            xmpp=endpoint("xmpp", 35222, 5222),
            market=endpoint("market", 40110, 40110),
        )

    window._data_selection = RuntimeDataSelection(
        data_source=object(),  # type: ignore[arg-type]
        endpoints=endpoints(39991),
        target_identity="docker:fixture-target",
        settings_identity=settings_identity,
        monitor_generation=7,
    )
    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        endpoints=endpoints(32601),
        target_identity="docker:fixture-target",
        settings_identity=settings_identity,
        monitor_generation=7,
    )

    target = window._current_portrait_target()

    assert target is not None
    assert target.target_identity == "docker:fixture-target"
    assert target.settings_identity == settings_identity
    assert target.monitor_generation == 7
    assert target.image_endpoint is not None
    assert target.image_endpoint.port == 32601

    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        endpoints=endpoints(32601),
        target_identity="docker:different-target",
        settings_identity=settings_identity,
        monitor_generation=7,
    )
    assert window._current_portrait_target() is None


def test_docker_data_factory_carries_current_private_settings_and_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "connect_only",
        "evejs_root": "C:/Private/Fixture",
        "docker_compose_file": "C:/Private/Fixture/compose.yaml",
        "docker_project_name": "private-project",
    }
    window._monitor_generation = 11
    target = object()
    window._docker_log_target_factory = lambda: (lambda: target)
    captured: dict[str, object] = {}

    def inspect(actual_target, **kwargs):
        captured.update(target=actual_target, **kwargs)
        return RuntimeDataSelection(
            data_source=object(),  # type: ignore[arg-type]
            endpoints=None,
            target_identity="docker:fixture-target",
            settings_identity=kwargs["settings_identity"],
            monitor_generation=kwargs["monitor_generation"],
        )

    monkeypatch.setattr(app_module, "inspect_docker_data_source", inspect)

    selection = window._make_data_selection_factory()()

    expected_settings = docker_settings_identity(
        "C:/Private/Fixture",
        "C:/Private/Fixture/compose.yaml",
        "private-project",
    )
    assert captured["target"] is target
    assert captured["settings_identity"] == expected_settings
    assert captured["monitor_generation"] == 11
    assert selection.settings_identity == expected_settings
    assert selection.monitor_generation == 11
    assert "Private" not in expected_settings
    assert "private-project" not in expected_settings


def test_docker_account_refresh_rejects_result_from_other_observed_target() -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "connect_only",
        "evejs_root": "C:/Fixture",
        "docker_compose_file": "C:/Fixture/compose.yaml",
        "docker_project_name": "fixture",
    }
    window._close_in_progress = False
    window._monitor_generation = 7
    window._settings_generation = 3
    window._data_request_sequence = 0
    window._account_worker = None
    window._account_request_token = None
    expected_settings = docker_settings_identity(
        "C:/Fixture",
        "C:/Fixture/compose.yaml",
        "fixture",
    )
    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        target_identity="docker:observed-target",
        settings_identity=expected_settings,
        monitor_generation=7,
    )
    window._cancel_detail_load = lambda: None
    selection_factory = lambda: None
    window._make_data_selection_factory = lambda: selection_factory
    scheduled: list[bool] = []
    window._schedule_account_load = lambda: scheduled.append(True)

    window._refresh_characters()

    token = window._account_request_token
    assert token is not None
    assert token.target_identity == "docker:observed-target"
    assert window._pending_account_request == (token, selection_factory)
    assert scheduled == [True]

    current_account = _account("Current")
    stale_account = _account("Other Target", 202)
    current_selection = object()
    window._accounts = [current_account]
    window._data_selection = current_selection
    window._refresh_character_views = lambda: pytest.fail(
        "other-target account result was rendered"
    )

    window._on_account_load_completed(
        AccountLoadResult(
            RuntimeDataSelection(
                data_source=object(),  # type: ignore[arg-type]
                endpoints=None,
                target_identity="docker:other-target",
                settings_identity=expected_settings,
                monitor_generation=7,
            ),
            (stale_account,),
            token,
        )
    )

    assert window._accounts == [current_account]
    assert window._data_selection is current_selection


def test_docker_account_results_require_observed_target_authority() -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "connect_only",
        "evejs_root": "C:/Fixture",
        "docker_compose_file": "C:/Fixture/compose.yaml",
        "docker_project_name": "fixture",
    }
    window._close_in_progress = False
    window._monitor_generation = 7
    window._settings_generation = 3
    window._data_request_sequence = 0
    window._account_worker = None
    window._account_request_token = None
    expected_settings = docker_settings_identity(
        "C:/Fixture",
        "C:/Fixture/compose.yaml",
        "fixture",
    )
    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.UNKNOWN,
        ServiceState.UNKNOWN,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        target_identity=None,
        settings_identity=expected_settings,
        monitor_generation=7,
    )
    window._cancel_detail_load = lambda: None
    selection_factory = lambda: None
    window._make_data_selection_factory = lambda: selection_factory
    scheduled: list[bool] = []
    window._schedule_account_load = lambda: scheduled.append(True)
    rendered: list[tuple[str, ...]] = []
    window._accounts = []
    window._data_selection = None
    window._refresh_character_views = lambda: rendered.append(
        tuple(account.username for account in window._accounts)
    )

    window._refresh_characters()
    unattributed_token = window._account_request_token
    assert unattributed_token is not None
    assert unattributed_token.target_identity is None

    attributed_selection = RuntimeDataSelection(
        data_source=object(),  # type: ignore[arg-type]
        endpoints=None,
        target_identity="docker:observed-target",
        settings_identity=expected_settings,
        monitor_generation=7,
    )
    window._on_account_load_completed(
        AccountLoadResult(
            attributed_selection,
            (_account("Premature"),),
            unattributed_token,
        )
    )

    assert window._accounts == []
    assert window._data_selection is None
    assert rendered == []

    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        target_identity="docker:observed-target",
        settings_identity=expected_settings,
        monitor_generation=7,
    )
    window._refresh_characters()
    attributed_token = window._account_request_token
    assert attributed_token is not None
    assert attributed_token.target_identity == "docker:observed-target"

    accepted_account = _account("Accepted", 202)
    window._on_account_load_completed(
        AccountLoadResult(
            attributed_selection,
            (accepted_account,),
            attributed_token,
        )
    )

    assert window._accounts == [accepted_account]
    assert window._data_selection is attributed_selection
    assert rendered == [(accepted_account.username,)]
    assert scheduled == [True, True]
