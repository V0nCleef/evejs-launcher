import hashlib
import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from src.core.mod_update_source import (Version, parse_update_source, select_release, download_release, ModUpdateError, validate_release_metadata)
from src.core import mod_updates
from src.core.mod_manifest import scan_mods
from src.core.mod_operations import ModOperationContext

SOURCE = dict(provider='github', repository='V0nCleef/EveJS-DLSS5', asset='EveJS-DLSS5-{version}.zip', preserveFiles=['config.json'])


def release(version='1.1.0', data=b'zip'):
    return dict(draft=False, prerelease='-' in version, tag_name='v' + version, body='# Changelog\n<script>never execute</script>',
                html_url='https://github.com/V0nCleef/EveJS-DLSS5/releases/tag/v' + version,
                assets=[dict(name=f'EveJS-DLSS5-{version}.zip', state='uploaded', size=len(data),
                    digest='sha256:' + hashlib.sha256(data).hexdigest(),
                    browser_download_url=f'https://github.com/V0nCleef/EveJS-DLSS5/releases/download/v{version}/EveJS-DLSS5-{version}.zip')])


@pytest.mark.parametrize('older,newer', [('1.9.0', '1.10.0'), ('1.0.0-rc.9', '1.0.0-rc.10'), ('1.0.0-rc.4', '1.0.0'), ('1.0.0-1', '1.0.0-beta')])
def test_semver_order(older, newer):
    assert Version.parse(older) < Version.parse(newer)


def test_channel_asset_changelog_and_equal_version():
    source = parse_update_source(SOURCE)
    selected = select_release(source, '1.0.0', [release('1.2.0-rc.1'), release('1.1.0')])
    assert selected.version == '1.1.0'
    assert '<script>' in selected.notes  # Kept as text, never interpreted by the dialog.
    assert select_release(source, '1.1.0+build.2', [release('1.1.0')]) is None
    assert select_release(parse_update_source(dict(SOURCE, channel='prerelease')), '1.0.0', [release('1.2.0-rc.1')]).version == '1.2.0-rc.1'


@pytest.mark.parametrize('change', [dict(repository='owner/../repo'), dict(asset='*.zip'), dict(preserveFiles=['../config.json']), dict(preserveFiles=['loader.js']), dict(preserveFiles=['evejs-launcher.mod.json'])])
def test_invalid_declarations(change):
    with pytest.raises(ValueError):
        parse_update_source(dict(SOURCE, **change))


def test_ambiguous_asset_and_wrong_repository():
    entry = release()
    entry['assets'] *= 2
    with pytest.raises(ModUpdateError):
        select_release(parse_update_source(SOURCE), '1.0.0', [entry])
    entry = release()
    entry['assets'][0]['browser_download_url'] = 'https://github.com/other/repo/releases/download/v1.1.0/a.zip'
    with pytest.raises(ModUpdateError):
        select_release(parse_update_source(SOURCE), '1.0.0', [entry])


@pytest.mark.parametrize('download', [b'bad', b'toolong', b''])
def test_download_rejection_retains_no_partial(tmp_path, download):
    selected = select_release(parse_update_source(SOURCE), '1.0.0', [release()])
    target = tmp_path / 'download.zip'
    with pytest.raises(ModUpdateError):
        download_release(selected, target, opener=lambda _url: io.BytesIO(download))
    assert not target.exists()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    from src import config
    from src.core import mod_management
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path / 'appdata')
    monkeypatch.setenv('APPDATA', str(tmp_path / 'appdata'))
    def absent(_):
        raise mod_management.ModNotManagedError('Fixture has no installer enrollment')
    monkeypatch.setattr(mod_management, 'read_managed_mod_registration', absent)
    root = tmp_path / 'EveJS'
    target = root / 'mods' / 'Example'
    target.mkdir(parents=True)
    manifest = dict(schemaVersion=3, id='author.example', displayName='Example', version='1.0.0',
                    kind='loader', activation=dict(strategy='loader_rename'), restart='game_server', updates=SOURCE)
    (target / 'evejs-launcher.mod.json').write_text(json.dumps(manifest))
    (target / 'loader.js').write_text('//old')
    (target / 'config.json').write_text('{"user":42}')
    (root / 'unrelated.txt').write_text('keep')
    archive = tmp_path / 'new.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('Example/evejs-launcher.mod.json', json.dumps(dict(manifest, version='1.1.0')))
        output.writestr('Example/loader.js', '//new')
        output.writestr('Example/config.json', '{"user":0}')
    offer = select_release(parse_update_source(SOURCE), '1.0.0', [release(data=archive.read_bytes())])
    offer = validate_release_metadata(offer, dict(schemaVersion=1, id='author.example', version='1.1.0', asset='EveJS-DLSS5-1.1.0.zip'))
    return root, target, offer, lambda _release, destination: shutil.copyfile(archive, destination)


