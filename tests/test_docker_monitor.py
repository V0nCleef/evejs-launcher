"""Focused read-only Docker observation worker tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import threading

import pytest
from PyQt6.QtCore import QEventLoop, QThread, QTimer

from src.core.runtime.data import docker_project_identity, docker_settings_identity
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord, PreflightReport
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import ServiceState
from src.workers.docker_monitor import DockerMonitor, DockerObservation


class FakeInspector:
    def __init__(self, report: PreflightReport, records: dict[str, ContainerRecord] | None = None) -> None:
        self.report, self.records = report, records or {}
        self.preflight_calls = 0
        self.status_calls = 0
        self.runtime_identity: str | None = None
        self.runtime_identity_calls = 0

    def preflight(self, target):
        self.preflight_calls += 1
        return self.report

    def status(self, target):
        self.status_calls += 1
        return self.records

    def container_runtime_identity(self, target, record):
        self.runtime_identity_calls += 1
        return self.runtime_identity


def _records(state: ServiceState = ServiceState.ONLINE) -> dict[str, ContainerRecord]:
    return {"server": ContainerRecord("server", "game", "game-id", state, "healthy", None, ()),
            "market": ContainerRecord("market", "market", "market-id", state, "healthy", None, ())}


def _endpoints(offset: int = 0) -> RuntimeEndpoints:
    def endpoint(service: str, target: int, port: int) -> Endpoint:
        return Endpoint(service, "127.0.0.1", port + offset, target, "tcp")

    return RuntimeEndpoints(
        game=endpoint("server", 26000, 32600),
        image=endpoint("server", 26001, 32601),
        proxy=endpoint("server", 26002, 32602),
        assets=endpoint("server", 26003, 34443),
        xmpp=endpoint("server", 5222, 35222),
        market=endpoint("market", 40110, 40110),
    )


def test_docker_observation_is_immutable_and_has_no_pid() -> None:
    endpoints = _endpoints()
    observation = DockerObservation(ServiceState.ONLINE, ServiceState.STARTING,
                                    game_identity="abc123", market_identity="def456",
                                    endpoints=endpoints)
    assert observation.game is ServiceState.ONLINE
    assert not hasattr(observation, "game_pid")
    assert observation.endpoints is endpoints
    with pytest.raises(FrozenInstanceError):
        observation.game = ServiceState.OFFLINE  # type: ignore[misc]


def test_successful_preflight_emits_and_retains_exact_endpoints_on_status_polls() -> None:
    endpoints = _endpoints()
    config = type("Config", (), {"endpoints": endpoints})()
    inspector = FakeInspector(
        PreflightReport(True, (), config=config, records=_records()),  # type: ignore[arg-type]
        _records(ServiceState.STARTING),
    )
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: inspector)
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()
    monitor.observe_now()

    assert len(emitted) == 2
    assert emitted[0].endpoints is endpoints
    assert emitted[1].endpoints is endpoints


@pytest.mark.parametrize(
    ("reachable", "expected"),
    [
        (False, ServiceState.STARTING),
        (True, ServiceState.ONLINE),
    ],
)
def test_running_server_without_healthcheck_requires_game_and_proxy_health(
    reachable: bool,
    expected: ServiceState,
) -> None:
    endpoints = _endpoints(700)
    no_healthcheck = type("Service", (), {"has_healthcheck": False})()
    with_healthcheck = type("Service", (), {"has_healthcheck": True})()
    config = type(
        "Config",
        (),
        {
            "endpoints": endpoints,
            "project_name": "effective-project",
            "services": {
                "server": no_healthcheck,
                "market": with_healthcheck,
            },
        },
    )()
    records = {
        "server": ContainerRecord(
            "server",
            "game",
            "game-id",
            ServiceState.ONLINE,
            None,
            None,
            (),
            raw_state="running",
        ),
        "market": ContainerRecord(
            "market",
            "market",
            "market-id",
            ServiceState.ONLINE,
            "healthy",
            None,
            (),
            raw_state="running",
        ),
    }
    inspector = FakeInspector(
        PreflightReport(True, (), config=config, records=records),  # type: ignore[arg-type]
    )
    probed = []
    monitor = DockerMonitor(
        lambda: object(),
        inspector_factory=lambda: inspector,
        endpoint_probe=lambda endpoint: probed.append(("tcp", endpoint)) or reachable,
        http_probe=lambda endpoint: probed.append(("http", endpoint)) or reachable,
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()

    assert emitted[-1].game is expected
    assert emitted[-1].market is ServiceState.ONLINE
    expected_probes = [("tcp", endpoints.game)]
    if reachable:
        expected_probes.append(("http", endpoints.proxy))
    assert probed == expected_probes


def test_running_market_without_healthcheck_requires_market_health_response() -> None:
    endpoints = _endpoints(750)
    with_healthcheck = type("Service", (), {"has_healthcheck": True})()
    no_healthcheck = type("Service", (), {"has_healthcheck": False})()
    config = type(
        "Config",
        (),
        {
            "endpoints": endpoints,
            "services": {
                "server": with_healthcheck,
                "market": no_healthcheck,
            },
        },
    )()
    records = {
        "server": ContainerRecord(
            "server", "game", "game-id", ServiceState.ONLINE, "healthy",
            None, (), raw_state="running",
        ),
        "market": ContainerRecord(
            "market", "market", "market-id", ServiceState.ONLINE, None,
            None, (), raw_state="running",
        ),
    }
    inspector = FakeInspector(
        PreflightReport(True, (), config=config, records=records),  # type: ignore[arg-type]
    )
    probed = []
    monitor = DockerMonitor(
        lambda: object(),
        inspector_factory=lambda: inspector,
        endpoint_probe=lambda endpoint: probed.append(("tcp", endpoint)) or True,
        http_probe=lambda endpoint: probed.append(("http", endpoint)) or True,
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()

    assert emitted[-1].market is ServiceState.ONLINE
    assert probed == [("http", endpoints.market)]


def test_healthchecked_or_nonrunning_services_never_use_endpoint_fallback() -> None:
    endpoints = _endpoints(800)
    with_healthcheck = type("Service", (), {"has_healthcheck": True})()
    no_healthcheck = type("Service", (), {"has_healthcheck": False})()
    config = type(
        "Config",
        (),
        {
            "endpoints": endpoints,
            "services": {
                "server": with_healthcheck,
                "market": no_healthcheck,
            },
        },
    )()
    records = {
        "server": ContainerRecord(
            "server",
            "game",
            "game-id",
            ServiceState.FAILED,
            "unhealthy",
            None,
            (),
            raw_state="running",
        ),
        "market": ContainerRecord(
            "market",
            "market",
            "market-id",
            ServiceState.OFFLINE,
            None,
            0,
            (),
            raw_state="exited",
        ),
    }
    inspector = FakeInspector(
        PreflightReport(True, (), config=config, records=records),  # type: ignore[arg-type]
    )
    monitor = DockerMonitor(
        lambda: object(),
        inspector_factory=lambda: inspector,
        endpoint_probe=lambda _endpoint: pytest.fail(
            "healthchecked and non-running services must not be probed"
        ),
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()

    assert emitted[-1].game is ServiceState.FAILED
    assert emitted[-1].market is ServiceState.OFFLINE
    assert emitted[-1].market_error == "Container exited with code 0."


def test_endpoint_only_change_is_not_deduplicated() -> None:
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: object())
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor._emit(DockerObservation(ServiceState.ONLINE, ServiceState.ONLINE, endpoints=_endpoints()))
    monitor._emit(DockerObservation(ServiceState.ONLINE, ServiceState.ONLINE, endpoints=_endpoints(100)))

    assert len(emitted) == 2


def test_every_poll_emits_its_sample_even_when_presentation_is_unchanged() -> None:
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: object())
    sampled: list[DockerObservation] = []
    changed: list[DockerObservation] = []
    events: list[tuple[str, int]] = []

    def record_sample(observation: DockerObservation) -> None:
        sampled.append(observation)
        events.append(("sampled", observation.sample_started_monotonic_ns))

    def record_change(observation: DockerObservation) -> None:
        changed.append(observation)
        events.append(("changed", observation.sample_started_monotonic_ns))

    monitor.observation_sampled.connect(record_sample)
    monitor.observation_changed.connect(record_change)
    first = DockerObservation(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        sample_started_monotonic_ns=101,
    )
    second = replace(first, sample_started_monotonic_ns=202)

    monitor._emit(first)
    monitor._emit(second)

    assert [item.sample_started_monotonic_ns for item in sampled] == [101, 202]
    assert changed == [first]
    assert events == [("changed", 101), ("sampled", 101), ("sampled", 202)]


def test_observation_carries_private_safe_target_and_monitor_identity(
    tmp_path: Path,
) -> None:
    target = ComposeTarget(
        tmp_path / "compose.yaml",
        tmp_path,
        "fixture-project",
    )
    endpoints = _endpoints()
    config = type(
        "Config",
        (),
        {"endpoints": endpoints, "project_name": "effective-project"},
    )()
    inspector = FakeInspector(
        PreflightReport(True, (), config=config, records=_records()),  # type: ignore[arg-type]
    )
    settings_identity = docker_settings_identity(
        str(tmp_path),
        str(tmp_path / "compose.yaml"),
        "fixture-project",
    )
    monitor = DockerMonitor(
        lambda: target,
        inspector_factory=lambda: inspector,
        monitor_generation=7,
        settings_identity=settings_identity,
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()

    observation = emitted[-1]
    assert observation.target_identity == docker_project_identity(
        target,
        "effective-project",
    )
    assert observation.settings_identity == settings_identity
    assert observation.monitor_generation == 7
    assert str(tmp_path) not in observation.target_identity
    assert "effective-project" not in observation.target_identity


def test_later_status_failure_retains_established_target_and_clears_endpoints(
    tmp_path: Path,
) -> None:
    target = ComposeTarget(
        tmp_path / "compose.yaml",
        tmp_path,
        "fixture-project",
    )
    endpoints = _endpoints()
    config = type(
        "Config",
        (),
        {"endpoints": endpoints, "project_name": "effective-project"},
    )()

    class StatusFailureInspector(FakeInspector):
        def status(self, observed_target):
            self.status_calls += 1
            raise OSError("synthetic status failure")

    inspector = StatusFailureInspector(
        PreflightReport(True, (), config=config, records=_records()),  # type: ignore[arg-type]
    )
    settings_identity = docker_settings_identity(
        str(tmp_path),
        str(tmp_path / "compose.yaml"),
        "fixture-project",
    )
    monitor = DockerMonitor(
        lambda: target,
        inspector_factory=lambda: inspector,
        monitor_generation=7,
        settings_identity=settings_identity,
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()
    established = emitted[-1]
    monitor.observe_now()
    failed = emitted[-1]

    assert established.target_identity is not None
    assert established.endpoints is endpoints
    assert failed.game is ServiceState.UNKNOWN
    assert failed.market is ServiceState.UNKNOWN
    assert failed.target_identity == established.target_identity
    assert failed.endpoints is None
    assert failed.settings_identity == settings_identity
    assert failed.monitor_generation == 7


def test_observation_equality_includes_target_settings_and_generation_identity() -> None:
    base = DockerObservation(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        target_identity="docker:target-a",
        settings_identity="docker-settings:a",
        monitor_generation=7,
    )

    assert base.equality_key() != replace(
        base,
        target_identity="docker:target-b",
    ).equality_key()
    assert base.equality_key() != replace(
        base,
        settings_identity="docker-settings:b",
    ).equality_key()
    assert base.equality_key() != replace(
        base,
        monitor_generation=8,
    ).equality_key()
    assert base.equality_key() != replace(
        base,
        game_runtime_identity="runtime-b",
    ).equality_key()


def test_same_container_id_with_new_start_epoch_emits_changed_runtime_identity() -> None:
    inspector = FakeInspector(
        PreflightReport(True, (), records=_records()),
        _records(),
    )
    inspector.runtime_identity = "runtime-a"
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: inspector)
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()
    inspector.runtime_identity = "runtime-b"
    monitor.observe_now()

    assert [item.game_identity for item in emitted] == ["game-id", "game-id"]
    assert [item.game_runtime_identity for item in emitted] == [
        "runtime-a",
        "runtime-b",
    ]
    assert inspector.runtime_identity_calls == 2


def test_failed_preflight_observation_has_no_target_or_endpoint_context(
    tmp_path: Path,
) -> None:
    target = ComposeTarget(
        tmp_path / "compose.yaml",
        tmp_path,
        "fixture-project",
    )
    settings_identity = docker_settings_identity(
        str(tmp_path),
        str(tmp_path / "compose.yaml"),
        "fixture-project",
    )
    inspector = FakeInspector(PreflightReport(False, ("daemon unavailable",)))
    monitor = DockerMonitor(
        lambda: target,
        inspector_factory=lambda: inspector,
        monitor_generation=7,
        settings_identity=settings_identity,
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()

    observation = emitted[-1]
    assert observation.target_identity is None
    assert observation.endpoints is None
    assert observation.settings_identity == settings_identity
    assert observation.monitor_generation == 7


def test_first_observation_preflights_then_status_only() -> None:
    inspector = FakeInspector(PreflightReport(True, (), records=_records()), _records())
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: inspector)
    monitor.observe_now()
    monitor.observe_now()
    assert inspector.preflight_calls == 1
    assert inspector.status_calls == 1


def test_unchanged_observations_deduplicate_ignoring_checked_at() -> None:
    inspector = FakeInspector(PreflightReport(True, (), records=_records()), _records())
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: inspector)
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)
    monitor.observe_now(); monitor.observe_now()
    assert len(emitted) == 1


def test_changed_identity_and_health_emit() -> None:
    inspector = FakeInspector(PreflightReport(True, (), records=_records()), _records())
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: inspector)
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)
    monitor.observe_now()
    inspector.records = _records(ServiceState.FAILED)
    monitor.observe_now()
    assert len(emitted) == 2
    assert emitted[-1].game is ServiceState.FAILED


@pytest.mark.parametrize("factory", [
    lambda: (_ for _ in ()).throw(ValueError("invalid project")),
    lambda: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
])
def test_invalid_or_missing_target_is_unknown_not_exception(factory) -> None:
    monitor = DockerMonitor(factory, inspector_factory=lambda: pytest.fail("inspector must not run"))
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)
    monitor.observe_now()
    assert emitted[-1].game is ServiceState.UNKNOWN
    assert "Docker observation unavailable" in emitted[-1].game_error


def test_missing_cli_factory_emits_actionable_preflight_diagnostic() -> None:
    def missing_cli():
        raise FileNotFoundError("synthetic executable details must not escape")

    monitor = DockerMonitor(lambda: object(), inspector_factory=missing_cli)
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()

    observation = emitted[-1]
    assert observation.game is ServiceState.UNKNOWN
    assert observation.game_error == (
        "Docker CLI was not found. Install Docker Desktop or add docker.exe to PATH."
    )
    assert observation.market_error == observation.game_error
    assert "synthetic" not in observation.game_error


def test_preflight_failure_retries() -> None:
    inspector = FakeInspector(PreflightReport(False, ("daemon unavailable",)))
    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: inspector)
    monitor.observe_now(); monitor.observe_now()
    assert inspector.preflight_calls == 2


def test_shutdown_before_observation_does_no_factory_or_cli_work() -> None:
    monitor = DockerMonitor(lambda: pytest.fail("factory must not run"),
                            inspector_factory=lambda: pytest.fail("inspector must not run"))
    monitor.request_shutdown()
    monitor.observe_now()


def test_shutdown_during_preflight_does_not_emit_stale_records() -> None:
    class ShutdownInspector(FakeInspector):
        def preflight(self, target):
            report = super().preflight(target)
            monitor.request_shutdown()
            return report

    monitor = DockerMonitor(lambda: object(), inspector_factory=lambda: ShutdownInspector(PreflightReport(True, (), records=_records())))
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)
    monitor.observe_now()
    assert emitted == []


def _wait_for_signal(signal, timeout_ms: int = 2_000) -> bool:
    loop = QEventLoop()
    timed_out = [True]
    signal.connect(lambda: (timed_out.__setitem__(0, False), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return not timed_out[0]


def test_real_qthread_runs_target_and_inspector_factories_off_gui_thread(qapp) -> None:
    gui_thread = QThread.currentThread()
    calls: list[QThread] = []
    inspector = FakeInspector(PreflightReport(True, (), records=_records()))

    def target_factory():
        calls.append(QThread.currentThread())
        return object()

    def inspector_factory():
        calls.append(QThread.currentThread())
        return inspector

    thread = QThread()
    monitor = DockerMonitor(target_factory, inspector_factory=inspector_factory)
    monitor.moveToThread(thread)
    monitor.observation_changed.connect(lambda _observation: (monitor.request_shutdown(), thread.quit()))
    thread.started.connect(monitor.start)
    thread.start()
    assert _wait_for_signal(thread.finished)
    assert thread.wait(2_000)
    assert calls and all(worker_thread is not gui_thread for worker_thread in calls)
    monitor.moveToThread(gui_thread)


def test_real_qthread_runs_endpoint_fallback_off_gui_thread(qapp) -> None:
    gui_thread = QThread.currentThread()
    probe_threads: list[QThread] = []
    endpoints = _endpoints(900)
    no_healthcheck = type("Service", (), {"has_healthcheck": False})()
    with_healthcheck = type("Service", (), {"has_healthcheck": True})()
    config = type(
        "Config",
        (),
        {
            "endpoints": endpoints,
            "services": {
                "server": no_healthcheck,
                "market": with_healthcheck,
            },
        },
    )()
    records = {
        "server": ContainerRecord(
            "server", "game", "game-id", ServiceState.ONLINE,
            None, None, (), raw_state="running",
        ),
        "market": ContainerRecord(
            "market", "market", "market-id", ServiceState.ONLINE,
            "healthy", None, (), raw_state="running",
        ),
    }
    inspector = FakeInspector(
        PreflightReport(True, (), config=config, records=records),  # type: ignore[arg-type]
    )

    def endpoint_probe(_endpoint) -> bool:
        probe_threads.append(QThread.currentThread())
        return False

    thread = QThread()
    monitor = DockerMonitor(
        lambda: object(),
        inspector_factory=lambda: inspector,
        endpoint_probe=endpoint_probe,
    )
    monitor.moveToThread(thread)
    monitor.observation_changed.connect(
        lambda _observation: (monitor.request_shutdown(), thread.quit())
    )
    thread.started.connect(monitor.start)
    thread.start()

    assert _wait_for_signal(thread.finished)
    assert thread.wait(2_000)
    assert probe_threads and all(
        worker_thread is not gui_thread for worker_thread in probe_threads
    )
    monitor.moveToThread(gui_thread)


def test_shutdown_before_real_qthread_start_skips_factory_without_sleep(qapp) -> None:
    called = threading.Event()
    thread = QThread()
    monitor = DockerMonitor(lambda: called.set(), inspector_factory=lambda: called.set())
    monitor.request_shutdown()
    monitor.moveToThread(thread)
    thread.started.connect(monitor.start)
    thread.start()
    assert _wait_for_signal(thread.finished)
    assert thread.wait(2_000)
    assert not called.is_set()
    monitor.moveToThread(QThread.currentThread())
