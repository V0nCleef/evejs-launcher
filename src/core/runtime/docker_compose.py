"""Read-only Docker Compose target validation and status inspection."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, TypeVar

from src.core.runtime.docker_cli import (
    DockerCommandError,
    DockerCommandResult,
    DockerStructuredOutputError,
)
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import ServiceState


_REQUIRED_SERVICES = frozenset({"server", "market"})
_PROJECT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_ENDPOINTS = {
    "game": ("server", 26000), "image": ("server", 26001),
    "proxy": ("server", 26002), "assets": ("server", 26003),
    "xmpp": ("server", 5222), "market": ("market", 40110),
}
_DAEMON_VERSION = ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}")
_COMPOSE_VERSION = ("compose", "version", "--format", "{{.Version}}")
_TOOLS_PROFILE_SERVICES = ("--profile", "tools", "config", "--services")
_CONTAINER_ID = re.compile(r"[0-9a-f]{64}\Z")
_CONTAINER_SHORT_ID = re.compile(r"[0-9a-f]{12,64}\Z")
_CONTAINER_STARTED_AT = re.compile(r"[^\t\r\n]{1,128}\Z")
_CONTAINER_RUNTIME_INSPECT_FORMAT = (
    "{{.Id}}\t{{.State.StartedAt}}\t{{.State.Running}}"
)
ParsedOutput = TypeVar("ParsedOutput")


class ComposeValidationError(ValueError):
    """Selected Compose project is invalid or unsafe for local-only EveJS."""


class PreflightFailureKind(str, Enum):
    """Stable machine-readable reason that Docker preflight could not complete."""

    CLI_MISSING = "cli_missing"
    DAEMON_UNAVAILABLE = "daemon_unavailable"
    WRONG_ENGINE_MODE = "wrong_engine_mode"
    COMPOSE_UNAVAILABLE = "compose_unavailable"
    COMPOSE_CONFIG_INVALID = "compose_config_invalid"
    SERVICE_SET_INVALID = "service_set_invalid"
    INSPECT_FAILED = "inspect_failed"


_PREFLIGHT_DIAGNOSTICS = {
    PreflightFailureKind.CLI_MISSING: (
        "Docker CLI was not found. Install Docker Desktop or add docker.exe to PATH."
    ),
    PreflightFailureKind.DAEMON_UNAVAILABLE: (
        "Docker Desktop engine is unavailable. Start Docker Desktop and wait for the engine to finish starting."
    ),
    PreflightFailureKind.WRONG_ENGINE_MODE: (
        "Docker is using Windows containers. Switch Docker Desktop to Linux containers."
    ),
    PreflightFailureKind.COMPOSE_UNAVAILABLE: (
        "Docker Compose plugin is unavailable. Install or enable Docker Compose in Docker Desktop."
    ),
    PreflightFailureKind.COMPOSE_CONFIG_INVALID: (
        "Compose configuration is invalid. Check the selected Compose file and its local paths."
    ),
    PreflightFailureKind.SERVICE_SET_INVALID: (
        "Compose configuration must define the required EveJS server and market services."
    ),
    PreflightFailureKind.INSPECT_FAILED: (
        "Docker Compose service status could not be inspected. Check Docker Desktop and the selected project."
    ),
}

_EXPLICIT_PROJECT_NAME_DIAGNOSTIC = (
    "Compose could not derive a project name from this folder. Set the "
    "advanced Compose Project Name to a stable lowercase ASCII value such as "
    "evejs-local, or define a valid top-level name in the Compose file."
)
_PROJECT_NAME_DIAGNOSTIC_PROBE = "evejs-local"


def preflight_failure_diagnostic(kind: PreflightFailureKind) -> str:
    """Return the static user guidance for one typed preflight failure."""
    return _PREFLIGHT_DIAGNOSTICS[kind]


def _normalized_derived_project_name(folder_name: str) -> str:
    """Mirror Compose's normalization for a project-directory basename."""
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_-"
    normalized = "".join(
        character
        for character in folder_name.lower()
        if character in allowed
    )
    return normalized.lstrip("_-")