def test_real_package_swap_preserves_settings_order_and_active(fixture):
    root, target, offer, downloader = fixture
    mod = scan_mods(root)[0]
    mod_updates.install_release(mod, offer, ModOperationContext(root), guard=lambda: None, downloader=downloader)
    updated = scan_mods(root)[0]
    assert updated.version == '1.1.0' and updated.active
    assert (target / 'loader.js').read_text() == '//new'
    assert json.loads((target / 'config.json').read_text()) == {'user': 42}
    assert (root / 'unrelated.txt').read_text() == 'keep'
    assert not mod_updates.pending_updates(root)
    assert list((root / mod_updates.UPDATE_ROOT).glob('*/previous/loader.js.off')) or list((root / mod_updates.UPDATE_ROOT).glob('*/previous/loader.js.disabled'))


def test_package_update_through_dialog_signal_preserves_settings(qapp, fixture):
    from concurrent.futures import ThreadPoolExecutor
    from src.widgets.mod_update_dialog import ModUpdateDialog
    from src.workers.mod_operation_worker import ModOperationResult
    root, target, offer, downloader = fixture
    mod = scan_mods(root)[0]
    dialog = ModUpdateDialog(mod, offer)
    dialog.begin()
    try:
        # The controller passes this exact Qt signal to the worker. Plain
        # Python callbacks with default arguments previously hid the mismatch.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(mod_updates.install_release, mod, offer,
                        ModOperationContext(root), guard=lambda: None,
                        downloader=downloader,
                        progress=dialog.progress_received.emit).result(timeout=15)
        qapp.processEvents()
        assert dialog.status.text() == 'Verifying installation'
        assert scan_mods(root)[0].version == '1.1.0'
        assert scan_mods(root)[0].active
        assert json.loads((target / 'config.json').read_text()) == {'user': 42}
        assert not mod_updates.pending_updates(root)
        dialog.finish(ModOperationResult(None, True))
        assert dialog.status.text() == 'Update complete.'
        assert dialog.close_btn.isEnabled()
    finally:
        dialog.running = False
        dialog.close()
        dialog.deleteLater()


def test_failed_new_activation_restores_old_version(fixture, monkeypatch):
    root, target, offer, downloader = fixture
    original = mod_updates.change_mod_state
    def fail_new(mod, desired, context):
        if desired and mod.version == '1.1.0':
            raise RuntimeError('injected install failure')
        return original(mod, desired, context)
    monkeypatch.setattr(mod_updates, 'change_mod_state', fail_new)
    with pytest.raises(ModUpdateError, match='restored'):
        mod_updates.install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None, downloader=downloader)
    assert scan_mods(root)[0].version == '1.0.0' and scan_mods(root)[0].active
    assert (target / 'loader.js').read_text() == '//old'
    assert not mod_updates.pending_updates(root)


def test_process_guard_prevents_mutation(fixture):
    root, target, offer, downloader = fixture
    def busy():
        raise RuntimeError('client still running')
    with pytest.raises(RuntimeError, match='client still running'):
        mod_updates.install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=busy, downloader=downloader)
    assert scan_mods(root)[0].version == '1.0.0' and scan_mods(root)[0].active
    assert not mod_updates.pending_updates(root)


