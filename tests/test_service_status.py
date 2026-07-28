"""Pure runtime service-state and semantic endpoint tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from src.constants import Ports
from src.core.service_status import RuntimeSnapshot, ServiceState, derive_service_state


class FakeProcess:
    def __init__(self, pid: int, return_code: int | None = None) -> None:
        self.pid = pid
        self._return_code = return_code

    def poll(self) -> int | None:
        return self._return_code


def test_semantic_endpoint_constants_cannot_confuse_market_proxy_and_daemon() -> None:
    assert int(Ports.GAME_TCP) == 26000
    assert int(Ports.GAME_MARKET_PROXY) == 26001
    assert int(Ports.CLIENT_HTTP_PROXY) == 26002
    assert int(Ports.MARKET_HTTP) == 40110
    assert int(Ports.MARKET_RPC) == 40111


def test_external_reachable_service_is_online_without_owned_pid() -> None:
    state, pid, error = derive_service_state(reachable=True, process=None)

    assert state is ServiceState.ONLINE
    assert pid is None
    assert error is None


def test_owned_live_process_is_starting_until_reachable() -> None:
    state, pid, error = derive_service_state(
        reachable=False,
        process=FakeProcess(1234),
        intent=ServiceState.STARTING,
    )

    assert state is ServiceState.STARTING
    assert pid == 1234
    assert error is None


def test_owned_process_exit_before_readiness_is_failed() -> None:
    state, pid, error = derive_service_state(
        reachable=False,
        process=FakeProcess(1234, return_code=7),
        intent=ServiceState.STARTING,
    )

    assert state is ServiceState.FAILED
    assert pid is None
    assert "7" in str(error)


def test_readiness_timeout_is_failed_even_while_owned_process_is_alive() -> None:
    state, pid, error = derive_service_state(
        reachable=False,
        process=FakeProcess(1234),
        intent=ServiceState.STARTING,
        last_error="Game service did not become ready before the timeout.",
    )

    assert state is ServiceState.FAILED
    assert pid == 1234
    assert "timeout" in str(error)


def test_stopping_intent_takes_precedence_while_owned_process_is_alive() -> None:
    state, pid, error = derive_service_state(
        reachable=False,
        process=FakeProcess(1234),
        intent=ServiceState.STOPPING,
    )

    assert state is ServiceState.STOPPING
    assert pid == 1234
    assert error is None


def test_runtime_snapshot_tracks_game_and_market_independently() -> None:
    snapshot = RuntimeSnapshot(
        game=ServiceState.ONLINE,
        market=ServiceState.OFFLINE,
        running_clients=2,
        game_pid=None,
        market_pid=None,
    )

    assert snapshot.game is ServiceState.ONLINE
    assert snapshot.market is ServiceState.OFFLINE
    assert snapshot.running_clients == 2
    assert replace(snapshot, market=ServiceState.STARTING).game is ServiceState.ONLINE
