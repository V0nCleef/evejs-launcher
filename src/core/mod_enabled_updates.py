"""Opt-in enabled-to-enabled package replacement with a producer-owned handoff.

The incoming provider owns native snapshots/rollback. The launcher owns package
folders and its durable commit decision; neither runs financial recovery.
"""
from dataclasses import replace
import logging
import json
from pathlib import Path
import shutil
import uuid

from .local_mod_packages import LocalModPackages, LocalModPackageError, _fingerprint, _ordinary
from .mod_api_manifest import ENABLED_UPDATE_PROTOCOL, read_api_manifest, _relative_path, _safe_path
from .mod_api_runtime import run_mod_helper_locked, validate_enabled_update_context
from .mod_activation_state import prepare_mod_activation, mark_mod_activation_pending
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_loader_state import resolve_loader_state
from .mod_manifest import scan_mods, set_mod_active_locked
from .mod_settings import ModSettingsContext
from .mod_update_source import ModUpdateError

PHASES = frozenset({"prepared", "preparing", "handoff_ready", "quiescing", "swapping", "installed",
                    "applying", "applied", "enabling", "enabled", "committing", "complete",
                    "rolling_back", "restoring", "rolled_back"})


def package_fingerprint(folder, packages):
    selected = resolve_loader_state(folder, root=packages.root).selected_path
    if selected is None:
        raise ModUpdateError("An enabled update package needs one ordinary loader.")
    return _fingerprint(folder, packages.limits, canonical_names={selected.name: "loader.js"})


def _package(packages, folder, data, *, previous):
    descriptor = read_api_manifest(packages.root, folder)
    version = data["oldVersion"] if previous else data["newVersion"]
    fingerprint = data["oldFingerprint"] if previous else data["newFingerprint"]
    if (descriptor.id != data["modId"] or descriptor.version != version
            or package_fingerprint(folder, packages) != fingerprint):
        raise ModUpdateError("An enabled update package changed; its files were retained for recovery.")
    return descriptor


def _installed(packages, target):
    matches = [mod for mod in scan_mods(packages.root) if mod.path == target]
    if len(matches) != 1 or not matches[0].valid:
        raise ModUpdateError("The enabled update target cannot be identified safely.")
    return matches[0]


def _activate(mod):
    # Caller owns the root lease. Temporary quiescing does not change the user's
    # desired state; final activation uses the ordinary durable state owners.
    prepare_mod_activation(mod, True)
    if set_mod_active_locked(mod, True) is not True:
        raise ModUpdateError("The enabled update did not retain its requested activation.")
    mark_mod_activation_pending(mod, True)


def _registry(packages, data, *, previous):
    before = packages.registry_path.read_bytes()
    try:
        registry = packages._load()
        old = packages._record(data["recordId"], data["oldRecord"])
    except LocalModPackageError as exc:
        raise ModUpdateError("The enabled update registry ownership is invalid; recovery stopped.") from exc
    new = replace(old, fingerprint=data["newFingerprint"])
    if registry["records"].get(old.record_id) not in (vars(old), vars(new)):
        raise ModUpdateError("The enabled update registry ownership changed; recovery stopped.")
    wanted = old if previous else new
    if previous is not None and registry["records"][old.record_id] != vars(wanted):
        packages._put(registry, wanted)
        packages._save(registry, expected_content=before)


def _provider(packages, journal, data, operation):
    provider = _safe_path(packages.root, _relative_path(
        (journal.parent / "provider").relative_to(packages.root).as_posix(), "Update provider"), "Update provider")
    if _fingerprint(provider, packages.limits) != data["providerFingerprint"]:
        raise ModUpdateError("The retained enabled update provider changed; recovery stopped.")
    descriptor = read_api_manifest(packages.root, provider)
    if descriptor.id != data["modId"] or descriptor.version != data["newVersion"]:
        raise ModUpdateError("The retained enabled update provider has a different identity.")
    context = ModSettingsContext(packages.root, provider, operation.client_root)
    validate_enabled_update_context(descriptor, context, data["update"], operation.backend)
    return descriptor, context


def _call(packages, journal, data, operation, action, guard):
    guard()
    _registry(packages, data, previous=None)
    descriptor, context = _provider(packages, journal, data, operation)
    result = run_mod_helper_locked(descriptor, action, context, backend=operation.backend,
                                   update=dict(data["update"]))
    result.require_ready()
    # The runtime validator also enforces this. Keep the orchestration boundary
    # explicit, including injected runners used by transaction tests.
    if (result.contributions or result.environment or result.arguments or result.restart_required
            or result.receipt is not None or result.client_script_delivery is not None or result.client_preparation is not None):
        raise ModUpdateError("An enabled update provider requested unrelated changes.")
    _provider(packages, journal, data, operation)
    _registry(packages, data, previous=None)