@dataclass(frozen=True)
class ComposeTarget:
    """Explicit selected Compose files and project directory for every call."""

    compose_file: Path
    project_directory: Path
    project_name: str | None = None
    override_files: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not _is_absolute(self.compose_file) or not _is_absolute(self.project_directory):
            raise ValueError("Compose file and project directory must be absolute.")
        compose_file = self.compose_file.resolve()
        project_directory = self.project_directory.resolve()
        if self.project_name is not None and not _PROJECT_NAME.fullmatch(self.project_name):
            raise ValueError("Compose project name contains unsupported characters.")
        if any(not _is_absolute(path) for path in self.override_files):
            raise ValueError("Compose override files must be absolute.")
        override_files = tuple(path.resolve() for path in self.override_files)
        if len(set(override_files)) != len(override_files):
            raise ValueError("Compose override files must be unique.")
        if compose_file in override_files:
            raise ValueError("The primary Compose file cannot also be an override.")
        object.__setattr__(self, "compose_file", compose_file)
        object.__setattr__(self, "project_directory", project_directory)
        object.__setattr__(self, "override_files", override_files)

    def base_argv(self, docker: str) -> tuple[str, ...]:
        compose_files = (self.compose_file, *self.override_files)
        file_args = tuple(
            argument
            for path in compose_files
            for argument in ("-f", str(path))
        )
        args = (
            docker,
            "compose",
            *file_args,
            "--project-directory",
            str(self.project_directory),
        )
        return args if self.project_name is None else (*args, "-p", self.project_name)

    def compose_args(self, docker: str, *command: str) -> tuple[str, ...]:
        return (*self.base_argv(docker)[1:], *command)


@dataclass(frozen=True)
class Mount:
    type: str
    source: str
    target: str


@dataclass(frozen=True)
class ComposeService:
    name: str
    has_healthcheck: bool
    dependencies: tuple[str, ...]
    stop_grace_period: str | None
    mounts: tuple[Mount, ...]
    image: str | None = None
    pull_policy: str | None = None


@dataclass(frozen=True)
class ComposeCapabilities:
    init: bool
    market_tools: bool


@dataclass(frozen=True)
class ComposeConfig:
    project_name: str | None
    services: Mapping[str, ComposeService]
    endpoints: RuntimeEndpoints
    capabilities: ComposeCapabilities
    # Hash only: the effective JSON can contain secrets from Compose
    # interpolation and must never be retained beyond parsing.
    effective_config_digest: str | None = None
    # Retain only the exact effective server NODE_OPTIONS digest for mod
    # attestation. The environment value itself may contain private material.
    server_node_options_sha256: str | None = None


@dataclass(frozen=True)
class ContainerRecord:
    service: str
    name: str | None
    short_id: str | None
    state: ServiceState
    health: str | None
    exit_code: int | None
    publishers: tuple[str, ...]
    exists: bool = True
    raw_state: str | None = None

    @classmethod
    def absent(cls, service: str) -> "ContainerRecord":
        return cls(service, None, None, ServiceState.OFFLINE, None, None, (), False, None)


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    diagnostics: tuple[str, ...]
    config: ComposeConfig | None = None
    records: Mapping[str, ContainerRecord] | None = None
    failure_kind: PreflightFailureKind | None = None

    @classmethod
    def failed(
        cls,
        kind: PreflightFailureKind,
        diagnostic: str | None = None,
    ) -> "PreflightReport":
        return cls(
            False,
            (diagnostic or preflight_failure_diagnostic(kind),),
            failure_kind=kind,
        )


