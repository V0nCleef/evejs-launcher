"""Read-only inventory for the Mods page, suitable for a background worker."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .dlss5 import discover_dlss5_client_mod
from .local_mod_packages import (
    LocalModPackages, LocalModRecord, RegistryRelocationPreview,
)
from .mod_activation_state import ModActivationState, read_mod_activation_state
from .mod_api_runtime import public_package_owns_legacy_folder
from .mod_management import (
    ManagedModRegistration, ModManagementError, ModNotManagedError,
    managed_mod_registry_path, read_managed_mod_registration,
)
from . import mod_management
from .mod_manifest import ActivationKind, Mod, scan_mods
from .mod_relationships import plan_mod_order


def folder_key(mod: Mod) -> str:
    return str(mod.path.resolve()).casefold()


@dataclass(frozen=True)
class ModInventory:
    root: str
    mods: tuple[Mod, ...] = ()
    activation_state: ModActivationState | None = None
    state_error: str = ""
    management: dict[str, ManagedModRegistration] = field(default_factory=dict)
    management_errors: dict[str, str] = field(default_factory=dict)
    local_records: dict[str, LocalModRecord] = field(default_factory=dict)
    local_removable: frozenset[str] = frozenset()
    quarantined: tuple[LocalModRecord, ...] = ()
    registry_error: str = ""
    recovery_pending: bool = False
    update_recovery_pending: bool = False
    evejs_version: str | None = None
    relocation_preview: RegistryRelocationPreview | None = None


def load_mod_inventory(root: str) -> ModInventory:
    if not root.strip() or not Path(root).is_dir():
        return ModInventory(root)
    mods = scan_mods(root)
    from .mod_evejs_compatibility import installed_evejs_version
    evejs_version = installed_evejs_version(root)
    if not public_package_owns_legacy_folder(root, "mods/DLSS5", mods=mods):
        legacy_client = discover_dlss5_client_mod(root)
        if legacy_client is not None:
            mods.append(legacy_client)
    state, state_error, registry_error = None, "", ""
    try:
        state = read_mod_activation_state(root)
    except (OSError, ValueError) as exc:
        state_error = str(exc)
    management, errors, local = {}, {}, {}
    removable, quarantined = set(), ()
    pending = False
    update_pending = False
    relocation_preview = None
    relocation_preview_error = ""
    try:
        from .mod_updates import pending_updates
        update_pending = bool(pending_updates(root))
    except (OSError, ValueError, RuntimeError) as exc:
        registry_error = str(exc)
        update_pending = True
    try:
        packages = LocalModPackages(root)
        try:
            relocation_preview = packages.relocation_preview()
        except (OSError, ValueError, RuntimeError) as exc:
            # Ordinary roots, invalid copied registries, and unsafe/pending
            # copies all continue through the normal strict ownership path.
            relocation_preview = None
            relocation_preview_error = str(exc)
        if relocation_preview is None:
            mods = packages.sort_mods(mods)
        else:
            # A valid copied registry can supply ordering for display, but
            # its records do not grant removal ownership before confirmation.
            mods = packages.sort_mods_for_runtime(mods)
        plan = plan_mod_order(mods)
        mods = list(plan.mods)
        if plan.issues:
            state_error = "\n".join(filter(None, [state_error, *(issue.message for issue in plan.issues)]))
        records = () if relocation_preview is not None else packages.records()
        local = {str((Path(root) / record.relative_path).resolve()).casefold(): record
                 for record in records if record.status == "installed"}
        quarantined = tuple(record for record in records if record.status == "quarantined")
        pending = any(record.transaction is not None for record in records)
        for mod in mods:
            if packages.can_manage(mod):
                removable.add(folder_key(mod))
    except (OSError, ValueError, RuntimeError) as exc:
        # Strict ownership rejects foreign roots. When preview found a more
        # useful reason (such as pending recovery), keep that diagnostic.
        failure = (
            relocation_preview_error
            if relocation_preview_error
            and "belongs to another EveJS root" in str(exc)
            else str(exc)
        )
        details = [message for message in (registry_error, failure) if message]
        registry_error = "\n".join(dict.fromkeys(details))
        relocation_preview = None
    for mod in mods:
        key = folder_key(mod)
        if mod.activation_kind is ActivationKind.CLIENT_PACKAGE:
            continue
        try:
            managed_mod_registry_path(mod.id)
        except ModManagementError:
            continue
        try:
            management[key] = mod_management.read_managed_mod_registration(mod)
        except ModNotManagedError:
            pass
        except ModManagementError as exc:
            errors[key] = str(exc)
    return ModInventory(root, tuple(mods), state, state_error, management, errors,
                        local, frozenset(removable), quarantined, registry_error, pending, update_pending,
                        evejs_version, relocation_preview)