def _load(packages, journal, data, operation):
    required = {"schemaVersion", "mode", "phase", "target", "modId", "oldVersion", "newVersion", "enabled",
                "recordId", "oldRecord", "oldFingerprint", "newFingerprint", "providerFingerprint", "update"}
    if (not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - {"rollbackFrom"}
            or data["schemaVersion"] != 2 or data["mode"] != ENABLED_UPDATE_PROTOCOL
            or data["phase"] not in PHASES or data["enabled"] is not True):
        raise ModUpdateError("Invalid enabled update recovery journal.")
    target = _safe_path(packages.root, _relative_path(data["target"], "Update target"), "Update target")
    if target.parent != packages.root / "mods":
        raise ModUpdateError("The enabled update target is outside this installation's mods folder.")
    old = packages._record(data["recordId"], data["oldRecord"])
    if old.relative_path != data["target"] or old.mod_id != data["modId"] or old.status != "installed":
        raise ModUpdateError("The enabled update registry record does not own its target.")
    for name in ("oldFingerprint", "newFingerprint", "providerFingerprint"):
        value = data[name]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ModUpdateError("The enabled update fingerprint is invalid.")
    update = data["update"]
    if (not isinstance(update, dict) or update.get("fromModRoot") != str(target)
            or update.get("fromVersion") != data["oldVersion"] or update.get("toVersion") != data["newVersion"]
            or str(uuid.UUID(journal.parent.name)) != update.get("transactionId")):
        raise ModUpdateError("The enabled update context does not match its retained transaction.")
    _provider(packages, journal, data, operation)
    _registry(packages, data, previous=None)
    return target


def _save(journal, data, phase):
    from .mod_updates import _save as save
    data["phase"] = phase
    save(journal, data)


def _discard_provider(packages, journal):
    try:
        storage = packages.root / "_local/launcher-mod-updates"
        if (journal.parent.parent != storage or len(journal.parent.name) != 32
                or any(c not in "0123456789abcdef" for c in journal.parent.name)):
            raise ModUpdateError("The completed provider does not belong to this update storage.")
        data = json.loads(journal.read_text(encoding="utf-8"))
        if data.get("mode") != ENABLED_UPDATE_PROTOCOL or data.get("phase") not in {"complete", "rolled_back"}:
            raise ModUpdateError("An unfinished update provider must be retained for recovery.")
        provider = _safe_path(packages.root, _relative_path(
            (journal.parent / "provider").relative_to(packages.root).as_posix(), "Update provider"), "Update provider")
        if _fingerprint(provider, packages.limits) != data["providerFingerprint"]:
            raise ModUpdateError("The completed provider changed; preserve it for review.")
        # Fingerprinting validates the entire bounded ordinary tree; only this
        # completed transaction's unchanged provider copy is disposable.
        shutil.rmtree(provider)
    except Exception:
        # A completed transaction is not undone by disposable cleanup failure.
        logging.getLogger(__name__).warning("Completed update provider retained for cleanup", exc_info=True)