class ComposeInspector:
    """Only read-only Docker/Compose discovery for a selected local project."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    def preflight(self, target: ComposeTarget) -> PreflightReport:
        diagnostics: list[str] = []
        try:
            if not target.compose_file.is_file():
                return PreflightReport.failed(
                    PreflightFailureKind.COMPOSE_CONFIG_INVALID
                )
            try:
                version = self._run(_DAEMON_VERSION, target)
            except DockerCommandError:
                return PreflightReport.failed(PreflightFailureKind.DAEMON_UNAVAILABLE)
            os_name = version.stdout.strip().split("|", 1)[0].casefold()
            if os_name != "linux":
                return PreflightReport.failed(PreflightFailureKind.WRONG_ENGINE_MODE)
            diagnostics.append("Docker daemon reachable in Linux-container mode.")

            try:
                compose_version = self._run(_COMPOSE_VERSION, target)
            except DockerCommandError:
                return PreflightReport.failed(PreflightFailureKind.COMPOSE_UNAVAILABLE)
            if not compose_version.stdout.strip():
                return PreflightReport.failed(PreflightFailureKind.COMPOSE_UNAVAILABLE)
            diagnostics.append("Docker Compose plugin reachable.")

            try:
                services_result = self._compose(target, "config", "--services")
            except DockerCommandError:
                diagnostic = None
                if (
                    target.project_name is None
                    and not _normalized_derived_project_name(
                        target.project_directory.name
                    )
                ):
                    # Command failures deliberately expose no stderr. Confirm
                    # the cause with the same read-only command, changing only
                    # the otherwise-empty derived project name.
                    try:
                        self._compose(
                            replace(
                                target,
                                project_name=_PROJECT_NAME_DIAGNOSTIC_PROBE,
                            ),
                            "config",
                            "--services",
                        )
                    except DockerCommandError:
                        pass
                    else:
                        diagnostic = _EXPLICIT_PROJECT_NAME_DIAGNOSTIC
                return PreflightReport.failed(
                    PreflightFailureKind.COMPOSE_CONFIG_INVALID,
                    diagnostic=diagnostic,
                )
            services = {line.strip() for line in services_result.stdout.splitlines() if line.strip()}
            missing = _REQUIRED_SERVICES - services
            if missing:
                return PreflightReport.failed(
                    PreflightFailureKind.SERVICE_SET_INVALID
                )
            diagnostics.append("Required Compose services found.")

            profile_services_result = self._compose(target, *_TOOLS_PROFILE_SERVICES)
            profile_services = {
                line.strip()
                for line in profile_services_result.stdout.splitlines()
                if line.strip()
            }
            config = self._compose_parsed(
                target,
                "config",
                "--format",
                "json",
                parser=_parse_compose_config_output,
            )
            config = replace(
                config,
                capabilities=ComposeCapabilities(
                    init=config.capabilities.init,
                    market_tools="market-tools" in profile_services,
                ),
            )
            # `config --services` is authoritative for active services; JSON supplies only
            # the deliberately narrow safe fields parsed above.
            if not _REQUIRED_SERVICES.issubset(config.services):
                return PreflightReport.failed(
                    PreflightFailureKind.SERVICE_SET_INVALID
                )
            diagnostics.append("Safe local endpoints and effective mounts validated.")

            try:
                found = {
                    record.service: record
                    for record in self._compose_parsed(
                        target,
                        "ps",
                        "--all",
                        "--format",
                        "json",
                        parser=parse_ps_output,
                    )
                }
            except (
                DockerCommandError,
                DockerStructuredOutputError,
                ComposeValidationError,
                json.JSONDecodeError,
            ):
                return PreflightReport.failed(PreflightFailureKind.INSPECT_FAILED)
            # Preserve every effective/observed service for destructive
            # controllers.  Ordinary presentation still reads its known keys,
            # while maintenance can fail closed when a profile or extension
            # service could share the same persistent store.
            record_services = set(config.services) | set(found) | _REQUIRED_SERVICES | {"init"}
            records = MappingProxyType(
                {
                    service: found.get(service, ContainerRecord.absent(service))
                    for service in record_services
                }
            )
            diagnostics.append("Compose service records inspected.")
            return PreflightReport(True, tuple(diagnostics), config, records)
        except (
            DockerCommandError,
            DockerStructuredOutputError,
            ComposeValidationError,
            json.JSONDecodeError,
            OSError,
        ):
            # Never include command output/config/environment payloads in diagnostics.
            return PreflightReport.failed(
                PreflightFailureKind.COMPOSE_CONFIG_INVALID
            )

    def status(self, target: ComposeTarget) -> Mapping[str, ContainerRecord]:
        """Read only current records after a successful cached preflight."""
        records = self._compose_parsed(
            target,
            "ps",
            "--all",
            "--format",
            "json",
            parser=parse_ps_output,
        )
        found = {record.service: record for record in records}
        record_services = set(found) | _REQUIRED_SERVICES
        return MappingProxyType(
            {
                service: found.get(service, ContainerRecord.absent(service))
                for service in record_services
            }
        )

    def container_runtime_identity(
        self,
        target: ComposeTarget,
        record: ContainerRecord,
    ) -> str | None:
        """Return a privacy-safe identity for one currently running container."""

        if record.raw_state != "running" or not record.exists:
            return None
        short_id = record.short_id
        if (
            not isinstance(short_id, str)
            or not _CONTAINER_SHORT_ID.fullmatch(short_id.casefold())
        ):
            raise ComposeValidationError(
                "The running container identity is unavailable."
            )
        result = self._run(
            (
                "inspect",
                "--type",
                "container",
                "--format",
                _CONTAINER_RUNTIME_INSPECT_FORMAT,
                short_id,
            ),
            target,
        )
        if result.truncated:
            raise ComposeValidationError(
                "The running container inspection was truncated."
            )
        parts = result.stdout.rstrip("\r\n").split("\t", 2)
        if len(parts) != 3 or parts[2] != "true":
            raise ComposeValidationError(
                "The running container inspection is malformed."
            )
        return docker_container_runtime_identity(
            parts[0],
            parts[1],
            expected_short_id=short_id,
        )

    def _run(self, args: tuple[str, ...], target: ComposeTarget) -> DockerCommandResult:
        self._assert_read_only(args, target)
        return self._runner.run(args, cwd=target.project_directory)

    def _compose(self, target: ComposeTarget, *command: str) -> DockerCommandResult:
        args = target.compose_args(self._runner.executable, *command)
        return self._run(args, target)

    def _run_parsed(
        self,
        args: tuple[str, ...],
        target: ComposeTarget,
        *,
        parser: Callable[[str], ParsedOutput],
    ) -> ParsedOutput:
        self._assert_read_only(args, target)
        return self._runner.run_parsed(
            args,
            cwd=target.project_directory,
            parser=parser,
        )

    def _compose_parsed(
        self,
        target: ComposeTarget,
        *command: str,
        parser: Callable[[str], ParsedOutput],
    ) -> ParsedOutput:
        args = target.compose_args(self._runner.executable, *command)
        return self._run_parsed(args, target, parser=parser)

    @staticmethod
    def _assert_read_only(args: tuple[str, ...], target: ComposeTarget) -> None:
        allowed = {
            _DAEMON_VERSION,
            _COMPOSE_VERSION,
            target.compose_args("docker", "config", "--services"),
            target.compose_args("docker", *_TOOLS_PROFILE_SERVICES),
            target.compose_args("docker", "config", "--format", "json"),
            target.compose_args("docker", "ps", "--all", "--format", "json"),
        }
        inspect_allowed = (
            len(args) == 6
            and args[:5]
            == (
                "inspect",
                "--type",
                "container",
                "--format",
                _CONTAINER_RUNTIME_INSPECT_FORMAT,
            )
            and isinstance(args[5], str)
            and bool(_CONTAINER_SHORT_ID.fullmatch(args[5].casefold()))
        )
        if args not in allowed and not inspect_allowed:
            raise RuntimeError("Docker command is outside the Phase 2A read-only allowlist.")


def docker_container_runtime_identity(
    full_id: str,
    started_at: str,
    *,
    expected_short_id: str,
) -> str:
    """Hash Docker's immutable id plus start epoch without retaining either."""

    normalized_short_id = expected_short_id.casefold()
    if (
        not _CONTAINER_SHORT_ID.fullmatch(normalized_short_id)
        or not _CONTAINER_ID.fullmatch(full_id)
        or not full_id.startswith(normalized_short_id)
        or not _CONTAINER_STARTED_AT.fullmatch(started_at)
    ):
        raise ComposeValidationError("The running container identity is malformed.")
    return hashlib.sha256(f"{full_id}\0{started_at}".encode("utf-8")).hexdigest()


