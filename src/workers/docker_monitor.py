"""Cancelable, read-only Docker Compose status monitor."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Callable, Mapping

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from src.core.runtime.data import docker_project_identity, docker_settings_identity
from src.core.runtime.docker_compose import (
    ComposeInspector,
    ComposeTarget,
    ContainerRecord,
    PreflightFailureKind,
    preflight_failure_diagnostic,
)
from src.core.runtime.docker_cli import redact_docker_diagnostic
from src.core.runtime.endpoints import (
    Endpoint,
    RuntimeEndpoints,
    probe_endpoint,
    probe_http_health,
)
from src.core.service_status import ServiceState


@dataclass(frozen=True)
class DockerObservation:
    """Small immutable presentation observation; never carries a host PID."""

    game: ServiceState
    market: ServiceState
    game_identity: str | None = None
    market_identity: str | None = None
    game_health: str | None = None
    market_health: str | None = None
    game_error: str | None = None
    market_error: str | None = None
    endpoints: RuntimeEndpoints | None = None
    target_identity: str | None = None
    settings_identity: str | None = None
    monitor_generation: int | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    game_runtime_identity: str | None = None
    sample_started_monotonic_ns: int = field(default_factory=time.monotonic_ns)

    def equality_key(self) -> tuple[object, ...]:
        return (self.game, self.market, self.game_identity, self.market_identity,
                self.game_runtime_identity,
                self.game_health, self.market_health, self.game_error,
                self.market_error, self.endpoints, self.target_identity,
                self.settings_identity, self.monitor_generation)


class DockerMonitor(QObject):
    """Run target construction and preflight in a worker, then Compose ``ps`` only."""

    observation_changed = pyqtSignal(object)
    observation_sampled = pyqtSignal(object)

    def __init__(self, target_factory: Callable[[], ComposeTarget], *,
                 inspector_factory: Callable[[], ComposeInspector],
                 endpoint_probe: Callable[[Endpoint], bool] = probe_endpoint,
                 http_probe: Callable[[Endpoint], bool] = probe_http_health,
                 interval_ms: int = 5_000,
                 monitor_generation: int | None = None,
                 settings_identity: str | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._target_factory = target_factory
        self._inspector_factory = inspector_factory
        self._endpoint_probe = endpoint_probe
        self._http_probe = http_probe
        self._interval_ms = max(250, int(interval_ms))
        self._shutdown_requested = threading.Event()
        self._startup_timer: QTimer | None = None
        self._timer: QTimer | None = None
        self._inspector: ComposeInspector | None = None
        self._target: ComposeTarget | None = None
        self._preflight_ok = False
        self._endpoints: RuntimeEndpoints | None = None
        self._service_healthchecks = {"server": True, "market": True}
        self._target_identity: str | None = None
        self._settings_identity = settings_identity
        self._monitor_generation = monitor_generation
        self._last_key: tuple[object, ...] | None = None

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    @pyqtSlot()
    def start(self) -> None:
        if self._shutdown_requested.is_set():
            QThread.currentThread().quit()
            return
        if self._startup_timer is None:
            self._startup_timer = QTimer(self)
            self._startup_timer.setSingleShot(True)
            self._startup_timer.timeout.connect(self._begin_monitoring)
        self._startup_timer.start(0)

    @pyqtSlot()
    def _begin_monitoring(self) -> None:
        if self._shutdown_requested.is_set():
            QThread.currentThread().quit()
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(self._interval_ms)
            self._timer.timeout.connect(self.observe_now)
        self._timer.start()
        self.observe_now()

    @pyqtSlot()
    def observe_now(self) -> None:
        if self._shutdown_requested.is_set():
            return
        sample_started_monotonic_ns = time.monotonic_ns()
        try:
            if self._target is None:
                self._target = self._target_factory()
                if isinstance(self._target, ComposeTarget):
                    if self._settings_identity is None:
                        self._settings_identity = docker_settings_identity(
                            str(self._target.project_directory),
                            str(self._target.compose_file),
                            self._target.project_name,
                        )
            if self._inspector is None:
                try:
                    self._inspector = self._inspector_factory()
                except FileNotFoundError:
                    self._emit_unknown(
                        preflight_failure_diagnostic(
                            PreflightFailureKind.CLI_MISSING
                        ),
                        sample_started_monotonic_ns=sample_started_monotonic_ns,
                    )
                    return
            if not self._preflight_ok:
                report = self._inspector.preflight(self._target)
                if self._shutdown_requested.is_set():
                    return
                if not report.ok:
                    self._emit_unknown(
                        report.diagnostics[0]
                        if report.diagnostics
                        else "Docker is unavailable.",
                        sample_started_monotonic_ns=sample_started_monotonic_ns,
                    )
                    return
                self._preflight_ok = True
                self._endpoints = (
                    report.config.endpoints
                    if report.config is not None
                    else None
                )
                services = getattr(report.config, "services", None)
                if isinstance(services, Mapping):
                    self._service_healthchecks = {
                        service: bool(
                            getattr(services.get(service), "has_healthcheck", True)
                        )
                        for service in ("server", "market")
                    }
                else:
                    self._service_healthchecks = {"server": True, "market": True}
                if isinstance(self._target, ComposeTarget):
                    self._target_identity = docker_project_identity(
                        self._target,
                        getattr(report.config, "project_name", None),
                        config=report.config,
                    )
                records = report.records or {}
            else:
                records = self._inspector.status(self._target)
            if not self._shutdown_requested.is_set():
                self._emit_records(
                    records,
                    sample_started_monotonic_ns=sample_started_monotonic_ns,
                )
        except Exception as exc:
            if not self._shutdown_requested.is_set():
                self._emit_unknown(
                    _safe_diagnostic(exc),
                    sample_started_monotonic_ns=sample_started_monotonic_ns,
                )

    def _emit_records(
        self,
        records: Mapping[str, ContainerRecord],
        *,
        sample_started_monotonic_ns: int,
    ) -> None:
        game = records.get("server", ContainerRecord.absent("server"))
        market = records.get("market", ContainerRecord.absent("market"))
        game_runtime_identity = self._inspector.container_runtime_identity(
            self._target,
            game,
        )
        game_endpoint = self._endpoints.game if self._endpoints is not None else None
        market_endpoint = (
            self._endpoints.market if self._endpoints is not None else None
        )
        game_state, game_probe_error = self._effective_service_state(
            "server", game, game_endpoint
        )
        market_state, market_probe_error = self._effective_service_state(
            "market", market, market_endpoint
        )
        self._emit(DockerObservation(game=game_state, market=market_state,
                   game_identity=game.short_id, market_identity=market.short_id,
                   game_runtime_identity=game_runtime_identity,
                   game_health=game.health, market_health=market.health,
                   game_error=game_probe_error or _record_error(game),
                   market_error=market_probe_error or _record_error(market),
                   endpoints=self._endpoints,
                   target_identity=self._target_identity,
                   settings_identity=self._settings_identity,
                   monitor_generation=self._monitor_generation,
                   sample_started_monotonic_ns=sample_started_monotonic_ns))

    def _effective_service_state(
        self,
        service: str,
        record: ContainerRecord,
        endpoint: Endpoint | None,
    ) -> tuple[ServiceState, str | None]:
        """Verify every semantic endpoint for running services without healthchecks."""
        if self._service_healthchecks.get(service, True):
            return record.state, None
        if record.raw_state != "running":
            return record.state, None
        if endpoint is None:
            return ServiceState.STARTING, "Required endpoint is unavailable."
        try:
            if service == "server":
                if not self._endpoint_probe(endpoint):
                    return (
                        ServiceState.STARTING,
                        "Game TCP endpoint verification failed.",
                    )
                proxy = (
                    self._endpoints.proxy
                    if self._endpoints is not None
                    else None
                )
                if proxy is None or not self._http_probe(proxy):
                    return (
                        ServiceState.STARTING,
                        "Proxy health endpoint verification failed.",
                    )
            elif service == "market":
                if not self._http_probe(endpoint):
                    return (
                        ServiceState.STARTING,
                        "Market health endpoint verification failed.",
                    )
            else:
                return ServiceState.STARTING, "Required endpoint is unavailable."
        except (OSError, ValueError):
            return ServiceState.STARTING, "Endpoint verification failed."
        return ServiceState.ONLINE, None

    def _emit_unknown(
        self,
        diagnostic: str,
        *,
        sample_started_monotonic_ns: int,
    ) -> None:
        self._preflight_ok = False
        self._endpoints = None
        self._emit(DockerObservation(ServiceState.UNKNOWN, ServiceState.UNKNOWN,
                   game_error=diagnostic, market_error=diagnostic, endpoints=None,
                   target_identity=self._target_identity,
                   settings_identity=self._settings_identity,
                   monitor_generation=self._monitor_generation,
                   sample_started_monotonic_ns=sample_started_monotonic_ns))

    def _emit(self, observation: DockerObservation) -> None:
        if self._shutdown_requested.is_set():
            return
        key = observation.equality_key()
        if key != self._last_key:
            self._last_key = key
            self.observation_changed.emit(observation)
        # The app can complete verification and begin a corrective lifecycle
        # from this signal. Emit it only after presentation consumed the same
        # sample, so a delayed ``observation_changed`` cannot overwrite the
        # corrective STOPPING transition with the rejected runtime state.
        self.observation_sampled.emit(observation)

    @pyqtSlot()
    def stop(self) -> None:
        self.request_shutdown()
        for timer_name in ("_startup_timer", "_timer"):
            timer = getattr(self, timer_name)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                setattr(self, timer_name, None)
        QThread.currentThread().quit()


def _safe_diagnostic(exc: Exception) -> str:
    detail = redact_docker_diagnostic(exc, limit=160).splitlines()[0].strip()
    return "Docker observation unavailable." if not detail else f"Docker observation unavailable: {detail[:160]}"


def _record_error(record: ContainerRecord) -> str | None:
    if record.state is ServiceState.FAILED:
        return "Container unhealthy" if record.health == "unhealthy" else "Container exited with an error"
    if record.raw_state == "exited":
        if record.exit_code is not None:
            return f"Container exited with code {record.exit_code}."
        return "Container exited."
    return None
