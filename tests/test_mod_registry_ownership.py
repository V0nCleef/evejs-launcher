"""Ownership collisions must fail before writes; repair must preserve evidence."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.core import local_mod_packages as packages_module
from src.core.local_mod_packages import LocalModPackageError, LocalModPackages
from src.core.mod_inventory import load_mod_inventory
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock, ModLifecycleBusyError
from src.core.mod_manifest import MANIFEST_FILENAME, scan_mods


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / 'world'
    (root / 'mods').mkdir(parents=True)
    from src import config
    from src.core import mod_management
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path / 'launcher-state')
    monkeypatch.setenv('APPDATA', str(tmp_path / 'profile'))
    def unregistered(_mod):
        raise mod_management.ModNotManagedError('No fixture installer enrollment')
    monkeypatch.setattr(mod_management, 'read_managed_mod_registration', unregistered)
    return root


def source(parent, *, public=True, mod_id='example.mod'):
    folder = parent / 'package'
    folder.mkdir()
    (folder / 'loader.js').write_bytes(b'module.exports = {};\n')
    if public:
        (folder / MANIFEST_FILENAME).write_text(json.dumps({
            'schemaVersion': 3, 'id': mod_id, 'displayName': 'Example', 'version': '1.0.0',
            'kind': 'loader', 'restart': 'game_server', 'activation': {'strategy': 'loader_rename'},
        }))
    return folder


def files(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def broken(world, tmp_path, *, identical=False):
    service = LocalModPackages(world)
    first = service.import_package(source(tmp_path), folder_name='Example')
    value = json.loads(service.registry_path.read_bytes())
    other = deepcopy(value['records'][first.record_id])
    other['record_id'] = 'f' * 32
    assert other['record_id'] != first.record_id
    if not identical:
        other['fingerprint'] = '0' * 64
    value['records'][other['record_id']] = other
    # Construct historical invalid metadata; the production save API must refuse it.
    raw = (json.dumps(value, indent=3) + '\n').encode()
    service.registry_path.write_bytes(raw)
    return service, first, value, raw


@pytest.mark.parametrize('public', [False, True])
def test_missing_installed_folder_reimport_does_not_add_an_owner(world, tmp_path, public):
    package = source(tmp_path, public=public)
    service = LocalModPackages(world)
    service.import_package(package, folder_name='Example')
    before = service.registry_path.read_bytes()
    (world / 'mods/Example').rename(world / 'held-original')
    original_files = files(world)
    with pytest.raises(LocalModPackageError, match='registered|installed|claims|identity'):
        service.import_package(package, folder_name='Example')
    assert service.registry_path.read_bytes() == before
    assert files(world) == original_files
    assert len(service.records()) == 1


def test_public_display_id_may_still_repeat_in_distinct_folders(world, tmp_path):
    package = source(tmp_path)
    service = LocalModPackages(world)
    service.import_package(package, folder_name='First')
    service.import_package(package, folder_name='Second')
    assert len(service.records()) == 2
    assert len(service.sort_mods_for_runtime(scan_mods(world))) == 2


@pytest.mark.parametrize('relative', ['mods/Example', 'mods/example', 'mods\\Example', 'mods/Example/'])
def test_save_rejects_duplicate_canonical_folder_claims_without_replacing_registry(world, tmp_path, relative):
    service, first, value, _raw = broken(world, tmp_path)
    value['records']['f' * 32]['relative_path'] = relative
    # Restore the original valid file before asking the public write boundary to save corruption.
    valid = deepcopy(value)
    del valid['records']['f' * 32]
    original = json.dumps(valid).encode()
    service.registry_path.write_bytes(original)
    with pytest.raises(LocalModPackageError):
        service._save(value)
    assert service.registry_path.read_bytes() == original
    assert not list(service.registry_path.parent.glob('.registry-*.tmp'))


def test_put_refuses_a_second_owner_without_mutating_the_in_memory_registry(world, tmp_path):
    service = LocalModPackages(world)
    record = service.import_package(source(tmp_path), folder_name='Example')
    value = service._load()
    before = deepcopy(value)
    with pytest.raises(LocalModPackageError):
        service._put(value, replace(record, record_id='f' * 32))
    assert value == before


def test_existing_claim_prevents_restoring_another_record_into_a_missing_folder(world, tmp_path):
    service = LocalModPackages(world)
    first = service.import_package(source(tmp_path), folder_name='Example')
    mod = scan_mods(world)[0]
    quarantined = service.remove(mod)
    package = tmp_path / 'package'
    service.import_package(package, folder_name='Example')
    (world / 'mods/Example').rename(world / 'held-newer')
    before = files(world)
    with pytest.raises(LocalModPackageError):
        service.restore(quarantined.record_id)
    assert files(world) == before
    assert len(service.records()) == 2


@pytest.mark.parametrize('identical', [False, True])
def test_reviewed_repair_keeps_the_verified_owner_and_exact_backup(world, tmp_path, identical):
    service, first, value, original = broken(world, tmp_path, identical=identical)
    before_mods = files(world / 'mods')
    before_order = value['order'][:]
    with pytest.raises(LocalModPackageError):
        service.sort_mods_for_runtime(scan_mods(world))
    preview = service.registry_repair_preview()
    assert preview.registry_sha256 == hashlib.sha256(original).hexdigest()
    assert len(preview.removed_record_ids) == 1 and preview.duplicate_folder_count == 1
    assert service.registry_path.read_bytes() == original
    assert preview == service.registry_repair_preview()
    backup = service.repair_registry(preview)
    assert backup.read_bytes() == original
    assert 'before-duplicate-repair-' in backup.name
    records = service.records()
    assert len(records) == 1
    if not identical:
        assert records[0].record_id == first.record_id
    repaired = json.loads(service.registry_path.read_bytes())
    assert repaired['order'] == before_order
    assert repaired['root'] == value['root']
    assert files(world / 'mods') == before_mods
    assert len(service.sort_mods_for_runtime(scan_mods(world))) == 1
    inventory = load_mod_inventory(str(world))
    assert not inventory.registry_error and len(inventory.local_records) == 1


@pytest.mark.parametrize('damage', ['identity', 'kind', 'cleanup', 'archive', 'no-match', 'missing-folder', 'pending-package', 'pending-update'])
def test_repair_refuses_ambiguous_or_pending_ownership_without_writes(world, tmp_path, damage):
    service, first, value, _raw = broken(world, tmp_path)
    other = value['records']['f' * 32]
    if damage == 'identity':
        other['mod_id'] = 'different.mod'
    elif damage == 'kind':
        other['package_kind'] = 'settings'
    elif damage == 'cleanup':
        other['cleanup'] = {'ready': False}
    elif damage == 'archive':
        other['archive_path'] = '_local/launcher-mods/quarantine/' + 'f' * 32
    elif damage == 'no-match':
        value['records'][first.record_id]['fingerprint'] = '1' * 64
    elif damage == 'missing-folder':
        (world / 'mods/Example').rename(world / 'held-original')
    elif damage == 'pending-package':
        other['transaction'] = {'action': 'import', 'source': '_local/launcher-mods/staging/' + 'f' * 32,
                                'destination': other['relative_path'], 'status': 'installed', 'fingerprint': other['fingerprint']}
    elif damage == 'pending-update':
        folder = world / '_local/launcher-mod-updates' / ('a' * 32)
        folder.mkdir(parents=True)
        (folder / 'update.json').write_text(json.dumps({'schemaVersion': 1, 'phase': 'prepared'}))
    service.registry_path.write_text(json.dumps(value))
    before = files(world)
    with pytest.raises(LocalModPackageError):
        service.registry_repair_preview()
    assert files(world) == before


@pytest.mark.parametrize('changed', ['registry', 'package'])
def test_stale_repair_preview_never_replaces_unreviewed_state(world, tmp_path, changed):
    service, first, value, raw = broken(world, tmp_path)
    preview = service.registry_repair_preview()
    if changed == 'registry':
        service.registry_path.write_bytes(raw + b' ')
    else:
        (world / 'mods/Example/loader.js.disabled').write_bytes(b'// changed since review\n')
    before = files(world)
    with pytest.raises(LocalModPackageError):
        service.repair_registry(preview)
    assert files(world) == before


def test_failed_repair_backup_leaves_original_ownership_untouched(world, tmp_path, monkeypatch):
    service, first, value, raw = broken(world, tmp_path)
    preview = service.registry_repair_preview()
    def failed(*_args, **_kwargs):
        raise OSError('fixture backup failure')
    monkeypatch.setattr(service, '_backup_registry', failed)
    with pytest.raises((OSError, LocalModPackageError)):
        service.repair_registry(preview)
    assert service.registry_path.read_bytes() == raw


def test_failed_atomic_repair_retains_original_registry_and_backup(world, tmp_path, monkeypatch):
    service, first, value, raw = broken(world, tmp_path)
    preview = service.registry_repair_preview()
    real_replace = packages_module.os.replace
    def fail_registry_replace(src, dst):
        if Path(dst) == service.registry_path:
            raise OSError('fixture atomic publication failure')
        return real_replace(src, dst)
    monkeypatch.setattr(packages_module.os, 'replace', fail_registry_replace)
    with pytest.raises((OSError, LocalModPackageError)):
        service.repair_registry(preview)
    assert service.registry_path.read_bytes() == raw
    assert [p.read_bytes() for p in service.registry_path.parent.glob('registry.before-duplicate-repair-*.json')] == [raw]
    assert not list(service.registry_path.parent.glob('.registry-*.tmp'))


def test_repair_obeys_existing_lifecycle_lock(world, tmp_path):
    service, first, value, raw = broken(world, tmp_path)
    preview = service.registry_repair_preview()
    with acquire_mod_lifecycle_lock(world):
        with pytest.raises(ModLifecycleBusyError):
            service.repair_registry(preview)
    assert service.registry_path.read_bytes() == raw


def test_deleted_registry_can_register_existing_packages_idempotently_without_changing_files(world, tmp_path):
    service = LocalModPackages(world)
    service.import_package(source(tmp_path), folder_name='Example')
    service.registry_path.unlink()
    before = files(world / 'mods')
    mod = scan_mods(world)[0]
    assert mod.valid and not mod.active
    first = service.adopt(mod)
    second = service.adopt(mod)
    assert first.record_id == second.record_id
    assert len(service.records()) == 1
    assert files(world / 'mods') == before


def show_page(world):
    from src.pages.mods_page import ModsPage
    page = ModsPage(defer_inventory=True)
    page.set_evejs_root(str(world))
    page.show_inventory(load_mod_inventory(str(world)))
    return page


def test_conflict_has_a_review_action_and_never_masquerades_as_external(qapp, world, tmp_path):
    service, first, value, raw = broken(world, tmp_path)
    page = show_page(world)
    inventory = page._inventory
    assert inventory.registry_conflict and inventory.registry_repair_preview is not None
    assert not page.registry_banner.isHidden()
    assert page.register_mods_btn.text() == 'Review repair' and page.register_mods_btn.isEnabled()
    assert page._rows[0].remove_btn.text() == 'OWNERSHIP'
    assert not page._rows[0].toggle.isEnabled()
    assert not page._rows[0].configure_btn.isEnabled()
    assert not page.apply_btn.isEnabled()
    assert not page.add_zip_btn.isEnabled()
    page.set_lifecycle_busy(True)
    assert not page.register_mods_btn.isEnabled()
    page.set_lifecycle_busy(False)
    assert page.register_mods_btn.isEnabled()
    assert not page._rows[0].configure_btn.isEnabled()
    emitted = []
    page.registry_repair_requested.connect(emitted.append)
    page.register_mods_btn.click()
    assert emitted == [inventory.registry_repair_preview]
    assert service.registry_path.read_bytes() == raw
    page.deleteLater()


def test_unverifiable_conflict_explains_why_review_is_unavailable(qapp, world, tmp_path):
    service, first, value, raw = broken(world, tmp_path)
    value['records'][first.record_id]['fingerprint'] = '1' * 64
    service.registry_path.write_text(json.dumps(value))
    page = show_page(world)
    assert page._inventory.registry_conflict
    assert page._inventory.registry_repair_preview is None
    assert not page.registry_banner.isHidden() and not page.register_mods_btn.isEnabled()
    assert 'safe repair could not be verified' in page.registry_banner_description.text()
    assert page._rows[0].remove_btn.text() == 'OWNERSHIP'
    page.deleteLater()


def test_copied_registry_with_duplicate_claims_never_looks_like_external_ownership(qapp, world, tmp_path):
    service, first, value, raw = broken(world, tmp_path)
    value['root'] = str(tmp_path / 'previous-location')
    service.registry_path.write_text(json.dumps(value))
    original = service.registry_path.read_bytes()
    page = show_page(world)
    assert page._inventory.registry_conflict
    assert page._inventory.registry_repair_preview is None
    assert not page.registry_banner.isHidden() and not page.register_mods_btn.isEnabled()
    assert page._rows[0].remove_btn.text() == 'OWNERSHIP'
    assert service.registry_path.read_bytes() == original
    page.deleteLater()


def test_connect_only_never_enables_metadata_repair(qapp, world, tmp_path):
    from src.core.service_status import DockerControlPolicy, RuntimeBackend
    broken(world, tmp_path)
    page = show_page(world)
    page.set_runtime_context(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.CONNECT_ONLY)
    assert not page.registry_banner.isHidden()
    assert not page.register_mods_btn.isEnabled()
    assert not page.apply_btn.isEnabled()
    page.deleteLater()


def owner_and_coordinator(page, world):
    from PyQt6.QtWidgets import QMainWindow
    from src.ui.mod_coordinator import ModCoordinator
    owner = QMainWindow()
    owner._cfg = {'evejs_root': str(world), 'client_path': ''}
    owner._close_in_progress = False
    owner._docker_mode = lambda: False
    owner._set_operation_controls_busy = page.set_lifecycle_busy
    coordinator = ModCoordinator(owner)
    coordinator._page = page
    page.registry_repair_requested.connect(coordinator.repair_registry)
    return owner, coordinator


def test_cancelled_repair_does_not_create_a_backup_or_change_registry(qapp, world, tmp_path, monkeypatch):
    from src.ui import mod_coordinator as module
    service, first, value, raw = broken(world, tmp_path)
    page = show_page(world)
    owner, coordinator = owner_and_coordinator(page, world)
    prompts = []
    def cancel(*args):
        prompts.append(args[2])
        return module.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(module.QMessageBox, 'question', cancel)
    page.register_mods_btn.click()
    assert prompts and 'mods/example' in prompts[0] and 'Verified redundant records: 1' in prompts[0]
    assert service.registry_path.read_bytes() == raw
    assert not list(service.registry_path.parent.glob('registry.before-duplicate-repair-*.json'))
    owner.deleteLater()
    page.deleteLater()


@pytest.mark.parametrize('changed', ['installation', 'backend', 'busy', 'preview'])
def test_confirmation_rechecks_its_target_before_starting_a_worker(qapp, world, tmp_path, monkeypatch, changed):
    from src.core.service_status import DockerControlPolicy, RuntimeBackend
    from src.ui import mod_coordinator as module
    service, first, value, raw = broken(world, tmp_path)
    page = show_page(world)
    owner, coordinator = owner_and_coordinator(page, world)
    warnings = []
    def confirmed(*_args):
        if changed == 'installation':
            other = tmp_path / 'other-world'
            other.mkdir()
            owner._cfg['evejs_root'] = str(other)
        elif changed == 'backend':
            page.set_runtime_context(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.CONNECT_ONLY)
        elif changed == 'busy':
            page.set_lifecycle_busy(True)
        else:
            page._inventory = replace(page._inventory, registry_repair_preview=None)
        return module.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(module.QMessageBox, 'question', confirmed)
    monkeypatch.setattr(module.QMessageBox, 'warning', lambda *_args: warnings.append(_args[-1]))
    page.register_mods_btn.click()
    assert warnings and coordinator._token is None
    assert service.registry_path.read_bytes() == raw
    assert not list(service.registry_path.parent.glob('registry.before-duplicate-repair-*.json'))
    owner.deleteLater()
    page.deleteLater()


def test_confirmed_repair_uses_the_real_worker_and_refreshes_ownership(qapp, world, tmp_path, monkeypatch):
    import time
    from src.ui import mod_coordinator as module
    service, first, value, raw = broken(world, tmp_path)
    page = show_page(world)
    owner, coordinator = owner_and_coordinator(page, world)
    warnings, notices = [], []
    monkeypatch.setattr(module.QMessageBox, 'question', lambda *_args: module.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(module.QMessageBox, 'warning', lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(module.QMessageBox, 'information', lambda *_args: notices.append(_args[-1]))
    page.register_mods_btn.click()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        qapp.processEvents()
        if coordinator._token is None and not page._inventory.registry_error:
            break
        time.sleep(0.005)
    assert coordinator._token is None and not warnings
    assert not page._inventory.registry_error and not page._inventory.registry_conflict
    assert page.registry_banner.isHidden()
    assert len(service.records()) == 1
    assert notices and 'Backup:' in notices[0]
    assert page._rows[0].remove_btn.text() == 'REMOVE'
    assert [p.read_bytes() for p in service.registry_path.parent.glob('registry.before-duplicate-repair-*.json')] == [raw]
    owner.deleteLater()
    page.deleteLater()


def test_review_retains_its_reservation_through_nested_inventory_refresh(qapp, world, tmp_path, monkeypatch):
    from src.core.application_operations import active_mutations
    from src.ui import mod_coordinator as module
    service, first, value, raw = broken(world, tmp_path)
    page = show_page(world)
    owner, coordinator = owner_and_coordinator(page, world)
    def cancel_after_refresh(*_args):
        assert active_mutations(owner) == ('mod_operation',)
        coordinator.refresh()
        qapp.processEvents()
        assert coordinator._token is None
        assert coordinator._refresh_pending
        assert active_mutations(owner) == ('mod_operation',)
        return module.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(module.QMessageBox, 'question', cancel_after_refresh)
    page.register_mods_btn.click()
    assert not active_mutations(owner)
    assert service.registry_path.read_bytes() == raw
    coordinator._refresh_timer.stop()
    owner.deleteLater()
    page.deleteLater()


def test_automatic_update_check_silently_defers_during_ownership_review(qapp, world, tmp_path, monkeypatch):
    from src.ui import mod_coordinator as module
    service, first, value, raw = broken(world, tmp_path)
    page = show_page(world)
    owner, coordinator = owner_and_coordinator(page, world)
    notices = []
    def cancel_after_update_check(*_args):
        coordinator._updates.check()
        assert coordinator._token is None
        return module.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(module.QMessageBox, 'question', cancel_after_update_check)
    monkeypatch.setattr(module.QMessageBox, 'information', lambda *_args: notices.append(_args[-1]))
    page.register_mods_btn.click()
    assert not notices and service.registry_path.read_bytes() == raw
    assert owner._mod_operation_request is None
    owner.deleteLater()
    page.deleteLater()


def test_repair_preserves_unrelated_quarantine_cleanup_and_order(world, tmp_path):
    service, first, value, raw = broken(world, tmp_path)
    other_id = 'a' * 32
    unrelated = replace(first, record_id=other_id, mod_id='unrelated.mod', relative_path='mods/Other',
                        status='quarantined', archive_path='_local/launcher-mods/quarantine/' + other_id,
                        cleanup={'ready': False, 'state': 'keep-for-recovery'})
    value['records'][other_id] = vars(unrelated)
    value['order'].insert(0, 'mods/other|unrelated.mod')
    service.registry_path.write_text(json.dumps(value))
    original = service.registry_path.read_bytes()
    preview = service.registry_repair_preview()
    backup = service.repair_registry(preview)
    result = json.loads(service.registry_path.read_bytes())
    assert result['records'][other_id] == value['records'][other_id]
    assert result['order'] == value['order']
    assert backup.read_bytes() == original


@pytest.mark.parametrize('changed', ['registry', 'package'])
def test_change_during_backup_is_not_overwritten(world, tmp_path, monkeypatch, changed):
    service, first, value, raw = broken(world, tmp_path)
    preview = service.registry_repair_preview()
    backup = service._backup_registry
    def backup_then_change(*args, **kwargs):
        result = backup(*args, **kwargs)
        if changed == 'registry':
            service.registry_path.write_bytes(raw + b' ')
        else:
            (world / 'mods/Example/loader.js.disabled').write_bytes(b'// changed during backup\n')
        return result
    monkeypatch.setattr(service, '_backup_registry', backup_then_change)
    with pytest.raises(LocalModPackageError):
        service.repair_registry(preview)
    assert service.registry_path.read_bytes() == (raw + b' ' if changed == 'registry' else raw)
    assert [p.read_bytes() for p in service.registry_path.parent.glob('registry.before-duplicate-repair-*.json')] == [raw]


def test_foreign_repair_preview_never_grants_another_root_ownership(world, tmp_path):
    service, first, value, raw = broken(world, tmp_path)
    preview = service.registry_repair_preview()
    with pytest.raises(LocalModPackageError):
        service.repair_registry(replace(preview, root=str(tmp_path)))
    assert service.registry_path.read_bytes() == raw


def test_repair_diagnostics_do_not_expose_arbitrary_exception_values(world, tmp_path, monkeypatch, caplog):
    from src.core import mod_registry_repair
    service, first, value, raw = broken(world, tmp_path)
    def failed(*_args, **_kwargs):
        raise ValueError('{"accountName":"PRIVATE_ACCOUNT_SENTINEL","password":"PRIVATE_PASSWORD_SENTINEL"}')
    monkeypatch.setattr(mod_registry_repair, '_fingerprint', failed)
    with pytest.raises(LocalModPackageError) as error:
        service.registry_repair_preview()
    inventory = load_mod_inventory(str(world))
    result = str(error.value) + inventory.registry_repair_error + caplog.text
    assert 'PRIVATE_ACCOUNT_SENTINEL' not in result and 'PRIVATE_PASSWORD_SENTINEL' not in result
    assert service.registry_path.read_bytes() == raw


@pytest.mark.parametrize('language', ['zh_CN', 'ja', 'ko', 'fr', 'de', 'nl', 'ru'])
def test_repair_controls_and_confirmation_have_complete_translations(qapp, world, tmp_path, language):
    from src import i18n
    from src.translations_mod_ownership import SOURCE_PHRASES
    broken(world, tmp_path)
    for phrase in SOURCE_PHRASES:
        translated = i18n.translate_ui_phrase(phrase, language)
        assert translated and translated != phrase
        import string
        placeholders = lambda text: {field for _, field, _, _ in string.Formatter().parse(text) if field}
        assert placeholders(translated) == placeholders(phrase)
    try:
        i18n.set_language(language)
        page = show_page(world)
        assert page.register_mods_btn.text() == i18n.translate_ui_phrase('Review repair', language)
        assert page._rows[0].remove_btn.text() == i18n.translate_ui_phrase('OWNERSHIP', language)
        page.deleteLater()
    finally:
        i18n.set_language('en')