def install_enabled_release(current, candidate, stage, journal, packages, operation, preserved, *, guard, report):
    spec = candidate.launcher_api.enabled_update
    old_spec = current.api_descriptor.launcher_api.enabled_update if current.api_descriptor.launcher_api else None
    if (operation.backend != "native" or current.version not in spec.from_versions
            or (old_spec is not None and (old_spec.protocol, old_spec.compatibility) != (spec.protocol, spec.compatibility))):
        raise ModUpdateError("The incoming enabled update contract does not admit this installed version/backend.")
    guard()
    provider = journal.parent / "provider"
    # Existing package ownership is adopted explicitly, before the critical
    # lease. It never runs the installed helper or changes native files.
    record = packages.adopt(current)
    with acquire_mod_lifecycle_lock(packages.root):
        guard()
        current = operation.current(current)
        if not current.active:
            raise ModUpdateError("The enabled update target changed activation before preparation.")
        for filename in preserved:
            old = _safe_path(current.path, _relative_path(filename, "Preserved file"), "Preserved file")
            new = _safe_path(stage, _relative_path(filename, "Preserved file"), "Preserved file")
            if old.exists():
                _ordinary(old, directory=False)
                if old.stat().st_size > packages.limits.max_file_bytes:
                    raise ModUpdateError("A preserved setting exceeds the package limit.")
                new.parent.mkdir(parents=True, exist_ok=True)
                new.write_bytes(old.read_bytes())
        if read_api_manifest(packages.root, stage) != candidate:
            raise ModUpdateError("Preserving settings changed the enabled update declaration.")
        # The retained provider must observe the same preserved user settings
        # as the incoming installation, rather than its archive defaults.
        packages._copy_package(packages.inspect(stage, folder_name=current.path.name), provider)
        provider_fingerprint = _fingerprint(provider, packages.limits)
        set_mod_active_locked(replace(current, path=stage, api_descriptor=None), False)
        data = dict(schemaVersion=2, mode=ENABLED_UPDATE_PROTOCOL, phase="prepared",
            target=current.path.relative_to(packages.root).as_posix(), modId=current.id,
            oldVersion=current.version, newVersion=candidate.version, enabled=True,
            recordId=record.record_id, oldRecord=dict(vars(record)),
            oldFingerprint=package_fingerprint(current.path, packages),
            newFingerprint=package_fingerprint(stage, packages), providerFingerprint=provider_fingerprint,
            update=dict(protocol=ENABLED_UPDATE_PROTOCOL, transactionId=str(uuid.UUID(journal.parent.name)),
                        fromVersion=current.version, fromModRoot=str(current.path), toVersion=candidate.version))
        _save(journal, data, "prepared")
        _save(journal, data, "preparing")
        report("Preparing enabled mod update")
        _call(packages, journal, data, operation, "prepare_update", guard)
        _save(journal, data, "handoff_ready")
        _package(packages, current.path, data, previous=True)
        _package(packages, stage, data, previous=False)
        guard()
        _save(journal, data, "quiescing")
        set_mod_active_locked(current, False)
        _save(journal, data, "swapping")
        current.path.rename(journal.parent / "previous")
        stage.rename(current.path)
        _save(journal, data, "installed")
        _save(journal, data, "applying")
        report("Applying enabled mod update")
        _call(packages, journal, data, operation, "apply_update", guard)
        _save(journal, data, "applied")
        _package(packages, current.path, data, previous=False)
        guard()
        _save(journal, data, "enabling")
        _activate(_installed(packages, current.path))
        _registry(packages, data, previous=False)
        _save(journal, data, "enabled")
        _finish_commit(packages, journal, data, operation, guard)
    _discard_provider(packages, journal)
    return "Update complete."


def _finish_commit(packages, journal, data, operation, guard):
    target = packages.root / data["target"]
    _package(packages, target, data, previous=False)
    if not _installed(packages, target).active:
        raise ModUpdateError("The committing enabled update is no longer enabled; recovery stopped.")
    _registry(packages, data, previous=False)
    # After this durable decision, even a lost helper acknowledgement must
    # finish commit, never restore the obsolete package/native payload.
    _save(journal, data, "committing")
    _call(packages, journal, data, operation, "commit_update", guard)
    _save(journal, data, "complete")


def recover_enabled_release(journal, data, operation, *, guard):
    packages = LocalModPackages(operation.evejs_root)
    with acquire_mod_lifecycle_lock(packages.root):
        target = _load(packages, journal, data, operation)
        guard()
        if data["phase"] == "committing":
            _finish_commit(packages, journal, data, operation, guard)
        else:
            original = data.get("rollbackFrom", data["phase"])
            if original not in PHASES or original in {"committing", "complete", "rolled_back", "rolling_back", "restoring"}:
                raise ModUpdateError("The enabled update rollback origin is invalid.")
            backup, failed = journal.parent / "previous", journal.parent / "failed"
            if backup.exists():
                _package(packages, backup, data, previous=True)
                if target.exists():
                    _package(packages, target, data, previous=False)
            else:
                _package(packages, target, data, previous=True)
                if original not in {"prepared", "preparing", "handoff_ready", "quiescing", "swapping"} and data["phase"] != "restoring":
                    raise ModUpdateError("The previous enabled package is missing; recovery stopped.")
            if failed.exists():
                _package(packages, failed, data, previous=False)
            # No provider was invoked in prepared. For an interrupted prepare,
            # replay its snapshot-only step with the same UUID before rollback.
            if original == "preparing" and data["phase"] not in {"rolling_back", "restoring"}:
                _call(packages, journal, data, operation, "prepare_update", guard)
            data["rollbackFrom"] = original
            if data["phase"] != "restoring":
                _save(journal, data, "rolling_back")
            if original != "prepared":
                # Terminal reentry must revalidate native ownership too, even
                # after a crash following the first successful native rollback.
                _call(packages, journal, data, operation, "rollback_update", guard)
            if data["phase"] != "restoring":
                _save(journal, data, "restoring")
            if backup.exists():
                if target.exists():
                    if failed.exists():
                        raise ModUpdateError("A failed package already occupies the recovery slot.")
                    target.rename(failed)
                backup.rename(target)
            _package(packages, target, data, previous=True)
            _activate(_installed(packages, target))
            _registry(packages, data, previous=True)
            _save(journal, data, "rolled_back")
    _discard_provider(packages, journal)
