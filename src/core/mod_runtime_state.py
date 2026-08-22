"""Verified runtime evidence for launcher-managed EveJS mods.

The module is intentionally independent from the launcher UI and process
workers. Native callers feed it bounded game-server stdout after discovery;
Docker callers feed it the selected launcher-owned Compose override. Snapshot
writes do **not** acquire the mod lifecycle lock: the caller must already own
the target root's lifecycle lock before calling :func:`write_mod_runtime_snapshot`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable

from .mod_manifest import ActivationKind, Mod
from .runtime.docker_mods import (
    DockerModOverride,
    build_docker_mod_override,
    docker_mod_override_path,
)


RUNTIME_SNAPSHOT_SCHEMA_VERSION = 1
STATUS_PROTOCOL = "evejs_mod_status_v1"
STATUS_TRANSPORT = "server_stdout"
STATUS_MARKER_PREFIX = "EVEJS_MOD_STATUS "
RUNTIME_SNAPSHOT_FILENAME = ".evejs-launcher-mod-runtime.json"

MAX_SERVER_CONSOLE_BYTES = 2 * 1024 * 1024
MAX_STATUS_LINE_BYTES = 4 * 1024
MAX_RUNTIME_SNAPSHOT_BYTES = 512 * 1024
MAX_LOADER_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_RUNTIME_IDENTITY_BYTES = 1024
MAX_DOCKER_OVERRIDE_BYTES = 512 * 1024
MAX_DOCKER_NODE_OPTIONS_BYTES = 128 * 1024

NATIVE_BACKEND = "native"
DOCKER_BACKEND = "docker_compose"
NATIVE_STATUS_EVIDENCE = "server_stdout"
NATIVE_LOADER_EVIDENCE = "native_mode"
DOCKER_OVERRIDE_EVIDENCE = "docker_override"

_VALID_MODES = frozenset({"vanilla", "modded"})
_VALID_STATES = frozenset({"running", "disabled"})
_SNAPSHOT_KEYS = frozenset(
    {
        "schemaVersion",
        "root",
        "backend",
        "mode",
        "runtimeIdentity",
        "planSha256",
        "dockerOverride",
        "dockerNodeOptionsSha256",
        "selectedLoaderIds",
        "pid",
        "observedAt",
        "mods",
    }
)
_SNAPSHOT_MOD_KEYS = frozenset(
    {"id", "activationKind", "contractSha256", "effective", "evidence"}
)
_DOCKER_OVERRIDE_KEYS = frozenset({"path", "sha256"})
_MARKER_KEYS = frozenset({"id", "pid", "state"})
_INTEGRATED_MOD_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIME_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z"
)
_UTF8_BOM = b"\xef\xbb\xbf"
# Keep this closed set identical to mod_manifest._LOADER_FILENAMES. The
# filename is mutable activation state; the bytes behind exactly one of these
# names are the immutable loader contract.
_LOADER_PAYLOAD_FILENAMES = (
    "loader.js",
    "loader.js.disabled",
    "loader.js.off",
    "loader.js.bak",
)


class ModRuntimeStateError(ValueError):
    """Runtime mod evidence is missing, contradictory, or unsafe."""


class ModStatusProtocolError(ModRuntimeStateError):
    """Native server stdout violates the declared status protocol."""


class ModRuntimeSnapshotError(ModRuntimeStateError):
    """A persisted runtime snapshot is invalid or cannot be written safely."""


@dataclass(frozen=True)
class ModStatusMarker:
    """One schema-v1 source-integration marker from server stdout."""

    id: str
    pid: int
    state: str

    @property
    def effective(self) -> bool:
        return self.state == "running"


@dataclass(frozen=True)
class ModRuntimePlanEntry:
    """One prelaunch mod contract and its configured activation state."""

    id: str
    activation_kind: ActivationKind
    configured_active: bool
    contract_sha256: str


@dataclass(frozen=True)
class ModRuntimePlan:
    """Immutable prelaunch authority for one exact runtime invocation."""

    root: Path
    backend: str
    mode: str
    runtime_identity: str
    mods: tuple[ModRuntimePlanEntry, ...]
    selected_loader_ids: tuple[str, ...]
    docker_override_path: Path | None
    docker_override_sha256: str | None
    docker_node_options: str | None
    plan_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "mods", tuple(self.mods))
        object.__setattr__(
            self,
            "selected_loader_ids",
            tuple(self.selected_loader_ids),
        )
        if self.docker_override_path is not None:
            object.__setattr__(
                self,
                "docker_override_path",
                Path(self.docker_override_path),
            )


@dataclass(frozen=True)
class RuntimeModEvidence:
    """Immutable contract identity and observed effective state for one mod."""

    id: str
    activation_kind: ActivationKind
    contract_sha256: str
    effective: bool
    evidence: str


@dataclass(frozen=True)
class ModRuntimeSnapshot:
    """A complete, verified mod-state observation for one EveJS runtime."""

    schema_version: int
    root: Path
    backend: str
    mode: str
    runtime_identity: str
    plan_sha256: str
    docker_override_path: Path | None
    docker_override_sha256: str | None
    docker_node_options_sha256: str | None
    selected_loader_ids: tuple[str, ...]
    pid: int | None
    observed_at: datetime
    mods: tuple[RuntimeModEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "mods", tuple(self.mods))
        object.__setattr__(
            self,
            "selected_loader_ids",
            tuple(self.selected_loader_ids),
        )
        if self.docker_override_path is not None:
            object.__setattr__(
                self,
                "docker_override_path",
                Path(self.docker_override_path),
            )

    def effective_for(self, mod: Mod) -> bool | None:
        """Return verified effectiveness, or ``None`` for an unknown contract.

        A matching ID is deliberately insufficient. Activation mechanism,
        immutable paths/metadata, and the schema-v2 status declaration are all
        fingerprinted so a changed manifest cannot inherit stale evidence.
        """

        if not mod.valid or not isinstance(mod.activation_kind, ActivationKind):
            return None
        try:
            mod_root = _canonical_mod_root(mod)
            snapshot_root = self.root.resolve(strict=True)
            if mod_root != snapshot_root:
                return None
            fingerprint = mod_contract_sha256(mod)
        except (OSError, ModRuntimeStateError, TypeError, ValueError):
            return None

        for evidence in self.mods:
            if evidence.id != mod.id:
                continue
            if evidence.activation_kind is not mod.activation_kind:
                return None
            if evidence.contract_sha256 != fingerprint:
                return None
            return evidence.effective
        return None


def read_server_console_bytes(
    path: str | Path,
    *,
    maximum_bytes: int = MAX_SERVER_CONSOLE_BYTES,
) -> bytes:
    """Read at most ``maximum_bytes + 1`` bytes from a regular console log.

    Oversized input is rejected rather than read wholesale. This helper is the
    intended boundary between an on-disk server console and the status parser.
    """

    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise ValueError("maximum_bytes must be a positive integer.")
    return _read_stable_bounded_file(
        Path(path),
        maximum_bytes,
        label="Server console",
        error_type=ModStatusProtocolError,
    )


def parse_native_status_markers(
    stdout: bytes,
    mods: Iterable[Mod],
    *,
    pid: int,
) -> dict[str, ModStatusMarker]:
    """Parse one exact PID-bound marker for every declared integrated mod.

    Unrelated stdout lines are ignored. A line beginning with the marker token
    must use exactly ``EVEJS_MOD_STATUS <json-object>`` and consume the whole
    line. Missing, duplicate, malformed, stale-PID, or unexpected markers fail
    the complete observation closed.
    """

    if type(stdout) is not bytes:
        raise TypeError("Native server stdout must be bytes.")
    if type(pid) is not int or pid < 1:
        raise ModStatusProtocolError("The current game-server PID must be positive.")
    if len(stdout) > MAX_SERVER_CONSOLE_BYTES:
        raise ModStatusProtocolError(
            f"Server stdout exceeds the {MAX_SERVER_CONSOLE_BYTES}-byte limit."
        )
    integrated = tuple(
        mod for mod in tuple(mods) if mod.activation_kind is ActivationKind.JSON_BOOLEAN
    )
    expected_ids: dict[str, Mod] = {}
    for mod in integrated:
        _require_status_declaration(mod)
        _require_integrated_mod_id(mod.id)
        if mod.id in expected_ids:
            raise ModStatusProtocolError(
                f"Integrated mod id {mod.id!r} was declared more than once."
            )
        expected_ids[mod.id] = mod

    markers: dict[str, ModStatusMarker] = {}
    marker_token = b"EVEJS_MOD_STATUS"
    marker_prefix = STATUS_MARKER_PREFIX.encode("ascii")
    for raw_line in stdout.split(b"\n"):
        line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
        if not line.startswith(marker_token):
            continue
        if len(line) > MAX_STATUS_LINE_BYTES:
            raise ModStatusProtocolError(
                "An EVEJS_MOD_STATUS line exceeds the "
                f"{MAX_STATUS_LINE_BYTES}-byte limit."
            )
        if not line.startswith(marker_prefix):
            raise ModStatusProtocolError("Malformed EVEJS_MOD_STATUS marker prefix.")
        payload_text = line[len(STATUS_MARKER_PREFIX) :]
        if payload_text != payload_text.strip() or not payload_text:
            raise ModStatusProtocolError(
                "EVEJS_MOD_STATUS payload must consume the complete stdout line."
            )
        payload = _parse_json_object(
            payload_text,
            "EVEJS_MOD_STATUS payload",
            error_type=ModStatusProtocolError,
        )
        _require_exact_keys(
            payload,
            _MARKER_KEYS,
            "EVEJS_MOD_STATUS payload",
            error_type=ModStatusProtocolError,
        )
        mod_id = payload["id"]
        marker_pid = payload["pid"]
        state = payload["state"]
        if type(mod_id) is not str or not _INTEGRATED_MOD_ID_PATTERN.fullmatch(mod_id):
            raise ModStatusProtocolError("Marker id is invalid.")
        if type(marker_pid) is not int or marker_pid < 1:
            raise ModStatusProtocolError("Marker pid must be a positive integer.")
        if marker_pid != pid:
            raise ModStatusProtocolError(
                f"Marker for {mod_id!r} does not belong to current PID {pid}."
            )
        if type(state) is not str or state not in _VALID_STATES:
            raise ModStatusProtocolError(
                "Marker state must be exactly 'running' or 'disabled'."
            )
        if mod_id not in expected_ids:
            raise ModStatusProtocolError(
                f"Unexpected integrated mod marker id {mod_id!r}."
            )
        if mod_id in markers:
            raise ModStatusProtocolError(
                f"Integrated mod {mod_id!r} emitted more than one marker."
            )
        markers[mod_id] = ModStatusMarker(mod_id, marker_pid, state)

    missing = sorted(set(expected_ids) - set(markers))
    if missing:
        raise ModStatusProtocolError(
            "Missing integrated mod status marker(s): " + ", ".join(missing) + "."
        )
    return markers


def build_mod_runtime_plan(
    evejs_root: str | Path,
    mods: Iterable[Mod],
    *,
    backend: object,
    mode: str,
    runtime_identity: str,
    selected_loader_ids: Iterable[str],
    docker_override_material: DockerModOverride | None = None,
) -> ModRuntimePlan:
    """Freeze every applicable prelaunch mod contract and loader selection."""

    root = _canonical_root(evejs_root)
    normalized_backend = _normalize_backend(backend)
    normalized_mode = _require_mode(mode)
    normalized_identity = _require_runtime_identity(runtime_identity)
    normalized_mods = _validate_mods_for_root(
        mods,
        root,
        backend=normalized_backend,
    )
    if normalized_backend == DOCKER_BACKEND and any(
        mod.activation_kind is not ActivationKind.LOADER_RENAME
        for mod in normalized_mods
    ):
        raise ModRuntimeStateError(
            "Docker runtime plans support only valid Docker loader mods."
        )
    selected = _validate_selected_loaders(
        normalized_mods,
        selected_loader_ids,
        mode=normalized_mode,
        backend=normalized_backend,
    )
    if normalized_backend == DOCKER_BACKEND:
        override_path, override_sha256, docker_node_options = _freeze_docker_override(
            root,
            selected,
            material=docker_override_material,
        )
    else:
        if docker_override_material is not None:
            raise ModRuntimeStateError(
                "Native runtime plans cannot bind Docker override material."
            )
        override_path = None
        override_sha256 = None
        docker_node_options = None
    entries = tuple(
        sorted(
            (
                ModRuntimePlanEntry(
                    id=mod.id,
                    activation_kind=mod.activation_kind,
                    configured_active=_require_configured_state(mod),
                    contract_sha256=mod_contract_sha256(mod),
                )
                for mod in normalized_mods
            ),
            key=lambda item: (item.id.casefold(), item.id),
        )
    )
    plan_without_hash = ModRuntimePlan(
        root=root,
        backend=normalized_backend,
        mode=normalized_mode,
        runtime_identity=normalized_identity,
        mods=entries,
        selected_loader_ids=tuple(selected),
        docker_override_path=override_path,
        docker_override_sha256=override_sha256,
        docker_node_options=docker_node_options,
        plan_sha256="0" * 64,
    )
    plan = ModRuntimePlan(
        root=root,
        backend=normalized_backend,
        mode=normalized_mode,
        runtime_identity=normalized_identity,
        mods=entries,
        selected_loader_ids=tuple(selected),
        docker_override_path=override_path,
        docker_override_sha256=override_sha256,
        docker_node_options=docker_node_options,
        plan_sha256=_compute_plan_sha256(plan_without_hash),
    )
    _validate_plan(plan)
    return plan


def validate_mod_runtime_plan(
    plan: ModRuntimePlan,
    *,
    backend: object | None = None,
) -> None:
    """Validate a plan before sharing it across launch and attestation code."""

    expected = None if backend is None else _normalize_backend(backend)
    _validate_plan(plan, expected_backend=expected)


def native_mod_preload_paths(plan: ModRuntimePlan) -> tuple[Path, ...]:
    """Return the exact ordered loader paths authorized by a Native plan."""

    _validate_plan(plan, expected_backend=NATIVE_BACKEND)
    paths: list[Path] = []
    for mod_id in plan.selected_loader_ids:
        path = plan.root / "mods" / mod_id / "loader.js"
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ModRuntimeStateError(
                    f"Native preload for {mod_id!r} is not a safe regular file."
                )
            path.resolve(strict=True).relative_to(plan.root)
        except ModRuntimeStateError:
            raise
        except (OSError, ValueError) as exc:
            raise ModRuntimeStateError(
                f"Native preload for {mod_id!r} is unavailable or unsafe."
            ) from exc
        paths.append(path)
    return tuple(paths)


def build_native_mod_runtime_snapshot(
    plan: ModRuntimePlan,
    mods: Iterable[Mod],
    stdout: bytes,
    *,
    pid: int,
    observed_at: datetime | None = None,
) -> ModRuntimeSnapshot:
    """Attest a Native start against its immutable plan and a fresh rescan."""

    _validate_plan(plan, expected_backend=NATIVE_BACKEND)
    rescanned = _validate_post_start_mods(plan, mods)
    markers = parse_native_status_markers(
        stdout,
        (item[0] for item in rescanned.values()),
        pid=pid,
    )
    selected = frozenset(plan.selected_loader_ids)

    entries: list[RuntimeModEvidence] = []
    for planned in plan.mods:
        mod, _ = rescanned[planned.id]
        if planned.activation_kind is ActivationKind.JSON_BOOLEAN:
            marker = markers[mod.id]
            if marker.effective is not planned.configured_active:
                raise ModRuntimeStateError(
                    f"Runtime marker for {mod.id!r} does not match planned state."
                )
            effective = marker.effective
            evidence = NATIVE_STATUS_EVIDENCE
        elif planned.activation_kind is ActivationKind.LOADER_RENAME:
            effective = mod.id in selected
            evidence = NATIVE_LOADER_EVIDENCE
        else:  # Defensive against future enum additions.
            raise ModRuntimeStateError(
                f"Unsupported activation kind for {mod.id!r}."
            )
        entries.append(
            _runtime_evidence_from_plan(planned, effective, evidence)
        )

    return _build_snapshot(
        root=plan.root,
        backend=NATIVE_BACKEND,
        mode=plan.mode,
        runtime_identity=plan.runtime_identity,
        plan_sha256=plan.plan_sha256,
        docker_override_path=None,
        docker_override_sha256=None,
        docker_node_options_sha256=None,
        selected_loader_ids=plan.selected_loader_ids,
        pid=pid,
        observed_at=observed_at,
        entries=entries,
    )


def build_docker_mod_runtime_snapshot(
    plan: ModRuntimePlan,
    mods: Iterable[Mod],
    *,
    effective_node_options_sha256: str,
    runtime_identity: str,
    pid: int | None = None,
    observed_at: datetime | None = None,
) -> ModRuntimeSnapshot:
    """Attest a Docker override start against its plan and a fresh rescan."""

    _validate_plan(plan, expected_backend=DOCKER_BACKEND)
    observed_runtime_identity = _require_runtime_identity(runtime_identity)
    rescanned = _validate_post_start_mods(plan, mods)
    _verify_docker_override(plan)
    if (
        type(effective_node_options_sha256) is not str
        or not _SHA256_PATTERN.fullmatch(effective_node_options_sha256)
    ):
        raise ModRuntimeStateError(
            "Effective Docker NODE_OPTIONS SHA-256 is invalid."
        )
    expected_node_options_sha256 = hashlib.sha256(
        (plan.docker_node_options or "").encode("utf-8")
    ).hexdigest()
    if effective_node_options_sha256 != expected_node_options_sha256:
        raise ModRuntimeStateError(
            "Effective Docker NODE_OPTIONS does not match the runtime plan."
        )
    if pid is not None and (type(pid) is not int or pid < 1):
        raise ModRuntimeStateError("Docker PID must be null or a positive integer.")
    selected = frozenset(plan.selected_loader_ids)

    entries = [
        _runtime_evidence_from_plan(
            planned,
            planned.id in selected,
            DOCKER_OVERRIDE_EVIDENCE,
        )
        for planned in plan.mods
    ]
    return _build_snapshot(
        root=plan.root,
        backend=DOCKER_BACKEND,
        mode=plan.mode,
        runtime_identity=observed_runtime_identity,
        plan_sha256=plan.plan_sha256,
        docker_override_path=plan.docker_override_path,
        docker_override_sha256=plan.docker_override_sha256,
        docker_node_options_sha256=effective_node_options_sha256,
        selected_loader_ids=plan.selected_loader_ids,
        pid=pid,
        observed_at=observed_at,
        entries=entries,
    )


def mod_contract_sha256(mod: Mod) -> str:
    """Hash immutable discovery/activation metadata, excluding configured state."""

    if not mod.valid:
        raise ModRuntimeStateError(f"Cannot fingerprint invalid mod {mod.id!r}.")
    if not isinstance(mod.activation_kind, ActivationKind):
        raise ModRuntimeStateError(f"Mod {mod.id!r} has an invalid activation kind.")
    _require_mod_identity(mod.id, mod.activation_kind)
    root = _canonical_mod_root(mod)
    common: dict[str, object] = {
        "id": mod.id,
        "name": mod.name,
        "version": mod.version,
        "description": mod.description,
        "activationKind": mod.activation_kind.value,
        "supportedBackends": list(mod.supported_backends),
        "restartScope": mod.restart_scope,
        "path": _relative_contract_path(mod.path, root, "mod path"),
    }
    if mod.activation_kind is ActivationKind.JSON_BOOLEAN:
        _require_status_declaration(mod)
        if mod.manifest_path is None or mod.config_path is None:
            raise ModRuntimeStateError(
                f"Integrated mod {mod.id!r} has incomplete contract paths."
            )
        if mod.config_key != "enabled" or not mod.allowed_config_schema_versions:
            raise ModRuntimeStateError(
                f"Integrated mod {mod.id!r} has incomplete activation metadata."
            )
        common["activation"] = {
            "manifestPath": _relative_contract_path(
                mod.manifest_path, root, "manifest path"
            ),
            "configPath": _relative_contract_path(
                mod.config_path, root, "configuration path"
            ),
            "property": mod.config_key,
            "allowedConfigSchemaVersions": list(
                mod.allowed_config_schema_versions
            ),
            "statusProtocol": getattr(mod, "status_protocol", None),
            "statusTransport": getattr(mod, "status_transport", None),
        }
    else:
        common["activation"] = {
            "loaderPath": _relative_contract_path(
                mod.path / "loader.js", root, "loader path"
            ),
            "loaderPayloadSha256": _loader_payload_sha256(mod, root),
        }
    try:
        encoded = json.dumps(
            common,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModRuntimeStateError(
            f"Mod {mod.id!r} has non-serializable contract metadata."
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def mod_runtime_snapshot_path(evejs_root: str | Path) -> Path:
    """Return the fixed per-root runtime snapshot path."""

    return Path(evejs_root).resolve() / "_local" / RUNTIME_SNAPSHOT_FILENAME


def write_mod_runtime_snapshot(snapshot: ModRuntimeSnapshot) -> Path:
    """Atomically persist a snapshot; the caller must own the lifecycle lock."""

    root = _canonical_root(snapshot.root)
    _validate_snapshot(snapshot, expected_root=root, expected_backend=snapshot.backend)
    local_directory = root / "_local"
    try:
        if local_directory.exists() or local_directory.is_symlink():
            metadata = local_directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ModRuntimeSnapshotError(
                    "The EveJS _local path is not a safe directory."
                )
        else:
            local_directory.mkdir()
        _require_within_root(local_directory, root, "Snapshot directory")
    except ModRuntimeSnapshotError:
        raise
    except OSError as exc:
        raise ModRuntimeSnapshotError(
            "The EveJS runtime snapshot directory is unavailable."
        ) from exc

    destination = local_directory / RUNTIME_SNAPSHOT_FILENAME
    if destination.exists() or destination.is_symlink():
        try:
            metadata = destination.lstat()
        except OSError as exc:
            raise ModRuntimeSnapshotError(
                "The existing runtime snapshot could not be inspected."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ModRuntimeSnapshotError(
                "The existing runtime snapshot is not a safe regular file."
            )

    payload = _snapshot_payload(snapshot)
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    if len(content) > MAX_RUNTIME_SNAPSHOT_BYTES:
        raise ModRuntimeSnapshotError("The runtime snapshot exceeds its size limit.")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{RUNTIME_SNAPSHOT_FILENAME}.",
        suffix=".tmp",
        dir=local_directory,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise ModRuntimeSnapshotError(
            "The runtime snapshot could not be committed atomically."
        ) from exc
    finally:
        try:
            temporary_path.unlink()
        except OSError:
            pass
    return destination


def read_mod_runtime_snapshot(
    evejs_root: str | Path,
    *,
    backend: object,
    runtime_identity: str | None = None,
    pid: int | None = None,
) -> ModRuntimeSnapshot | None:
    """Safely read a snapshot, returning ``None`` for corrupt or stale evidence.

    Omitting both identity and PID permits historical comparison. Current
    Native evidence requires the exact runtime identity and PID; current Docker
    evidence requires the exact identity. A PID alone is never current proof.
    """

    try:
        root = _canonical_root(evejs_root)
        expected_backend = _normalize_backend(backend)
        if runtime_identity is not None:
            expected_identity = _require_runtime_identity(
                runtime_identity,
                error_type=ModRuntimeSnapshotError,
            )
        else:
            expected_identity = None
        if pid is not None and (type(pid) is not int or pid < 1):
            raise ModRuntimeSnapshotError(
                "Expected runtime PID must be null or a positive integer."
            )
        path = root / "_local" / RUNTIME_SNAPSHOT_FILENAME
        _require_safe_snapshot_file(path, root)
        content = _read_bounded_file(path, MAX_RUNTIME_SNAPSHOT_BYTES)
        payload = _parse_json_object(
            content,
            "Runtime snapshot",
            error_type=ModRuntimeSnapshotError,
        )
        snapshot = _snapshot_from_payload(
            payload,
            expected_root=root,
            expected_backend=expected_backend,
        )
        if expected_identity is None and pid is not None:
            return None
        if expected_identity is not None:
            if snapshot.runtime_identity != expected_identity:
                return None
            if expected_backend == NATIVE_BACKEND and pid is None:
                return None
        if pid is not None and snapshot.pid != pid:
            return None
        return snapshot
    except (OSError, ModRuntimeStateError, TypeError, ValueError):
        return None


def _build_snapshot(
    *,
    root: Path,
    backend: str,
    mode: str,
    runtime_identity: str,
    plan_sha256: str,
    docker_override_path: Path | None,
    docker_override_sha256: str | None,
    docker_node_options_sha256: str | None,
    selected_loader_ids: tuple[str, ...],
    pid: int | None,
    observed_at: datetime | None,
    entries: Iterable[RuntimeModEvidence],
) -> ModRuntimeSnapshot:
    snapshot = ModRuntimeSnapshot(
        schema_version=RUNTIME_SNAPSHOT_SCHEMA_VERSION,
        root=root,
        backend=backend,
        mode=mode,
        runtime_identity=runtime_identity,
        plan_sha256=plan_sha256,
        docker_override_path=docker_override_path,
        docker_override_sha256=docker_override_sha256,
        docker_node_options_sha256=docker_node_options_sha256,
        selected_loader_ids=selected_loader_ids,
        pid=pid,
        observed_at=_normalize_time(observed_at),
        mods=tuple(sorted(entries, key=lambda item: (item.id.casefold(), item.id))),
    )
    _validate_snapshot(snapshot, expected_root=root, expected_backend=backend)
    return snapshot


def _runtime_evidence_from_plan(
    planned: ModRuntimePlanEntry,
    effective: bool,
    evidence: str,
) -> RuntimeModEvidence:
    if type(effective) is not bool:
        raise ModRuntimeStateError("Effective mod state must be a boolean.")
    return RuntimeModEvidence(
        id=planned.id,
        activation_kind=planned.activation_kind,
        contract_sha256=planned.contract_sha256,
        effective=effective,
        evidence=evidence,
    )


def _validate_post_start_mods(
    plan: ModRuntimePlan,
    mods: Iterable[Mod],
) -> dict[str, tuple[Mod, ModRuntimePlanEntry]]:
    """Require a fresh discovery to match the complete prelaunch plan."""

    normalized = _validate_mods_for_root(mods, plan.root, backend=plan.backend)
    current = {mod.id: mod for mod in normalized}
    planned = {item.id: item for item in plan.mods}
    if set(current) != set(planned):
        raise ModRuntimeStateError(
            "Post-start mod discovery does not match the prelaunch plan."
        )

    matched: dict[str, tuple[Mod, ModRuntimePlanEntry]] = {}
    for mod_id, entry in planned.items():
        mod = current[mod_id]
        if mod.activation_kind is not entry.activation_kind:
            raise ModRuntimeStateError(
                f"Activation kind for {mod_id!r} drifted after launch."
            )
        if _require_configured_state(mod) is not entry.configured_active:
            raise ModRuntimeStateError(
                f"Configured state for {mod_id!r} drifted after launch."
            )
        if mod_contract_sha256(mod) != entry.contract_sha256:
            raise ModRuntimeStateError(
                f"Immutable contract for {mod_id!r} drifted after launch."
            )
        matched[mod_id] = (mod, entry)
    return matched


def _validate_mods_for_root(
    mods: Iterable[Mod],
    root: Path,
    *,
    backend: str,
) -> tuple[Mod, ...]:
    normalized = tuple(mods)
    seen: set[str] = set()
    for mod in normalized:
        if not isinstance(mod, Mod):
            raise ModRuntimeStateError("Runtime snapshots accept only discovered mods.")
        if not mod.valid:
            raise ModRuntimeStateError(
                f"Cannot build runtime snapshot while mod {mod.id!r} is invalid."
            )
        _require_mod_identity(mod.id, mod.activation_kind)
        _require_configured_state(mod)
        folded = mod.id.casefold()
        if folded in seen:
            raise ModRuntimeStateError("Discovered mod ids must be unique.")
        seen.add(folded)
        if _canonical_mod_root(mod) != root:
            raise ModRuntimeStateError(
                f"Mod {mod.id!r} belongs to a different EveJS root."
            )
        if mod.activation_kind is ActivationKind.LOADER_RENAME:
            if backend == DOCKER_BACKEND and mod.id != mod.id.strip():
                raise ModRuntimeStateError(
                    f"Docker loader id {mod.id!r} must be trimmed exactly."
                )
            expected_path = root / "mods" / mod.id
            try:
                actual_path = mod.path.resolve(strict=True)
                expected_path = expected_path.resolve(strict=True)
            except OSError as exc:
                raise ModRuntimeStateError(
                    f"Loader mod {mod.id!r} has an unsafe folder path."
                ) from exc
            if actual_path != expected_path or mod.path.name != mod.id:
                raise ModRuntimeStateError(
                    f"Loader mod {mod.id!r} does not match its root folder."
                )
        support_name = "docker" if backend == DOCKER_BACKEND else NATIVE_BACKEND
        if not mod.supports_backend(support_name):
            raise ModRuntimeStateError(
                f"Mod {mod.id!r} does not support the {backend} backend."
            )
        if mod.activation_kind is ActivationKind.JSON_BOOLEAN:
            _require_status_declaration(mod)
    return normalized


def _validate_selected_loaders(
    mods: tuple[Mod, ...],
    selected_mods: Iterable[str],
    *,
    mode: str,
    backend: str,
) -> tuple[str, ...]:
    """Validate the exact loader IDs consumed by the launched runtime."""

    loaders = {
        mod.id: mod
        for mod in mods
        if mod.activation_kind is ActivationKind.LOADER_RENAME
    }
    selected: set[str] = set()
    selected_folded: set[str] = set()
    try:
        requested = tuple(selected_mods)
    except TypeError as exc:
        raise ModRuntimeStateError("Selected loader mods must be iterable.") from exc
    for mod_id in requested:
        _require_loader_mod_id(mod_id)
        folded = mod_id.casefold()
        if folded in selected_folded:
            raise ModRuntimeStateError("Selected loader ids must be unique.")
        if mod_id not in loaders:
            raise ModRuntimeStateError(
                f"The {backend} launch plan contains unexpected loader {mod_id!r}."
            )
        selected.add(mod_id)
        selected_folded.add(folded)

    if mode == "vanilla" and selected:
        raise ModRuntimeStateError("A vanilla launch plan cannot preload mods.")
    expected = {
        mod.id
        for mod in loaders.values()
        if mode == "modded" and _require_configured_state(mod)
    }
    if selected != expected:
        raise ModRuntimeStateError(
            f"The {backend} launch plan does not match configured loader state."
        )
    return requested


def _validate_plan(
    plan: ModRuntimePlan,
    *,
    expected_backend: str | None = None,
) -> None:
    if not isinstance(plan, ModRuntimePlan):
        raise ModRuntimeStateError("A ModRuntimePlan is required.")
    root = _canonical_root(plan.root)
    if plan.root != root:
        raise ModRuntimeStateError("Runtime plan root is not canonical.")
    backend = _normalize_backend(plan.backend)
    if expected_backend is not None and backend != expected_backend:
        raise ModRuntimeStateError(
            f"Runtime plan backend must be {expected_backend!r}."
        )
    mode = _require_mode(plan.mode)
    _require_runtime_identity(plan.runtime_identity)
    if type(plan.plan_sha256) is not str or not _SHA256_PATTERN.fullmatch(
        plan.plan_sha256
    ):
        raise ModRuntimeStateError("Runtime plan SHA-256 is invalid.")

    if any(not isinstance(item, ModRuntimePlanEntry) for item in plan.mods):
        raise ModRuntimeStateError("Runtime plan entries are invalid.")
    expected_order = sorted(plan.mods, key=lambda item: (item.id.casefold(), item.id))
    if list(plan.mods) != expected_order:
        raise ModRuntimeStateError("Runtime plan mods are not deterministic.")
    seen: set[str] = set()
    loaders: dict[str, ModRuntimePlanEntry] = {}
    for entry in plan.mods:
        _require_mod_identity(entry.id, entry.activation_kind)
        folded = entry.id.casefold()
        if folded in seen:
            raise ModRuntimeStateError("Runtime plan mod ids must be unique.")
        seen.add(folded)
        if type(entry.configured_active) is not bool:
            raise ModRuntimeStateError("Planned configured state must be a boolean.")
        if type(entry.contract_sha256) is not str or not _SHA256_PATTERN.fullmatch(
            entry.contract_sha256
        ):
            raise ModRuntimeStateError("Runtime plan contract SHA-256 is invalid.")
        if entry.activation_kind is ActivationKind.LOADER_RENAME:
            loaders[entry.id] = entry
        elif backend == DOCKER_BACKEND:
            raise ModRuntimeStateError("Docker runtime plans may contain only loaders.")

    selected: set[str] = set()
    selected_folded: set[str] = set()
    for mod_id in plan.selected_loader_ids:
        _require_loader_mod_id(mod_id)
        folded = mod_id.casefold()
        if folded in selected_folded:
            raise ModRuntimeStateError("Planned loader ids must be unique.")
        if mod_id not in loaders:
            raise ModRuntimeStateError("Runtime plan selects an unknown loader.")
        selected.add(mod_id)
        selected_folded.add(folded)
    expected_selected = {
        entry.id
        for entry in loaders.values()
        if mode == "modded" and entry.configured_active
    }
    if mode == "vanilla" and selected:
        raise ModRuntimeStateError("A vanilla runtime plan cannot preload mods.")
    if selected != expected_selected:
        raise ModRuntimeStateError(
            "Runtime plan loader selection does not match configured state."
        )
    _validate_docker_override_binding(
        root=root,
        backend=backend,
        mode=mode,
        selected_loader_ids=plan.selected_loader_ids,
        override_path=plan.docker_override_path,
        override_sha256=plan.docker_override_sha256,
        docker_node_options=plan.docker_node_options,
    )
    if _compute_plan_sha256(plan) != plan.plan_sha256:
        raise ModRuntimeStateError("Runtime plan SHA-256 does not match its contract.")


def _compute_plan_sha256(plan: ModRuntimePlan) -> str:
    payload = {
        "root": str(plan.root),
        "backend": plan.backend,
        "mode": plan.mode,
        "runtimeIdentity": plan.runtime_identity,
        "mods": [
            {
                "id": entry.id,
                "activationKind": entry.activation_kind.value,
                "configuredActive": entry.configured_active,
                "contractSha256": entry.contract_sha256,
            }
            for entry in plan.mods
        ],
        "selectedLoaderIds": list(plan.selected_loader_ids),
        "dockerOverride": (
            None
            if plan.docker_override_path is None
            else {
                "path": str(plan.docker_override_path),
                "sha256": plan.docker_override_sha256,
            }
        ),
        "dockerNodeOptions": plan.docker_node_options,
    }
    try:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ModRuntimeStateError("Runtime plan cannot be serialized.") from exc
    return hashlib.sha256(content).hexdigest()


def _require_configured_state(mod: Mod) -> bool:
    if type(mod.active) is not bool:
        raise ModRuntimeStateError(
            f"Configured state for mod {mod.id!r} must be a boolean."
        )
    return mod.active


def _freeze_docker_override(
    root: Path,
    selected_loader_ids: tuple[str, ...],
    *,
    material: DockerModOverride | None = None,
) -> tuple[Path, str, str]:
    path = docker_mod_override_path(root)
    try:
        expected = build_docker_mod_override(root, selected_loader_ids)
    except (OSError, TypeError, ValueError) as exc:
        raise ModRuntimeStateError(
            "The selected Docker loaders cannot produce a safe override."
        ) from exc
    if expected.selected_mods != selected_loader_ids:
        raise ModRuntimeStateError(
            "Rendered Docker loader selection diverges from the runtime plan."
        )
    _require_docker_node_options(expected.node_options)
    if material is not None:
        if not isinstance(material, DockerModOverride) or material != expected:
            raise ModRuntimeStateError(
                "Candidate Docker override material does not match the selected loaders."
            )
        # Candidate plans are frozen before mutating Compose state. Every mod
        # contract has already been fingerprinted above, while this exact
        # deterministic renderer output is what Apply is allowed to publish.
        return path, expected.content_hash, expected.node_options

    _require_safe_docker_override_parent(path, root, allow_missing=False)
    content = _read_stable_bounded_file(
        path,
        MAX_DOCKER_OVERRIDE_BYTES,
        label="Docker mod override",
        error_type=ModRuntimeStateError,
    )
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if (
        actual_sha256 != expected.content_hash
        or content != expected.content.encode("utf-8")
    ):
        raise ModRuntimeStateError(
            "Installed Docker mod override does not match the selected loaders."
        )
    return path, actual_sha256, expected.node_options


def _verify_docker_override(plan: ModRuntimePlan) -> None:
    path = plan.docker_override_path
    if path is None or plan.docker_override_sha256 is None:
        raise ModRuntimeStateError("Docker plan has no override binding.")
    _require_safe_docker_override_parent(path, plan.root, allow_missing=False)
    content = _read_stable_bounded_file(
        path,
        MAX_DOCKER_OVERRIDE_BYTES,
        label="Docker mod override",
        error_type=ModRuntimeStateError,
    )
    if hashlib.sha256(content).hexdigest() != plan.docker_override_sha256:
        raise ModRuntimeStateError("Docker mod override drifted after launch.")


def _validate_docker_override_binding(
    *,
    root: Path,
    backend: str,
    mode: str,
    selected_loader_ids: tuple[str, ...],
    override_path: Path | None,
    override_sha256: str | None,
    docker_node_options: str | None,
    docker_node_options_sha256: str | None = None,
) -> None:
    if backend == NATIVE_BACKEND:
        if (
            override_path is not None
            or override_sha256 is not None
            or docker_node_options is not None
            or docker_node_options_sha256 is not None
        ):
            raise ModRuntimeStateError(
                "Native runtime plans cannot contain a Docker override binding."
            )
        return
    expected_path = docker_mod_override_path(root)
    if override_path != expected_path:
        raise ModRuntimeStateError("Docker override path is not canonical.")
    if type(override_sha256) is not str or not _SHA256_PATTERN.fullmatch(
        override_sha256
    ):
        raise ModRuntimeStateError("Docker override SHA-256 is invalid.")
    try:
        expected_override = build_docker_mod_override(
            root,
            selected_loader_ids,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ModRuntimeStateError(
            "Docker override binding cannot be rendered safely."
        ) from exc
    if expected_override.selected_mods != selected_loader_ids:
        raise ModRuntimeStateError(
            "Rendered Docker loader selection diverges from the runtime plan."
        )
    if override_sha256 != expected_override.content_hash:
        raise ModRuntimeStateError(
            "Docker override SHA-256 does not match selected loaders."
        )
    expected_node_options = expected_override.node_options
    expected_node_options_sha256 = hashlib.sha256(
        expected_node_options.encode("utf-8")
    ).hexdigest()
    if docker_node_options is not None:
        _require_docker_node_options(docker_node_options)
        if docker_node_options != expected_node_options:
            raise ModRuntimeStateError(
                "Docker NODE_OPTIONS binding does not match selected loaders."
            )
        if docker_node_options_sha256 is not None:
            raise ModRuntimeStateError(
                "Docker NODE_OPTIONS binding cannot contain raw and hashed forms."
            )
    elif docker_node_options_sha256 != expected_node_options_sha256:
        raise ModRuntimeStateError(
            "Docker NODE_OPTIONS SHA-256 does not match selected loaders."
        )


def _require_safe_docker_override_parent(
    path: Path,
    root: Path,
    *,
    allow_missing: bool,
) -> None:
    parent = path.parent
    try:
        metadata = parent.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise ModRuntimeStateError("Docker override directory is missing.")
    except OSError as exc:
        raise ModRuntimeStateError(
            "Docker override directory could not be inspected."
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ModRuntimeStateError("Docker override directory is unsafe.")
    try:
        parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ModRuntimeStateError(
            "Docker override directory escapes the EveJS root."
        ) from exc


def _require_status_declaration(mod: Mod) -> None:
    if (
        getattr(mod, "status_protocol", None) != STATUS_PROTOCOL
        or getattr(mod, "status_transport", None) != STATUS_TRANSPORT
    ):
        raise ModStatusProtocolError(
            f"Integrated mod {mod.id!r} does not declare the supported status protocol."
        )


def _snapshot_payload(snapshot: ModRuntimeSnapshot) -> dict[str, object]:
    return {
        "schemaVersion": snapshot.schema_version,
        "root": str(snapshot.root),
        "backend": snapshot.backend,
        "mode": snapshot.mode,
        "runtimeIdentity": snapshot.runtime_identity,
        "planSha256": snapshot.plan_sha256,
        "dockerOverride": (
            None
            if snapshot.docker_override_path is None
            else {
                "path": str(snapshot.docker_override_path),
                "sha256": snapshot.docker_override_sha256,
            }
        ),
        "dockerNodeOptionsSha256": snapshot.docker_node_options_sha256,
        "selectedLoaderIds": list(snapshot.selected_loader_ids),
        "pid": snapshot.pid,
        "observedAt": _format_time(snapshot.observed_at),
        "mods": [
            {
                "id": item.id,
                "activationKind": item.activation_kind.value,
                "contractSha256": item.contract_sha256,
                "effective": item.effective,
                "evidence": item.evidence,
            }
            for item in snapshot.mods
        ],
    }


def _snapshot_from_payload(
    payload: dict[str, object],
    *,
    expected_root: Path,
    expected_backend: str,
) -> ModRuntimeSnapshot:
    _require_exact_keys(
        payload,
        _SNAPSHOT_KEYS,
        "Runtime snapshot",
        error_type=ModRuntimeSnapshotError,
    )
    schema_version = payload["schemaVersion"]
    root_value = payload["root"]
    backend = payload["backend"]
    mode = payload["mode"]
    runtime_identity = payload["runtimeIdentity"]
    plan_sha256 = payload["planSha256"]
    raw_docker_override = payload["dockerOverride"]
    docker_node_options_sha256 = payload["dockerNodeOptionsSha256"]
    raw_selected_loader_ids = payload["selectedLoaderIds"]
    pid = payload["pid"]
    observed_value = payload["observedAt"]
    raw_mods = payload["mods"]
    if type(schema_version) is not int or schema_version != RUNTIME_SNAPSHOT_SCHEMA_VERSION:
        raise ModRuntimeSnapshotError("Unsupported runtime snapshot schemaVersion.")
    if type(root_value) is not str or not root_value:
        raise ModRuntimeSnapshotError("Runtime snapshot root is invalid.")
    try:
        stored_root = Path(root_value).resolve(strict=True)
    except OSError as exc:
        raise ModRuntimeSnapshotError("Runtime snapshot root is stale.") from exc
    if stored_root != expected_root:
        raise ModRuntimeSnapshotError("Runtime snapshot belongs to another EveJS root.")
    if type(backend) is not str or backend != expected_backend:
        raise ModRuntimeSnapshotError("Runtime snapshot backend is stale.")
    normalized_mode = _require_mode(mode)
    normalized_identity = _require_runtime_identity(
        runtime_identity,
        error_type=ModRuntimeSnapshotError,
    )
    if type(plan_sha256) is not str or not _SHA256_PATTERN.fullmatch(plan_sha256):
        raise ModRuntimeSnapshotError("Runtime snapshot plan SHA-256 is invalid.")
    if raw_docker_override is None:
        override_path = None
        override_sha256 = None
    else:
        if type(raw_docker_override) is not dict:
            raise ModRuntimeSnapshotError(
                "Runtime snapshot Docker override binding is invalid."
            )
        _require_exact_keys(
            raw_docker_override,
            _DOCKER_OVERRIDE_KEYS,
            "Runtime snapshot Docker override",
            error_type=ModRuntimeSnapshotError,
        )
        raw_override_path = raw_docker_override["path"]
        override_sha256 = raw_docker_override["sha256"]
        if type(raw_override_path) is not str or not raw_override_path:
            raise ModRuntimeSnapshotError("Docker override path is invalid.")
        override_path = Path(raw_override_path)
    if type(raw_selected_loader_ids) is not list:
        raise ModRuntimeSnapshotError(
            "Runtime snapshot selectedLoaderIds must be an array."
        )
    selected_loader_ids: list[str] = []
    selected_seen: set[str] = set()
    for mod_id in raw_selected_loader_ids:
        _require_loader_mod_id(mod_id, error_type=ModRuntimeSnapshotError)
        folded = mod_id.casefold()
        if folded in selected_seen:
            raise ModRuntimeSnapshotError(
                "Runtime snapshot selected loader ids must be unique."
            )
        selected_seen.add(folded)
        selected_loader_ids.append(mod_id)
    if pid is not None and (type(pid) is not int or pid < 1):
        raise ModRuntimeSnapshotError("Runtime snapshot PID is invalid.")
    if backend == NATIVE_BACKEND and (type(pid) is not int or pid < 1):
        raise ModRuntimeSnapshotError("Native runtime snapshot PID is invalid.")
    if type(observed_value) is not str:
        raise ModRuntimeSnapshotError("Runtime snapshot observation time is invalid.")
    observed_at = _parse_time(observed_value)
    if type(raw_mods) is not list:
        raise ModRuntimeSnapshotError("Runtime snapshot mods must be an array.")

    entries: list[RuntimeModEvidence] = []
    for raw_entry in raw_mods:
        if type(raw_entry) is not dict:
            raise ModRuntimeSnapshotError("Runtime snapshot mod entry must be an object.")
        _require_exact_keys(
            raw_entry,
            _SNAPSHOT_MOD_KEYS,
            "Runtime snapshot mod entry",
            error_type=ModRuntimeSnapshotError,
        )
        mod_id = raw_entry["id"]
        activation_value = raw_entry["activationKind"]
        fingerprint = raw_entry["contractSha256"]
        effective = raw_entry["effective"]
        evidence = raw_entry["evidence"]
        if type(mod_id) is not str:
            raise ModRuntimeSnapshotError("Runtime snapshot mod id is invalid.")
        if type(activation_value) is not str:
            raise ModRuntimeSnapshotError("Runtime activation kind is invalid.")
        try:
            activation_kind = ActivationKind(activation_value)
        except ValueError as exc:
            raise ModRuntimeSnapshotError("Runtime activation kind is unsupported.") from exc
        _require_mod_identity(
            mod_id,
            activation_kind,
            error_type=ModRuntimeSnapshotError,
        )
        if type(fingerprint) is not str or not _SHA256_PATTERN.fullmatch(fingerprint):
            raise ModRuntimeSnapshotError("Runtime contract fingerprint is invalid.")
        if type(effective) is not bool:
            raise ModRuntimeSnapshotError("Runtime effective state must be a boolean.")
        if type(evidence) is not str:
            raise ModRuntimeSnapshotError("Runtime evidence is invalid.")
        _validate_evidence(backend, activation_kind, evidence)
        entries.append(
            RuntimeModEvidence(
                id=mod_id,
                activation_kind=activation_kind,
                contract_sha256=fingerprint,
                effective=effective,
                evidence=evidence,
            )
        )

    snapshot = ModRuntimeSnapshot(
        schema_version=schema_version,
        root=stored_root,
        backend=backend,
        mode=normalized_mode,
        runtime_identity=normalized_identity,
        plan_sha256=plan_sha256,
        docker_override_path=override_path,
        docker_override_sha256=override_sha256,
        docker_node_options_sha256=docker_node_options_sha256,
        selected_loader_ids=tuple(selected_loader_ids),
        pid=pid,
        observed_at=observed_at,
        mods=tuple(entries),
    )
    _validate_snapshot(
        snapshot,
        expected_root=expected_root,
        expected_backend=expected_backend,
    )
    return snapshot


def _validate_snapshot(
    snapshot: ModRuntimeSnapshot,
    *,
    expected_root: Path,
    expected_backend: object,
) -> None:
    if (
        type(snapshot.schema_version) is not int
        or snapshot.schema_version != RUNTIME_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ModRuntimeSnapshotError("Unsupported runtime snapshot schemaVersion.")
    try:
        snapshot_root = snapshot.root.resolve(strict=True)
    except OSError as exc:
        raise ModRuntimeSnapshotError("Runtime snapshot root is unavailable.") from exc
    if snapshot_root != expected_root:
        raise ModRuntimeSnapshotError("Runtime snapshot root does not match its target.")
    backend = _normalize_backend(expected_backend)
    if snapshot.backend != backend:
        raise ModRuntimeSnapshotError("Runtime snapshot backend does not match its target.")
    _require_mode(snapshot.mode)
    _require_runtime_identity(
        snapshot.runtime_identity,
        error_type=ModRuntimeSnapshotError,
    )
    if type(snapshot.plan_sha256) is not str or not _SHA256_PATTERN.fullmatch(
        snapshot.plan_sha256
    ):
        raise ModRuntimeSnapshotError("Runtime snapshot plan SHA-256 is invalid.")
    if snapshot.pid is not None and (type(snapshot.pid) is not int or snapshot.pid < 1):
        raise ModRuntimeSnapshotError("Runtime snapshot PID is invalid.")
    if backend == NATIVE_BACKEND and (
        type(snapshot.pid) is not int or snapshot.pid < 1
    ):
        raise ModRuntimeSnapshotError("Native runtime snapshot requires a positive PID.")
    _normalize_time(snapshot.observed_at)

    ids: set[str] = set()
    if any(not isinstance(item, RuntimeModEvidence) for item in snapshot.mods):
        raise ModRuntimeSnapshotError("Runtime snapshot mod entries are invalid.")
    expected_order = sorted(snapshot.mods, key=lambda item: (item.id.casefold(), item.id))
    if list(snapshot.mods) != expected_order:
        raise ModRuntimeSnapshotError("Runtime snapshot mods are not deterministic.")
    for item in snapshot.mods:
        _require_mod_identity(
            item.id,
            item.activation_kind,
            error_type=ModRuntimeSnapshotError,
        )
        folded = item.id.casefold()
        if folded in ids:
            raise ModRuntimeSnapshotError("Runtime snapshot mod ids must be unique.")
        ids.add(folded)
        if not isinstance(item.activation_kind, ActivationKind):
            raise ModRuntimeSnapshotError("Runtime activation kind is invalid.")
        if not _SHA256_PATTERN.fullmatch(item.contract_sha256):
            raise ModRuntimeSnapshotError("Runtime contract fingerprint is invalid.")
        if type(item.effective) is not bool:
            raise ModRuntimeSnapshotError("Runtime effective state must be a boolean.")
        _validate_evidence(backend, item.activation_kind, item.evidence)
        if (
            snapshot.mode == "vanilla"
            and item.activation_kind is ActivationKind.LOADER_RENAME
            and item.effective
        ):
            raise ModRuntimeSnapshotError(
                "A vanilla runtime snapshot cannot contain an effective loader."
            )
    effective_loader_ids = {
        item.id
        for item in snapshot.mods
        if item.activation_kind is ActivationKind.LOADER_RENAME and item.effective
    }
    selected_loader_ids: list[str] = []
    selected_folded: set[str] = set()
    for mod_id in snapshot.selected_loader_ids:
        _require_loader_mod_id(mod_id, error_type=ModRuntimeSnapshotError)
        folded = mod_id.casefold()
        if folded in selected_folded:
            raise ModRuntimeSnapshotError(
                "Runtime snapshot selected loader ids must be unique."
            )
        selected_folded.add(folded)
        selected_loader_ids.append(mod_id)
    if set(selected_loader_ids) != effective_loader_ids:
        raise ModRuntimeSnapshotError(
            "Runtime snapshot selected loaders do not match effective evidence."
        )
    if snapshot.mode == "vanilla" and selected_loader_ids:
        raise ModRuntimeSnapshotError(
            "A vanilla runtime snapshot cannot select loader preloads."
        )
    try:
        _validate_docker_override_binding(
            root=expected_root,
            backend=backend,
            mode=snapshot.mode,
            selected_loader_ids=tuple(selected_loader_ids),
            override_path=snapshot.docker_override_path,
            override_sha256=snapshot.docker_override_sha256,
            docker_node_options=None,
            docker_node_options_sha256=snapshot.docker_node_options_sha256,
        )
    except ModRuntimeStateError as exc:
        raise ModRuntimeSnapshotError(str(exc)) from exc


def _validate_evidence(
    backend: str,
    activation_kind: ActivationKind,
    evidence: str,
) -> None:
    expected: str
    if backend == NATIVE_BACKEND:
        expected = (
            NATIVE_STATUS_EVIDENCE
            if activation_kind is ActivationKind.JSON_BOOLEAN
            else NATIVE_LOADER_EVIDENCE
        )
    else:
        if activation_kind is not ActivationKind.LOADER_RENAME:
            raise ModRuntimeSnapshotError(
                "Docker snapshots cannot contain source-integrated mods."
            )
        expected = DOCKER_OVERRIDE_EVIDENCE
    if evidence != expected:
        raise ModRuntimeSnapshotError(
            f"Runtime evidence must be {expected!r} for this activation contract."
        )


def _canonical_root(evejs_root: str | Path) -> Path:
    try:
        root = Path(evejs_root).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ModRuntimeStateError("The EveJS root is unavailable.") from exc
    if not root.is_dir():
        raise ModRuntimeStateError("The EveJS root is not a directory.")
    return root


def _canonical_mod_root(mod: Mod) -> Path:
    if mod.evejs_root is None:
        raise ModRuntimeStateError(f"Mod {mod.id!r} is not bound to an EveJS root.")
    return _canonical_root(mod.evejs_root)


def _relative_contract_path(path: Path, root: Path, label: str) -> str:
    try:
        relative = Path(path).resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ModRuntimeStateError(f"Mod {label} escapes its EveJS root.") from exc
    if not relative.parts:
        raise ModRuntimeStateError(f"Mod {label} cannot be the EveJS root.")
    return relative.as_posix()


def _loader_payload_sha256(mod: Mod, root: Path) -> str:
    """Hash exactly one recognized loader payload without hashing its state name."""

    candidates: list[tuple[Path, os.stat_result]] = []
    for filename in _LOADER_PAYLOAD_FILENAMES:
        candidate = mod.path / filename
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ModRuntimeStateError(
                f"Loader payload for {mod.id!r} could not be inspected."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ModRuntimeStateError(
                f"Loader payload for {mod.id!r} is unsafe or not a regular file."
            )
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise ModRuntimeStateError(
                f"Loader payload for {mod.id!r} escapes its EveJS root."
            ) from exc
        candidates.append((candidate, metadata))

    if len(candidates) != 1:
        raise ModRuntimeStateError(
            f"Loader mod {mod.id!r} must contain exactly one recognized loader payload."
        )
    candidate, before = candidates[0]
    if before.st_size > MAX_LOADER_PAYLOAD_BYTES:
        raise ModRuntimeStateError(
            f"Loader payload for {mod.id!r} exceeds the "
            f"{MAX_LOADER_PAYLOAD_BYTES}-byte limit."
        )
    try:
        with candidate.open("rb") as stream:
            content = stream.read(MAX_LOADER_PAYLOAD_BYTES + 1)
            opened = os.fstat(stream.fileno())
        after = candidate.lstat()
    except OSError as exc:
        raise ModRuntimeStateError(
            f"Loader payload for {mod.id!r} could not be read safely."
        ) from exc
    if len(content) > MAX_LOADER_PAYLOAD_BYTES:
        raise ModRuntimeStateError(
            f"Loader payload for {mod.id!r} exceeds the "
            f"{MAX_LOADER_PAYLOAD_BYTES}-byte limit."
        )
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size != len(content)
        or after.st_size != len(content)
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ModRuntimeStateError(
            f"Loader payload for {mod.id!r} changed while it was being fingerprinted."
        )
    return hashlib.sha256(content).hexdigest()


def _require_integrated_mod_id(
    mod_id: object,
    *,
    error_type: type[ModRuntimeStateError] = ModRuntimeStateError,
) -> None:
    if type(mod_id) is not str or not _INTEGRATED_MOD_ID_PATTERN.fullmatch(mod_id):
        raise error_type("Integrated mod id is invalid.")


def _require_loader_mod_id(
    mod_id: object,
    *,
    error_type: type[ModRuntimeStateError] = ModRuntimeStateError,
) -> None:
    if type(mod_id) is not str:
        raise error_type("Loader mod id is invalid.")
    try:
        encoded = mod_id.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise error_type("Loader mod id is not valid UTF-8 text.") from exc
    if (
        not mod_id
        or len(mod_id) > 255
        or len(encoded) > 1024
        or mod_id in {".", ".."}
        or "/" in mod_id
        or "\\" in mod_id
        or "\0" in mod_id
        or mod_id[-1] in {" ", "."}
        or any(character in '<>:"|?*' for character in mod_id)
        or any(ord(character) < 32 for character in mod_id)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in mod_id)
    ):
        raise error_type("Loader mod id cannot be represented safely.")


def _require_mod_identity(
    mod_id: object,
    activation_kind: ActivationKind,
    *,
    error_type: type[ModRuntimeStateError] = ModRuntimeStateError,
) -> None:
    if activation_kind is ActivationKind.JSON_BOOLEAN:
        _require_integrated_mod_id(mod_id, error_type=error_type)
        return
    if activation_kind is ActivationKind.LOADER_RENAME:
        _require_loader_mod_id(mod_id, error_type=error_type)
        return
    raise error_type("Mod activation kind is invalid.")


def _require_runtime_identity(
    value: object,
    *,
    error_type: type[ModRuntimeStateError] = ModRuntimeStateError,
) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise error_type("Runtime identity must be a non-empty trimmed string.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise error_type("Runtime identity must be valid UTF-8 text.") from exc
    if (
        len(encoded) > MAX_RUNTIME_IDENTITY_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise error_type("Runtime identity is unsafe or exceeds its size limit.")
    return value


def _require_docker_node_options(value: object) -> str:
    if type(value) is not str:
        raise ModRuntimeStateError("Docker NODE_OPTIONS binding must be text.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ModRuntimeStateError(
            "Docker NODE_OPTIONS binding must be valid UTF-8."
        ) from exc
    if (
        len(encoded) > MAX_DOCKER_NODE_OPTIONS_BYTES
        or "\0" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ModRuntimeStateError(
            "Docker NODE_OPTIONS binding is unsafe or exceeds its size limit."
        )
    return value


def _require_mode(mode: object) -> str:
    if type(mode) is not str or mode not in _VALID_MODES:
        raise ModRuntimeStateError("Runtime mode must be 'vanilla' or 'modded'.")
    return mode


def _normalize_backend(backend: object) -> str:
    value = getattr(backend, "value", backend)
    if value not in {NATIVE_BACKEND, DOCKER_BACKEND}:
        raise ModRuntimeSnapshotError("Runtime backend is unsupported.")
    return value


def _normalize_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ModRuntimeStateError("Runtime observation time must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    normalized = _normalize_time(value)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(value: str) -> datetime:
    if not _UTC_TIME_PATTERN.fullmatch(value):
        raise ModRuntimeSnapshotError("Runtime observation time is not canonical UTC.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ModRuntimeSnapshotError("Runtime observation time is invalid.") from exc


def _require_exact_keys(
    payload: dict[str, object],
    expected: frozenset[str],
    label: str,
    *,
    error_type: type[ModRuntimeStateError],
) -> None:
    if set(payload) == expected:
        return
    raise error_type(f"{label} fields are not exact.")


def _parse_json_object(
    content: bytes,
    label: str,
    *,
    error_type: type[ModRuntimeStateError],
) -> dict[str, object]:
    if content.startswith(_UTF8_BOM):
        raise error_type(f"{label} must be UTF-8 without a BOM.")
    try:
        text = content.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=lambda pairs: _unique_object(
                pairs, label, error_type=error_type
            ),
            parse_constant=lambda value: _reject_constant(
                value, label, error_type=error_type
            ),
        )
    except error_type:
        raise
    except RecursionError as exc:
        raise error_type(f"{label} exceeds the JSON nesting limit.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise error_type(f"{label} is not strict UTF-8 JSON.") from exc
    if type(payload) is not dict:
        raise error_type(f"{label} must be a JSON object.")
    return payload


def _unique_object(
    pairs: list[tuple[str, object]],
    label: str,
    *,
    error_type: type[ModRuntimeStateError],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise error_type(f"{label} contains duplicate JSON key {key!r}.")
        result[key] = value
    return result


def _reject_constant(
    value: str,
    label: str,
    *,
    error_type: type[ModRuntimeStateError],
) -> None:
    raise error_type(f"{label} contains unsupported JSON constant {value!r}.")


def _require_safe_snapshot_file(path: Path, root: Path) -> None:
    try:
        local_directory = path.parent
        local_metadata = local_directory.lstat()
        if stat.S_ISLNK(local_metadata.st_mode) or not stat.S_ISDIR(
            local_metadata.st_mode
        ):
            raise ModRuntimeSnapshotError("Snapshot directory is unsafe.")
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ModRuntimeSnapshotError("Snapshot file is unsafe.")
        _require_within_root(path, root, "Runtime snapshot")
    except ModRuntimeSnapshotError:
        raise
    except OSError as exc:
        raise ModRuntimeSnapshotError("Runtime snapshot is unavailable.") from exc


def _require_within_root(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ModRuntimeSnapshotError(f"{label} escapes the EveJS root.") from exc


def _read_bounded_file(path: Path, maximum_bytes: int) -> bytes:
    return _read_stable_bounded_file(
        path,
        maximum_bytes,
        label="Runtime snapshot",
        error_type=ModRuntimeSnapshotError,
    )


def _read_stable_bounded_file(
    path: Path,
    maximum_bytes: int,
    *,
    label: str,
    error_type: type[ModRuntimeStateError],
) -> bytes:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise error_type(f"{label} must be a regular, non-symlink file.")
        if before.st_size > maximum_bytes:
            raise error_type(f"{label} exceeds the {maximum_bytes}-byte limit.")
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
            opened = os.fstat(stream.fileno())
        after = path.lstat()
    except error_type:
        raise
    except OSError as exc:
        raise error_type(f"{label} could not be read safely.") from exc
    if len(content) > maximum_bytes:
        raise error_type(f"{label} exceeds the {maximum_bytes}-byte limit.")
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size != len(content)
        or after.st_size != len(content)
        or opened.st_mtime_ns != before.st_mtime_ns
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise error_type(f"{label} changed while it was being read.")
    return content


__all__ = [
    "DOCKER_BACKEND",
    "DOCKER_OVERRIDE_EVIDENCE",
    "MAX_DOCKER_NODE_OPTIONS_BYTES",
    "MAX_DOCKER_OVERRIDE_BYTES",
    "MAX_LOADER_PAYLOAD_BYTES",
    "MAX_RUNTIME_IDENTITY_BYTES",
    "MAX_RUNTIME_SNAPSHOT_BYTES",
    "MAX_SERVER_CONSOLE_BYTES",
    "MAX_STATUS_LINE_BYTES",
    "ModRuntimePlan",
    "ModRuntimePlanEntry",
    "ModRuntimeSnapshot",
    "ModRuntimeSnapshotError",
    "ModRuntimeStateError",
    "ModStatusMarker",
    "ModStatusProtocolError",
    "NATIVE_BACKEND",
    "NATIVE_LOADER_EVIDENCE",
    "NATIVE_STATUS_EVIDENCE",
    "RUNTIME_SNAPSHOT_FILENAME",
    "RUNTIME_SNAPSHOT_SCHEMA_VERSION",
    "RuntimeModEvidence",
    "STATUS_MARKER_PREFIX",
    "STATUS_PROTOCOL",
    "STATUS_TRANSPORT",
    "build_docker_mod_runtime_snapshot",
    "build_mod_runtime_plan",
    "build_native_mod_runtime_snapshot",
    "mod_contract_sha256",
    "mod_runtime_snapshot_path",
    "native_mod_preload_paths",
    "parse_native_status_markers",
    "read_mod_runtime_snapshot",
    "read_server_console_bytes",
    "validate_mod_runtime_plan",
    "write_mod_runtime_snapshot",
]
