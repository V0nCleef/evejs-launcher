"""Frozen portable server preparation, executed before Game acquires its state."""
from pathlib import Path

from src.core.mod_manifest import scan_mods
from src.core.mod_relationships import plan_mod_order
from src.core.mod_runtime_state import mod_contract_sha256
from src.core.mod_operations import ModOperationContext, run_docker_helper_locked
from src.core.mod_settings import ModSettingsContext


def capture_docker_mod_preparation(target, client_root=None):
    root = target.project_directory.resolve(strict=True)
    plan = plan_mod_order(scan_mods(root), backend="docker")
    selected = tuple(mod for mod in plan.mods if mod.valid and mod.active and mod.supports_backend("docker")
        and mod.api_descriptor is not None and mod.api_descriptor.docker is not None
        and "prepare_server_start" in mod.api_descriptor.docker.server_actions)
    plan.require_valid(selected)
    if not selected:
        return None
    signature = tuple((mod.id, mod_contract_sha256(mod)) for mod in selected)
    client = Path(client_root).resolve(strict=True) if client_root else None

    def prepare():
        fresh = {mod.id: mod for mod in scan_mods(root)}
        if any(mod.id not in fresh or not fresh[mod.id].active
               or mod_contract_sha256(fresh[mod.id]) != digest for mod, (_, digest) in zip(selected, signature)):
            raise RuntimeError("The Docker preparation mod set changed. Retry the selected start.")
        operation = ModOperationContext(root, client, "docker", docker_target_factory=lambda: target)
        for mod in selected:
            run_docker_helper_locked(mod.api_descriptor, "prepare_server_start",
                ModSettingsContext(root, mod.path, client), operation).require_ready()

    return prepare
