"""Public mod-management API retained for existing launcher imports."""

from .mod_activation_service import request_mod_activation
from .mod_manifest import (
    ActivationKind,
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    RUNTIME_STATUS_PROTOCOL,
    RUNTIME_STATUS_TRANSPORT,
    Mod,
    ModActivationError,
    ModManagerError,
    ModManifestError,
    active_loader_mods,
    active_loader_names,
    legacy_mods_directory,
    scan_mods,
    set_mod_active,
    set_mod_active_locked,
    toggle_mod,
)


__all__ = [
    "ActivationKind",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "RUNTIME_STATUS_PROTOCOL",
    "RUNTIME_STATUS_TRANSPORT",
    "Mod",
    "ModActivationError",
    "ModManagerError",
    "ModManifestError",
    "active_loader_mods",
    "active_loader_names",
    "legacy_mods_directory",
    "scan_mods",
    "request_mod_activation",
    "set_mod_active",
    "set_mod_active_locked",
    "toggle_mod",
]
