"""Tests for the background semantic service monitor."""
from __future__ import annotations

import threading

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from src.constants import Ports
from src.workers import server_worker
from src.workers.server_worker import ServiceMonitor


def test_monitor_emits_an_initial_offline_probe(
    qapp: QApplication,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server_worker.server_launcher,
        "is_server_running",
        lambda **_kwargs: False,
    )
    observed = []
    monitor = ServiceMonitor(interval_ms=10_000)
    monitor.probe_changed.connect(observed.append)

    monitor.start()
    try:
        qapp.processEvents()
        assert len(observed) == 1
        assert observed[0].game_reachable is False
        assert observed[0].market_reachable is False
        assert monitor._timer is not None
        assert monitor._timer.thread() is QThread.currentThread()
    finally:
        monitor.stop()


def test_monitor_change_detects_later_endpoint_observations(
    qapp: QApplication,
    monkeypatch,
) -> None:
    endpoints = {
        int(Ports.GAME_TCP): False,
        int(Ports.MARKET_RPC): False,
    }
    monkeypatch.setattr(
        server_worker.server_launcher,
        "is_server_running",
        lambda *, port, **_kwargs: endpoints[port],
    )
    observed = []
    monitor = ServiceMonitor(interval_ms=10_000)
    monitor.probe_changed.connect(observed.append)

    monitor.start()
    try:
        qapp.processEvents()
        monitor.probe_now()
        assert len(observed) == 1

        endpoints[int(Ports.GAME_TCP)] = True
        monitor.probe_now()
        assert len(observed) == 2
        assert observed[-1].game_reachable is True
        assert observed[-1].market_reachable is False
    finally:
        monitor.stop()


def test_monitor_observes_stable_reachability_without_reemitting_change(
    qapp: QApplication,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        server_worker.server_launcher,
        "is_server_running",
        lambda **_kwargs: True,
    )
    observations = []
    changes = []
    monitor = ServiceMonitor(interval_ms=10_000)
    monitor.probe_observed.connect(observations.append)
    monitor.probe_changed.connect(changes.append)

    monitor.start()
    try:
        qapp.processEvents()
        monitor.probe_now()

        assert len(observations) == 2
        assert all(probe.game_reachable for probe in observations)
        assert len(changes) == 1
    finally:
        monitor.stop()


def test_monitor_probes_market_rpc_not_game_market_proxy(
    qapp: QApplication,
    monkeypatch,
) -> None:
    probed_ports: list[int] = []

    def record_probe(*, port: int, **_kwargs) -> bool:
        probed_ports.append(port)
        return False

    monkeypatch.setattr(
        server_worker.server_launcher,
        "is_server_running",
        record_probe,
    )
    monitor = ServiceMonitor(interval_ms=10_000, game_port=27555)

    monitor.start()
    try:
        qapp.processEvents()
        assert probed_ports == [27555, int(Ports.MARKET_RPC)]
        assert int(Ports.GAME_MARKET_PROXY) not in probed_ports
    finally:
        monitor.stop()


def test_monitor_shutdown_requested_before_start_exits_without_probing(
    qapp: QApplication,
    monkeypatch,
) -> None:
    """A pending shutdown must win over monitor startup in a real QThread."""
    probe_called = threading.Event()

    def record_probe(**_kwargs) -> bool:
        probe_called.set()
        return False

    monkeypatch.setattr(
        server_worker.server_launcher,
        "is_server_running",
        record_probe,
    )
    thread = QThread()
    monitor = ServiceMonitor(interval_ms=10_000)
    monitor.moveToThread(thread)
    thread.started.connect(monitor.start)

    try:
        monitor.request_shutdown()
        thread.start()

        assert thread.wait(1_000)
        assert probe_called.is_set() is False
        assert monitor._timer is None
    finally:
        if thread.isRunning():
            thread.quit()
            assert thread.wait(2_000)
