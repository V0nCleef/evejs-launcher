"""Review and save the same dependency order used by the runtime planner."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .local_mod_packages import LocalModPackages, LocalModPackageError
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_manifest import scan_mods
from .mod_relationships import plan_mod_order
from .mod_runtime_state import mod_contract_sha256


@dataclass(frozen=True)
class ModSortEntry:
    key: str
    name: str
    folder: str
    active: bool
    # Manifest terms are kept as data; the dialog translates their labels.
    rules: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True)
class ModSortPreview:
    root: Path
    backend: str
    saved: tuple[ModSortEntry, ...]
    proposed: tuple[ModSortEntry, ...]
    issues: tuple[str, ...]
    signature: str

    @property
    def changed(self) -> bool:
        return tuple(item.key for item in self.saved) != tuple(item.key for item in self.proposed)


def _capture(root: Path, backend: str):
    if backend not in {"native", "docker"}:
        raise ValueError("Select a supported runtime before sorting mods.")
    packages = LocalModPackages(root)
    registry, _owns_root, content = packages._read_registry(allow_foreign_root=False)
    packages._no_pending(registry)
    from .mod_updates import pending_updates
    if pending_updates(root):
        raise LocalModPackageError("Recover pending mod updates before sorting mods.")
    mods = packages._ordered(scan_mods(root), registry)
    issues, contracts, entries = [], [], {}
    for mod in mods:
        key = packages.mod_key(mod)
        descriptor = mod.api_descriptor
        rules = () if descriptor is None else tuple((name, tuple(values)) for name, values in (
            ("requires", descriptor.requires), ("loadBefore", descriptor.load_before),
            ("loadAfter", descriptor.load_after), ("conflicts", descriptor.conflicts)) if values)
        entry = ModSortEntry(key, mod.name, mod.path.relative_to(root).as_posix(), mod.active, rules)
        entries[key] = entry
        error = mod.descriptor_error or (mod.error if not mod.valid else None)
        contract = None
        if error:
            issues.append(f"{mod.name}: {error}")
        else:
            try:
                contract = mod_contract_sha256(mod)
            except (OSError, ValueError, RuntimeError) as exc:
                issues.append(f"{mod.name}: {exc}")
        if mod.active and backend not in mod.supported_backends:
            issues.append(f"{mod.name}: this enabled mod does not declare support for {backend}.")
        contracts.append((key, mod.active, mod.valid, error, contract, mod.version, mod.supported_backends))
    plan = plan_mod_order(mods, backend=backend)
    issues.extend(issue.message for issue in plan.issues)
    signature = hashlib.sha256(json.dumps({
        "root": str(root), "backend": backend,
        "registry": None if content is None else hashlib.sha256(content).hexdigest(),
        "mods": contracts,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    preview = ModSortPreview(root, backend,
        tuple(entries[packages.mod_key(mod)] for mod in mods),
        tuple(entries[packages.mod_key(mod)] for mod in plan.mods),
        tuple(dict.fromkeys(issues)), signature)
    return preview, packages, registry, content


def preview_mod_sort(root: str | Path, *, backend: str) -> ModSortPreview:
    """Read only: no registry, activation, helper, Docker or process mutations."""
    return _capture(Path(root).resolve(strict=True), backend)[0]


def apply_mod_sort(preview: ModSortPreview, *, root: str | Path, backend: str,
                   docker_managed: bool = False) -> bool:
    """Recompute under the root lease; save only the explicitly reviewed order."""
    root = Path(root).resolve(strict=True)
    if root != preview.root or backend != preview.backend:
        raise LocalModPackageError("The selected installation or runtime changed. Review the order again.")
    if backend == "docker" and not docker_managed:
        raise LocalModPackageError("Connect-only Docker mode cannot change the saved mod order.")
    with acquire_mod_lifecycle_lock(root):
        current, packages, registry, content = _capture(root, backend)
        if current.signature != preview.signature:
            raise LocalModPackageError("Mods or their saved order changed after preview. Review the order again.")
        if current.issues:
            raise LocalModPackageError("\n".join(current.issues))
        if not current.changed:
            return False
        keys = [item.key for item in current.proposed]
        registry["order"] = keys + [key for key in registry["order"] if key not in keys]
        packages._save(registry, expected_content=content, expected_absent=content is None)
        return True