def parse_ps_output(text: str) -> tuple[ContainerRecord, ...]:
    """Parse Compose's array, object, wrapper collection, or NDJSON variants."""
    stripped = text.strip()
    if not stripped:
        return ()
    try:
        parsed = json.loads(stripped)
        values = _records_from_json(parsed)
    except json.JSONDecodeError:
        values = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    if not all(isinstance(value, Mapping) for value in values):
        raise ComposeValidationError("Compose ps output did not contain service records.")
    return tuple(_container_record(value) for value in values)


def _parse_compose_config_output(text: str) -> ComposeConfig:
    """Project effective Compose JSON into the retained secret-free model."""

    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise ComposeValidationError(
            "Effective Compose config must be a JSON object."
        )
    return parse_compose_config(payload)


def _records_from_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping):
        for key in ("containers", "Containers", "services", "Services"):
            collection = value.get(key)
            if isinstance(collection, list):
                return collection
        return [value]
    raise ComposeValidationError("Compose ps output is not JSON records.")


def _container_record(value: Mapping[str, Any]) -> ContainerRecord:
    service = str(value.get("Service") or value.get("service") or "")
    if not service:
        raise ComposeValidationError("Compose ps record has no service name.")
    raw_state = str(value.get("State") or value.get("state") or "").casefold()
    health = str(value.get("Health") or value.get("health") or "").casefold() or None
    exit_code_value = value.get("ExitCode", value.get("exitCode"))
    try:
        exit_code = None if exit_code_value in (None, "") else int(exit_code_value)
    except (TypeError, ValueError):
        exit_code = None
    state = _map_state(raw_state, health)
    publishers = value.get("Publishers", value.get("publishers", []))
    publisher_values = tuple(_publisher_text(item) for item in publishers if _publisher_text(item)) if isinstance(publishers, list) else ()
    container_id = str(value.get("ID") or value.get("id") or "") or None
    return ContainerRecord(service, str(value.get("Name") or value.get("name") or "") or None, container_id[:12] if container_id else None, state, health, exit_code, publisher_values, True, raw_state or None)


