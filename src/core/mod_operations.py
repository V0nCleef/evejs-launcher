"""Package-neutral lifecycle actions shared by the Mods UI and tests."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
import os
from pathlib import Path

from .local_mod_packages import CleanupDecision, CleanupRequest, LocalModCleanupPending, LocalModPackages
from .mod_activation_service import request_mod_activation
from .mod_api_runtime import (
    commit_helper_contributions_locked, helper_coordination_root, run_mod_helper_locked,
)
from .mod_contributions import ContributionOwner, ContributionStore, RemovalEditConflict, RemovalReviewRequired
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_client_delivery import record_delivery
from .mod_client_preparation import record_preparation
from .mod_manifest import ActivationKind, Mod, scan_mods, set_mod_active_locked
from .mod_settings import ModSettingsContext
from .profiles import PROFILES_ROOT


@dataclass(frozen=True)
class ModOperationContext:
    evejs_root: Path
    client_root: Path | None = None
    backend: str = "native"
    profiles_root: Path = PROFILES_ROOT
    local_appdata: Path | None = None

    def capture(self, mod: Mod) -> ModSettingsContext:
        context = ModSettingsContext(self.evejs_root, mod.path, self.client_root)
        if mod.evejs_root is None or mod.evejs_root.resolve() != context.evejs_root:
            raise RuntimeError("The mod belongs to a different EveJS installation. Refresh Mods.")
        if self.backend not in mod.supported_backends:
            raise RuntimeError("This mod does not support the selected backend.")
        return context

    def current(self, mod: Mod) -> Mod:
        context = self.capture(mod)
        matches = [item for item in scan_mods(context.evejs_root) if item.path == context.mod_folder]
        if len(matches) != 1:
            raise RuntimeError("The selected mod is no longer installed. Refresh Mods.")
        current = matches[0]
        if (current.id, current.api_descriptor, current.activation_kind) != (mod.id, mod.api_descriptor, mod.activation_kind):
            raise RuntimeError("The mod declaration changed. Refresh Mods before retrying.")
        return current


def _helper_locked(mod: Mod, action: str, operation: ModOperationContext):
    context = operation.capture(mod)
    descriptor = mod.api_descriptor
    if descriptor is None:
        raise RuntimeError("This mod has no public lifecycle helper.")
    root = helper_coordination_root(descriptor, context, action)
    # Loader helpers can also own a client companion. Serialize their explicit
    # installation/cleanup against other roots preparing or spawning that client.
    lock_root = context.client_root if (context.client_root is not None and
        descriptor.launcher_api is not None and 'prepare_profile' in descriptor.launcher_api.capabilities) else root
    # The outer EveJS lease prevents package replacement. Client helpers also
    # serialize against every launch/settings save using that physical client.
    lease = nullcontext() if lock_root == context.evejs_root else acquire_mod_lifecycle_lock(lock_root)
    with lease:
        result = run_mod_helper_locked(descriptor, action, context, backend=operation.backend)
        if result.success and result.state == "ready":
            commit_helper_contributions_locked(result)
            record_preparation(result)
            record_delivery(result, operation.backend)
        return result


def run_public_action(mod: Mod, action: str, operation: ModOperationContext):
    with acquire_mod_lifecycle_lock(operation.evejs_root):
        current = operation.current(mod)
        return _helper_locked(current, action, operation)


def _remove_shared_contributions(mod: Mod, operation: ModOperationContext) -> None:
    context = operation.capture(mod)
    global_owner = ContributionOwner(context.evejs_root, context.mod_folder.relative_to(context.evejs_root).as_posix())
    roots = {context.evejs_root}
    if context.client_root is not None:
        roots.add(context.client_root)
    allowed = set(roots)
    profiles_anchor = operation.profiles_root.parent.resolve(strict=False)
    if profiles_anchor.is_dir():
        allowed.add(profiles_anchor)
    local_appdata = operation.local_appdata or (Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None)
    if local_appdata is not None and local_appdata.is_dir():
        allowed.add(local_appdata.resolve())
    for root in sorted(roots, key=str):
        lease = nullcontext() if root == context.evejs_root else acquire_mod_lifecycle_lock(root)
        with lease:
            store = ContributionStore(root, allowed_roots=allowed)
            owners = store.owners_for_mod(global_owner)
            if not owners:
                continue
            preserved = [context.mod_folder]
            for owner in owners:
                if not owner.profile_id:
                    continue
                profile_root = Path(owner.profile_id)
                if profile_root.parent != operation.profiles_root:
                    raise RuntimeError("A recorded profile is outside the launcher's profile storage. Its settings were preserved.")
                private = replace(context, profile_id=owner.profile_id, profile_root=profile_root).mod_data_root
                preserved.append(private)
            try:
                plan = store.plan_remove_batch_locked(owners, preserve_roots=preserved)
            except RemovalEditConflict:
                raise RemovalReviewRequired(store.review_remove_locked(owners, preserve_roots=preserved)) from None
            store.commit_locked(plan)


def cleanup_gate(operation: ModOperationContext):
    """Called under the package's root lease before any unload or folder move."""
    def prepare(request):
        mod = operation.current(request.mod)
        api = mod.api_descriptor.launcher_api if mod.api_descriptor else None
        action = "prepare_remove" if request.action == "remove" else "prepare_disable"
        if api is not None and action in api.capabilities:
            result = _helper_locked(mod, action, operation)
            if not result.success or result.state != "ready":
                return CleanupDecision(False, result.state, result.message, bool(result.restart_required))
        elif mod.activation_kind is ActivationKind.CLIENT_PACKAGE:
            return CleanupDecision(False, "provider-missing", "This client mod must declare a cleanup action before it can be disabled or removed.")
        _remove_shared_contributions(mod, operation)
        return CleanupDecision(True, message="Cleanup is complete.")
    return prepare


