import json
from types import SimpleNamespace
import pytest
from PyQt6.QtCore import QObject
from src.core.mod_update_source import parse_update_source, select_release
from src.core.mod_inventory import load_mod_inventory
from src.pages.mods_page import ModsPage
from src.widgets.mod_update_dialog import ModUpdateDialog
from src.ui.mod_update_controller import ModUpdateController
from src.i18n import set_language, translate_ui_phrase


@pytest.mark.parametrize('language', ['en', 'zh_CN', 'ja', 'ko', 'fr', 'de', 'nl', 'ru'])
def test_release_dialog_localizes_frame_and_preserves_author_text(qapp, language):
    source = parse_update_source(dict(provider='github', repository='Author/Example', asset='Example-{version}.zip'))
    notes = '# Author release notes\n<img src="https://invalid.test/tracker">\n<script>not executable</script>'
    release = select_release(source, '1.0.0', [dict(draft=False, prerelease=False, tag_name='v1.1.0', body=notes,
        html_url='https://github.com/Author/Example/releases/tag/v1.1.0',
        assets=[dict(name='Example-1.1.0.zip', state='uploaded', size=100,
        browser_download_url='https://github.com/Author/Example/releases/download/v1.1.0/Example-1.1.0.zip')])])
    set_language(language)
    try:
        dialog = ModUpdateDialog(SimpleNamespace(name='Example', version='1.0.0'), release)
        assert dialog.windowTitle() == translate_ui_phrase('Mod update')
        assert dialog.update_btn.text() == translate_ui_phrase('Update mod')
        from src.widgets.update_button import UpdateButton
        from src.constants import COLORS
        assert isinstance(dialog.update_btn, UpdateButton)
        assert COLORS['gold'] in dialog.update_btn.styleSheet()
        assert dialog.notes.toPlainText() == notes
        assert dialog.notes.isReadOnly()
        dialog.deleteLater()
    finally:
        set_language('en')


def test_missing_package_recovery_is_visible_without_network(qapp, tmp_path, monkeypatch):
    from src.ui import mod_update_controller
    job = tmp_path / '_local/launcher-mod-updates' / ('a' * 32)
    job.mkdir(parents=True)
    (job / 'update.json').write_text(json.dumps(dict(schemaVersion=1, phase='swapping')))
    page = ModsPage(defer_inventory=True)
    page._evejs_root = str(tmp_path)
    page.show_inventory(load_mod_inventory(str(tmp_path)))
    host = QObject()
    host._page = page
    controller = ModUpdateController(host)
    monkeypatch.setattr(controller, 'check', lambda: None)
    controller.checked_roots.add(str(tmp_path))
    controller.inventory_ready()
    assert not page.recover_update_btn.isHidden()
    (job / 'update.json').write_text(json.dumps(dict(schemaVersion=1, phase='rolled_back')))
    page.show_inventory(load_mod_inventory(str(tmp_path)))
    controller.inventory_ready()
    assert page.recover_update_btn.isHidden()
    page.deleteLater()
    host.deleteLater()


