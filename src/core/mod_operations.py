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
    docker_target_factory: object | None = None
    docker_target_identity: str | None = None
    docker_base_target_factory: object | None = None

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


def run_docker_helper_locked(descriptor, action, context, operation, *, update=None, image_prepared=False):
    """Shared dispatch for UI, frozen client launches and durable updates."""
    from .runtime.docker_mod_helpers import DockerModHelperController
    from .runtime.docker_compose import ComposeInspector
    from .runtime.docker_cli import DockerCommandRunner
    from .service_status import DockerControlPolicy
    from .mod_api_runtime import ModHelperResult, _settings_values
    from .mod_api_manifest import read_api_manifest
    from src.constants import APP_VERSION
    spec = descriptor.docker
    if spec is None or action not in spec.server_actions or not callable(operation.docker_target_factory):
        raise RuntimeError("A captured declared Docker helper target is required.")
    api = descriptor.launcher_api
    if ("docker" not in descriptor.supported_backends or api is None or action not in api.capabilities
            or read_api_manifest(context.evejs_root,context.mod_folder) != descriptor):
        raise RuntimeError("The Docker lifecycle descriptor changed or does not declare this action.")
    if tuple(map(int,api.min_launcher_version.split("."))) > tuple(map(int,APP_VERSION.split("."))):
        raise RuntimeError(f"This mod requires launcher {api.min_launcher_version} or later.")
    runner = DockerCommandRunner(output_limit=128 * 1024)
    inspector = ComposeInspector(runner)
    host = {}
    if action in {"install", "recover", "prepare_server_start"} and "prepare_host" in spec.host_actions:
        if context.client_root is None:
            raise RuntimeError("Select a physical EVE client before preparing this mod.")
        with acquire_mod_lifecycle_lock(context.client_root):
            proof = run_mod_helper_locked(descriptor, "prepare_host", context, backend="docker").require_ready()
            host = dict(proof.host_capabilities or {})
    image = spec.image
    if image is not None and not image_prepared and action in {"install", "recover", "apply_update"}:
        from .runtime.docker_mod_images import stage_image
        if not callable(operation.docker_base_target_factory):
            raise RuntimeError("A captured base-image target is required.")
        stage_image(descriptor, operation.docker_base_target_factory(), inspector, runner,
                    **({"transaction_id":update["transactionId"]} if update is not None else {}))
    # The saved preload/image override can intentionally describe the previous
    # launch while a stopped world's mod switches are being edited. Cleanup
    # runs with NODE_OPTIONS cleared and must use the captured base authority,
    # not try to validate that obsolete loader list against the new state.
    if (action in {"install", "recover", "prepare_disable", "prepare_remove"} or action.endswith("_update")) and callable(operation.docker_base_target_factory):
        target, expected = operation.docker_base_target_factory(), None
    else:
        target, expected = operation.docker_target_factory(), operation.docker_target_identity
    controller = DockerModHelperController(target, inspector, runner,
        policy=DockerControlPolicy.MANAGED, expected_identity=expected)
    request_id, value = controller.execute(descriptor, action,
        settings=_settings_values(descriptor, context), host_capabilities=host, update=update)
    return ModHelperResult(descriptor, context, action, request_id, value["success"], value["state"],
        value["message"], tuple(value.get("restartRequired", ())), (), (), {}, (), None,
        descriptor.manifest_path, value.get("clientScriptDelivery"))


def _helper_locked(mod: Mod, action: str, operation: ModOperationContext, *, image_prepared=False):
    context = operation.capture(mod)
    descriptor = mod.api_descriptor
    if descriptor is None:
        raise RuntimeError("This mod has no public lifecycle helper.")
    if operation.backend == "docker" and descriptor.docker is not None:
        if action in descriptor.docker.server_actions:
            return run_docker_helper_locked(descriptor, action, context, operation, image_prepared=image_prepared)
        if action not in descriptor.docker.host_actions:
            raise RuntimeError("The Docker contract has not declared this host action.")
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
    preparation = None
    spec = getattr(current.api_descriptor,"docker",None)
    if desired and operation.backend == "docker" and spec is not None and spec.image is not None:
        def preparation(public_mod, enabled):
            from .runtime.docker_mod_images import stage_image
            from .runtime.docker_compose import ComposeInspector
            from .runtime.docker_cli import DockerCommandRunner
            if not callable(operation.docker_base_target_factory):
                raise RuntimeError("A captured base-image target is required.")
            runner = DockerCommandRunner(output_limit=128*1024)
            stage_image(public_mod.api_descriptor,operation.docker_base_target_factory(),ComposeInspector(runner),runner)
    prepared_args = {"preparation":preparation} if preparation is not None else {}
    if not desired and packages.can_manage(current):
        return packages.disable(current, cleanup_gate=cleanup_gate(operation))
    if current.activation_kind in {ActivationKind.CLIENT_PACKAGE, ActivationKind.PACKAGE}:
        record = packages.adopt(current)
        def apply(public_mod, enabled):
            api = public_mod.api_descriptor.launcher_api
            if enabled and api is not None and "install" in api.capabilities:
                _helper_locked(public_mod, "install", operation, **({"image_prepared":True} if preparation else {})).require_ready()
            packages.set_enabled_locked(record.record_id, enabled, cleanup_gate=cleanup_gate(operation))
            return enabled
        return request_mod_activation(current, desired, mutation=apply, **prepared_args)
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
            _helper_locked(public_mod, "install", operation, **({"image_prepared":True} if preparation else {})).require_ready()
            return set_mod_active_locked(public_mod, enabled)
        return request_mod_activation(current, desired, mutation=enable, **prepared_args)
    return request_mod_activation(current, desired)


def remove_local_mod(mod: Mod, operation: ModOperationContext):
    current = operation.current(mod)
    return LocalModPackages(operation.evejs_root).remove(current, cleanup_gate=cleanup_gate(operation))
