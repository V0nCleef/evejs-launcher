"""Policy-gated managed Docker Compose lifecycle operations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any, Callable, Mapping

from src.core.runtime.docker_cli import (
    DockerCommandError,
    redact_docker_diagnostic,
)
from src.core.runtime.docker_compose import (
    ComposeTarget,
    ContainerRecord,
    docker_container_runtime_identity,
)
from src.core.runtime.data import docker_project_identity
from src.core.runtime.endpoints import Endpoint, probe_endpoint, probe_http_health
from src.core.service_status import DockerControlPolicy, ServiceState

log = logging.getLogger(__name__)


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
    RECREATE_STACK = "recreate_stack"


_COMMANDS: Mapping[DockerLifecycleAction, tuple[str, ...]] = {
    DockerLifecycleAction.RECREATE_STACK: ("up", "-d", "--force-recreate", "market", "server"),
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
    DockerLifecycleAction.RECREATE_STACK: 180.0,
    DockerLifecycleAction.START_MARKET: 180.0,
    DockerLifecycleAction.START_GAME: 180.0,
    DockerLifecycleAction.START_STACK: 180.0,
    DockerLifecycleAction.STOP_GAME: 75.0,
    DockerLifecycleAction.STOP_MARKET: 60.0,
    DockerLifecycleAction.STOP_ALL: 75.0,
    DockerLifecycleAction.RESTART_GAME: 75.0,
    DockerLifecycleAction.RECREATE_GAME: 180.0,
}
_MOD_RUNTIME_INSPECT_FORMAT = (
    "{{.Id}}\t{{.State.StartedAt}}\t{{.State.Running}}\t{{json .Config.Env}}"
)


@dataclass(frozen=True)
class DockerLifecycleResult:
    """Bounded lifecycle outcome for the GUI worker boundary."""

    action: DockerLifecycleAction
    succeeded: bool
    records: Mapping[str, ContainerRecord] | None = None
    error: str | None = None
    target_identity: str | None = None
    server_node_options_sha256: str | None = None
    game_runtime_identity: str | None = None
    diagnostic_code: str | None = None
    immutable_images: tuple[tuple[str, str], ...] = ()


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
        expected_target_identity: str | None = None,
        before_game_start=None,
        lifecycle_lease=None,
        verification_only: bool = False,
        installation_profile_id: str | None = None,
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
        self._expected_target_identity = expected_target_identity
        self._config: Any | None = None
        self._before_game_start = before_game_start
        self._lifecycle_lease = lifecycle_lease
        self._verification_only = verification_only
        self._profile_id = (installation_profile_id if isinstance(installation_profile_id, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", installation_profile_id) else "unassigned")
        self._operation_id = ""
        self._started = time.monotonic()

    def _trace(self, action, stage, *, target=None, records=None, reason=""):
        states = ",".join(f"{name}:{record.state.value}:{record.short_id or 'absent'}"
            for name, record in sorted((records or {}).items()) if name in {"server", "market"})
        log.info("Docker lifecycle operation=%s installation=%s action=%s stage=%s elapsed=%.2f "
                 "readiness_budget=%.1f read_only=%s target=%s states=%s reason=%s",
                 self._operation_id, self._profile_id, action.value, stage,
                 time.monotonic() - self._started, self._timeout, self._verification_only,
                 target or self._expected_target_identity or "unresolved", states or "unobserved", reason or "none")

    def execute(self, action: DockerLifecycleAction) -> DockerLifecycleResult:
        """Preflight, mutate once, then poll authoritative Compose state."""
        if not isinstance(action, DockerLifecycleAction):
            raise ValueError("Docker lifecycle action is not allowed.")
        self._operation_id, self._started = uuid.uuid4().hex, time.monotonic()
        self._trace(action, "preflight")
        result = None
        try:
            result = self._execute_with_lease(action)
            return result
        finally:
            self._trace(action, "finished", target=getattr(result, "target_identity", None),
                records=getattr(result, "records", None), reason=(
                    "verified" if result is not None and result.succeeded else
                    getattr(result, "diagnostic_code", None) or "operation_failed"))

    def _execute_with_lease(self, action):
        if self._before_game_start is None:
            return self._execute(action)
        from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock
        if self._lifecycle_lease is not None:
            if self._lifecycle_lease.released or self._lifecycle_lease.root != self._target.project_directory.resolve():
                raise RuntimeError("The retained Docker preparation lease no longer owns this root.")
            return self._execute(action)
        with acquire_mod_lifecycle_lock(self._target.project_directory):
            return self._execute(action)

    def _execute(self, action: DockerLifecycleAction) -> DockerLifecycleResult:
        if not isinstance(action, DockerLifecycleAction):
            raise ValueError("Docker lifecycle action is not allowed.")
        preflight = self._inspector.preflight(self._target)
        if not preflight.ok or preflight.records is None:
            return DockerLifecycleResult(
                action,
                False,
                error=_diagnostic(preflight.diagnostics),
                target_identity=self._expected_target_identity,
            )
        self._config = getattr(preflight, "config", None)
        services = getattr(self._config, "services", {})
        if action in {DockerLifecycleAction.START_GAME, DockerLifecycleAction.START_MARKET,
                      DockerLifecycleAction.START_STACK, DockerLifecycleAction.RECREATE_GAME, DockerLifecycleAction.RECREATE_STACK,
                      DockerLifecycleAction.RESTART_GAME}:
            self._timeout = min(900.0, max(self._timeout, *(float(getattr(service, "readiness_budget_sec", 0.0))
                                                        for service in services.values()))) if services else self._timeout
        target_identity = docker_project_identity(
            self._target,
            getattr(self._config, "project_name", None),
            config=self._config,
        )
        if (
            self._expected_target_identity is not None
            and target_identity != self._expected_target_identity
        ):
            return DockerLifecycleResult(
                action,
                False,
                error="Docker target changed before the lifecycle operation could run.",
                target_identity=target_identity,
            )
        if action is DockerLifecycleAction.STOP_MARKET and not _safely_stopped(preflight.records.get("server")):
            return DockerLifecycleResult(action, False, records=preflight.records,
                                         error="Server state is not safely stopped; Market was not stopped.",
                                         target_identity=target_identity)
        if docker_project_identity(
            self._target,
            getattr(self._config, "project_name", None),
            config=self._config,
        ) != target_identity:
            return DockerLifecycleResult(
                action,
                False,
                error="Docker target changed before the lifecycle operation could run.",
                target_identity=target_identity,
            )
        if action not in {DockerLifecycleAction.STOP_GAME,DockerLifecycleAction.STOP_MARKET,DockerLifecycleAction.STOP_ALL}:
            from .docker_mod_images import require_current_image_bases
            try:
                require_current_image_bases(self._target,self._inspector,self._runner)
            except (DockerCommandError,OSError,ValueError) as exc:
                return DockerLifecycleResult(action,False,records=preflight.records,
                    error=_diagnostic(exc),target_identity=target_identity,diagnostic_code="base_image_changed")
        if self._verification_only:
            self._trace(action, "read_only_verification", target=target_identity)
            return self._poll(action, target_identity)
        try:
            argv = self._target.compose_args(self._runner.executable, *_COMMANDS[action])
            self._trace(action, "command", target=target_identity,
                reason="explicit_stop" if action in {DockerLifecycleAction.STOP_GAME, DockerLifecycleAction.STOP_MARKET,
                    DockerLifecycleAction.STOP_ALL} else "authorized_start_or_restart")
            preparing = self._before_game_start is not None and action in {
                DockerLifecycleAction.START_GAME, DockerLifecycleAction.START_STACK,
                DockerLifecycleAction.RESTART_GAME, DockerLifecycleAction.RECREATE_GAME, DockerLifecycleAction.RECREATE_STACK}
            replace_game = action in {DockerLifecycleAction.RESTART_GAME, DockerLifecycleAction.RECREATE_GAME, DockerLifecycleAction.RECREATE_STACK}
            if action is DockerLifecycleAction.RECREATE_STACK or (preparing and replace_game and not _safely_stopped(preflight.records.get("server"))):
                # The authorized mod-image transition stops only Game before
                # replacing Market, then Compose honors normal health dependencies.
                self._trace(action, "stop_game", target=target_identity, reason="authorized_mod_recreation")
                self._runner.run(self._target.compose_args(self._runner.executable, "stop", "server"),
                                 cwd=self._target.project_directory, timeout=75.0)
                stopped = self._inspector.status(self._target)
                if not _safely_stopped(stopped.get("server")):
                    raise ValueError("Game did not stop; mod preparation was refused.")
            if action is DockerLifecycleAction.RECREATE_STACK or (preparing and not self._service_ready("market",preflight.records.get("market"))):
                market_command = (("up","-d","--force-recreate","market") if action is DockerLifecycleAction.RECREATE_STACK
                                  else ("up","-d","market"))
                self._runner.run(self._target.compose_args(self._runner.executable,*market_command),
                                 cwd=self._target.project_directory,timeout=180.0)
                market_result = self._poll(DockerLifecycleAction.START_MARKET,target_identity)
                if not market_result.succeeded:
                    from dataclasses import replace
                    return replace(market_result,action=action)
                if action is DockerLifecycleAction.RECREATE_STACK:
                    argv = self._target.compose_args(self._runner.executable,*_COMMANDS[DockerLifecycleAction.RECREATE_GAME])
            if preparing and (replace_game or _safely_stopped(preflight.records.get("server"))):
                self._trace(action, "prepare_server", target=target_identity)
                self._before_game_start()
                fresh = self._inspector.preflight(self._target)
                if not fresh.ok or fresh.config is None or docker_project_identity(self._target, fresh.config.project_name, config=fresh.config) != target_identity:
                    raise ValueError("Docker authority changed during server preparation.")
                if action is DockerLifecycleAction.RESTART_GAME:
                    argv = self._target.compose_args(self._runner.executable, "up", "-d", "server")
            self._runner.run(argv, cwd=self._target.project_directory,
                             timeout=self._command_timeouts[action])
        except DockerCommandError as exc:
            return DockerLifecycleResult(action, False, error=_diagnostic(exc),
                                         target_identity=target_identity)
        except (OSError, ValueError) as exc:
            return DockerLifecycleResult(action, False,
                                         error=_diagnostic(f"Docker lifecycle command failed: {exc}"),
                                         target_identity=target_identity)
        return self._poll(action, target_identity)

    def _poll(
        self,
        action: DockerLifecycleAction,
        target_identity: str,
    ) -> DockerLifecycleResult:
        deadline, last_records = self._clock() + self._timeout, None
        self._trace(action, "observe", target=target_identity)
        inspection_error = None
        previous_states = None
        while True:
            try:
                last_records = self._inspector.status(self._target)
                inspection_error = None
                states = tuple((name, record.state, record.short_id) for name, record in sorted(last_records.items())
                               if name in {"server", "market"})
                if states != previous_states:
                    self._trace(action, "health", target=target_identity, records=last_records)
                    previous_states = states
            except (DockerCommandError, OSError, ValueError) as exc:
                inspection_error = _diagnostic(f"Docker state inspection is unavailable: {exc}")
            if inspection_error is None:
                required_roles = ({"market"} if action is DockerLifecycleAction.START_MARKET else {"server", "market"})
                if action not in {DockerLifecycleAction.STOP_GAME, DockerLifecycleAction.STOP_MARKET, DockerLifecycleAction.STOP_ALL}:
                    for role in sorted(required_roles):
                        record = last_records.get(role)
                        if record is not None and record.raw_state in {"exited", "dead"} and record.exit_code not in {None, 0, 143}:
                            return DockerLifecycleResult(action,False,records=last_records,
                                error=f"Docker {role} exited with code {record.exit_code}. Other services were left unchanged.",
                                target_identity=target_identity,diagnostic_code="container_exited")
            if inspection_error is None and self._desired(action, last_records):
                node_options_sha256 = getattr(
                    self._config,
                    "server_node_options_sha256",
                    None,
                )
                game_runtime_identity = None
                if action in {DockerLifecycleAction.RECREATE_GAME, DockerLifecycleAction.RECREATE_STACK}:
                    try:
                        (
                            node_options_sha256,
                            game_runtime_identity,
                        ) = self._inspect_recreated_game_runtime(
                            last_records.get("server")
                        )
                    except (DockerCommandError, OSError, TypeError, ValueError):
                        return DockerLifecycleResult(
                            action,
                            False,
                            records=last_records,
                            error=(
                                "The recreated server reached readiness, but its "
                                "effective mod environment could not be verified."
                            ),
                            target_identity=target_identity,
                        )
                try:
                    immutable_images = ()
                    if action not in {DockerLifecycleAction.STOP_GAME, DockerLifecycleAction.STOP_MARKET, DockerLifecycleAction.STOP_ALL}:
                        immutable_images = self._verify_immutable_images(last_records,
                            roles={"market"} if action is DockerLifecycleAction.START_MARKET else {"server", "market"})
                except (DockerCommandError, OSError, ValueError):
                    return DockerLifecycleResult(action, False, records=last_records,
                        error="Service health is ready, but its declared mod image could not be attested. Containers were left running.",
                        target_identity=target_identity, diagnostic_code="image_unverified")
                if immutable_images or game_runtime_identity:
                    try:
                        fresh = self._inspector.status(self._target)
                        roles = {name for name, _image in immutable_images}
                        if game_runtime_identity:
                            roles.add("server")
                        if (not self._desired(action, fresh) or any(
                            name not in fresh or name not in last_records
                            or fresh[name].short_id != last_records[name].short_id
                            or not self._service_ready(name, fresh.get(name))
                            for name in roles)):
                            raise ValueError("Docker services changed during runtime attestation.")
                        if docker_project_identity(self._target, getattr(self._config, "project_name", None),
                                                   config=self._config) != target_identity:
                            raise ValueError("Docker configuration changed during runtime attestation.")
                    except (DockerCommandError, OSError, ValueError):
                        return DockerLifecycleResult(action, False, records=last_records,
                            error="Docker services changed or could not be observed after attestation. Retry verification; containers were left running.",
                            target_identity=target_identity, diagnostic_code="runtime_changed")
                    last_records = fresh
                return DockerLifecycleResult(
                    action,
                    True,
                    records=last_records,
                    target_identity=target_identity,
                    server_node_options_sha256=node_options_sha256,
                    game_runtime_identity=game_runtime_identity,
                    immutable_images=immutable_images,
                )
            if self._clock() >= deadline:
                log.warning("Docker lifecycle remains unverified action=%s target=%s; no stop requested", action.value, target_identity)
                return DockerLifecycleResult(action, False, records=last_records,
                    error=inspection_error or "Docker lifecycle operation did not reach its desired state before the timeout. Containers were left unchanged.",
                    target_identity=target_identity,
                    diagnostic_code="inspection_unavailable" if inspection_error else "readiness_timeout")
            self._sleep(self._interval)

    def _verify_immutable_images(self, records, *, roles):
        verified = []
        for name, service in getattr(self._config, "services", {}).items():
            image = getattr(service, "image", None)
            if name not in roles or not isinstance(image, str) or not image.startswith("sha256:"):
                continue
            record = records.get(name)
            if record is None or not record.short_id or record.raw_state != "running":
                raise ValueError("The image-owning service is not running.")
            actual = self._runner.run_parsed(("inspect", "--type", "container", "--format", "{{.Image}}", record.short_id),
                cwd=self._target.project_directory, parser=lambda text: text.strip(), timeout=15.0)
            if actual != image:
                raise ValueError("The current service is not using its declared immutable image.")
            verified.append((name, actual))
        return tuple(sorted(verified))

    def _inspect_recreated_game_runtime(
        self,
        record: ContainerRecord | None,
    ) -> tuple[str | None, str]:
        """Bind mod evidence to the recreated container's actual environment."""

        short_id = getattr(record, "short_id", None)
        if not isinstance(short_id, str) or not short_id:
            raise ValueError("The recreated server container id is unavailable.")
        return self._runner.run_parsed(
            (
                "inspect",
                "--type",
                "container",
                "--format",
                _MOD_RUNTIME_INSPECT_FORMAT,
                short_id,
            ),
            cwd=self._target.project_directory,
            parser=lambda stdout: _parse_recreated_game_runtime(
                stdout,
                expected_short_id=short_id,
            ),
            timeout=15.0,
        )

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
            DockerLifecycleAction.RECREATE_STACK,
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


