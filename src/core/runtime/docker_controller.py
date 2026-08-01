"""Policy-gated managed Docker Compose lifecycle operations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Callable, Mapping

from src.core.runtime.docker_cli import DockerCommandError, redact_docker_diagnostic
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord
from src.core.runtime.endpoints import Endpoint, probe_endpoint, probe_http_health
from src.core.service_status import DockerControlPolicy, ServiceState


class DockerLifecycleAction(Enum):
    """The only ordinary Compose lifecycle actions exposed by the launcher."""

    START_MARKET = "start_market"
    START_GAME = "start_game"
    START_STACK = "start_stack"
    STOP_GAME = "stop_game"
    STOP_MARKET = "stop_market"
    STOP_ALL = "stop_all"
    RESTART_GAME = "restart_game"
    RECREATE_GAME = "recreate_game"


_COMMANDS: Mapping[DockerLifecycleAction, tuple[str, ...]] = {
    DockerLifecycleAction.START_MARKET: ("up", "-d", "market"),
    DockerLifecycleAction.START_GAME: ("up", "-d", "server"),
    DockerLifecycleAction.START_STACK: ("up", "-d", "server"),
    DockerLifecycleAction.STOP_GAME: ("stop", "server"),
    DockerLifecycleAction.STOP_MARKET: ("stop", "market"),
    DockerLifecycleAction.STOP_ALL: ("stop", "server", "market"),
    DockerLifecycleAction.RESTART_GAME: ("restart", "server"),
    DockerLifecycleAction.RECREATE_GAME: (
        "up", "-d", "--no-deps", "--force-recreate", "server",
    ),
}
# Deliberately separate from readiness: initial up and Compose grace periods can
# exceed ordinary observation bounds. Callers can inject only these fixed actions.
_COMMAND_TIMEOUTS: Mapping[DockerLifecycleAction, float] = {
    DockerLifecycleAction.START_MARKET: 180.0,
    DockerLifecycleAction.START_GAME: 180.0,
    DockerLifecycleAction.START_STACK: 180.0,
    DockerLifecycleAction.STOP_GAME: 75.0,
    DockerLifecycleAction.STOP_MARKET: 60.0,
    DockerLifecycleAction.STOP_ALL: 75.0,
    DockerLifecycleAction.RESTART_GAME: 75.0,
    DockerLifecycleAction.RECREATE_GAME: 180.0,
}


@dataclass(frozen=True)
class DockerLifecycleResult:
    """Bounded lifecycle outcome for the GUI worker boundary."""

    action: DockerLifecycleAction
    succeeded: bool
    records: Mapping[str, ContainerRecord] | None = None
    error: str | None = None


class ManagedComposeController:
    """Execute the narrowly allowlisted managed Compose lifecycle contract."""

    def __init__(
        self, target: ComposeTarget, inspector: Any, runner: Any, *,
        policy: DockerControlPolicy, readiness_timeout_sec: float = 60.0,
        command_timeouts: Mapping[DockerLifecycleAction, float] | None = None,
        poll_interval_sec: float = 0.25, clock_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        endpoint_probe: Callable[[Endpoint], bool] = probe_endpoint,
        http_probe: Callable[[Endpoint], bool] = probe_http_health,
    ) -> None:
        if policy is not DockerControlPolicy.MANAGED:
            raise PermissionError("Docker lifecycle requires Managed control policy.")
        self._target, self._inspector, self._runner = target, inspector, runner
        self._timeout = max(0.01, float(readiness_timeout_sec))
        supplied = command_timeouts or {}
        self._command_timeouts = {
            action: max(0.01, float(supplied.get(action, timeout)))
            for action, timeout in _COMMAND_TIMEOUTS.items()
        }
        self._interval, self._clock, self._sleep = max(0.0, float(poll_interval_sec)), clock_fn, sleep_fn
        self._endpoint_probe = endpoint_probe
        self._http_probe = http_probe
        self._config: Any | None = None

    def execute(self, action: DockerLifecycleAction) -> DockerLifecycleResult:
        """Preflight, mutate once, then poll authoritative Compose state."""
        if not isinstance(action, DockerLifecycleAction):
            raise ValueError("Docker lifecycle action is not allowed.")
        preflight = self._inspector.preflight(self._target)
        if not preflight.ok or preflight.records is None:
            return DockerLifecycleResult(action, False, error=_diagnostic(preflight.diagnostics))
        self._config = getattr(preflight, "config", None)
        if action is DockerLifecycleAction.STOP_MARKET and not _safely_stopped(preflight.records.get("server")):
            return DockerLifecycleResult(action, False, records=preflight.records,
                                         error="Server state is not safely stopped; Market was not stopped.")
        try:
            argv = self._target.compose_args(self._runner.executable, *_COMMANDS[action])
            self._runner.run(argv, cwd=self._target.project_directory,
                             timeout=self._command_timeouts[action])
        except DockerCommandError as exc:
            return DockerLifecycleResult(action, False, error=_diagnostic(exc))
        except (OSError, ValueError) as exc:
            return DockerLifecycleResult(action, False,
                                         error=_diagnostic(f"Docker lifecycle command failed: {exc}"))
        return self._poll(action)

    def _poll(self, action: DockerLifecycleAction) -> DockerLifecycleResult:
        deadline, last_records = self._clock() + self._timeout, None
        while True:
            try:
                last_records = self._inspector.status(self._target)
            except DockerCommandError as exc:
                return DockerLifecycleResult(action, False, error=_diagnostic(exc))
            except (OSError, ValueError) as exc:
                return DockerLifecycleResult(action, False,
                                             error=_diagnostic(f"Docker state inspection failed: {exc}"))
            if self._desired(action, last_records):
                return DockerLifecycleResult(action, True, records=last_records)
            if self._clock() >= deadline:
                return DockerLifecycleResult(action, False, records=last_records,
                    error="Docker lifecycle operation did not reach its desired state before the timeout.")
            self._sleep(self._interval)

    def _desired(
        self,
        action: DockerLifecycleAction,
        records: Mapping[str, ContainerRecord],
    ) -> bool:
        game, market = records.get("server"), records.get("market")
        if action is DockerLifecycleAction.START_MARKET:
            return self._service_ready("market", market)
        if action in {
            DockerLifecycleAction.START_GAME,
            DockerLifecycleAction.START_STACK,
        }:
            return (
                self._service_ready("server", game)
                and self._service_ready("market", market)
            )
        if action in {
            DockerLifecycleAction.RESTART_GAME,
            DockerLifecycleAction.RECREATE_GAME,
        }:
            return self._service_ready("server", game)
        if action is DockerLifecycleAction.STOP_GAME:
            return _safely_stopped(game)
        if action is DockerLifecycleAction.STOP_MARKET:
            return _safely_stopped(market)
        return _safely_stopped(game) and _safely_stopped(market)

    def _service_ready(
        self,
        service: str,
        record: ContainerRecord | None,
    ) -> bool:
        """Require health evidence or the service's semantic endpoints."""
        if record is None or record.raw_state != "running":
            return False
        services = getattr(self._config, "services", None)
        service_config = (
            services.get(service)
            if isinstance(services, Mapping)
            else None
        )
        if bool(getattr(service_config, "has_healthcheck", True)):
            return record.state is ServiceState.ONLINE

        endpoints = getattr(self._config, "endpoints", None)
        try:
            if service == "server":
                game = getattr(endpoints, "game", None)
                proxy = getattr(endpoints, "proxy", None)
                return (
                    isinstance(game, Endpoint)
                    and isinstance(proxy, Endpoint)
                    and self._endpoint_probe(game)
                    and self._http_probe(proxy)
                )
            if service == "market":
                market = getattr(endpoints, "market", None)
                return (
                    isinstance(market, Endpoint)
                    and self._http_probe(market)
                )
        except (OSError, ValueError):
            return False
        return False


def _safely_stopped(record: ContainerRecord | None) -> bool:
    """Fail closed: absent, created, exited, and dead are the only stop states."""
    return record is None or not record.exists or record.raw_state in {"created", "exited", "dead"}


def _diagnostic(values: object) -> str:
    if isinstance(values, tuple) and values:
        return redact_docker_diagnostic(values[0])
    return redact_docker_diagnostic(values) if values else "Docker lifecycle preflight failed."
