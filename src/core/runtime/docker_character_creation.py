"""Closed managed-Compose character creation contract."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from src.core.client_autologin import LOCAL_DUMMY_PASSWORD
from src.core.character_creation import normalize_character_name
from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_cli import DockerCommandError, DockerCommandResult
from src.core.runtime.docker_compose import (
    ComposeCapabilities,
    ComposeConfig,
    ComposeTarget,
    ComposeValidationError,
    ContainerRecord,
    resolve_mount,
)
from src.core.service_status import DockerControlPolicy


_RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="
_ACCOUNT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}\Z")
_HELPER_NAME = "docker_create_character.js"
_HELPER_MOUNT = "/run/evejs-launcher/helpers"
_HELPER_PATH = f"{_HELPER_MOUNT}/{_HELPER_NAME}"
_BACKUP_MOUNT = "/run/evejs-launcher/backup"
_GAME_STORE_TARGET = "/var/lib/evejs/gameStore"
_COMMAND_TIMEOUT = 300.0
_MAX_INPUT_BYTES = 4 * 1024
_MAX_OUTPUT_BYTES = 16 * 1024
_SAFE_STOPPED_STATES = frozenset({"created", "exited", "dead"})
_REVIEWED_SERVICES = frozenset({"server", "market", "init"})
_EFFECTIVE_CONFIG_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class DockerCharacterCreationRequest:
    """The only user-controlled values admitted to helper stdin."""

    username: str
    character_name: str
    is_gm: bool = False


@dataclass(frozen=True)
class DockerCharacterCreationResult:
    """Private-safe semantic result; names and command output never escape."""

    succeeded: bool
    account_id: int | None = None
    character_id: int | None = None
    backup_created: bool | None = None
    rollback_confirmed: bool | None = None
    restart_safe: bool | None = None
    cleanup_confirmed: bool | None = None
    message: str = ""
    error: str = ""
    request_token: object | None = None
    target_identity: str | None = None


class ManagedDockerCharacterCreationController:
    """Create one character through one reviewed, offline Compose operation."""

    def __init__(
        self,
        target: ComposeTarget,
        inspector: Any,
        runner: Any,
        *,
        policy: DockerControlPolicy,
        expected_target_identity: str,
        helper_directory: Path,
        backup_directory: Path,
    ) -> None:
        if policy is not DockerControlPolicy.MANAGED:
            raise PermissionError(
                "Docker character creation requires Managed policy."
            )
        if (
            not isinstance(expected_target_identity, str)
            or not expected_target_identity.startswith("docker:")
        ):
            raise ValueError("A Docker target identity is required.")
        helper_path = Path(helper_directory)
        backup_path = Path(backup_directory)
        if not helper_path.is_absolute() or not backup_path.is_absolute():
            raise ValueError("Docker helper and backup directories must be absolute.")

        self._target = target
        self._inspector = inspector
        self._runner = runner
        self._expected_target_identity = expected_target_identity
        self._helper_directory = helper_path.resolve()
        self._backup_directory = backup_path

    def execute(
        self,
        request: DockerCharacterCreationRequest,
    ) -> DockerCharacterCreationResult:
        """Revalidate the target and offline matrix, then invoke the helper once."""
        normalized = _normalize_request(request)
        if normalized is None:
            return self._pre_mutation_failure(
                "Account or character details are invalid. Check the entered values."
            )

        report = self._inspector.preflight(self._target)
        if (
            not getattr(report, "ok", False)
            or not isinstance(getattr(report, "config", None), ComposeConfig)
            or not isinstance(getattr(report, "records", None), Mapping)
        ):
            return self._pre_mutation_failure(
                "Docker character preflight failed. Check Docker Desktop and the "
                "selected Compose project."
            )

        config = report.config
        effective_config_digest = _valid_effective_config_digest(config)
        if effective_config_digest is None:
            return self._pre_mutation_failure(
                "Docker character preflight did not provide a verifiable effective "
                "Compose configuration."
            )
        target_identity = docker_project_identity(
            self._target,
            config.project_name,
            config=config,
        )
        if target_identity != self._expected_target_identity:
            return self._pre_mutation_failure(
                "Docker target changed before character creation could run.",
                target_identity=target_identity,
            )
        if not self._supports_reviewed_contract(config):
            return self._pre_mutation_failure(
                "The selected Compose project does not provide the reviewed "
                "character-maintenance layout.",
                target_identity=target_identity,
            )

        # This transaction is reviewed only for the ordinary supported stack.
        # A profile/extension service that shares the selected GameStore could
        # otherwise write behind the helper's scoped backup.  Fail closed until
        # every effective service has its own explicit maintenance contract.
        if set(config.services) != _REVIEWED_SERVICES:
            return self._pre_mutation_failure(
                "The selected Compose project has additional services; Docker "
                "character creation is unavailable for this layout.",
                target_identity=target_identity,
            )
        if set(report.records) != _REVIEWED_SERVICES:
            return self._pre_mutation_failure(
                "The selected Compose project has additional service records; "
                "Docker character creation is unavailable for this layout.",
                target_identity=target_identity,
            )

        for service, label in (
            ("server", "Server"),
            ("market", "Market"),
            ("init", "Init"),
        ):
            if (
                not isinstance(report.records.get(service), ContainerRecord)
                or not _safely_stopped(report.records.get(service))
            ):
                return self._pre_mutation_failure(
                    f"{label} must be stopped before Docker character creation.",
                    target_identity=target_identity,
                )

        payload = _canonical_payload(normalized)
        if len(payload) > _MAX_INPUT_BYTES:
            return self._pre_mutation_failure(
                "Character details exceed the safe Docker request limit.",
                target_identity=target_identity,
            )
        frozen_records = tuple(
            (service, report.records[service]) for service in sorted(_REVIEWED_SERVICES)
        )
        final_report = self._inspector.preflight(self._target)
        if (
            not getattr(final_report, "ok", False)
            or not isinstance(getattr(final_report, "config", None), ComposeConfig)
            or not isinstance(getattr(final_report, "records", None), Mapping)
        ):
            return self._pre_mutation_failure(
                "Docker authority could not be confirmed immediately before "
                "character creation.",
                target_identity=target_identity,
            )
        final_config = final_report.config
        final_records = final_report.records
        final_identity = docker_project_identity(
            self._target,
            final_config.project_name,
            config=final_config,
        )
        final_record_snapshot = tuple(
            (service, final_records.get(service))
            for service in sorted(_REVIEWED_SERVICES)
        )
        if (
            _valid_effective_config_digest(final_config) != effective_config_digest
            or final_identity != target_identity
            or set(final_config.services) != _REVIEWED_SERVICES
            or set(final_records) != _REVIEWED_SERVICES
            or not self._supports_reviewed_contract(final_config)
            or final_record_snapshot != frozen_records
            or any(
                not isinstance(record, ContainerRecord) or not _safely_stopped(record)
                for _service, record in final_record_snapshot
            )
        ):
            return self._pre_mutation_failure(
                "Docker authority changed before character creation could run.",
                target_identity=target_identity,
            )

        # No filesystem mutation or helper launch is allowed until this second
        # effective-config and stopped-record authority snapshot is frozen.
        if not self._prepare_backup_directory():
            return self._pre_mutation_failure(
                "The scoped Docker character backup directory could not be "
                "prepared safely.",
                target_identity=target_identity,
            )

        try:
            argv = self._target.compose_args(
                self._runner.executable,
                *self._command(),
            )
            command_result = self._runner.run_with_input(
                argv,
                cwd=self._target.project_directory,
                input_bytes=payload,
                timeout=_COMMAND_TIMEOUT,
            )
        except (DockerCommandError, OSError, TypeError, ValueError):
            return self._failure(
                "Docker character creation failed. Keep services stopped until "
                "the game store is verified.",
                target_identity=target_identity,
            )

        if not isinstance(command_result, DockerCommandResult):
            return self._failure(
                "Docker character creation returned an unsupported command result.",
                target_identity=target_identity,
            )
        if (
            not command_result.ok
            or command_result.truncated
            or len(command_result.stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES
        ):
            return self._failure(
                "Docker character creation did not return a complete result. Keep "
                "services stopped until the game store is verified.",
                target_identity=target_identity,
            )

        marker = _parse_result_marker(command_result.stdout)
        if marker is None:
            return self._failure(
                "Docker character creation returned an unverifiable result. Keep "
                "services stopped until the game store is verified.",
                target_identity=target_identity,
            )
        if marker.get("ok") is not True:
            rollback = marker.get("rollbackSucceeded")
            rollback_confirmed = rollback if isinstance(rollback, bool) else None
            restart_safe = _optional_bool(marker.get("restartSafe"))
            error = (
                "Docker character creation failed; the scoped game-store backup "
                "was restored."
                if rollback_confirmed is True and restart_safe is True
                else "Docker character creation failed and automatic rollback was "
                "not confirmed. Keep services stopped and retain the backup."
            )
            return self._failure(
                error,
                target_identity=target_identity,
                backup_created=_optional_bool(marker.get("backupCreated")),
                rollback_confirmed=rollback_confirmed,
                restart_safe=restart_safe,
            )

        account_id = _positive_int(marker.get("accountId"))
        character_id = _positive_int(marker.get("characterId"))
        restart_safe = _optional_bool(marker.get("restartSafe"))
        cleanup_confirmed = _optional_bool(marker.get("cleanupConfirmed"))
        if (
            account_id is None
            or character_id is None
            or marker.get("rookieShipVerified") is not True
            or marker.get("backupCreated") is not True
            or cleanup_confirmed is None
            or restart_safe is not cleanup_confirmed
        ):
            return self._failure(
                "Docker character creation returned an unverifiable result. Keep "
                "services stopped until the game store is verified.",
                target_identity=target_identity,
            )
        return DockerCharacterCreationResult(
            True,
            account_id=account_id,
            character_id=character_id,
            backup_created=True,
            restart_safe=restart_safe,
            cleanup_confirmed=cleanup_confirmed,
            message=(
                "Docker character creation completed."
                if cleanup_confirmed
                else "Docker character creation committed; maintenance cleanup "
                "was not confirmed."
            ),
            target_identity=target_identity,
        )

    def _supports_reviewed_contract(self, config: ComposeConfig) -> bool:
        """Gate the narrow reviewed service, mount, and helper layout."""
        if not {"server", "market", "init"}.issubset(config.services):
            return False
        server_service = config.services["server"]
        if not server_service.image or server_service.pull_policy != "never":
            return False
        capabilities = config.capabilities
        if not isinstance(capabilities, ComposeCapabilities) or not capabilities.init:
            return False
        try:
            if any(
                _is_strict_container_descendant(
                    getattr(candidate, "target", ""),
                    _GAME_STORE_TARGET,
                )
                for candidate in server_service.mounts
            ):
                return False
            mount = resolve_mount(
                server_service.mounts,
                _GAME_STORE_TARGET,
            )
        except (ComposeValidationError, TypeError, ValueError):
            return False
        if (
            mount is None
            or mount.type.casefold() not in {"bind", "volume"}
            or not mount.source.strip()
        ):
            return False
        if mount.type.casefold() == "bind":
            live_game_store = _resolved_bind_container_path(
                mount.source,
                mount.target,
                _GAME_STORE_TARGET,
            )
            try:
                resolved_helper = self._helper_directory.resolve(strict=True)
                resolved_backup = self._backup_directory.resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                return False
            if (
                live_game_store is None
                or _paths_overlap(live_game_store, resolved_helper)
                or _paths_overlap(live_game_store, resolved_backup)
            ):
                return False
        if not self._helper_directory.is_dir():
            return False
        for filename in (
            _HELPER_NAME,
            "game_store_maintenance.js",
            "terminal_result.js",
        ):
            candidate = self._helper_directory / filename
            if not candidate.is_file() or candidate.resolve().parent != self._helper_directory:
                return False
        return True

    def _prepare_backup_directory(self) -> bool:
        """Create the scoped host mount only after every Docker authority gate."""
        try:
            unresolved_target = self._backup_directory.resolve(strict=False)
            if _paths_overlap(self._helper_directory, unresolved_target):
                return False
            self._backup_directory.mkdir(parents=True, exist_ok=True)
            if self._backup_directory.is_symlink():
                return False
            resolved = self._backup_directory.resolve(strict=True)
            if not resolved.is_dir() or _paths_overlap(
                self._helper_directory,
                resolved,
            ):
                return False
        except (OSError, RuntimeError, ValueError):
            return False
        self._backup_directory = resolved
        return True

    def _command(self) -> tuple[str, ...]:
        return (
            "run",
            "--pull",
            "never",
            "--rm",
            "--no-deps",
            "-T",
            "--user",
            "node",
            "--volume",
            "/app/server/logs",
            "--volume",
            f"{self._helper_directory}:{_HELPER_MOUNT}:ro",
            "--volume",
            f"{self._backup_directory}:{_BACKUP_MOUNT}:rw",
            "--env",
            "NODE_OPTIONS=",
            "--env",
            "EVEJS_LOG_LEVEL=0",
            "--env",
            "EVEJS_GAMESTORE_OWNER_ROLE=maintenance",
            "--env",
            "EVEJS_PERSISTENCE_OWNER_LEASE_MS=900000",
            "--env",
            "EVEJS_GAMESTORE_SQLITE_PATH=/var/lib/evejs/gameStore/gamestore.sqlite",
            "--env",
            "EVEJS_GAMESTORE_DATA_DIR=/var/lib/evejs/gameStore/data",
            "--entrypoint",
            "node",
            "--workdir",
            "/app",
            "server",
            _HELPER_PATH,
        )

    def _failure(
        self,
        error: str,
        *,
        target_identity: str | None = None,
        backup_created: bool | None = None,
        rollback_confirmed: bool | None = None,
        restart_safe: bool | None = None,
    ) -> DockerCharacterCreationResult:
        return DockerCharacterCreationResult(
            False,
            backup_created=backup_created,
            rollback_confirmed=rollback_confirmed,
            restart_safe=restart_safe,
            error=error,
            target_identity=(
                target_identity
                if target_identity is not None
                else self._expected_target_identity
            ),
        )

    def _pre_mutation_failure(
        self,
        error: str,
        *,
        target_identity: str | None = None,
    ) -> DockerCharacterCreationResult:
        """Confirm that no helper ran, so restoring prior services is safe."""
        return self._failure(
            error,
            target_identity=target_identity,
            backup_created=False,
            rollback_confirmed=True,
            restart_safe=True,
        )


def _normalize_request(
    request: DockerCharacterCreationRequest,
) -> DockerCharacterCreationRequest | None:
    if not isinstance(request, DockerCharacterCreationRequest):
        return None
    if not isinstance(request.username, str) or not isinstance(
        request.character_name, str
    ):
        return None
    if not isinstance(request.is_gm, bool):
        return None
    username = request.username.strip()
    character_name = normalize_character_name(request.character_name)
    if not _ACCOUNT_PATTERN.fullmatch(username):
        return None
    if character_name is None:
        return None
    return DockerCharacterCreationRequest(username, character_name, request.is_gm)


def _canonical_payload(request: DockerCharacterCreationRequest) -> bytes:
    return json.dumps(
        {
            "characterName": request.character_name,
            "isGM": request.is_gm,
            "password": LOCAL_DUMMY_PASSWORD,
            "username": request.username,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_result_marker(stdout: str) -> Mapping[str, object] | None:
    markers = [
        line[len(_RESULT_PREFIX) :]
        for line in stdout.splitlines()
        if line.startswith(_RESULT_PREFIX)
    ]
    if len(markers) != 1 or len(markers[0].encode("utf-8")) > _MAX_INPUT_BYTES:
        return None
    try:
        payload = json.loads(markers[0])
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _valid_effective_config_digest(config: ComposeConfig) -> str | None:
    value = config.effective_config_digest
    if not isinstance(value, str) or not _EFFECTIVE_CONFIG_DIGEST.fullmatch(value):
        return None
    return value


def _safely_stopped(record: ContainerRecord | None) -> bool:
    return (
        record is None
        or not record.exists
        or record.raw_state in _SAFE_STOPPED_STATES
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _normal_container_parts(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return PurePosixPath("/" + value.strip().lstrip("/")).parts


def _is_strict_container_descendant(candidate: object, parent: str) -> bool:
    candidate_parts = _normal_container_parts(candidate)
    parent_parts = _normal_container_parts(parent)
    return bool(
        candidate_parts
        and parent_parts
        and len(candidate_parts) > len(parent_parts)
        and candidate_parts[: len(parent_parts)] == parent_parts
    )


def _resolved_bind_container_path(
    source: str,
    mount_target: str,
    container_path: str,
) -> Path | None:
    target_parts = _normal_container_parts(mount_target)
    container_parts = _normal_container_parts(container_path)
    if (
        target_parts is None
        or container_parts is None
        or container_parts[: len(target_parts)] != target_parts
    ):
        return None
    source_path = Path(source)
    if not source_path.is_absolute():
        return None
    try:
        resolved = source_path.joinpath(
            *container_parts[len(target_parts) :]
        ).resolve(strict=True)
        return resolved if resolved.is_dir() else None
    except (OSError, RuntimeError, ValueError):
        return None