def _parse_recreated_game_runtime(
    stdout: str,
    *,
    expected_short_id: str,
) -> tuple[str | None, str]:
    """Project secret-bearing inspect JSON into digests inside the runner."""

    parts = stdout.rstrip("\r\n").split("\t", 3)
    if len(parts) != 4:
        raise ValueError("The recreated server inspection is malformed.")
    full_id, started_at, running, raw_environment = parts
    if running != "true":
        raise ValueError("The recreated server identity is malformed.")
    environment = json.loads(raw_environment)
    if not isinstance(environment, list) or not all(
        isinstance(item, str) for item in environment
    ):
        raise ValueError("The recreated server environment is malformed.")
    node_options = [
        item[len("NODE_OPTIONS=") :]
        for item in environment
        if item.startswith("NODE_OPTIONS=")
    ]
    if len(node_options) > 1:
        raise ValueError("The recreated server NODE_OPTIONS is ambiguous.")
    node_options_sha256 = (
        hashlib.sha256(node_options[0].encode("utf-8")).hexdigest()
        if node_options
        else None
    )
    runtime_identity = docker_container_runtime_identity(
        full_id,
        started_at,
        expected_short_id=expected_short_id,
    )
    return node_options_sha256, runtime_identity


def _diagnostic(values: object) -> str:
    if isinstance(values, DockerCommandError):
        detail = values.result.stderr.strip()
        return redact_docker_diagnostic(f"{values} {detail}" if detail else str(values))
    if isinstance(values, tuple) and values:
        return redact_docker_diagnostic(values[0])
    return redact_docker_diagnostic(values) if values else "Docker lifecycle preflight failed."