def _publisher_text(value: Any) -> str:
    return str(value.get("URL") or value.get("url") or "") if isinstance(value, Mapping) else str(value)


def _map_state(raw_state: str, health: str | None) -> ServiceState:
    if raw_state == "running":
        if health == "healthy":
            return ServiceState.ONLINE
        if health is None:
            return ServiceState.STARTING
        if health == "starting":
            return ServiceState.STARTING
        if health == "unhealthy":
            return ServiceState.FAILED
        return ServiceState.STARTING
    if raw_state in {"restarting", "starting"}:
        return ServiceState.STARTING
    if raw_state == "stopping":
        return ServiceState.STOPPING
    if raw_state == "exited":
        return ServiceState.OFFLINE
    if not raw_state:
        return ServiceState.UNKNOWN
    return ServiceState.OFFLINE


def parse_compose_config(payload: Mapping[str, Any]) -> ComposeConfig:
    """Retain only service/dependency/port/mount/grace fields; never environment."""
    raw_services = payload.get("services")
    if not isinstance(raw_services, Mapping):
        raise ComposeValidationError("Effective Compose config has no services object.")
    services: dict[str, ComposeService] = {}
    publications: list[Endpoint] = []
    for name, raw in raw_services.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            continue
        mounts = tuple(_parse_mount(item) for item in raw.get("volumes", []) if _parse_mount(item) is not None)
        targets = [mount.target for mount in mounts]
        if len(targets) != len(set(targets)):
            raise ComposeValidationError(f"Duplicate exact mount targets for service {name} are ambiguous.")
        dependencies = _dependencies(raw.get("depends_on"))
        healthcheck = raw.get("healthcheck")
        has_healthcheck = (
            isinstance(healthcheck, Mapping)
            and not bool(healthcheck.get("disable", False))
        )
        services[name] = ComposeService(
            name,
            has_healthcheck,
            dependencies,
            _string_or_none(raw.get("stop_grace_period")),
            mounts,
            _string_or_none(raw.get("image")),
            _string_or_none(raw.get("pull_policy")),
        )
        for item in raw.get("ports", []):
            publication = _parse_port(item)
            if publication is not None:
                publications.append(Endpoint(name, publication.host, publication.port, publication.target, publication.protocol))
    missing = _REQUIRED_SERVICES - set(services)
    if missing:
        raise ComposeValidationError(f"Effective Compose config is missing required services: {', '.join(sorted(missing))}.")
    endpoints = _resolve_endpoints(publications)
    effective_config_digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    server_node_options_sha256 = _server_node_options_sha256(raw_services)
    return ComposeConfig(
        _string_or_none(payload.get("name")),
        MappingProxyType(services),
        endpoints,
        ComposeCapabilities("init" in services, "market-tools" in services),
        effective_config_digest,
        server_node_options_sha256,
    )