def test_crash_between_folder_moves_is_recoverable(fixture, monkeypatch):
    root, target, offer, downloader = fixture
    rename = Path.rename
    class Crash(BaseException):
        pass
    def crash(self, destination):
        if self.name == 'package':
            raise Crash()
        return rename(self, destination)
    monkeypatch.setattr(Path, 'rename', crash)
    with pytest.raises(Crash):
        mod_updates.install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None, downloader=downloader)
    monkeypatch.setattr(Path, 'rename', rename)
    journal, _ = mod_updates.pending_updates(root)[0]
    mod_updates.recover_update(journal, ModOperationContext(root), guard=lambda: None)
    assert scan_mods(root)[0].version == '1.0.0' and scan_mods(root)[0].active


def test_update_keeps_enabled_dependents_and_order(fixture):
    from src.core.local_mod_packages import LocalModPackages
    root, target, offer, downloader = fixture
    dependent = root / 'mods/Dependent'
    dependent.mkdir()
    (dependent / 'loader.js').write_text('//unchanged dependent')
    (dependent / 'evejs-launcher.mod.json').write_text(json.dumps(dict(schemaVersion=3, id='author.dependent',
        displayName='Dependent', version='1.0.0', kind='loader', activation=dict(strategy='loader_rename'),
        restart='game_server', requires=['author.example'])))
    packages = LocalModPackages(root)
    before = [item.id for item in packages.sort_mods(scan_mods(root))]
    selected = next(item for item in scan_mods(root) if item.id == 'author.example')
    mod_updates.install_release(selected, offer, ModOperationContext(root), guard=lambda: None, downloader=downloader)
    after = packages.sort_mods(scan_mods(root))
    assert [item.id for item in after] == before
    assert all(item.active for item in after)
    assert (dependent / 'loader.js').read_text() == '//unchanged dependent'


def test_implicit_settings_cannot_replace_checked_manifest(fixture):
    root, target, offer, downloader = fixture
    path = target / 'evejs-launcher.mod.json'
    document = json.loads(path.read_text())
    document['settings'] = dict(schemaVersion=1,
        files=[dict(id='manifest', base='mod', path='evejs-launcher.mod.json', format='json')],
        fields=[dict(id='name', label='Name', type='string', default='Example', file='manifest', key=['displayName'])])
    path.write_text(json.dumps(document))
    with pytest.raises(ModUpdateError, match='descriptor'):
        mod_updates.install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None, downloader=downloader)
    assert json.loads(path.read_text())['version'] == '1.0.0'


def test_existing_download_destination_is_never_removed(tmp_path):
    selected = select_release(parse_update_source(SOURCE), '1.0.0', [release()])
    target = tmp_path / 'download.zip'
    target.write_bytes(b'keep existing file')
    with pytest.raises(FileExistsError):
        download_release(selected, target, opener=lambda _: io.BytesIO(b'zip'))
    assert target.read_bytes() == b'keep existing file'


def test_download_reports_real_byte_progress(tmp_path):
    data = b'x' * 400000
    offer = select_release(parse_update_source(SOURCE), '1.0.0', [release(data=data)])
    events = []
    download_release(offer, tmp_path / 'download.zip', opener=lambda _: io.BytesIO(data),
                     progress=lambda done, total: events.append((done, total)))
    assert events[0] == (0, len(data))
    assert events[-1] == (len(data), len(data))
    assert events == sorted(events) and len(events) > 2


def test_update_reports_install_and_recovery_stages(fixture, monkeypatch):
    root, target, offer, downloader = fixture
    events = []
    original = mod_updates.change_mod_state
    def fail_new(mod, desired, context):
        if desired and mod.version == '1.1.0':
            raise RuntimeError('progress rollback test')
        return original(mod, desired, context)
    monkeypatch.setattr(mod_updates, 'change_mod_state', fail_new)
    with pytest.raises(ModUpdateError, match='restored'):
        mod_updates.install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None,
            downloader=downloader, progress=lambda phase, *args: events.append(phase))
    assert events == ['Checking update', 'Downloading mod update', 'Checking package',
                      'Backing up mod', 'Installing mod update', 'Restoring previous version']
    monkeypatch.setattr(mod_updates, 'change_mod_state', original)
    events.clear()
    mod_updates.install_release(scan_mods(root)[0], offer, ModOperationContext(root), guard=lambda: None,
        downloader=downloader, progress=lambda phase, *args: events.append(phase))
    assert events[-1] == 'Verifying installation'
