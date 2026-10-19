"""Opt-in mod preparation before Native services acquire their files."""
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path

from .local_mod_packages import LocalModPackages
from .mod_api_runtime import MAX_HELPER_BYTES, run_mod_helper_locked
from .mod_manifest import ActivationKind, Mod, scan_mods
from .mod_relationships import plan_mod_order
from .mod_settings import ModSettingsContext

log = logging.getLogger(__name__)
CAPABILITY = "prepare_server_start"


def _signature(mods):
    result = []
    for mod in mods:
        descriptor = mod.api_descriptor
        helper = descriptor.launcher_api.helper
        with helper.path.open("rb") as stream:
            content = stream.read(MAX_HELPER_BYTES + 1)
        if len(content) > MAX_HELPER_BYTES:
            raise ValueError("The server preparation helper exceeds the supported size.")
        result.append((descriptor, hashlib.sha256(content).hexdigest()))
    return tuple(result)


@dataclass(frozen=True)
class NativeModPreparation:
    root: Path
    mods: tuple[Mod, ...]
    signature: tuple

    def run_locked(self) -> None:
        """Caller retains the root's lifecycle lease through service startup."""
        current = capture_native_mod_preparation(self.root)
        if current is None or current.signature != self.signature:
            raise RuntimeError("The server preparation mod set or helper changed. Retry startup.")
        for mod in self.mods:
            result = run_mod_helper_locked(mod.api_descriptor, CAPABILITY,
                ModSettingsContext(self.root, mod.path), backend="native").require_ready()
            if result.contributions or result.environment or result.arguments:
                raise RuntimeError("Server preparation cannot change client settings or launch options.")
            log.info("Native server preparation verified mod=%s version=%s", mod.id, mod.version)


def capture_native_mod_preparation(root, *, mods=None) -> NativeModPreparation | None:
    root = Path(root)
    if not root.is_dir():
        return None
    root = root.resolve(strict=True)
    if mods is None:
        mods = LocalModPackages(root).sort_mods_for_runtime(scan_mods(root))
    order = plan_mod_order(mods, backend="native")
    mods = order.mods
    selected = tuple(mod for mod in mods if mod.valid and mod.active
        and "native" in mod.supported_backends
        and mod.activation_kind in {ActivationKind.LOADER_RENAME, ActivationKind.JSON_BOOLEAN}
        and mod.api_descriptor is not None and mod.api_descriptor.launcher_api is not None
        and CAPABILITY in mod.api_descriptor.launcher_api.capabilities)
    order.require_valid(selected)
    return NativeModPreparation(root, selected, _signature(selected)) if selected else None
