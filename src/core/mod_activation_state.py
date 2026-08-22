"""Durable, fail-closed activation intent for launcher-managed EveJS mods.

The mod configuration and this journal answer different questions. A mod's
configuration records what the next Game process should load; this journal
records whether that requested transition has been verified at runtime.

This module deliberately does not acquire the EveJS mod lifecycle lock. Every
mutating caller must own the target root's lifecycle lock across the complete
``prepare -> configuration write -> pending/failed`` sequence. Runtime
confirmation must use that same ordering boundary before clearing records.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable

from .. import config
from .mod_manifest import Mod, scan_mods
from .mod_runtime_state import (
    ModRuntimeSnapshot,
    ModRuntimeStateError,
    mod_contract_sha256,
)


ACTIVATION_STATE_SCHEMA_VERSION = 1
ACTIVATION_STATE_DIRECTORY = "mod_activation_state"
MAX_ACTIVATION_STATE_BYTES = 256 * 1024
MAX_ACTIVATION_RECORDS = 512
MAX_ERROR_CODE_LENGTH = 96

_DOCUMENT_KEYS = frozenset({"schemaVersion", "root", "records"})
_RECORD_KEYS = frozenset(
    {"contractSha256", "desired", "phase", "errorCode", "updatedAt"}
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODE_PATTERN = re.compile(
    rf"[a-z0-9][a-z0-9._-]{{0,{MAX_ERROR_CODE_LENGTH - 1}}}\Z"
)
_UTC_TIME_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z"
)
_UTF8_BOM = b"\xef\xbb\xbf"


class ModActivationStateError(ValueError):
    """Activation intent is invalid, unsafe, or cannot be persisted."""


class ModActivationStateReadError(ModActivationStateError):
    """The durable activation journal cannot be trusted."""


class ModActivationStateWriteError(ModActivationStateError):
    """The durable activation journal could not be committed atomically."""


class ModActivationTransitionError(ModActivationStateError):
    """An activation journal transition is missing or out of order."""


class ActivationPhase(str, Enum):
    """Durable phases for one requested activation operation."""

    PREPARED = "prepared"
    PENDING_RESTART = "pending_restart"
    FAILED = "failed"


class ModActivationStatus(str, Enum):
    """User-facing projection of configuration, intent, and runtime evidence."""

    VERIFIED = "verified"
    RESTART_REQUIRED = "restart_required"
    RUNTIME_UNVERIFIED = "runtime_unverified"
    VERIFICATION_FAILED = "verification_failed"
    STALE_CONTRACT = "stale_contract"


@dataclass(frozen=True)
class ModActivationIntent:
    """One immutable-contract activation operation awaiting confirmation."""

    id: str
    contract_sha256: str
    desired: bool
    phase: ActivationPhase
    error_code: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ModActivationState:
    """Strict per-root journal document."""

    schema_version: int
    root: Path
    intents: tuple[ModActivationIntent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "intents", tuple(self.intents))

    def for_mod(self, mod_id: str) -> ModActivationIntent | None:
        """Return the exact case-sensitive record for ``mod_id``."""

        return next((item for item in self.intents if item.id == mod_id), None)


@dataclass(frozen=True)
class ModActivationProjection:
    """A conservative UI/runtime view of one current mod contract."""

    status: ModActivationStatus
    configured: bool
    effective: bool | None
    desired: bool
    intent_phase: ActivationPhase | None
    error_code: str | None
    reason_code: str

    @property
    def pending(self) -> bool:
        return self.status is ModActivationStatus.RESTART_REQUIRED

    @property
    def clearable(self) -> bool:
        return (
            self.status is ModActivationStatus.VERIFIED
            and self.intent_phase is not None
        )


def mod_activation_state_path(evejs_root: str | Path) -> Path:
    """Return the launcher-owned journal path for a canonical EveJS root."""

    root = _canonical_root(evejs_root)
    digest = hashlib.sha256(_root_identity(root).encode("utf-8")).hexdigest()
    return (
        Path(config.CONFIG_DIR)
        / ACTIVATION_STATE_DIRECTORY
        / f"{digest}.json"
    )


def read_mod_activation_state(evejs_root: str | Path) -> ModActivationState:
    """Read one strict journal, or return an empty state if none exists.

    Malformed, oversized, duplicated-key, BOM-prefixed, stale-root, symlinked,
    or otherwise unsafe files raise :class:`ModActivationStateReadError`.
    They are never treated as empty and are never silently replaced.
    """

    root = _canonical_root(evejs_root, error_type=ModActivationStateReadError)
    return _read_state(root)


def list_mod_activation_intents(
    evejs_root: str | Path,
) -> tuple[ModActivationIntent, ...]:
    """Return deterministic activation records for ``evejs_root``."""

    return read_mod_activation_state(evejs_root).intents


def prepare_mod_activation(
    mod: Mod,
    desired: bool,
    *,
    updated_at: datetime | None = None,
) -> ModActivationIntent:
    """Durably prepare an operation before changing the mod configuration.

    The caller must already own the target root's lifecycle lock. A new
    operation intentionally replaces an older valid record for the same mod,
    allowing a user to toggle back before restarting. Corrupt journal content
    still fails closed and is never overwritten.
    """

    _require_bool(desired, "Desired activation state")
    root, fingerprint = _current_mod_contract(mod)
    state = _read_state(root)
    intent = ModActivationIntent(
        id=mod.id,
        contract_sha256=fingerprint,
        desired=desired,
        phase=ActivationPhase.PREPARED,
        error_code=None,
        updated_at=_normalize_time(updated_at),
    )
    _replace_intent(state, intent)
    return intent


def mark_mod_activation_pending(
    mod: Mod,
    desired: bool,
    *,
    updated_at: datetime | None = None,
) -> ModActivationIntent:
    """Commit ``prepared -> pending_restart`` for the exact operation."""

    _require_bool(desired, "Desired activation state")
    root, fingerprint = _current_mod_contract(mod)
    state = _read_state(root)
    current = _require_matching_operation(state, mod, fingerprint, desired)
    if current.phase is not ActivationPhase.PREPARED:
        raise ModActivationTransitionError(
            "Activation can become pending only from the prepared phase."
        )
    intent = ModActivationIntent(
        id=current.id,
        contract_sha256=current.contract_sha256,
        desired=current.desired,
        phase=ActivationPhase.PENDING_RESTART,
        error_code=None,
        updated_at=_normalize_time(updated_at),
    )
    _replace_intent(state, intent)
    return intent


def fail_mod_activation(
    mod: Mod,
    desired: bool,
    error_code: str,
    *,
    updated_at: datetime | None = None,
) -> ModActivationIntent:
    """Mark the exact prepared or pending operation as failed.

    ``error_code`` is deliberately a short machine-readable token. Arbitrary
    exception text, paths, command output, and other potentially sensitive
    diagnostics must not be stored in this launcher journal.
    """

    _require_bool(desired, "Desired activation state")
    safe_error_code = _require_error_code(error_code)
    root, fingerprint = _current_mod_contract(mod)
    state = _read_state(root)
    current = _require_matching_operation(state, mod, fingerprint, desired)
    intent = ModActivationIntent(
        id=current.id,
        contract_sha256=current.contract_sha256,
        desired=current.desired,
        phase=ActivationPhase.FAILED,
        error_code=safe_error_code,
        updated_at=_normalize_time(updated_at),
    )
    _replace_intent(state, intent)
    return intent


def clear_confirmed_mod_activations(
    evejs_root: str | Path,
    snapshot: ModRuntimeSnapshot,
    mods: Iterable[Mod],
) -> tuple[str, ...]:
    """Clear only records proven effective for their exact current contracts.

    The caller must own the lifecycle lock that also guards runtime snapshot
    publication. A record is cleared only when the snapshot root, mod ID,
    immutable contract fingerprint, configured state, and effective state all
    agree with its desired state. Missing or contradictory evidence leaves the
    record untouched.
    """

    root = _canonical_root(evejs_root)
    if not isinstance(snapshot, ModRuntimeSnapshot):
        raise TypeError("snapshot must be a ModRuntimeSnapshot.")
    try:
        snapshot_root = snapshot.root.resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ModActivationStateError(
            "The runtime snapshot root is unavailable."
        ) from exc
    if _root_identity(snapshot_root) != _root_identity(root):
        raise ModActivationStateError(
            "The runtime snapshot belongs to a different EveJS root."
        )

    state = _read_state(root)
    if not state.intents:
        return ()

    current_mods: dict[str, Mod] = {}
    for mod in mods:
        if not isinstance(mod, Mod):
            raise TypeError("mods must contain only Mod instances.")
        mod_root, _ = _current_mod_contract(mod)
        if _root_identity(mod_root) != _root_identity(root):
            raise ModActivationStateError(
                f"Mod {mod.id!r} belongs to a different EveJS root."
            )
        if mod.id in current_mods:
            raise ModActivationStateError("Current mod ids must be unique.")
        current_mods[mod.id] = mod

    remaining: list[ModActivationIntent] = []
    cleared: list[str] = []
    for intent in state.intents:
        mod = current_mods.get(intent.id)
        if mod is None:
            remaining.append(intent)
            continue
        try:
            fingerprint = mod_contract_sha256(mod)
        except (ModRuntimeStateError, OSError, TypeError, ValueError):
            remaining.append(intent)
            continue
        evidence = tuple(item for item in snapshot.mods if item.id == mod.id)
        effective = snapshot.effective_for(mod)
        if (
            fingerprint == intent.contract_sha256
            and mod.active is intent.desired
            and len(evidence) == 1
            and evidence[0].contract_sha256 == intent.contract_sha256
            and effective is intent.desired
        ):
            cleared.append(intent.id)
        else:
            remaining.append(intent)

    if cleared:
        _write_state(
            ModActivationState(
                schema_version=ACTIVATION_STATE_SCHEMA_VERSION,
                root=root,
                intents=tuple(remaining),
            )
        )
    return tuple(cleared)


def clear_confirmed_mod_activation(
    mod: Mod,
    snapshot: ModRuntimeSnapshot,
) -> bool:
    """Single-mod wrapper for :func:`clear_confirmed_mod_activations`."""

    root, _ = _current_mod_contract(mod)
    return bool(clear_confirmed_mod_activations(root, snapshot, (mod,)))


def retire_removed_mod_activation(
    evejs_root: str | Path,
    mod_id: str,
    expected_contract_sha256: str,
) -> bool:
    """Retire one exact activation intent after verified physical removal.

    The caller must own the target root's lifecycle lock.  A matching current
    mod blocks retirement, as does a different recorded contract.  This keeps
    an uninstall completion from erasing a newer reinstall's activation state.
    """

    root = _canonical_root(evejs_root, error_type=ModActivationStateWriteError)
    _require_mod_id(mod_id, error_type=ModActivationStateWriteError)
    if (
        type(expected_contract_sha256) is not str
        or not _SHA256_PATTERN.fullmatch(expected_contract_sha256)
    ):
        raise ModActivationStateWriteError(
            "The removed mod contract fingerprint is invalid."
        )
    if any(mod.id == mod_id for mod in scan_mods(root)):
        raise ModActivationStateWriteError(
            "The mod is still installed or was reinstalled before activation cleanup."
        )

    state = _read_state(root)
    current = state.for_mod(mod_id)
    if current is None:
        return False
    if current.contract_sha256 != expected_contract_sha256:
        raise ModActivationStateWriteError(
            "The activation record belongs to a different mod contract."
        )
    _write_state(
        ModActivationState(
            schema_version=ACTIVATION_STATE_SCHEMA_VERSION,
            root=root,
            intents=tuple(item for item in state.intents if item.id != mod_id),
        )
    )
    return True


def project_mod_activation(
    mod: Mod,
    snapshot: ModRuntimeSnapshot | None,
    intent: ModActivationIntent | None = None,
) -> ModActivationProjection:
    """Project current config, durable intent, and runtime evidence safely.

    A recovered ``prepared`` record is considered pending only if the current
    configuration equals its desired value. If it does not, the interrupted
    operation is surfaced as failed/incomplete. No configuration value alone
    is ever promoted to verified runtime state.
    """

    if not isinstance(mod, Mod):
        raise TypeError("mod must be a Mod instance.")
    if snapshot is not None and not isinstance(snapshot, ModRuntimeSnapshot):
        raise TypeError("snapshot must be a ModRuntimeSnapshot or None.")
    if intent is not None and not isinstance(intent, ModActivationIntent):
        raise TypeError("intent must be a ModActivationIntent or None.")

    configured = mod.active
    try:
        root, fingerprint = _current_mod_contract(mod)
    except (ModActivationStateError, ModRuntimeStateError, OSError, TypeError, ValueError):
        return _projection(
            ModActivationStatus.STALE_CONTRACT,
            configured,
            None,
            configured,
            intent,
            "current-contract-invalid",
        )

    if intent is not None:
        try:
            _validate_intent(intent, error_type=ModActivationStateError)
        except ModActivationStateError:
            return _projection(
                ModActivationStatus.STALE_CONTRACT,
                configured,
                None,
                configured,
                intent,
                "intent-record-invalid",
            )
        if intent.id != mod.id or intent.contract_sha256 != fingerprint:
            return _projection(
                ModActivationStatus.STALE_CONTRACT,
                configured,
                None,
                configured,
                intent,
                "intent-contract-mismatch",
            )

    effective: bool | None = None
    if snapshot is not None:
        try:
            snapshot_root = snapshot.root.resolve(strict=True)
        except (OSError, TypeError, ValueError):
            return _projection(
                ModActivationStatus.STALE_CONTRACT,
                configured,
                None,
                intent.desired if intent else configured,
                intent,
                "runtime-root-unavailable",
            )
        if _root_identity(snapshot_root) != _root_identity(root):
            return _projection(
                ModActivationStatus.STALE_CONTRACT,
                configured,
                None,
                intent.desired if intent else configured,
                intent,
                "runtime-root-mismatch",
            )
        evidence = tuple(item for item in snapshot.mods if item.id == mod.id)
        if evidence and (
            len(evidence) != 1
            or evidence[0].contract_sha256 != fingerprint
        ):
            return _projection(
                ModActivationStatus.STALE_CONTRACT,
                configured,
                None,
                intent.desired if intent else configured,
                intent,
                "runtime-contract-mismatch",
            )
        effective = snapshot.effective_for(mod)
        if evidence and effective is None:
            return _projection(
                ModActivationStatus.STALE_CONTRACT,
                configured,
                None,
                intent.desired if intent else configured,
                intent,
                "runtime-contract-mismatch",
            )

    if intent is None:
        if effective is None:
            return _projection(
                ModActivationStatus.RUNTIME_UNVERIFIED,
                configured,
                None,
                configured,
                None,
                "runtime-evidence-missing",
            )
        if effective is configured:
            return _projection(
                ModActivationStatus.VERIFIED,
                configured,
                effective,
                configured,
                None,
                "configured-state-verified",
            )
        return _projection(
            ModActivationStatus.RESTART_REQUIRED,
            configured,
            effective,
            configured,
            None,
            "configured-runtime-mismatch",
        )

    desired = intent.desired
    if intent.phase is ActivationPhase.PREPARED and configured is not desired:
        return _projection(
            ModActivationStatus.VERIFICATION_FAILED,
            configured,
            effective,
            desired,
            intent,
            "prepared-operation-incomplete",
        )
    if intent.phase is ActivationPhase.PENDING_RESTART and configured is not desired:
        return _projection(
            ModActivationStatus.VERIFICATION_FAILED,
            configured,
            effective,
            desired,
            intent,
            "pending-configuration-drift",
        )

    if configured is desired and effective is desired:
        return _projection(
            ModActivationStatus.VERIFIED,
            configured,
            effective,
            desired,
            intent,
            "requested-state-verified",
        )

    if intent.phase is ActivationPhase.FAILED:
        return _projection(
            ModActivationStatus.VERIFICATION_FAILED,
            configured,
            effective,
            desired,
            intent,
            intent.error_code or "activation-failed",
        )

    # Both a normally pending record and a crash-recovered prepared record with
    # the desired config in place require a restart/verification. A missing
    # snapshot never becomes success merely because configuration matches.
    return _projection(
        ModActivationStatus.RESTART_REQUIRED,
        configured,
        effective,
        desired,
        intent,
        (
            "prepared-operation-recovered"
            if intent.phase is ActivationPhase.PREPARED
            else "restart-pending"
        ),
    )


def reconcile_mod_activation(
    mod: Mod,
    snapshot: ModRuntimeSnapshot | None,
    state: ModActivationState | None = None,
) -> ModActivationProjection:
    """Load (or use) the per-root journal and project one current mod."""

    if not isinstance(mod, Mod):
        raise TypeError("mod must be a Mod instance.")
    if mod.evejs_root is None:
        return project_mod_activation(mod, snapshot, None)
    try:
        root = _canonical_root(mod.evejs_root)
    except ModActivationStateError:
        return project_mod_activation(mod, snapshot, None)
    current_state = state if state is not None else read_mod_activation_state(root)
    if not isinstance(current_state, ModActivationState):
        raise TypeError("state must be a ModActivationState or None.")
    try:
        state_root = current_state.root.resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return _projection(
            ModActivationStatus.STALE_CONTRACT,
            mod.active,
            None,
            mod.active,
            None,
            "activation-state-root-unavailable",
        )
    if _root_identity(state_root) != _root_identity(root):
        return _projection(
            ModActivationStatus.STALE_CONTRACT,
            mod.active,
            None,
            mod.active,
            None,
            "activation-state-root-mismatch",
        )
    return project_mod_activation(mod, snapshot, current_state.for_mod(mod.id))


def _read_state(root: Path) -> ModActivationState:
    destination = mod_activation_state_path(root)
    if not destination.exists() and not destination.is_symlink():
        parent = destination.parent
        if parent.exists() or parent.is_symlink():
            _require_safe_directory(parent, "Activation state directory")
        return _empty_state(root)

    _require_safe_file(destination)
    try:
        metadata = destination.stat()
        if metadata.st_size > MAX_ACTIVATION_STATE_BYTES:
            raise ModActivationStateReadError(
                "The activation state exceeds its size limit."
            )
        with destination.open("rb") as stream:
            content = stream.read(MAX_ACTIVATION_STATE_BYTES + 1)
    except ModActivationStateReadError:
        raise
    except OSError as exc:
        raise ModActivationStateReadError(
            "The activation state could not be read."
        ) from exc
    if len(content) > MAX_ACTIVATION_STATE_BYTES:
        raise ModActivationStateReadError(
            "The activation state exceeds its size limit."
        )
    payload = _parse_json_object(content)
    _require_exact_keys(payload, _DOCUMENT_KEYS, "Activation state")
    if payload["schemaVersion"] != ACTIVATION_STATE_SCHEMA_VERSION or type(
        payload["schemaVersion"]
    ) is not int:
        raise ModActivationStateReadError(
            "Unsupported activation state schemaVersion."
        )

    root_value = payload["root"]
    if type(root_value) is not str or not root_value or len(root_value) > 32768:
        raise ModActivationStateReadError("Activation state root is invalid.")
    try:
        stored_root = Path(root_value).resolve(strict=True)
    except OSError as exc:
        raise ModActivationStateReadError(
            "Activation state root is stale."
        ) from exc
    if _root_identity(stored_root) != _root_identity(root):
        raise ModActivationStateReadError(
            "Activation state belongs to a different EveJS root."
        )

    raw_records = payload["records"]
    if type(raw_records) is not dict:
        raise ModActivationStateReadError(
            "Activation state records must be an object."
        )
    if len(raw_records) > MAX_ACTIVATION_RECORDS:
        raise ModActivationStateReadError(
            "Activation state contains too many records."
        )
    intents = tuple(
        sorted(
            (
                _intent_from_payload(mod_id, raw_record)
                for mod_id, raw_record in raw_records.items()
            ),
            key=lambda item: item.id,
        )
    )
    return ModActivationState(
        schema_version=ACTIVATION_STATE_SCHEMA_VERSION,
        root=root,
        intents=intents,
    )


def _intent_from_payload(
    mod_id: object,
    payload: object,
) -> ModActivationIntent:
    _require_mod_id(mod_id, error_type=ModActivationStateReadError)
    if type(payload) is not dict:
        raise ModActivationStateReadError(
            f"Activation record {mod_id!r} must be an object."
        )
    _require_exact_keys(payload, _RECORD_KEYS, f"Activation record {mod_id!r}")
    fingerprint = payload["contractSha256"]
    desired = payload["desired"]
    phase_value = payload["phase"]
    error_code = payload["errorCode"]
    updated_value = payload["updatedAt"]
    if type(fingerprint) is not str or not _SHA256_PATTERN.fullmatch(fingerprint):
        raise ModActivationStateReadError(
            f"Activation record {mod_id!r} has an invalid contract fingerprint."
        )
    _require_bool(
        desired,
        "Activation record desired state",
        error_type=ModActivationStateReadError,
    )
    if type(phase_value) is not str:
        raise ModActivationStateReadError(
            f"Activation record {mod_id!r} has an invalid phase."
        )
    try:
        phase = ActivationPhase(phase_value)
    except ValueError as exc:
        raise ModActivationStateReadError(
            f"Activation record {mod_id!r} has an unsupported phase."
        ) from exc
    if phase is ActivationPhase.FAILED:
        try:
            parsed_error = _require_error_code(error_code)
        except ModActivationStateError as exc:
            raise ModActivationStateReadError(
                f"Activation record {mod_id!r} has an invalid error code."
            ) from exc
    elif error_code is not None:
        raise ModActivationStateReadError(
            f"Activation record {mod_id!r} may not store an error outside the failed phase."
        )
    else:
        parsed_error = None
    if type(updated_value) is not str:
        raise ModActivationStateReadError(
            f"Activation record {mod_id!r} has an invalid update time."
        )
    updated_at = _parse_time(updated_value)
    return ModActivationIntent(
        id=mod_id,
        contract_sha256=fingerprint,
        desired=desired,
        phase=phase,
        error_code=parsed_error,
        updated_at=updated_at,
    )


def _replace_intent(
    state: ModActivationState,
    replacement: ModActivationIntent,
) -> None:
    records = {item.id: item for item in state.intents}
    records[replacement.id] = replacement
    if len(records) > MAX_ACTIVATION_RECORDS:
        raise ModActivationStateWriteError(
            "The activation state contains too many records."
        )
    _write_state(
        ModActivationState(
            schema_version=ACTIVATION_STATE_SCHEMA_VERSION,
            root=state.root,
            intents=tuple(records[key] for key in sorted(records)),
        )
    )


def _write_state(state: ModActivationState) -> Path:
    root = _canonical_root(state.root, error_type=ModActivationStateWriteError)
    if state.schema_version != ACTIVATION_STATE_SCHEMA_VERSION:
        raise ModActivationStateWriteError(
            "Unsupported activation state schemaVersion."
        )
    validated: list[ModActivationIntent] = []
    seen: set[str] = set()
    for intent in state.intents:
        _validate_intent(intent, error_type=ModActivationStateWriteError)
        if intent.id in seen:
            raise ModActivationStateWriteError(
                "Activation state mod ids must be unique."
            )
        seen.add(intent.id)
        validated.append(intent)
    if len(validated) > MAX_ACTIVATION_RECORDS:
        raise ModActivationStateWriteError(
            "The activation state contains too many records."
        )

    destination = mod_activation_state_path(root)
    directory = destination.parent
    _prepare_state_directory(directory)
    if destination.exists() or destination.is_symlink():
        _require_safe_file(destination, error_type=ModActivationStateWriteError)

    records = {
        intent.id: {
            "contractSha256": intent.contract_sha256,
            "desired": intent.desired,
            "phase": intent.phase.value,
            "errorCode": intent.error_code,
            "updatedAt": _format_time(intent.updated_at),
        }
        for intent in sorted(validated, key=lambda item: item.id)
    }
    payload = {
        "schemaVersion": ACTIVATION_STATE_SCHEMA_VERSION,
        "root": str(root),
        "records": records,
    }
    try:
        content = (
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModActivationStateWriteError(
            "The activation state is not serializable."
        ) from exc
    if len(content) > MAX_ACTIVATION_STATE_BYTES:
        raise ModActivationStateWriteError(
            "The activation state exceeds its size limit."
        )

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=directory,
        )
    except OSError as exc:
        raise ModActivationStateWriteError(
            "The activation state temporary file could not be created."
        ) from exc
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
        raise ModActivationStateWriteError(
            "The activation state could not be committed atomically."
        ) from exc
    finally:
        try:
            temporary_path.unlink()
        except OSError:
            pass
    return destination


def _require_matching_operation(
    state: ModActivationState,
    mod: Mod,
    fingerprint: str,
    desired: bool,
) -> ModActivationIntent:
    current = state.for_mod(mod.id)
    if current is None:
        raise ModActivationTransitionError(
            "No prepared activation operation exists for this mod."
        )
    if current.contract_sha256 != fingerprint or current.desired is not desired:
        raise ModActivationTransitionError(
            "The activation operation does not match the current request and contract."
        )
    return current


def _current_mod_contract(mod: Mod) -> tuple[Path, str]:
    if not isinstance(mod, Mod):
        raise TypeError("mod must be a Mod instance.")
    if mod.evejs_root is None:
        raise ModActivationStateError(
            f"Mod {mod.id!r} is not bound to an EveJS root."
        )
    root = _canonical_root(mod.evejs_root)
    try:
        fingerprint = mod_contract_sha256(mod)
    except (ModRuntimeStateError, OSError, TypeError, ValueError) as exc:
        raise ModActivationStateError(
            f"Mod {mod.id!r} does not have a valid immutable contract."
        ) from exc
    return root, fingerprint


def _validate_intent(
    intent: ModActivationIntent,
    *,
    error_type: type[ModActivationStateError],
) -> None:
    if not isinstance(intent, ModActivationIntent):
        raise error_type("Activation state contains an invalid record.")
    _require_mod_id(intent.id, error_type=error_type)
    if not _SHA256_PATTERN.fullmatch(intent.contract_sha256):
        raise error_type("Activation record contract fingerprint is invalid.")
    _require_bool(intent.desired, "Activation record desired state", error_type=error_type)
    if not isinstance(intent.phase, ActivationPhase):
        raise error_type("Activation record phase is invalid.")
    if intent.phase is ActivationPhase.FAILED:
        try:
            _require_error_code(intent.error_code)
        except ModActivationStateError as exc:
            raise error_type("Activation record error code is invalid.") from exc
    elif intent.error_code is not None:
        raise error_type("Only failed activation records may store an error code.")
    try:
        _normalize_time(intent.updated_at)
    except ModActivationStateError as exc:
        raise error_type("Activation record update time is invalid.") from exc


def _empty_state(root: Path) -> ModActivationState:
    return ModActivationState(
        schema_version=ACTIVATION_STATE_SCHEMA_VERSION,
        root=root,
        intents=(),
    )


def _projection(
    status: ModActivationStatus,
    configured: bool,
    effective: bool | None,
    desired: bool,
    intent: ModActivationIntent | None,
    reason_code: str,
) -> ModActivationProjection:
    return ModActivationProjection(
        status=status,
        configured=configured,
        effective=effective,
        desired=desired,
        intent_phase=intent.phase if intent else None,
        error_code=intent.error_code if intent else None,
        reason_code=reason_code,
    )


def _canonical_root(
    evejs_root: str | Path,
    *,
    error_type: type[ModActivationStateError] = ModActivationStateError,
) -> Path:
    try:
        root = Path(evejs_root).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise error_type("The EveJS root is unavailable.") from exc
    if not root.is_dir():
        raise error_type("The EveJS root is not a directory.")
    return root


def _root_identity(root: Path) -> str:
    return os.path.normcase(str(root))


def _prepare_state_directory(directory: Path) -> None:
    base = Path(config.CONFIG_DIR)
    try:
        if base.exists() or base.is_symlink():
            _require_safe_directory(
                base,
                "Launcher configuration directory",
                error_type=ModActivationStateWriteError,
            )
        else:
            base.mkdir(parents=True, exist_ok=False)
        if directory.exists() or directory.is_symlink():
            _require_safe_directory(
                directory,
                "Activation state directory",
                error_type=ModActivationStateWriteError,
            )
        else:
            directory.mkdir(exist_ok=False)
    except ModActivationStateWriteError:
        raise
    except OSError as exc:
        raise ModActivationStateWriteError(
            "The activation state directory could not be prepared."
        ) from exc


def _require_safe_directory(
    path: Path,
    label: str,
    *,
    error_type: type[ModActivationStateError] = ModActivationStateReadError,
) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise error_type(f"{label} is unavailable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise error_type(f"{label} is unsafe.")


def _require_safe_file(
    path: Path,
    *,
    error_type: type[ModActivationStateError] = ModActivationStateReadError,
) -> None:
    _require_safe_directory(path.parent, "Activation state directory", error_type=error_type)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise error_type("The activation state file is unavailable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise error_type("The activation state file is unsafe.")


def _parse_json_object(content: bytes) -> dict[str, object]:
    if content.startswith(_UTF8_BOM):
        raise ModActivationStateReadError(
            "Activation state must be UTF-8 without a BOM."
        )
    try:
        text = content.decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ModActivationStateReadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ModActivationStateReadError(
            "Activation state is not strict UTF-8 JSON."
        ) from exc
    if type(payload) is not dict:
        raise ModActivationStateReadError("Activation state must be a JSON object.")
    return payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ModActivationStateReadError(
                f"Activation state contains duplicate JSON key {key!r}."
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ModActivationStateReadError(
        f"Activation state contains unsupported JSON constant {value!r}."
    )


def _require_exact_keys(
    payload: dict[str, object],
    expected: frozenset[str],
    label: str,
) -> None:
    if set(payload) != expected:
        raise ModActivationStateReadError(f"{label} fields are not exact.")


def _require_mod_id(
    mod_id: object,
    *,
    error_type: type[ModActivationStateError] = ModActivationStateError,
) -> None:
    if type(mod_id) is not str:
        raise error_type("Activation record mod id is invalid.")
    try:
        encoded = mod_id.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise error_type("Activation record mod id is not valid UTF-8 text.") from exc
    if (
        not mod_id
        or len(mod_id) > 255
        or len(encoded) > 1024
        or mod_id in {".", ".."}
        or "/" in mod_id
        or "\\" in mod_id
        or "\0" in mod_id
        or any(ord(character) < 32 for character in mod_id)
        or any(0xD800 <= ord(character) <= 0xDFFF for character in mod_id)
    ):
        raise error_type("Activation record mod id cannot be represented safely.")


def _require_bool(
    value: object,
    label: str,
    *,
    error_type: type[ModActivationStateError] = ModActivationStateError,
) -> None:
    if type(value) is not bool:
        raise error_type(f"{label} must be a boolean.")


def _require_error_code(value: object) -> str:
    if type(value) is not str or not _ERROR_CODE_PATTERN.fullmatch(value):
        raise ModActivationStateError(
            "Activation error code must be a bounded lowercase machine token."
        )
    return value


def _normalize_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ModActivationStateError(
            "Activation update time must be timezone-aware."
        )
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _normalize_time(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(value: str) -> datetime:
    if not _UTC_TIME_PATTERN.fullmatch(value):
        raise ModActivationStateReadError(
            "Activation record update time is not canonical UTC."
        )
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ModActivationStateReadError(
            "Activation record update time is invalid."
        ) from exc


__all__ = [
    "ACTIVATION_STATE_DIRECTORY",
    "ACTIVATION_STATE_SCHEMA_VERSION",
    "ActivationPhase",
    "MAX_ACTIVATION_RECORDS",
    "MAX_ACTIVATION_STATE_BYTES",
    "MAX_ERROR_CODE_LENGTH",
    "ModActivationIntent",
    "ModActivationProjection",
    "ModActivationState",
    "ModActivationStateError",
    "ModActivationStateReadError",
    "ModActivationStateWriteError",
    "ModActivationStatus",
    "ModActivationTransitionError",
    "clear_confirmed_mod_activation",
    "clear_confirmed_mod_activations",
    "fail_mod_activation",
    "list_mod_activation_intents",
    "mark_mod_activation_pending",
    "mod_activation_state_path",
    "prepare_mod_activation",
    "project_mod_activation",
    "read_mod_activation_state",
    "reconcile_mod_activation",
    "retire_removed_mod_activation",
]