def test_generic_update_example_and_metadata_generator(tmp_path):
    import importlib.util
    import zipfile
    from pathlib import Path
    from src.core.local_mod_packages import LocalModPackages
    from src.core.mod_manifest import scan_mods
    source = Path(__file__).resolve().parents[1]
    root = tmp_path / 'evejs'
    root.mkdir()
    LocalModPackages(root).import_package(source / 'examples/mods/github-update-demo')
    mod = scan_mods(root)[0]
    assert mod.valid and not mod.active
    assert mod.api_descriptor.evejs_versions == ('0.12.7.1',)
    archive = tmp_path / 'YourMod-1.0.0.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        for path in mod.path.iterdir():
            output.write(path, path.name)
    spec = importlib.util.spec_from_file_location('metadata_example', source / 'examples/tools/build_mod_update_metadata.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = module.build(archive)
    assert json.loads(metadata.read_text())['evejsVersions'] == ['0.12.7.1']
    with pytest.raises(FileExistsError):
        module.build(archive)


def test_sidebar_badge_tracks_only_current_compatible_offers(qapp, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace
    from src.widgets.nav_panel import NavPanel
    from src.core.local_mod_packages import LocalModPackages
    source = Path(__file__).resolve().parents[1]
    (tmp_path / 'package.json').write_text('{"version":"0.12.7.1"}')
    LocalModPackages(tmp_path).import_package(source / 'examples/mods/github-update-demo')
    page = ModsPage(defer_inventory=True)
    page._evejs_root = str(tmp_path)
    page.show_inventory(load_mod_inventory(str(tmp_path)))
    navigation = NavPanel()
    host = QObject()
    host._page = page
    host.window = SimpleNamespace(_nav=navigation)
    controller = ModUpdateController(host)
    mod = page.mods()[0]
    controller.offers[controller.key(mod)] = object()
    controller.update_badge()
    assert navigation.btn_mods._badge_count == 1
    assert navigation.btn_mods.property('updateBadge') is True
    assert '1' in navigation.btn_mods.accessibleDescription()
    # The same folder on a newly installed EveJS version must not retain the
    # previous compatibility result or its sidebar marker.
    (tmp_path / 'package.json').write_text('{"version":"0.12.8"}')
    page.show_inventory(load_mod_inventory(str(tmp_path)))
    controller.update_badge()
    assert navigation.btn_mods._badge_count == 0
    assert not navigation.btn_mods.toolTip()
    navigation.deleteLater()
    page.deleteLater()
    host.deleteLater()


@pytest.mark.parametrize('language', ['en', 'zh_CN', 'ja', 'ko', 'fr', 'de', 'nl', 'ru'])
def test_progress_is_queued_localized_and_cannot_close_mid_update(qapp, language):
    from threading import Thread
    from src.workers.mod_operation_worker import ModOperationResult
    set_language(language)
    try:
        dialog = ModUpdateDialog(SimpleNamespace(name='DLSS5', version='1.0.0'),
            SimpleNamespace(version='1.1.0', notes='Author notes', page_url=''))
        dialog.show()
        dialog.begin()
        worker = Thread(target=lambda: dialog.progress_received.emit('Downloading mod update', 50, 100))
        worker.start()
        worker.join()
        qapp.processEvents()
        assert dialog.progress.value() == 50
        assert dialog.status.text() == translate_ui_phrase('Downloading mod update')
        if language != 'en':
            assert dialog.status.text() != 'Downloading mod update'
        dialog.reject()
        dialog.close()
        assert dialog.isVisible() and not dialog.close_btn.isEnabled()
        dialog.show_progress('Installing mod update')
        assert dialog.progress.maximum() == 0
        dialog.finish(ModOperationResult(None, False, error='<diagnostic> retained files'))
        assert '<diagnostic> retained files' in dialog.status.text()
        assert dialog.close_btn.isEnabled() and dialog.isVisible()
        dialog.progress_received.emit('Checking package', 0, 0)
        qapp.processEvents()
        assert '<diagnostic>' in dialog.status.text()  # Late signals cannot erase the result.
        dialog.close()
        assert not dialog.isVisible()
        dialog.deleteLater()
    finally:
        set_language('en')


def test_update_progress_remains_open_on_success(qapp):
    from src.workers.mod_operation_worker import ModOperationResult
    dialog = ModUpdateDialog(SimpleNamespace(name='DLSS5', version='1.0.0'),
        SimpleNamespace(version='1.1.0', notes='', page_url=''))
    dialog.show()
    dialog.begin()
    dialog.finish(ModOperationResult(None, True))
    assert dialog.isVisible() and dialog.progress.value() == 100
    assert dialog.status.text() == 'Update complete.'
    assert dialog.close_btn.isEnabled()
    dialog.close()
    dialog.deleteLater()
