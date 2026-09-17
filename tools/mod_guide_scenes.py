"""Offscreen walkthrough scenes; every object is disposable demonstration data.

Launcher views use the production widgets. Author file views are explicitly
labelled documentation viewers, never presented as a launcher feature.
"""
from __future__ import annotations

from dataclasses import replace
import json
import importlib.util
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPlainTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from src.core.local_mod_packages import LocalModPackages
from src.core.mod_activation_state import ModActivationProjection, ModActivationStatus
from src.core.mod_manifest import scan_mods
from src.core.mod_inventory import load_mod_inventory
from src.core.mod_operations import ModOperationContext, remove_local_mod
from src.core.mod_relationships import plan_mod_order
from src.core.mod_settings import ModSettingsContext, ModSettingsSession
from src.core.mod_settings_schema import parse_settings_schema
from src.i18n import set_language
from src.pages.mods_page import ModRow, ModsPage
from src.widgets.console_panel import ConsolePanel
from src.widgets.mod_settings_dialog import ModSettingsDialog
from src.widgets.mod_update_dialog import ModUpdateDialog


def render_scenes(app, root: Path, destination: Path, capture, repository: Path) -> list[str]:
    generated = []

    def save(widget, name, title, width=900, height=None):
        frame = QWidget()
        frame.resize(width, height or widget.sizeHint().height() + 75)
        box = QVBoxLayout(frame)
        label = QLabel('DEMO / ' + title)
        label.setWordWrap(True)
        label.setStyleSheet('color: #a7b6c5; font-size: 14px; padding: 7px;')
        box.addWidget(label)
        if widget.isWindow():
            widget.setParent(frame)
            widget.setWindowFlags(Qt.WindowType.Widget)
        box.addWidget(widget)
        capture(app, frame, destination / (name + '.png'))
        generated.append(name + '.png')

    def document(name, title, text, height=360):
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setFont(QFont('Consolas', 11))
        editor.setStyleSheet('QPlainTextEdit { font-family: Consolas; font-size: 15px; padding: 14px; }')
        editor.setPlainText(text)
        save(editor, name, 'Author file viewer — ' + title, height=height)

    packages = LocalModPackages(root)
    for name in ('hello-loader', 'source-overlay-demo', 'client-receipt-demo', 'profile-options'):
        packages.import_package(repository / 'examples/mods' / name)

    def mod(name):
        return next(m for m in scan_mods(root) if m.id == name)

    def row(name, *, enabled=False, status=ModActivationStatus.VERIFIED, **kwargs):
        item = replace(mod(name), active=enabled)
        projection = ModActivationProjection(status, enabled, enabled, enabled, None, None, 'guide-demo')
        return ModRow(item, projection=projection, projection_resolver=lambda _: projection,
                      local_removable=True, can_remove=True, delegated_activation=True, **kwargs)

    def settings(name, fields, values, scope='Global settings', height=465):
        dialog = ModSettingsDialog(name, parse_settings_schema(fields).fields, values, scope_label=scope)
        dialog.resize(700, height)
        return dialog

    def message(name, title, text, question=False):
        box = QMessageBox()
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Question if question else QMessageBox.Icon.Warning)
        box.setStandardButtons((QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
                               if question else QMessageBox.StandardButton.Ok)
        save(box, name, title + ' — fictional package', width=730, height=240)

    def choose(name, title, prompt, choices):
        dialog = QInputDialog()
        dialog.setWindowTitle(title)
        dialog.setLabelText(prompt)
        dialog.setComboBoxItems(choices)
        dialog.setComboBoxEditable(False)
        save(dialog, name, title, width=700, height=225)

    page = ModsPage(defer_inventory=True)
    page.set_evejs_root(str(root))
    inventory = load_mod_inventory(str(root))
    overview_mods = tuple(mod(name) for name in ('configure-demo', 'hello-loader', 'client-receipt-demo'))
    page.show_inventory(replace(inventory, mods=overview_mods))
    update_row = next(row for row in page._rows if row.mod.id == 'configure-demo')
    update_row.update_btn.set_update_available('1.2.3')
    update_row.update_btn._sync_motion(False)
    save(page, 'mods-overview', 'Mods page — fictional update available, no running game', width=1120, height=760)
    tree = QTreeWidget()
    tree.setHeaderLabels(['Package file', 'Purpose'])
    folder = QTreeWidgetItem(tree, ['hello-loader', 'Select this folder with Add Folder'])
    for filename in ('evejs-launcher.mod.json', 'loader.js.disabled'):
        assert (mod('hello-loader').path / filename).is_file()
        QTreeWidgetItem(folder, [filename, 'Name and launcher support' if filename.endswith('.json') else 'Starts disabled'])
    tree.expandAll()
    tree.setColumnWidth(0, 370)
    save(tree, 'package-files', 'Author file viewer — actual demo package files', height=260)
    document('package-manifest', 'evejs-launcher.mod.json', (mod('hello-loader').path/'evejs-launcher.mod.json').read_text(), 530)
    document('package-loader', 'loader.js.disabled', (mod('hello-loader').path/'loader.js.disabled').read_text(), 230)
    picker = QFileDialog(None, 'Add Mod Folder', str(mod('hello-loader').path))
    picker.setOption(QFileDialog.Option.DontUseNativeDialog)
    picker.setFileMode(QFileDialog.FileMode.Directory)
    save(picker, 'import-folder', 'Folder chooser — select hello-loader; appearance depends on Windows', height=485)
    save(row('hello-loader'), 'loader-imported', 'Imported Hello Loader — disabled', height=155)
    save(row('hello-loader', enabled=True, status=ModActivationStatus.RESTART_REQUIRED), 'loader-enabled', 'Enabled in configuration — restart still required', height=155)
    save(row('hello-loader', enabled=False, status=ModActivationStatus.RESTART_REQUIRED), 'loader-disabled', 'Disabled in configuration — restart still required', height=155)
    restart_page = ModsPage(defer_inventory=True)
    restart_page.set_evejs_root(str(root))
    rail = restart_page.action_rail
    rail.setParent(None)
    rail.show()
    save(rail, 'apply-restart', 'Apply the configured changes to the Game server', height=190)
    restart_page.deleteLater()
    for suffix, lines in (
        ('loaded', ['[Hello Loader] Loaded by EveJS Launcher.']),
        ('disabled', ['Example fresh run with the loader disabled.', 'The Hello Loader message is absent.']),
    ):
        console = ConsolePanel()
        console.begin_stream('DEMO / Expected server output — not a live server')
        for line in lines: console.append_stream_line(line)
        console.finish_stream()
        save(console, 'console-'+suffix, 'Illustrative console output; no server was started', height=250)

    item = mod('configure-demo')
    schema = item.settings_schema
    session = ModSettingsSession.open(ModSettingsContext(root, item.path), schema)
    save(settings(item.name, schema, session.values), 'configure-default', 'Open Configure — default value 10', width=730, height=470)
    session.save({'scanInterval': 20})
    reopened = ModSettingsSession.open(ModSettingsContext(root, item.path), schema)
    assert reopened.values['scanInterval'] == 20
    document('saved-preferences', 'Actual saved preferences.json', (item.path/'preferences.json').read_text(), 245)
    save(settings(item.name, schema, reopened.values), 'configure-reopened', 'Reopen Configure — saved value 20', width=730, height=470)

    choice = {'id':'quality','label':'Quality','type':'choice','default':'high',
              'choices':[{'value':'low','label':'Low'},{'value':'high','label':'High'}],
              'file':'prefs','key':['quality']}
    def form(fields, base='mod'):
        return {'schemaVersion':1,'files':[{'id':'prefs','base':base,'path':'preferences.json','format':'json'}], 'fields':fields}
    for field, suffix in (
        ({'id':'hints','label':'Show hints','type':'boolean','default':True,'file':'prefs','key':['hints']}, 'boolean'),
        ({'id':'interval','label':'Interval (seconds)','type':'integer','default':10,'minimum':1,'maximum':300,'file':'prefs','key':['interval']}, 'integer'),
        ({'id':'strength','label':'Effect strength','type':'number','default':0.5,'minimum':0,'maximum':1,'step':0.1,'file':'prefs','key':['strength']}, 'number'),
        ({'id':'label','label':'Profile label','type':'string','default':'Pilot A','file':'prefs','key':['label']}, 'string'),
        (choice, 'choice'),
    ):
        dialog = settings('Form Controls Demo', form([field]), {})
        if suffix=='choice': dialog._controls['quality'].setCurrentIndex(0)
        save(dialog, 'control-'+suffix, 'Actual '+suffix+' input', width=700, height=455)
    fields = [dict(choice, group='Display')]
    fields += [{'id':f'extra{i}','label':f'Extra option {i}','type':'integer','default':i,
                'file':'prefs','key':[f'extra{i}'],'group':'Advanced tuning','advanced':True} for i in range(1,8)]
    for suffix, advanced, search in (('hidden',False,''),('shown',True,''),('search',True,'Extra option 4')):
        dialog = settings('Advanced Settings Demo', form(fields), {})
        dialog.advanced_toggle.setChecked(advanced)
        dialog.search_edit.setText(search)
        save(dialog, 'advanced-'+suffix, 'Advanced fields and search — '+suffix, width=730, height=620)
    choose('profile-picker', 'Mod Settings', 'Choose a profile', ['Pilot A','Pilot B'])
    for pilot, quality in (('Pilot A','low'),('Pilot B','high')):
        save(settings('Profile Settings Demo', form([choice], 'profile'), {'quality':quality}, 'Profile: '+pilot),
             'profile-'+pilot[-1].lower(), 'Independent preferences for '+pilot, width=730, height=470)
    document('storage-locations', 'Illustrative storage map (not literal absolute paths)',
             'mod              -> mod package / preferences.json\nevejs            -> EveJS / config / mods / preferences.json\nprofile          -> selected profile / private mod data\nprofile_settings -> selected profile / EVE text settings\nclient           -> copied client / shared files\n\nPilot A / private mod data:  quality = low\nPilot B / private mod data:  quality = high', 335)
    localized = dict(choice, label={'en':'Quality','nl':'Kwaliteit'}, choices=[
        {'value':'low','label':{'en':'Low','nl':'Laag'}}, {'value':'high','label':{'en':'High','nl':'Hoog'}}])
    for lang in ('en','nl'):
        set_language(lang)
        save(settings('Language Demo', form([localized]), {'quality':'high'}),
             'language-'+lang, 'Same setting and value — '+lang, width=730, height=470)
    set_language('en')
    document('language-value', 'Same saved key and value in both languages', '{\n  "quality": "high"\n}', 235)
    release = SimpleNamespace(version='1.2.3', notes='Fictional demo release — nothing will be downloaded.\n\nChanges:\n- Added an example preference.\n- Existing saved preferences are kept.', page_url='https://example.invalid/demo')
    update = ModUpdateDialog(item, release)
    update.update_btn._sync_motion(False)
    save(update, 'update-review', 'Review version and release notes before choosing Update mod', width=740, height=560)
    document('release-files', 'Files to attach to release v1.2.3', 'YourMod-1.2.3.zip\nYourMod-1.2.3.update.json\n\nGenerate the sidecar with build_mod_update_metadata.py.\nDo not use the automatically generated GitHub source ZIP.', 270)
    release_folder = root / 'author-release-example'
    release_folder.mkdir()
    declaration = json.loads((item.path/'evejs-launcher.mod.json').read_text())
    declaration.update(id='your-mod', displayName='Your Mod', version='1.2.3',
                       updates={'provider':'github','repository':'your-name/your-mod','asset':'YourMod-{version}.zip','channel':'stable'})
    release_zip = release_folder / 'YourMod-1.2.3.zip'
    with zipfile.ZipFile(release_zip, 'w') as archive:
        archive.writestr('YourMod/evejs-launcher.mod.json', json.dumps(declaration,indent=2))
    document('release-zip', 'Actual demo ZIP contents — version 1.2.3',
             'YourMod-1.2.3.zip\n  YourMod/\n    evejs-launcher.mod.json\n\nThe mod instruction file declares:\n  "id": "your-mod"\n  "version": "1.2.3"', 305)
    spec = importlib.util.spec_from_file_location('guide_metadata', repository/'examples/tools/build_mod_update_metadata.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    result = generator.build(release_zip)
    document('release-information', 'Actual information file generated from the demo ZIP', result.read_text(encoding='utf-8'), 300)
    # Profile preparation has no dedicated new button. Show the real character
    # launch control, and clearly identified author views of the API boundary.
    from src.widgets.character_card import CharacterCard
    save(CharacterCard('Demo Account','Demo Pilot',123,isk='100000',ship='Example ship',sp='250000'),
         'helper-launch', 'Normal character launch triggers declared preparation', width=360, height=380)
    document('helper-request', 'Illustrative prepare_profile request fields',
             '"action": "prepare_profile",\n"settings": {\n  "profile": {"label": "Pilot A", "intensity": 2, "enabled": true}\n}\n\nThe complete request also supplies protocol, requestId and paths.', 315)
    document('helper-reply', 'Example helper proposes one owned setting',
             '"success": true,\n"state": "ready",\n"contributions": [{\n  "base": "profile", "path": "preferences.ini", "format": "ini",\n  "key": ["Example", "Intensity"], "value": 2\n}]\n\nThe launcher validates and applies the proposal.', 330)
    document('helper-result', 'Illustrative preferences.ini after accepted preparation', '[Example]\nLabel=Pilot A\nIntensity=2\nEnabled=1', 250)
    message('helper-failure', 'Mod Operation', 'DEMO: profile preparation failed. Review the helper result before retrying.')

    message('remove-confirm', 'Remove Mod', f'Remove {item.name}? Its folder will be kept for Undo Removal. Private settings are kept; shared changes are restored where ownership is recorded.', True)
    removed = remove_local_mod(item, ModOperationContext(root))
    assert not item.path.exists()
    choose('undo-removal', 'Undo Removal', 'Choose a removed mod', [removed.relative_path])
    packages.restore(removed.record_id)
    assert (item.path/'preferences.json').is_file()
    save(row('configure-demo'), 'restored-mod', 'Actual restored demo package — check settings before enabling', height=155)
    save(row('configure-demo', enabled=True), 'settings-enabled', 'Settings package enabled; Configure remains a separate action', height=155)
    document('owned-settings', 'Illustrative key ownership in one shared file',
             'Before removal:\n  Mod A owns: [Example] Intensity=2\n  Mod B owns: [Other] Enabled=1\n\nAfter removing Mod A:\n  [Example] Intensity -> previous value restored\n  [Other] Enabled=1   -> kept', 310)
    dependent = replace(mod('hello-loader'), active=True,
                        api_descriptor=replace(mod('hello-loader').api_descriptor, requires=('base-content',)))
    issue = plan_mod_order([dependent], backend='native').issues[0].message
    message('dependency-missing', 'Mod Operation', issue)
    base = replace(mod('hello-loader'), id='base-content', name='Base Content', active=True,
                   path=root/'mods/base-content')
    ordered = plan_mod_order([dependent,base], backend='native')
    assert not ordered.issues and ordered.mods[0].id=='base-content'
    document('dependency-order', 'Actual resolved preload order for the demo',
             '\n'.join(f'{i+1}. {m.name} ({m.id})' for i,m in enumerate(ordered.mods))
             +'\n\nThe required Base Content is loaded before Hello Loader.', 250)
    save(row('source-overlay-demo'), 'source-row', 'Source Overlay Demo — import starts disabled', width=1020, height=165)
    save(settings('Source Overlay Demo', mod('source-overlay-demo').settings_schema, {}),
         'source-options', 'Choose the region and replacement before enabling the overlay', width=730, height=570)
    document('source-files', 'Illustrative source contribution and restoration',
             'Before enable:  exports.first = "original";\nAfter enable:   exports.first = "modified";\nAfter cleanup:  exports.first = "original";\n\nThe helper proposes the owned edit.\nOther regions and other owners are preserved.', 275)
    client_row = row('client-receipt-demo')
    save(client_row, 'client-row', 'Client Receipt Example — no renderer payload', width=1050, height=165)
    menu_row = row('client-receipt-demo')
    menu = menu_row.helper_btn.menu()
    menu.setParent(None)
    save(menu, 'client-actions', 'Actual Actions menu — capabilities declared by the demo', width=510, height=245)
    menu_row.deleteLater()
    document('client-receipt', 'Illustrative receipt responsibilities — not proof of an installation',
             'Install -> validate exact client build and save originals\nVerify  -> compare installed files with the recorded state\nDisable -> restore shared integration\nRemove  -> finish cleanup before removing the package\nRecover -> resolve an interrupted install or restoration\n\nThe example has no real renderer binaries.', 335)
    for action,status,exit_code in (('launch_result','started',None),('client_exit','exited',0)):
        event={'launchId':'demo-launch-001','status':status,'pid':123,'exitCode':exit_code,'errorType':''}
        document('event-'+status, 'Illustrative '+action+' event — no extra launcher button',
                 json.dumps({'action':action,'event':event},indent=2), 400)
    document('event-failed', 'Illustrative failed process creation — not a login result',
             json.dumps({'action':'launch_result','event':{'launchId':'demo-launch-002','status':'failed','pid':None,'exitCode':None,'errorType':'OSError'}},indent=2), 400)
    return generated
