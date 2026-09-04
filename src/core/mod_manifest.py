"""Strict discovery and activation contracts for EveJS launcher mods.

Manifests are declarative data, never executable extension hooks. Unknown
fields and unsupported activation strategies fail closed and remain visible to
the launcher as invalid mod rows.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Iterable, NoReturn

from .mod_lifecycle_lock import (
    ModLifecycleBusyError,
    acquire_mod_lifecycle_lock,
)


MANIFEST_FILENAME = "evejs-launcher.mod.json"
MANIFEST_SCHEMA_VERSION = 2
RUNTIME_STATUS_PROTOCOL = "evejs_mod_status_v1"
RUNTIME_STATUS_TRANSPORT = "server_stdout"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 2 * 1024 * 1024

_LOADER_FILENAMES = (
    "loader.js",
    "loader.js.disabled",
    "loader.js.off",
    "loader.js.bak",
)
_DISABLED_LOADER_FILENAMES = _LOADER_FILENAMES[1:]
_MANIFEST_KEYS = {
    "schemaVersion",
    "id",
    "displayName",
    "version",
    "description",
    "kind",
    "supportedBackends",
    "activation",
    "status",
    "restart",
}
_ACTIVATION_KEYS = {
    "strategy",
    "configPath",
    "property",
    "allowedConfigSchemaVersions",
}
_STATUS_KEYS = {"protocol", "transport"}
_MOD_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class ModManagerError(RuntimeError):
    """Base class for launcher mod-management failures."""


class ModManifestError(ModManagerError):
    """Raised when an integrated mod manifest is invalid or unsafe."""


class ModActivationError(ModManagerError):
    """Raised when a requested mod state cannot be committed safely."""


class ActivationKind(str, Enum):
    """Activation mechanisms explicitly supported by the launcher."""

    LOADER_RENAME = "loader_rename"
    JSON_BOOLEAN = "json_boolean"
    CLIENT_PACKAGE = "client_package"


@dataclass
class Mod:
    """One discovered EveJS server mod.

    The first three fields intentionally preserve the launcher's original
    ``Mod(name, path, active)`` construction contract.
    """

    name: str
    path: Path
    active: bool
    id: str = ""
    version: str = ""
    description: str = ""
    activation_kind: ActivationKind = ActivationKind.LOADER_RENAME
    supported_backends: tuple[str, ...] = ("native", "docker")
    restart_scope: str = "game_server"
    manifest_path: Path | None = None
    config_path: Path | None = None
    config_key: str | None = None
    allowed_config_schema_versions: tuple[int, ...] = ()
    status_protocol: str = ""
    status_transport: str = ""
    manager_path: Path | None = None
    manager_protocol: str = ""
    manager_sha256: str = ""
    client_build: int | None = None
    evejs_version: str = ""
    valid: bool = True
    error: str | None = None
    evejs_root: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.id:
            self.id = self.path.name or self.name
        if self.manifest_path is not None:
            self.manifest_path = Path(self.manifest_path)
        if self.config_path is not None:
            self.config_path = Path(self.config_path)
        if self.manager_path is not None:
            self.manager_path = Path(self.manager_path)
        if self.evejs_root is not None:
            self.evejs_root = Path(self.evejs_root)

    def supports_backend(self, backend: object) -> bool:
        """Return whether this mod can be controlled by ``backend``."""

        value = str(getattr(backend, "value", backend)).casefold()
        if value == "docker_compose":
            value = "docker"
        return self.valid and value in self.supported_backends


def legacy_mods_directory(evejs_root: str | Path) -> Path:
    """Return the canonical launcher-compatible loader-mod directory.

    Keeping this path contract beside :func:`scan_mods` prevents the Mods page
    and discovery code from quietly disagreeing about where users should put
    a legacy ``loader.js`` mod.
    """

    return Path(evejs_root) / "mods"


def scan_mods(evejs_root: str | Path) -> list[Mod]:
    """Discover legacy loaders and explicit integrated-mod manifests.

    Invalid explicit manifests are returned as read-only rows instead of being
    silently ignored. Results are deterministic across both discovery roots.
    """

    if not str(evejs_root).strip():
        return []
    root = Path(evejs_root)
    if not root.is_dir():
        return []
    try:
        root = root.resolve(strict=True)
    except OSError:
        return []

    mods = [*_scan_legacy_mods(root), *_scan_integrated_mods(root)]
    mods = _invalidate_duplicate_ids(mods)
    return sorted(
        mods,
        key=lambda mod: (
            mod.name.casefold(),
            mod.id.casefold(),
            mod.activation_kind.value,
            str(mod.path).casefold(),
        ),
    )


def set_mod_active(mod: Mod, active: bool) -> bool:
    """Commit an explicit desired state and return the verified new state."""

    try:
        if mod.evejs_root is None:
            # Preserve the original public ``Mod(name, path, active)`` contract
            # for callers that construct a legacy row themselves. Every mod
            # returned by ``scan_mods`` carries a root and therefore uses the
            # cross-process launcher/installer lifecycle lock below.
            state = set_mod_active_locked(mod, active)
        else:
            with acquire_mod_lifecycle_lock(mod.evejs_root):
                state = set_mod_active_locked(mod, active)
    except ModLifecycleBusyError as exc:
        raise ModActivationError(f"Cannot change '{mod.name}': {exc}") from exc

    return state


def set_mod_active_locked(mod: Mod, active: bool) -> bool:
    """Commit state while the caller already owns the root lifecycle lock.

    UI code should normally use the durable activation transaction rather than
    this lower-level primitive.  It exists so that one lock can cover journal
    preparation, the filesystem mutation, and the pending-state commit without
    a racy release/reacquire gap.
    """

    if type(active) is not bool:
        raise TypeError("The requested mod state must be a boolean.")
    if not mod.valid:
        reason = mod.error or "the mod metadata is invalid"
        raise ModActivationError(f"Cannot change '{mod.name}': {reason}")
    state = _set_mod_active_unlocked(mod, active)
    mod.active = state
    return state


def _set_mod_active_unlocked(mod: Mod, active: bool) -> bool:
    """Dispatch one activation while the caller owns any required root lock."""

    if mod.activation_kind is ActivationKind.LOADER_RENAME:
        return _set_loader_active(mod, active)
    if mod.activation_kind is ActivationKind.JSON_BOOLEAN:
        return _set_json_boolean_active(mod, active)
    raise ModActivationError(
        f"Unsupported activation strategy for '{mod.name}'."
    )


def toggle_mod(mod: Mod) -> bool:
    """Legacy toggle wrapper; new callers should pass an explicit state."""

    return set_mod_active(mod, not mod.active)


def active_loader_mods(mods: Iterable[Mod]) -> tuple[Mod, ...]:
    """Return only valid active preload mods, preserving discovery order."""

    return tuple(
        mod
        for mod in mods
        if mod.valid
        and mod.active
        and mod.activation_kind is ActivationKind.LOADER_RENAME
    )


def active_loader_names(mods: Iterable[Mod]) -> tuple[str, ...]:
    """Return loader folder names suitable for the Docker preload bridge."""

    return tuple(mod.path.name for mod in active_loader_mods(mods))


def _scan_legacy_mods(root: Path) -> list[Mod]:
    mods_dir = legacy_mods_directory(root)
    if not mods_dir.exists():
        return []
    if not mods_dir.is_dir():
        return [
            _invalid_mod(
                name="Legacy mods",
                path=mods_dir,
                error="The root mods path is not a directory.",
                activation_kind=ActivationKind.LOADER_RENAME,
                root=root,
            )
        ]
    try:
        _require_within_root(mods_dir, root, "Legacy mods directory")
        folders = sorted(mods_dir.iterdir(), key=lambda item: item.name.casefold())
    except (OSError, ModManifestError) as exc:
        return [
            _invalid_mod(
                name="Legacy mods",
                path=mods_dir,
                error=str(exc),
                activation_kind=ActivationKind.LOADER_RENAME,
                root=root,
            )
        ]

    mods: list[Mod] = []
    for folder in folders:
        try:
            if not folder.is_dir():
                continue
            _require_within_root(folder, root, f"Legacy mod '{folder.name}'")
            for filename in _LOADER_FILENAMES:
                candidate = folder / filename
                if candidate.exists() or candidate.is_symlink():
                    _require_regular_file_within_root(
                        candidate,
                        root,
                        f"Legacy mod '{folder.name}' loader",
                    )
            active_loader = folder / "loader.js"
            disabled = [
                folder / filename
                for filename in _DISABLED_LOADER_FILENAMES
                if (folder / filename).exists()
            ]
            if not active_loader.exists() and not disabled:
                continue
            if active_loader.exists() and disabled:
                raise ModManifestError(
                    "Both active and disabled loader files exist."
                )
            if len(disabled) > 1:
                raise ModManifestError("Multiple disabled loader files exist.")
            mods.append(
                Mod(
                    name=folder.name,
                    path=folder,
                    active=active_loader.is_file(),
                    id=folder.name,
                    activation_kind=ActivationKind.LOADER_RENAME,
                    supported_backends=("native", "docker"),
                    evejs_root=root,
                )
            )
        except (OSError, ModManifestError) as exc:
            mods.append(
                _invalid_mod(
                    name=folder.name,
                    path=folder,
                    error=str(exc),
                    activation_kind=ActivationKind.LOADER_RENAME,
                    root=root,
                )
            )
    return mods


def _scan_integrated_mods(root: Path) -> list[Mod]:
    mods_dir = root / "server" / "mods"
    if not mods_dir.exists():
        return []
    if not mods_dir.is_dir():
        return [
            _invalid_mod(
                name="Integrated mods",
                path=mods_dir,
                error="The server mods path is not a directory.",
                activation_kind=ActivationKind.JSON_BOOLEAN,
                root=root,
            )
        ]
    try:
        _require_within_root(mods_dir, root, "Integrated mods directory")
        folders = sorted(mods_dir.iterdir(), key=lambda item: item.name.casefold())
    except (OSError, ModManifestError) as exc:
        return [
            _invalid_mod(
                name="Integrated mods",
                path=mods_dir,
                error=str(exc),
                activation_kind=ActivationKind.JSON_BOOLEAN,
                root=root,
            )
        ]

    mods: list[Mod] = []
    for folder in folders:
        manifest_path = folder / MANIFEST_FILENAME
        if not manifest_path.exists() and not manifest_path.is_symlink():
            continue
        try:
            if not folder.is_dir():
                raise ModManifestError("The manifest parent is not a directory.")
            _require_within_root(folder, root, f"Integrated mod '{folder.name}'")
            mods.append(_read_integrated_mod(root, folder, manifest_path))
        except (OSError, ModManifestError) as exc:
            mods.append(
                _invalid_mod(
                    name=folder.name,
                    path=folder,
                    error=str(exc),
                    activation_kind=ActivationKind.JSON_BOOLEAN,
                    root=root,
                    manifest_path=manifest_path,
                )
            )
    return mods


def _read_integrated_mod(
    root: Path,
    folder: Path,
    manifest_path: Path,
    *,
    active_override: bool | None = None,
) -> Mod:
    _require_regular_file_within_root(manifest_path, root, "Mod manifest")
    payload = _read_json_object(
        manifest_path,
        maximum_bytes=MAX_MANIFEST_BYTES,
        label="Mod manifest",
    )
    _require_exact_keys(payload, _MANIFEST_KEYS, "Mod manifest")
    if type(payload["schemaVersion"]) is not int:
        raise ModManifestError("Manifest schemaVersion must be an integer.")
    if payload["schemaVersion"] != MANIFEST_SCHEMA_VERSION:
        raise ModManifestError(
            f"Unsupported manifest schemaVersion {payload['schemaVersion']!r}."
        )

    mod_id = _require_text(payload["id"], "Manifest id", maximum=64)
    if not _MOD_ID_PATTERN.fullmatch(mod_id):
        raise ModManifestError(
            "Manifest id must contain only lowercase letters, numbers, '.', '_', or '-'."
        )
    if mod_id != folder.name:
        raise ModManifestError("Manifest id must exactly match its folder name.")

    display_name = _require_text(
        payload["displayName"],
        "Manifest displayName",
        maximum=100,
    )
    version = _require_text(payload["version"], "Manifest version", maximum=64)
    description = _require_text(
        payload["description"],
        "Manifest description",
        maximum=500,
        allow_empty=True,
    )
    if payload["kind"] != "source-integrated":
        raise ModManifestError("Manifest kind must be 'source-integrated'.")
    if payload["supportedBackends"] != ["native"]:
        raise ModManifestError(
            "Source-integrated schema v2 mods must declare only the Native backend."
        )
    if payload["restart"] != "game_server":
        raise ModManifestError("Manifest restart must be 'game_server'.")

    status = payload["status"]
    if type(status) is not dict:
        raise ModManifestError("Manifest status must be an object.")
    _require_exact_keys(status, _STATUS_KEYS, "Manifest status")
    if status["protocol"] != RUNTIME_STATUS_PROTOCOL:
        raise ModManifestError("Unsupported manifest runtime status protocol.")
    if status["transport"] != RUNTIME_STATUS_TRANSPORT:
        raise ModManifestError("Unsupported manifest runtime status transport.")

    activation = payload["activation"]
    if type(activation) is not dict:
        raise ModManifestError("Manifest activation must be an object.")
    _require_exact_keys(activation, _ACTIVATION_KEYS, "Manifest activation")
    if activation["strategy"] != ActivationKind.JSON_BOOLEAN.value:
        raise ModManifestError("Unsupported manifest activation strategy.")
    if activation["property"] != "enabled":
        raise ModManifestError(
            "Schema v2 can control only the top-level 'enabled' property."
        )

    allowed_value = activation["allowedConfigSchemaVersions"]
    if type(allowed_value) is not list or not allowed_value:
        raise ModManifestError(
            "allowedConfigSchemaVersions must be a non-empty array."
        )
    if (
        len(allowed_value) > 32
        or any(
            type(item) is not int or item < 1 or item > 65535
            for item in allowed_value
        )
        or len(set(allowed_value)) != len(allowed_value)
        or allowed_value != sorted(allowed_value)
    ):
        raise ModManifestError(
            "allowedConfigSchemaVersions must contain unique ascending positive integers."
        )
    allowed_versions = tuple(allowed_value)

    config_relative = _validate_config_relative_path(
        activation["configPath"],
        mod_id,
    )
    config_path = root.joinpath(*config_relative.parts)
    if active_override is None:
        _require_regular_file_within_root(config_path, root, "Mod configuration")
        config = _read_and_validate_config(config_path, allowed_versions)
        active = config["enabled"]
    else:
        if type(active_override) is not bool:
            raise TypeError("The integrated mod active override must be a boolean.")
        active = active_override

    return Mod(
        name=display_name,
        path=folder,
        active=active,
        id=mod_id,
        version=version,
        description=description,
        activation_kind=ActivationKind.JSON_BOOLEAN,
        supported_backends=("native",),
        restart_scope="game_server",
        manifest_path=manifest_path,
        config_path=config_path,
        config_key="enabled",
        allowed_config_schema_versions=allowed_versions,
        status_protocol=RUNTIME_STATUS_PROTOCOL,
        status_transport=RUNTIME_STATUS_TRANSPORT,
        evejs_root=root,
    )


def _invalidate_duplicate_ids(mods: list[Mod]) -> list[Mod]:
    indexes: dict[str, list[int]] = {}
    for index, mod in enumerate(mods):
        indexes.setdefault(mod.id.casefold(), []).append(index)
    duplicate_indexes = {
        index
        for group in indexes.values()
        if len(group) > 1
        for index in group
    }
    if not duplicate_indexes:
        return mods

    result = list(mods)
    for index in duplicate_indexes:
        mod = mods[index]
        result[index] = replace(
            mod,
            valid=False,
            supported_backends=(),
            error=f"Duplicate mod id '{mod.id}' was discovered.",
        )
    return result


def _invalid_mod(
    *,
    name: str,
    path: Path,
    error: str,
    activation_kind: ActivationKind,
    root: Path,
    manifest_path: Path | None = None,
) -> Mod:
    return Mod(
        name=name,
        path=path,
        active=False,
        id=path.name or name,
        activation_kind=activation_kind,
        supported_backends=(),
        manifest_path=manifest_path,
        valid=False,
        error=error,
        evejs_root=root,
    )


def _set_loader_active(mod: Mod, desired: bool) -> bool:
    root = mod.evejs_root
    if root is not None:
        _activation_path_check(mod.path, root, "Legacy mod directory")

    active_loader = mod.path / "loader.js"
    disabled_loaders = [
        mod.path / filename
        for filename in _DISABLED_LOADER_FILENAMES
        if (mod.path / filename).exists()
    ]
    if root is not None:
        for candidate in (active_loader, *disabled_loaders):
            if candidate.exists() or candidate.is_symlink():
                _activation_file_check(candidate, root, "Legacy mod loader")

    if active_loader.exists() and disabled_loaders:
        raise ModActivationError(
            f"Cannot change '{mod.name}': active and disabled loaders both exist."
        )
    if len(disabled_loaders) > 1:
        raise ModActivationError(
            f"Cannot change '{mod.name}': multiple disabled loaders exist."
        )

    if desired:
        if active_loader.is_file():
            return True
        if not disabled_loaders:
            raise ModActivationError(
                f"No disabled loader was found for '{mod.name}'."
            )
        try:
            disabled_loaders[0].rename(active_loader)
        except OSError as exc:
            raise ModActivationError(
                f"Could not enable loader mod '{mod.name}'."
            ) from exc
        if not active_loader.is_file():
            raise ModActivationError(
                f"Loader mod '{mod.name}' did not become enabled."
            )
        return True

    if not active_loader.exists():
        return False
    disabled_loader = mod.path / "loader.js.disabled"
    if disabled_loader.exists():
        raise ModActivationError(
            f"Cannot disable '{mod.name}': loader.js.disabled already exists."
        )
    try:
        active_loader.rename(disabled_loader)
    except OSError as exc:
        raise ModActivationError(
            f"Could not disable loader mod '{mod.name}'."
        ) from exc
    if active_loader.exists() or not disabled_loader.is_file():
        raise ModActivationError(
            f"Loader mod '{mod.name}' did not become disabled."
        )
    return False


def _set_json_boolean_active(mod: Mod, desired: bool) -> bool:
    if (
        mod.evejs_root is None
        or mod.manifest_path is None
        or mod.config_path is None
        or mod.config_key != "enabled"
        or not mod.allowed_config_schema_versions
    ):
        raise ModActivationError(
            f"Integrated mod '{mod.name}' has incomplete activation metadata."
        )

    try:
        root = mod.evejs_root.resolve(strict=True)
    except OSError as exc:
        raise ModActivationError(
            f"The selected EveJS root for '{mod.name}' is no longer available."
        ) from exc
    _revalidate_integrated_activation_contract(mod, root)
    config_path = mod.config_path
    try:
        _activation_file_check(config_path, root, "Mod configuration")
        original_bytes = _read_bounded_bytes(
            config_path,
            MAX_CONFIG_BYTES,
            "Mod configuration",
        )
        original = _parse_json_object(original_bytes, "Mod configuration")
        _validate_config_document(original, mod.allowed_config_schema_versions)
    except (OSError, ModManifestError) as exc:
        raise ModActivationError(
            f"Could not validate configuration for '{mod.name}': {exc}"
        ) from exc

    if original["enabled"] is desired:
        mod.active = desired
        return desired

    updated = deepcopy(original)
    updated["enabled"] = desired
    original_without_state = dict(original)
    updated_without_state = dict(updated)
    original_without_state.pop("enabled")
    updated_without_state.pop("enabled")
    if _semantic_fingerprint(original_without_state) != _semantic_fingerprint(
        updated_without_state
    ):
        raise ModActivationError(
            f"Refusing to change unrelated configuration for '{mod.name}'."
        )

    updated_bytes = (
        json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    replaced = False
    try:
        _activation_file_check(config_path, root, "Mod configuration")
        if _read_bounded_bytes(
            config_path,
            MAX_CONFIG_BYTES,
            "Mod configuration",
        ) != original_bytes:
            raise ModActivationError(
                "The mod configuration changed while it was being edited. Refresh and retry."
            )
        _atomic_replace_bytes(config_path, updated_bytes)
        replaced = True

        verified_bytes = _read_bounded_bytes(
            config_path,
            MAX_CONFIG_BYTES,
            "Mod configuration",
        )
        verified = _parse_json_object(verified_bytes, "Mod configuration")
        _validate_config_document(verified, mod.allowed_config_schema_versions)
        verified_without_state = dict(verified)
        verified_without_state.pop("enabled")
        if verified["enabled"] is not desired or _semantic_fingerprint(
            verified_without_state
        ) != _semantic_fingerprint(original_without_state):
            raise ModActivationError(
                "The committed configuration did not match the requested state."
            )
    except Exception as exc:
        if replaced:
            try:
                current_bytes = _read_bounded_bytes(
                    config_path,
                    MAX_CONFIG_BYTES,
                    "Mod configuration",
                )
                if current_bytes != updated_bytes:
                    raise ModActivationError(
                        f"Changing '{mod.name}' failed and another process changed "
                        "its configuration afterward. The launcher did not overwrite "
                        "that newer file; manual recovery is required."
                    )
                _atomic_replace_bytes(config_path, original_bytes)
            except ModActivationError:
                raise
            except Exception as rollback_exc:
                raise ModActivationError(
                    f"Changing '{mod.name}' failed and its configuration could not "
                    "be restored. Stop the server and restore the file from backup."
                ) from rollback_exc
        if isinstance(exc, ModActivationError):
            raise
        raise ModActivationError(
            f"Could not change integrated mod '{mod.name}'."
        ) from exc

    return desired


def _revalidate_integrated_activation_contract(mod: Mod, root: Path) -> None:
    """Reject a cached integrated row whose installed owner disappeared or changed.

    This runs only after ``set_mod_active`` owns the target lifecycle lock.  It
    deliberately re-reads the immutable manifest instead of trusting metadata
    cached when the Mods page was populated.  An external uninstall may leave
    the editable config behind; that config alone never proves the mod is still
    installed or authorizes the launcher to mutate it.
    """

    expected_folder = root / "server" / "mods" / mod.id
    expected_manifest = expected_folder / MANIFEST_FILENAME
    expected_config = root / "config" / "mods" / f"{mod.id}.json"
    if (
        mod.path != expected_folder
        or mod.manifest_path != expected_manifest
        or mod.config_path != expected_config
    ):
        raise ModActivationError(
            f"Cannot change '{mod.name}': its cached integrated-mod paths no "
            "longer match the selected EveJS root. Refresh Mods and retry."
        )

    _activation_directory_check(mod.path, root, "Integrated mod directory")
    _activation_file_check(mod.manifest_path, root, "Mod manifest")
    try:
        current = _read_integrated_mod(
            root,
            mod.path,
            mod.manifest_path,
            active_override=mod.active,
        )
    except (OSError, ModManifestError) as exc:
        raise ModActivationError(
            f"Cannot change '{mod.name}': the integrated mod installation is "
            f"no longer safe: {exc} Refresh Mods and retry."
        ) from exc

    cached_contract = (
        mod.id,
        mod.name,
        mod.version,
        mod.description,
        mod.activation_kind,
        mod.supported_backends,
        mod.restart_scope,
        mod.path,
        mod.manifest_path,
        mod.config_path,
        mod.config_key,
        mod.allowed_config_schema_versions,
        mod.status_protocol,
        mod.status_transport,
    )
    current_contract = (
        current.id,
        current.name,
        current.version,
        current.description,
        current.activation_kind,
        current.supported_backends,
        current.restart_scope,
        current.path,
        current.manifest_path,
        current.config_path,
        current.config_key,
        current.allowed_config_schema_versions,
        current.status_protocol,
        current.status_transport,
    )
    if current_contract != cached_contract:
        raise ModActivationError(
            f"Cannot change '{mod.name}': its manifest contract changed since "
            "the Mods page was loaded. Refresh Mods and retry."
        )


def _read_and_validate_config(
    path: Path,
    allowed_schema_versions: tuple[int, ...],
) -> dict[str, object]:
    payload = _read_json_object(
        path,
        maximum_bytes=MAX_CONFIG_BYTES,
        label="Mod configuration",
    )
    _validate_config_document(payload, allowed_schema_versions)
    return payload


def _validate_config_document(
    payload: dict[str, object],
    allowed_schema_versions: tuple[int, ...],
) -> None:
    schema_version = payload.get("schemaVersion")
    if type(schema_version) is not int:
        raise ModManifestError(
            "Mod configuration schemaVersion must be an integer."
        )
    if schema_version not in allowed_schema_versions:
        raise ModManifestError(
            f"Unsupported mod configuration schemaVersion {schema_version!r}."
        )
    if "enabled" not in payload or type(payload["enabled"]) is not bool:
        raise ModManifestError(
            "Mod configuration must contain a top-level boolean 'enabled'."
        )


def _validate_config_relative_path(value: object, mod_id: str) -> PurePosixPath:
    path_text = _require_text(
        value,
        "Manifest activation configPath",
        maximum=240,
    )
    if "\\" in path_text or any(ord(character) < 32 for character in path_text):
        raise ModManifestError(
            "Manifest configPath must use safe forward-slash path components."
        )
    if any(character in path_text for character in ':*?<>|"'):
        raise ModManifestError("Manifest configPath contains an unsafe character.")
    raw_parts = path_text.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ModManifestError("Manifest configPath cannot traverse directories.")
    relative = PurePosixPath(path_text)
    if relative.is_absolute() or relative.parts[:2] != ("config", "mods"):
        raise ModManifestError(
            "Manifest configPath must be relative to config/mods/."
        )
    if relative.suffix.casefold() != ".json":
        raise ModManifestError("Manifest configPath must reference a JSON file.")
    for part in relative.parts:
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED_NAMES or part.endswith((" ", ".")):
            raise ModManifestError("Manifest configPath contains a reserved name.")
    expected = PurePosixPath("config", "mods", f"{mod_id}.json")
    if relative != expected:
        raise ModManifestError(
            f"Schema v2 configPath must be '{expected.as_posix()}'."
        )
    return relative


def _require_text(
    value: object,
    label: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise ModManifestError(f"{label} must be a string.")
    if value != value.strip():
        raise ModManifestError(f"{label} cannot have surrounding whitespace.")
    if (not value and not allow_empty) or len(value) > maximum:
        qualifier = f"1-{maximum}" if not allow_empty else f"0-{maximum}"
        raise ModManifestError(f"{label} must contain {qualifier} characters.")
    if any(ord(character) < 32 for character in value):
        raise ModManifestError(f"{label} cannot contain control characters.")
    return value


def _require_exact_keys(
    payload: dict[str, object],
    expected: set[str],
    label: str,
) -> None:
    actual = set(payload)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {', '.join(unknown)}")
    raise ModManifestError(f"{label} fields are invalid ({'; '.join(details)}).")


def _read_json_object(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
) -> dict[str, object]:
    return _parse_json_object(
        _read_bounded_bytes(path, maximum_bytes, label),
        label,
    )


def _read_bounded_bytes(path: Path, maximum_bytes: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
        if size > maximum_bytes:
            raise ModManifestError(
                f"{label} exceeds the {maximum_bytes}-byte size limit."
            )
        content = path.read_bytes()
        if len(content) > maximum_bytes:
            raise ModManifestError(
                f"{label} exceeds the {maximum_bytes}-byte size limit."
            )
        return content
    except ModManifestError:
        raise
    except OSError as exc:
        raise ModManifestError(f"{label} could not be read.") from exc


def _parse_json_object(content: bytes, label: str) -> dict[str, object]:
    if content.startswith(b"\xef\xbb\xbf"):
        raise ModManifestError(f"{label} must be UTF-8 without a BOM.")
    try:
        text = content.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: _reject_json_constant(value),
        )
        _reject_unicode_surrogates(payload)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise ModManifestError(f"{label} is not strict UTF-8 JSON: {exc}") from exc
    if type(payload) is not dict:
        raise ModManifestError(f"{label} root must be an object.")
    return payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate property {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite number {value!r}")


def _reject_unicode_surrogates(value: object) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("Unicode surrogate code points are not allowed")
        return
    if type(value) is dict:
        for key, item in value.items():
            _reject_unicode_surrogates(key)
            _reject_unicode_surrogates(item)
        return
    if type(value) is list:
        for item in value:
            _reject_unicode_surrogates(item)


def _semantic_fingerprint(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _atomic_replace_bytes(path: Path, content: bytes) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file does not exist: {path.name}")
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _activation_path_check(path: Path, root: Path, label: str) -> None:
    try:
        _require_within_root(path, root, label)
    except (OSError, ModManifestError) as exc:
        raise ModActivationError(f"{label} is no longer safe: {exc}") from exc


def _activation_directory_check(path: Path, root: Path, label: str) -> None:
    try:
        if not path.is_dir():
            raise ModManifestError(
                f"{label} does not exist or is not a directory."
            )
        _require_within_root(path, root, label)
    except (OSError, ModManifestError) as exc:
        raise ModActivationError(f"{label} is no longer safe: {exc}") from exc


def _activation_file_check(path: Path, root: Path, label: str) -> None:
    try:
        _require_regular_file_within_root(path, root, label)
    except (OSError, ModManifestError) as exc:
        raise ModActivationError(f"{label} is no longer safe: {exc}") from exc


def _require_regular_file_within_root(path: Path, root: Path, label: str) -> None:
    if not path.is_file():
        raise ModManifestError(f"{label} does not exist or is not a regular file.")
    _require_within_root(path, root, label)


def _require_within_root(path: Path, root: Path, label: str) -> None:
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ModManifestError(f"{label} escapes the selected EveJS root.") from exc
