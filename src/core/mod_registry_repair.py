"""Explicit, evidence-preserving repair of redundant installed-folder claims.

This never repairs JSON corruption, adopts an external package, modifies a mod,
or decides which interrupted transaction should win. Ordinary readers remain
strict. Only this review path can inspect otherwise-valid duplicate claims.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib

from .local_mod_packages import (
    LocalModPackageError, LocalModPackages, RegistryRepairPreview, _fingerprint,
    _ordinary, _relative,
)
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_manifest import scan_mods


def _snapshot(packages: LocalModPackages):
    registry, _owns_root, content = packages._read_registry(
        allow_foreign_root=False, allow_duplicate_folders=True,
    )
    if content is None:
        raise LocalModPackageError("There is no saved mod registry to repair.")
    packages._no_pending(registry)
    from .mod_updates import pending_updates
    if pending_updates(packages.root):
        raise LocalModPackageError("Recover the pending mod update before reviewing ownership repair.")
    groups = defaultdict(list)
    for key, row in registry['records'].items():
        record = packages._record(key, row)
        if record.status == 'installed':
            groups[_relative(record.relative_path).as_posix().casefold()].append(record)
    duplicates = {key: records for key, records in groups.items() if len(records) > 1}
    if not duplicates:
        raise LocalModPackageError("The mod registry has no duplicate installed-folder claims.")
    if len(duplicates) > 64:
        raise LocalModPackageError("Too many conflicting folders need individual ownership review. The registry was left unchanged.")
    mods = scan_mods(packages.root)
    removed, retained, fingerprints = [], [], []
    for key, records in sorted(duplicates.items()):
        if any(record.transaction or record.cleanup is not None or record.archive_path for record in records):
            raise LocalModPackageError("A conflicting record has cleanup or recovery information. Keep the registry for individual review.")
        signatures = {(r.mod_id, r.package_kind, r.enabled, r.public_descriptor) for r in records}
        if len(signatures) != 1:
            raise LocalModPackageError("Conflicting records disagree about mod identity or configured state. No repair was selected.")
        folder = packages._path(records[0].relative_path)
        info = _ordinary(folder, directory=True)
        for record in records[1:]:
            other = _ordinary(packages._path(record.relative_path), directory=True)
            if (other.st_dev, other.st_ino) != (info.st_dev, info.st_ino):
                raise LocalModPackageError("The conflicting paths identify different physical folders. No repair was selected.")
        candidates = [mod for mod in mods if mod.path.resolve() == folder.resolve()]
        if len(candidates) != 1 or not candidates[0].valid:
            raise LocalModPackageError("The installed package cannot be verified for ownership repair. Its records were left unchanged.")
        mod = candidates[0]
        record = records[0]
        kind = mod.api_descriptor.kind if mod.api_descriptor is not None else 'loader'
        if (record.mod_id != mod.id or record.package_kind != kind
                or record.public_descriptor != (mod.api_descriptor is not None)
                or (record.enabled is not None and record.enabled != mod.active)):
            raise LocalModPackageError("The installed package does not match its recorded identity or state. No repair was selected.")
        fingerprint = _fingerprint(folder, packages.limits)
        matches = [r for r in records if r.fingerprint == fingerprint]
        if not matches:
            raise LocalModPackageError("No conflicting record matches the installed package files. Keep the registry for individual review.")
        # Equal fingerprints plus equal identity/state and no cleanup/archive or
        # transaction references make these claims equivalent. Order is never
        # treated as age. Keep one deterministic, already-existing owner ID.
        selected = min(matches, key=lambda item: item.record_id)
        retained.append(selected.record_id)
        removed.extend(r.record_id for r in records if r.record_id != selected.record_id)
        fingerprints.append((key, fingerprint))
    preview = RegistryRepairPreview(
        root=str(packages.root), registry_sha256=hashlib.sha256(content).hexdigest(),
        removed_record_ids=tuple(sorted(removed)), retained_record_ids=tuple(sorted(retained)),
        duplicate_folder_count=len(duplicates), package_fingerprints=tuple(fingerprints),
    )
    return registry, content, preview


def _checked_snapshot(packages):
    try:
        return _snapshot(packages)
    except LocalModPackageError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise LocalModPackageError(
            "Could not verify the mod files and recovery state. The registry was left unchanged."
        ) from None


def registry_repair_preview(packages: LocalModPackages) -> RegistryRepairPreview:
    return _checked_snapshot(packages)[2]


def repair_registry(packages: LocalModPackages, preview: RegistryRepairPreview):
    if not isinstance(preview, RegistryRepairPreview) or preview.root != str(packages.root):
        raise LocalModPackageError("Refresh Mods and review ownership repair for the selected folder.")
    with acquire_mod_lifecycle_lock(packages.root):
        registry, content, current = _checked_snapshot(packages)
        if current != preview:
            raise LocalModPackageError("Mod ownership or package files changed after review. Refresh Mods before repairing.")
        try:
            backup = packages._backup_registry(content, purpose='duplicate-repair')
        except (OSError, ValueError, RuntimeError):
            raise LocalModPackageError("Could not create a verified registry backup. No ownership records were changed.") from None
        reread, second_content, second_preview = _checked_snapshot(packages)
        if second_content != content or second_preview != preview or reread != registry:
            raise LocalModPackageError("Mod ownership changed while creating its backup. The reviewed registry was not replaced.")
        repaired = {**registry, 'records': {key: row for key, row in registry['records'].items()
                                          if key not in preview.removed_record_ids}}
        try:
            packages._save(repaired, expected_content=content)
            if packages._load() != repaired:
                raise LocalModPackageError("The repaired mod registry could not be verified.")
        except Exception:
            raise LocalModPackageError(
                f"Ownership repair did not finish. The original registry backup is at {backup}."
            ) from None
        return backup.resolve(strict=True)