def _server_node_options_sha256(
    raw_services: Mapping[str, Any],
) -> str | None:
    """Hash the exact effective server NODE_OPTIONS without retaining it."""

    server = raw_services.get("server")
    if not isinstance(server, Mapping):
        return None
    environment = server.get("environment")
    value: object | None = None
    if isinstance(environment, Mapping):
        value = environment.get("NODE_OPTIONS")
    elif isinstance(environment, list):
        matches = [
            item.partition("=")[2]
            for item in environment
            if isinstance(item, str) and item.partition("=")[0] == "NODE_OPTIONS"
        ]
        if len(matches) > 1:
            raise ComposeValidationError(
                "Effective server NODE_OPTIONS is declared more than once."
            )
        if matches:
            value = matches[0]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ComposeValidationError(
            "Effective server NODE_OPTIONS must resolve to a string."
        )
    return hashlib.sha256(value.encode("utf-8", errors="strict")).hexdigest()


def _dependencies(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(sorted(str(key) for key in value))
    if isinstance(value, list):
        return tuple(sorted(str(item) for item in value))
    return ()


def _parse_mount(value: Any) -> Mount | None:
    if isinstance(value, Mapping):
        source, target = _string_or_none(value.get("source")), _string_or_none(value.get("target"))
        if source and target:
            return Mount(str(value.get("type") or "volume"), source, _normal_target(target))
    if isinstance(value, str) and ":" in value:
        source, target = value.rsplit(":", 1)
        return Mount("bind" if source.startswith(".") or re.match(r"^[A-Za-z]:", source) else "volume", source, _normal_target(target))
    return None


def resolve_mount(mounts: Iterable[Mount | Mapping[str, Any]], container_path: str) -> Mount | None:
    """Return the longest matching normalized target; exact duplicates are unsafe."""
    normalized = [_parse_mount(item) if not isinstance(item, Mount) else item for item in mounts]
    candidates = [mount for mount in normalized if mount is not None and _is_parent_target(mount.target, container_path)]
    targets = [mount.target for mount in candidates]
    if len(targets) != len(set(targets)):
        raise ComposeValidationError("Duplicate exact mount targets are ambiguous.")
    return max(candidates, key=lambda mount: len(mount.target), default=None)


def _parse_port(value: Any) -> Endpoint | None:
    host, published, target, protocol = None, None, None, "tcp"
    if isinstance(value, Mapping):
        host, published, target = _string_or_none(value.get("host_ip") or value.get("hostIp")), value.get("published"), value.get("target")
        protocol = str(value.get("protocol") or "tcp").casefold()
    elif isinstance(value, str):
        parts = value.split(":")
        if len(parts) == 3:
            host, published, target = parts
    if host is None or published is None or target is None:
        return None
    if host not in {"127.0.0.1", "::1"}:
        raise ComposeValidationError("Published endpoints must bind to loopback in supported local mode.")
    try:
        target_text = str(target)
        if "/" in target_text:
            target_text, protocol = target_text.rsplit("/", 1)
        if protocol != "tcp":
            raise ComposeValidationError("Required endpoints must use TCP publications.")
        return Endpoint("", host, int(published), int(target_text), protocol)
    except ValueError as exc:
        raise ComposeValidationError("Compose port publication is invalid.") from exc


def _resolve_endpoints(publications: Iterable[Endpoint]) -> RuntimeEndpoints:
    publication_list = tuple(publications)
    binds = [(publication.host, publication.port, publication.protocol) for publication in publication_list]
    if len(binds) != len(set(binds)):
        raise ComposeValidationError("Duplicate host bind collision is unsafe.")
    found: dict[str, Endpoint] = {}
    for endpoint_name, (service, target) in _ENDPOINTS.items():
        matches = [publication for publication in publication_list if publication.service == service and publication.target == target]
        if len(matches) > 1:
            raise ComposeValidationError(f"Duplicate publication for {service} target {target} is unsafe.")
        if matches:
            found[endpoint_name] = matches[0]
    missing = [f"{service} target {target}" for name, (service, target) in _ENDPOINTS.items() if name not in found]
    if missing:
        raise ComposeValidationError(f"Missing required local endpoint publications: {', '.join(missing)}.")
    return RuntimeEndpoints(**found)


def _normal_target(value: str) -> str:
    return str(PurePosixPath("/" + value.lstrip("/")))


def _is_parent_target(parent: str, child: str) -> bool:
    parent_parts, child_parts = PurePosixPath(parent).parts, PurePosixPath(_normal_target(child)).parts
    return child_parts[: len(parent_parts)] == parent_parts


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_absolute(path: Path) -> bool:
    return path.is_absolute() or PureWindowsPath(path).is_absolute()
