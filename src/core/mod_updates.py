"""Downloaded package replacement with retained originals and explicit recovery.

The existing lifecycle providers own runtime install/cleanup. This module owns
only package folders; it never guesses at or overwrites external client files.
"""
from __future__ import annotations

from dataclasses import replace
import json
import logging
import os
from pathlib import Path
import shutil
import uuid

from .local_mod_packages import LocalModPackages, _fingerprint, _ordinary
from .mod_api_manifest import read_api_manifest, _relative_path, _safe_path
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_manifest import ActivationKind, scan_mods, set_mod_active_locked
from .mod_operations import change_mod_state, cleanup_gate
from .mod_relationships import plan_mod_order
from .mod_settings_schema import parse_settings_schema
from .mod_evejs_compatibility import installed_evejs_version, supports_evejs
from .mod_update_source import ModUpdateError, Version, download_release
from .mod_enabled_updates import PHASES as ENABLED_PHASES, install_enabled_release, recover_enabled_release
from .mod_api_manifest import ENABLED_UPDATE_PROTOCOL

UPDATE_ROOT = Path('_local/launcher-mod-updates')


def _save(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _jobs(root):
    base = _safe_path(root, _relative_path(UPDATE_ROOT.as_posix(), 'Update storage'), 'Update storage')
    if not base.exists():
        return []
    jobs = []
    for folder in base.iterdir():
        _ordinary(folder, directory=True)
        if len(folder.name) != 32 or any(c not in '0123456789abcdef' for c in folder.name):
            raise ModUpdateError('Unknown entry in mod update storage; preserve it for review.')
        path = folder / 'update.json'
        if not path.exists():
            continue  # A download/staging failure never touched the installed mod.
        _ordinary(path, directory=False)
        if path.stat().st_size > 65536:
            raise ModUpdateError('The mod update recovery record is too large.')
        data = json.loads(path.read_text(encoding='utf-8'))
        ordinary = data.get('schemaVersion') == 1 and data.get('phase') in {'prepared', 'disabled', 'swapping', 'installed', 'complete', 'rolled_back', 'recovering'}
        enabled = data.get('schemaVersion') == 2 and data.get('mode') == ENABLED_UPDATE_PROTOCOL and data.get('phase') in ENABLED_PHASES
        if not ordinary and not enabled:
            raise ModUpdateError('Unsupported mod update recovery record.')
        jobs.append((path, data))
    return jobs


def pending_updates(root):
    return [(path, data) for path, data in _jobs(Path(root).resolve(strict=True))
            if data['phase'] not in {'complete', 'rolled_back'}]


def _current(root, target):
    matches = [mod for mod in scan_mods(root) if mod.path == target]
    if len(matches) != 1 or not matches[0].valid:
        raise ModUpdateError('The mod package cannot be identified safely. Its files were retained for recovery.')
    return matches[0]


def install_release(mod, release, operation, *, guard, downloader=download_release, progress=None):
    """Called by the reserved mod worker, with an immutable target context.

    Every runtime mutation is preceded by the caller's stop/reachability guard.
    The package remains disabled until the final provider install succeeds.
    """
    def report(phase, done=0, total=0):
        # Qt signal emitters require all three arguments, including during
        # phases that have no byte counts yet.
        if progress is not None:
            progress(phase, done, total)

    report("Checking update")
    current = operation.current(mod)
    descriptor = current.api_descriptor
    if not current.valid or descriptor is None or descriptor.updates != release.source:
        raise ModUpdateError('The mod update source changed. Refresh Mods before retrying.')
    if not release.metadata_verified or release.mod_id != descriptor.id:
        raise ModUpdateError('The mod update has no matching release metadata.')
    if not supports_evejs(release.evejs_versions, installed_evejs_version(operation.evejs_root)):
        logging.getLogger(__name__).warning('Installing mod %s with unconfirmed EveJS compatibility.', current.id)
    if Version.parse(release.version) <= Version.parse(current.version):
        raise ModUpdateError('The selected mod release is not newer than the installed version.')
    packages = LocalModPackages(operation.evejs_root)
    if pending_updates(packages.root):
        raise ModUpdateError('Recover the previous mod update before installing another update.')
    folder = _safe_path(packages.root, _relative_path((UPDATE_ROOT / uuid.uuid4().hex).as_posix(), 'Update folder'), 'Update folder')
    folder.mkdir(parents=True)
    archive, stage, backup = folder / 'download.zip', folder / 'package', folder / 'previous'
    journal = folder / 'update.json'
    try:
        report("Downloading mod update", 0, release.size)
        if progress and downloader is download_release:
            downloader(release, archive, progress=lambda done, total: report("Downloading mod update", done, total))
        else:
            downloader(release, archive)
        report("Checking package")
        preview = packages.inspect(archive, folder_name=current.path.name)
        packages._copy_package(preview, stage)
        candidate = read_api_manifest(packages.root, stage)
        if (candidate.id, candidate.version, candidate.kind, candidate.activation_strategy) != (
                descriptor.id, release.version, descriptor.kind, descriptor.activation_strategy):
            raise ModUpdateError('The downloaded mod identity, version or activation kind does not match this update.')
        if candidate.updates != descriptor.updates:
            raise ModUpdateError('The update changes its source or preservation contract. Review and install it manually.')
        if candidate.evejs_versions != release.evejs_versions:
            raise ModUpdateError('The package EveJS compatibility differs from its release metadata.')
        if operation.backend not in candidate.supported_backends:
            raise ModUpdateError('The update does not support the selected backend.')
        if candidate.launcher_api:
            from src.updater.github import _read_version
            if Version.parse(candidate.launcher_api.min_launcher_version) > Version.parse(_read_version()):
                raise ModUpdateError('Update the launcher before installing this mod release.')
        # Review relationships before disabling the currently working package.
        prospective = replace(current, api_descriptor=candidate, version=candidate.version)
        plan_mod_order([prospective if item.path == current.path else item for item in scan_mods(packages.root)],
                       backend=operation.backend).require_valid()
        preserved = set(descriptor.updates.preserve_files)
        if descriptor.settings:
            preserved.update(file.path for file in parse_settings_schema(descriptor.settings).files if file.base == 'mod')
        if candidate.settings:
            parse_settings_schema(candidate.settings)
        if any(Path(name).name.casefold().startswith('evejs-launcher.') for name in preserved):
            raise ModUpdateError('Package settings cannot preserve or overwrite a mod descriptor during an update.')
        guard()
        if not supports_evejs(candidate.evejs_versions, installed_evejs_version(packages.root)):
            logging.getLogger(__name__).warning('EveJS support for mod %s is unconfirmed; proceeding with user-requested update.', current.id)
        current = operation.current(mod)
        if current.active and candidate.launcher_api and candidate.launcher_api.enabled_update is not None:
            return install_enabled_release(current, candidate, stage, journal, packages, operation,
                                           preserved, guard=guard, report=report)
        report("Backing up mod")
        record = packages.adopt(current)
        data = dict(schemaVersion=1, phase='prepared', target=current.path.relative_to(packages.root).as_posix(),
                    modId=current.id, packageKind=descriptor.kind, oldVersion=current.version, newVersion=release.version,
                    enabled=current.active, recordId=record.record_id, oldFingerprint='')
        _save(journal, data)
        if current.active:
            # The complete replacement relationship plan was checked above.
            # Dependents stay configured while the stopped target is replaced.
            packages.disable(current, cleanup_gate=cleanup_gate(operation), for_update=True)
        data['phase'] = 'disabled'
        _save(journal, data)
        guard()
        with acquire_mod_lifecycle_lock(packages.root):
            disabled = _current(packages.root, current.path)
            if disabled.active or disabled.version != current.version:
                raise ModUpdateError('The installed mod changed during the update. Recover before retrying.')
            for filename in preserved:
                old = _safe_path(current.path, _relative_path(filename, 'Preserved file'), 'Preserved file')
                new = _safe_path(stage, _relative_path(filename, 'Preserved file'), 'Preserved file')
                if old.exists():
                    _ordinary(old, directory=False)
                    if old.stat().st_size > packages.limits.max_file_bytes:
                        raise ModUpdateError('A preserved configuration file exceeds the package limit.')
                    new.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(old, new)
            if read_api_manifest(packages.root, stage) != candidate:
                raise ModUpdateError('Preserving settings changed the candidate declaration; the update was refused.')
            if preview.has_loader:
                set_mod_active_locked(replace(disabled, path=stage, api_descriptor=None), False)
            data.update(phase='swapping', oldFingerprint=_fingerprint(current.path, packages.limits))
            _save(journal, data)
            current.path.rename(backup)
            report("Installing mod update")
            stage.rename(current.path)
            data['phase'] = 'installed'
            _save(journal, data)
        replacement = _current(packages.root, current.path)
        if data['enabled']:
            guard()
            change_mod_state(replacement, True, operation)
        report("Verifying installation")
        verified = _current(packages.root, current.path)
        if verified.version != release.version or verified.active != data['enabled']:
            raise ModUpdateError('The updated package did not retain its expected version and enabled state.')
        data['phase'] = 'complete'
        _save(journal, data)
        return 'Update complete.'
    except Exception as error:
        if journal.exists():
            try:
                report("Restoring previous version")
                recover_update(journal, operation, guard=guard)
                if json.loads(journal.read_text(encoding='utf-8'))['phase'] == 'complete':
                    return 'Update complete.'
            except Exception as recovery:
                raise ModUpdateError(f'{error}\nRecovery is pending; transaction files were retained. {recovery}') from error
            raise ModUpdateError(f'{error}\nThe previous mod version and enabled state were restored.') from error
        raise
    finally:
        archive.unlink(missing_ok=True)


def recover_update(journal, operation, *, guard):
    packages = LocalModPackages(operation.evejs_root)
    matches = [(path, data) for path, data in _jobs(packages.root) if path == journal]
    if len(matches) != 1:
        raise ModUpdateError('The update recovery record is not registered in this root.')
    journal, data = matches[0]
    if data['phase'] in {'complete', 'rolled_back'}:
        return
    if data.get('schemaVersion') == 2:
        return recover_enabled_release(journal, data, operation, guard=guard)
    target = _safe_path(packages.root, _relative_path(data['target'], 'Update target'), 'Update target')
    if target.parent != packages.root / 'mods' or type(data.get('enabled')) is not bool:
        raise ModUpdateError('Invalid update recovery target.')
    backup = journal.parent / 'previous'
    guard()
    if data['phase'] == 'prepared' and not backup.exists() and data.get('packageKind') == 'loader':
        # A refused old cleanup has not swapped the package. Prove the exact
        # original is still active/owned and verify it without installing again.
        from .mod_api_runtime import run_mod_helper_locked
        with acquire_mod_lifecycle_lock(packages.root):
            restored = _current(packages.root, target)
            record = next((row for row in packages.records() if row.record_id == data.get('recordId')), None)
            if (record is None or record.relative_path != data['target'] or record.mod_id != data['modId']
                    or restored.id != data['modId'] or restored.version != data['oldVersion']
                    or restored.active != data['enabled']
                    or _fingerprint(target, packages.limits) != record.fingerprint):
                raise ModUpdateError('The original prepared update changed; automatic no-op recovery stopped.')
            descriptor = restored.api_descriptor
            api = descriptor.launcher_api if descriptor else None
            if data['enabled'] and api and {'install', 'prepare_disable', 'prepare_remove'}.intersection(api.capabilities):
                if 'verify' not in api.capabilities:
                    raise ModUpdateError('The unchanged original package needs its verification provider before recovery.')
                result = run_mod_helper_locked(descriptor, 'verify', operation.capture(restored), backend=operation.backend)
                result.require_ready()
                if (result.contributions or result.environment or result.arguments or result.restart_required
                        or result.client_script_delivery is not None or result.client_preparation is not None
                        or (result.receipt is not None and result.receipt.state != 'active')):
                    raise ModUpdateError('Prepared update verification requested changes instead of proving the original state.')
                if operation.current(restored) != restored or _fingerprint(target, packages.limits) != record.fingerprint:
                    raise ModUpdateError('The original package changed during prepared update recovery.')
            data['phase'] = 'rolled_back'
            _save(journal, data)
        return
    if backup.exists():
        if _fingerprint(backup, packages.limits) != data['oldFingerprint']:
            raise ModUpdateError('The retained previous package changed; automatic recovery stopped.')
        if target.exists():
            installed = _current(packages.root, target)
            if installed.id != data['modId'] or installed.version != data['newVersion']:
                raise ModUpdateError('The installed folder changed identity; automatic recovery stopped.')
            # Cleanup is required even if install failed before recording ON.
            api = installed.api_descriptor.launcher_api if installed.api_descriptor else None
            if installed.active or (api and 'prepare_disable' in api.capabilities):
                packages.disable(installed, cleanup_gate=cleanup_gate(operation), for_update=True)
        with acquire_mod_lifecycle_lock(packages.root):
            if _fingerprint(backup, packages.limits) != data['oldFingerprint']:
                raise ModUpdateError('The previous package changed during recovery.')
            if target.exists():
                target.rename(journal.parent / ('failed-' + uuid.uuid4().hex))
            backup.rename(target)
            data['phase'] = 'recovering'
            _save(journal, data)
    restored = _current(packages.root, target)
    if restored.id != data['modId'] or restored.version != data['oldVersion']:
        raise ModUpdateError('Recovery could not identify the previous package.')
    if data['enabled']:
        guard()
        change_mod_state(restored, True, operation)
    data['phase'] = 'rolled_back'
    _save(journal, data)
