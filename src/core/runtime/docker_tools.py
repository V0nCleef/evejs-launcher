"""Closed semantic actions for Docker-backed EveJS Tool Deck operations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_cli import DockerCommandError
from src.core.runtime.docker_compose import (
    ComposeCapabilities,
    ComposeTarget,
    ContainerRecord,
)
from src.core.service_status import DockerControlPolicy


class DockerToolAction(Enum):
    """The only container-side Tool Deck operations reviewed by the launcher."""

    INITIALIZE_DATABASE = "initialize_database"
    MARKET_STATUS = "market_status"
    MARKET_DOCTOR = "market_doctor"
    MARKET_BACKUP = "market_backup"
    MARKET_BACKUPS = "market_backups"
    MARKET_PRESETS = "market_presets"
    MARKET_REBUILD_V1_JITA = "market_rebuild_v1_jita"
    MARKET_REBUILD_V1_FULL_UNIVERSE = "market_rebuild_v1_full_universe"
    MARKET_RESTORE_LATEST = "market_restore_latest"
    MARKET_SNAPSHOT_INFO = "market_snapshot_info"
    MARKET_REBUILD_V2 = "market_rebuild_v2"


@dataclass(frozen=True)
class DockerToolSpec:
    """One fixed Compose command and its reviewed execution constraints."""

    command: tuple[str, ...]
    required_capability: str
    requires_services_stopped: bool
    timeout: float
    success_message: str


@dataclass(frozen=True)
class DockerToolResult:
    """Private-safe semantic outcome; command output never crosses this boundary."""

    action: DockerToolAction
    succeeded: bool
    message: str = ""
    error: str = ""
    request_token: object | None = None
    target_identity: str | None = None


_SPECS: Mapping[DockerToolAction, DockerToolSpec] = MappingProxyType({
    DockerToolAction.INITIALIZE_DATABASE: DockerToolSpec(
        ("run", "--rm", "--no-deps", "init"),
        "init",
        True,
        1_800.0,
        "Docker game data initialization completed.",
    ),
    DockerToolAction.MARKET_STATUS: DockerToolSpec(
        ("run", "--rm", "--no-deps", "market-tools", "status"),
        "market_tools",
        False,
        120.0,
        "Docker market status check completed.",
    ),
    DockerToolAction.MARKET_DOCTOR: DockerToolSpec(
        ("run", "--rm", "--no-deps", "market-tools", "doctor"),
        "market_tools",
        True,
        300.0,
        "Docker market doctor completed.",
    ),
    DockerToolAction.MARKET_BACKUP: DockerToolSpec(
        ("run", "--rm", "--no-deps", "market-tools", "backup"),
        "market_tools",
        True,
        600.0,
        "Docker market backup completed.",
    ),
    DockerToolAction.MARKET_BACKUPS: DockerToolSpec(
        ("run", "--rm", "--no-deps", "market-tools", "backups"),
        "market_tools",
        False,
        120.0,
        "Docker market backup inventory check completed.",
    ),
    DockerToolAction.MARKET_PRESETS: DockerToolSpec(
        ("run", "--rm", "--no-deps", "market-tools", "presets"),
        "market_tools",
        False,
        120.0,
        "Docker market preset inventory check completed.",
    ),
    DockerToolAction.MARKET_REBUILD_V1_JITA: DockerToolSpec(
        (
            "run", "--rm", "--no-deps", "market-tools", "rebuild", "v1",
            "--preset", "jita_new_caldari",
        ),
        "market_tools",
        True,
        3_600.0,
        "Docker market v1 rebuild completed with the Jita and New Caldari preset.",
    ),
    DockerToolAction.MARKET_REBUILD_V1_FULL_UNIVERSE: DockerToolSpec(
        (
            "run", "--rm", "--no-deps", "market-tools", "rebuild", "v1",
            "--preset", "full_universe",
        ),
        "market_tools",
        True,
        3_600.0,
        "Docker market v1 rebuild completed with the full-universe preset.",
    ),
    DockerToolAction.MARKET_RESTORE_LATEST: DockerToolSpec(
        (
            "run", "--rm", "--no-deps", "market-tools", "restore", "latest",
        ),
        "market_tools",
        True,
        600.0,
        "Latest Docker market backup restore completed.",
    ),
    DockerToolAction.MARKET_SNAPSHOT_INFO: DockerToolSpec(
        (
            "run", "--rm", "--no-deps", "market-tools", "snapshot-info",
        ),
        "market_tools",
        False,
        120.0,
        "Docker market snapshot information check completed.",
    ),
    DockerToolAction.MARKET_REBUILD_V2: DockerToolSpec(
        (
            "run", "--rm", "--no-deps", "market-tools", "rebuild", "v2",
        ),
        "market_tools",
        True,
        3_600.0,
        "Docker market v2 rebuild completed.",
    ),
})


def docker_tool_spec(action: DockerToolAction) -> DockerToolSpec:
    """Return the immutable command specification for one semantic action."""
    if not isinstance(action, DockerToolAction):
        raise ValueError("Docker Tool Deck action is not allowed.")
    return _SPECS[action]


class ManagedDockerToolController:
    """Execute only reviewed Tool Deck commands against one explicit target."""

    def __init__(
        self,
        target: ComposeTarget,
        inspector: Any,
        runner: Any,
        *,
        policy: DockerControlPolicy,
        expected_target_identity: str | None = None,
    ) -> None:
        if policy is not DockerControlPolicy.MANAGED:
            raise PermissionError("Docker Tool Deck operations require Managed policy.")
        self._target = target
        self._inspector = inspector
        self._runner = runner
        self._expected_target_identity = expected_target_identity

    def execute(self, action: DockerToolAction) -> DockerToolResult:
        """Preflight authoritatively, enforce stop guards, then execute once."""
        spec = docker_tool_spec(action)
        report = self._inspector.preflight(self._target)
        if (
            not getattr(report, "ok", False)
            or getattr(report, "config", None) is None
            or getattr(report, "records", None) is None
        ):
            return DockerToolResult(
                action,
                False,
                error=(
                    "Docker tool preflight failed. Check Docker Desktop and the "
                    "selected Compose project."
                ),
                target_identity=self._expected_target_identity,
            )

        capabilities = getattr(report.config, "capabilities", None)
        target_identity = docker_project_identity(
            self._target,
            getattr(report.config, "project_name", None),
            config=report.config,
        )
        if (
            self._expected_target_identity is not None
            and target_identity != self._expected_target_identity
        ):
            return DockerToolResult(
                action,
                False,
                error="Docker target changed before the tool operation could run.",
                target_identity=target_identity,
            )
        if not isinstance(capabilities, ComposeCapabilities):
            return DockerToolResult(
                action,
                False,
                error="Docker tool preflight returned unsupported capability data.",
                target_identity=target_identity,
            )
        if not getattr(capabilities, spec.required_capability, False):
            service = "Init" if spec.required_capability == "init" else "Market-tools"
            return DockerToolResult(
                action,
                False,
                error=f"{service} is not available in the effective Compose project.",
                target_identity=target_identity,
            )

        if spec.requires_services_stopped:
            records = report.records
            for service, label in (("server", "Server"), ("market", "Market")):
                if not _safely_stopped(records.get(service)):
                    return DockerToolResult(
                        action,
                        False,
                        error=(
                            f"{label} must be stopped before this Docker tool operation."
                        ),
                        target_identity=target_identity,
                    )

        try:
            argv = self._target.compose_args(
                self._runner.executable,
                *spec.command,
            )
            self._runner.run(
                argv,
                cwd=self._target.project_directory,
                timeout=spec.timeout,
            )
        except (DockerCommandError, OSError, ValueError):
            return DockerToolResult(
                action,
                False,
                error="Docker tool operation failed. Check Docker status and retry.",
                target_identity=target_identity,
            )
        return DockerToolResult(
            action,
            True,
            message=spec.success_message,
            target_identity=target_identity,
        )


def _safely_stopped(record: ContainerRecord | None) -> bool:
    """Accept only absent, created, exited, or dead authoritative records."""
    return (
        record is None
        or not record.exists
        or record.raw_state in {"created", "exited", "dead"}
    )