def change_mod_state(mod: Mod, desired: bool, operation: ModOperationContext) -> bool:
    current = operation.current(mod)
    if not current.valid:
        raise RuntimeError(current.error or "This mod declaration is invalid.")
    packages = LocalModPackages(operation.evejs_root)
    if not desired and packages.can_manage(current):
        return packages.disable(current, cleanup_gate=cleanup_gate(operation))
    if current.activation_kind in {ActivationKind.CLIENT_PACKAGE, ActivationKind.PACKAGE}:
        record = packages.adopt(current)
        def apply(public_mod, enabled):
            api = public_mod.api_descriptor.launcher_api
            if enabled and api is not None and "install" in api.capabilities:
                _helper_locked(public_mod, "install", operation).require_ready()
            packages.set_enabled_locked(record.record_id, enabled, cleanup_gate=cleanup_gate(operation))
            return enabled
        return request_mod_activation(current, desired, mutation=apply)
    if not desired and current.api_descriptor is not None:
        def disable(public_mod, enabled):
            decision = cleanup_gate(operation)(CleanupRequest(
                operation.evejs_root, "disable", public_mod, None, "",
            ))
            if not decision.ready:
                raise LocalModCleanupPending(decision)
            return set_mod_active_locked(public_mod, enabled)
        return request_mod_activation(current, desired, mutation=disable)
    api = current.api_descriptor.launcher_api if current.api_descriptor else None
    if desired and api is not None and "install" in api.capabilities:
        def enable(public_mod, enabled):
            _helper_locked(public_mod, "install", operation).require_ready()
            return set_mod_active_locked(public_mod, enabled)
        return request_mod_activation(current, desired, mutation=enable)
    return request_mod_activation(current, desired)


def remove_local_mod(mod: Mod, operation: ModOperationContext):
    current = operation.current(mod)
    return LocalModPackages(operation.evejs_root).remove(current, cleanup_gate=cleanup_gate(operation))
