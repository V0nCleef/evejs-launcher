"""Offline native contracts and exact-selection delivery; no running EVE required."""
import base64
from dataclasses import replace
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.core.mod_api_manifest import ModApiManifestError, read_api_manifest
from src.core.mod_manifest import scan_mods
from src.core.mod_runtime_state import (build_mod_runtime_plan, build_docker_mod_runtime_snapshot,
    ModRuntimeStateError, native_mod_preload_paths, validate_mod_runtime_plan, mod_contract_sha256,
    write_mod_runtime_snapshot, read_mod_runtime_snapshot)
from src.core.shared_mod_menu import capture_shared_menu, shared_menu_path, stage_shared_menu
from src.core.runtime.docker_mods import (apply_docker_mod_override, build_docker_mod_override,
    finalize_docker_mod_override, rollback_docker_mod_override, _parse_owned_override,
    _read_owned_override, DockerModBridgeError)
from src.core.service_status import DockerControlPolicy
from src.core.server_launcher import build_game_server_command

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / 'src/core/client/shared_menu.py'
# A small authored fixture with the exact reviewed local builder signature.
HANDSHAKE = r'''function buildTidiSignedFuncSource(clientId) { return "pass\\n"; }
function buildMarshaledString(value) {
 const b = Buffer.from(value, 'ascii'), p = Buffer.alloc(b.length + 5);
 p[0] = 0x74; p.writeUInt32LE(b.length, 1); b.copy(p, 5); return p;
}
function buildTidiSignedFunc(clientId) {
  const pyCode = buildTidiSignedFuncSource(clientId);
  const expr = 'eval(compile("' + pyCode + '", "<tidi>", "exec"))';
  return buildMarshaledString(expr);
}
module.exports = { packet: () => buildTidiSignedFunc(17) };
'''


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / 'EveJS with spaces'
    (root / 'server/src/network/tcp').mkdir(parents=True)
    (root / 'server/src/network/tcp/handshake.js').write_text(HANDSHAKE)
    (root / 'server/package.json').write_text('{"version":"0.12.9"}')
    (root / 'server/index.js').write_text('// fixture')
    monkeypatch.setenv('APPDATA', str(tmp_path / 'profile'))
    from src.core import mod_management
    def absent(_):
        raise mod_management.ModNotManagedError('No fixture enrollment')
    monkeypatch.setattr(mod_management, 'read_managed_mod_registration', absent)
    return root


def install(root, name='menu-test-calculator', *, active=True):
    folder = root / 'mods' / name
    shutil.copytree(ROOT / 'examples/mods' / name, folder)
    if not active:
        (folder / 'loader.js').rename(folder / 'loader.js.disabled')
    return folder


def plan(root, selected, backend='native', material=None):
    return build_mod_runtime_plan(root, scan_mods(root), backend=backend,
        mode='modded' if selected else 'vanilla', runtime_identity='menu-fixture',
        selected_loader_ids=selected, docker_override_material=material)


def test_manifest_data_only_and_safe_paths(root):
    folder = install(root)
    original = json.loads((folder / 'evejs-launcher.mod.json').read_text())
    for bad in ({'apiVersion': True, 'entrypoint': 'client/menu.py'},
                {'apiVersion': 2, 'entrypoint': 'client/menu.py'},
                {'apiVersion': 1, 'entrypoint': '../menu.py'},
                {'apiVersion': 1, 'entrypoint': 'client/menu.py', 'opener': 'eval("x")'}):
        with pytest.raises(ModApiManifestError):
            read_api_manifest(root, folder, dict(original, clientMenu=bad))
    assert read_api_manifest(root, folder).client_menu.api_version == 1


def test_complete_package_import_enable_disable_and_unsupported_seam(root):
    from src.core.local_mod_packages import LocalModPackages
    from src.core.mod_manifest import set_mod_active
    manager = LocalModPackages(root)
    manager.import_package(ROOT / 'examples/shared-menu', folder_name='menu-demo')
    mod, = scan_mods(root)
    assert mod.valid and not mod.active and mod.api_descriptor.client_menu is not None
    assert plan(root, ()).shared_menu is None
    set_mod_active(mod, True)
    frozen = plan(root, ('menu-demo',))
    assert [item.mod_id for item in frozen.shared_menu.participants] == ['author.menu-demo']
    set_mod_active(scan_mods(root)[0], False)
    assert plan(root, ()).shared_menu is None
    set_mod_active(scan_mods(root)[0], True)
    (root / 'server/src/network/tcp/handshake.js').write_text('// changed login API\n')
    with pytest.raises(ModRuntimeStateError, match='unsupported'):
        plan(root, ('menu-demo',))


def test_frozen_native_selection_and_disabled_entries(root):
    first = install(root)
    second = install(root, 'menu-test-notepad', active=False)
    frozen = plan(root, (first.name,))
    assert [item.mod_id for item in frozen.shared_menu.participants] == ['launcher.menu-test.calculator']
    # A newly enabled loader cannot enter an already frozen command.
    (second / 'loader.js.disabled').rename(second / 'loader.js')
    paths = native_mod_preload_paths(frozen)
    assert paths == (shared_menu_path(root, frozen.shared_menu.digest), first / 'loader.js')
    command = build_game_server_command(root, 'modded', mod_runtime_plan=frozen)
    assert command.count('--require') == 2 and str(second / 'loader.js') not in command
    assert not paths[0].exists()  # command rendering performs no installation
    stage_shared_menu(root, frozen.shared_menu)
    assert paths[0].read_bytes() == frozen.shared_menu.loader_content
    stage_shared_menu(root, frozen.shared_menu)  # idempotent
    with pytest.raises(ModRuntimeStateError, match='SHA-256'):
        validate_mod_runtime_plan(replace(frozen, shared_menu=None))
    # Vanilla never carries menu delivery even when a loader is configured on.
    vanilla = plan(root, ())
    assert vanilla.shared_menu is None and native_mod_preload_paths(vanilla) == ()


def test_menu_entrypoint_drift_changes_contract_and_rejects_stage(root):
    folder = install(root)
    mods = scan_mods(root)
    previous = mod_contract_sha256(mods[0])
    frozen = plan(root, (folder.name,))
    (folder / 'client/menu.py').write_text('raise Exception("changed")\n')
    assert mod_contract_sha256(scan_mods(root)[0]) != previous
    with pytest.raises(ValueError, match='changed after planning'):
        stage_shared_menu(root, frozen.shared_menu)


def test_two_mod_docker_plan_snapshot_disable_and_rollback(root):
    a, b = install(root), install(root, 'menu-test-notepad')
    selected = (b.name, a.name)
    material = capture_shared_menu(root, selected)
    override = build_docker_mod_override(root, selected, shared_menu_digest=material.digest)
    frozen = plan(root, selected, 'docker_compose', override)
    assert len(frozen.shared_menu.participants) == 2
    assert override.node_options.count('--require') == 3
    assert 'read_only: true' in override.content
    result = apply_docker_mod_override(root, selected, policy=DockerControlPolicy.MANAGED,
        shared_menu_material=frozen.shared_menu)
    finalize_docker_mod_override(result, policy=DockerControlPolicy.MANAGED)
    committed = plan(root, selected, 'docker_compose')
    assert committed == frozen
    snapshot = build_docker_mod_runtime_snapshot(committed, scan_mods(root),
        runtime_identity='menu-fixture', effective_node_options_sha256=hashlib.sha256(
            override.node_options.encode()).hexdigest())
    write_mod_runtime_snapshot(snapshot)
    assert read_mod_runtime_snapshot(root, backend='docker_compose') == snapshot
    assert _parse_owned_override(root, override.content.encode()).selected_mods == selected
    assert _read_owned_override(root, result.override_path, require_active_loaders=True)
    (b / 'loader.js').rename(b / 'loader.js.disabled')
    one = apply_docker_mod_override(root, (a.name,), policy=DockerControlPolicy.MANAGED)
    assert 'menu-test-notepad' not in one.committed_content.decode()
    rollback_docker_mod_override(one, policy=DockerControlPolicy.MANAGED)
    assert result.override_path.read_bytes() == result.committed_content
    # Rollback parser accepts the captured document even after a mod was disabled.
    with pytest.raises(DockerModBridgeError):
        _read_owned_override(root, result.override_path, require_active_loaders=True)
    empty = apply_docker_mod_override(root, (), policy=DockerControlPolicy.MANAGED)
    assert 'shared-menu' not in empty.committed_content.decode()
    finalize_docker_mod_override(empty, policy=DockerControlPolicy.MANAGED)


@pytest.fixture
def api(monkeypatch):
    spec = importlib.util.spec_from_file_location('evejs_mod_menu', CLIENT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'evejs_mod_menu', module)
    spec.loader.exec_module(module)
    module.session = SimpleNamespace(charid=11, userid=7, role=0, languageID='NL')
    module.sm = SimpleNamespace()
    return module


class FakeMenus:
    def __init__(self, registry):
        self.registry, self.visible, self.changes = registry, False, 0
    def sync(self, visible, changed):
        self.visible = visible
        self.changes += bool(changed)
    def close(self):
        self.visible = False


def registry(api, delivery=None):
    result = api.Registry(delivery or [{'id': 'test.a', 'source': ''}, {'id': 'test.b', 'source': ''}], FakeMenus)
    api._registry = result
    return result


def test_registry_duplicate_update_stale_handle_localization_and_bad_opener(api):
    r = registry(api)
    calls = []
    old = api.register('Test.A', {'en': 'A', 'nl': 'Een'}, lambda: calls.append('old'))
    current = api.register('test.a', {'en': 'A', 'nl': 'Een'}, lambda: calls.append('a'))
    assert not old.close() and len(r.entries) == 1
    current.update({'en': 'A', 'nl': 'Nieuw'}, lambda: calls.append('updated'))
    api.register('test.b', {'en': 'B'}, lambda: calls.append('b'))
    assert len(r.menu_entries()) == 2 and r.menus.visible
    assert any(entry[0] == 'Nieuw' for entry in r.menu_entries())
    for _, opener, args in r.menu_entries():
        opener(*args)
    assert sorted(calls) == ['b', 'updated']
    def broken():
        raise RuntimeError('mod broke')
    api.update('test.a', {'en': 'A'}, broken)
    r.open('test.a', r.entries['test.a'][3])
    assert [item[1] for item in r.usable()] == ['test.b']
    assert api.unregister('test.b') and not r.menus.visible
    with pytest.raises(ValueError):
        api.register('unselected', {'en': 'No'}, lambda: None)


def test_relogin_entrypoint_order_cleanup_and_isolation(api):
    api.sm.cleaned = []
    source = '''import evejs_mod_menu as m
assert m.API_VERSION == 1
handle = m.register('test.a', {'en':'A'}, lambda: None)
def cleanup():
 sm.cleaned.append('a')
 handle.close()
'''
    delivery = [{'id': 'test.a', 'source': base64.b64encode(source.encode()).decode()},
                {'id': 'test.b', 'source': base64.b64encode(b'raise ValueError("broken")').decode()}]
    r = registry(api, delivery)
    r.tick()
    assert len(r.entries) == 1
    r.tick()
    assert len(r.entries) == 1 and api.sm.cleaned == []
    r.OnSessionReset()
    assert not r.entries and api.sm.cleaned == ['a'] and not r.menus.visible
    r.tick()  # reset must not reload the old character
    assert not r.entries
    api.session.charid = 22
    r.OnSessionChanged(False, api.session, {'charid': (11, 22)})
    assert len(r.entries) == 1
    r.close()
    assert not r.entries and api.sm.cleaned == ['a', 'a']


def test_native_one_icon_ordinary_account_and_insider_top_level(api, monkeypatch):
    """Model the inspected build-3396210 contracts, not invented web widgets."""
    def module(name, **values):
        parts = name.split('.')
        for index in range(1, len(parts) + 1):
            path = '.'.join(parts[:index])
            if path not in sys.modules:
                item = ModuleType(path)
                item.__path__ = []
                monkeypatch.setitem(sys.modules, path, item)
                if index > 1:
                    setattr(sys.modules['.'.join(parts[:index - 1])], parts[index - 1], item)
        item = sys.modules[name]
        for key, value in values.items():
            monkeypatch.setattr(item, key, value, raising=False)
        return item

    class Signal:
        def __init__(self):
            self.callbacks = []
        def connect(self, callback):
            self.callbacks.append(callback)
        def disconnect(self, callback):
            self.callbacks.remove(callback)
        def __call__(self, *args):
            for callback in list(self.callbacks):
                callback(*args)

    class Extension:
        @property
        def on_visible_changed(self):
            if not hasattr(self, '_signal'):
                self._signal = Signal()
            return self._signal

    class Node:
        def __init__(self, **values):
            self.__dict__.update(values)

    class BaseButton:
        absoluteRight, absoluteTop = 48, 100
        width = 48
        def ConstructIcon(self):
            self.icon = SimpleNamespace(texturePath=None)
        def SetTexturePath(self, path):
            self.icon.texturePath = self.blinkSprite.texturePath = path
        def GetIconColor(self):
            return (0.3, 0.5, 0.9, 1.0)
        def UpdateIconColor(self, duration=None):
            self.icon.rgba = self.GetIconColor()

    class Label:
        def __init__(self, **attributes):
            self.__dict__.update(attributes)

    mapping = {'native-button': BaseButton}
    opened = []
    module('eve.client.script.ui.shared.neocom.neocom.fixedButtonExtension', FixedButtonExtension=Extension)
    module('eve.client.script.ui.shared.neocom.neocom.btnData.btnDataNodeGroup', BtnDataNodeGroup=Node)
    module('eve.client.script.ui.shared.neocom.neocom.buttons.baseNeocomButton', BaseNeocomButton=BaseButton)
    module('eve.client.script.ui.control.eveLabel', EveLabelSmall=Label)
    module('eve.client.script.ui.shared.neocom.neocom.neocomConst', BTNTYPE_GROUP=3, ICONPATH_GROUP='native-folder')
    module('eve.client.script.ui.shared.neocom.neocom.neocomButtonClasses', NEOCOM_BUTTON_CLASSES_BY_ID=mapping)
    module('carbonui.control.contextMenu.contextMenu', ShowMenu=lambda button: opened.append(button.GetMenu()))
    module('carbon.common.script.sys.serviceConst', ROLEMASK_ELEVATEDPLAYER=6)
    module('carbonui.uiconst', TOLEFT=1, CENTER=2, UI_DISABLED=3)

    class Neocom:
        def __init__(self):
            self.native = object()
            self._fixedButtonExtensions = [self.native]
            self.nodes = []
        def refresh(self, extension):
            self.nodes = [item.create_button_data(None) for item in self._fixedButtonExtensions
                          if item is not self.native and item.is_visible]
        def RegisterFixedButtonExtension(self, extension):
            assert extension not in self._fixedButtonExtensions
            extension.on_visible_changed.connect(self.refresh)
            self._fixedButtonExtensions.append(extension)
            self.refresh(extension)
        def UnregisterFixedButtonExtension(self, extension):
            extension.on_visible_changed.disconnect(self.refresh)
            self._fixedButtonExtensions.remove(extension)
            self.refresh(extension)

    class MenuBar:
        def __init__(self):
            self.children = []
    class MenuBarItem:
        def __init__(self, parent, text, callback, **kwargs):
            self.parent, self.text, self.callback, self.destroyed = parent, text, callback, False
            parent.children.append(self)
        def Close(self):
            self.destroyed = True
            self.parent.children.remove(self)
    class InsiderWnd:
        value = None
        @classmethod
        def GetIfOpen(cls):
            return cls.value
    class InsiderService:
        def Show(self, show=True, force=False):
            if not api.session.role & 6:
                return
            InsiderWnd.value = None
            if show:
                toolbar = MenuBar()
                for name in ['Tools', 'Macro', 'Ship']:
                    MenuBarItem(toolbar, name, lambda: [])
                InsiderWnd.value = SimpleNamespace(header=SimpleNamespace(
                    extra_content=SimpleNamespace(children=[toolbar])), destroyed=False)
            return 'original-result'

    module('eve.devtools.script.insider', InsiderService=InsiderService, InsiderWnd=InsiderWnd)
    module('eve.devtools.script.menu_bar', MenuBar=MenuBar, MenuBarItem=MenuBarItem)
    neo, requested = Neocom(), []
    def service(name):
        requested.append(name)
        assert name == 'neocom'  # Ordinary account must never start Insider.
        return neo
    api.sm.GetService = service
    r = api.Registry([{'id': 'test.a', 'source': ''}, {'id': 'test.b', 'source': ''}])
    api._registry = r
    original_show = InsiderService.Show
    api.register('test.a', {'en': 'A'}, lambda: None)
    api.register('test.b', {'en': 'B'}, lambda: None)
    api.register('test.a', {'en': 'A'}, lambda: None)
    for _ in range(3):
        r.refresh()
    assert len(neo.nodes) == 1 and len(neo._fixedButtonExtensions) == 2
    assert neo.nodes[0].iconPath == 'native-folder'
    assert not neo.nodes[0].isRemovable and not neo.nodes[0].isDraggable
    assert mapping['native-button'] is BaseButton
    assert InsiderService.Show is original_show and InsiderWnd.value is None
    button = mapping[api._BUTTON_ID]()
    button.ConstructIcon()
    button.blinkSprite = SimpleNamespace(texturePath=None)
    button.SetTexturePath('native-folder')
    button.UpdateIconColor()
    assert button.mods_label.text == 'MODS' and button.mods_label.bold
    assert button.mods_label.state == 3 and button.mods_label.parent is button
    assert button.mods_label.fontsize == 10 and button.mods_label.color == button.GetIconColor()
    assert button.icon.texturePath == button.blinkSprite.texturePath == ''
    button.width = 32  # The actual native minimum Neocom width.
    button.ConstructIcon()
    assert button.mods_label.fontsize == 9
    button.OnClickCommand()
    assert len(opened[-1]) == 2
    # Native reset drops its list without disconnecting old signals.
    neo._fixedButtonExtensions = [neo.native]
    r.refresh()
    assert len(neo._fixedButtonExtensions) == 2
    assert len(r.menus.extension.on_visible_changed.callbacks) == 1
    api.session.role = 2
    r.refresh()
    assert InsiderService().Show() == 'original-result'
    toolbar = InsiderWnd.value.header.extra_content.children[0]
    assert [item.text for item in toolbar.children] == ['Tools', 'Macro', 'Ship', 'MODS']
    r.refresh()
    assert [item.text for item in toolbar.children].count('MODS') == 1
    assert len(toolbar.children[-1].callback()) == 2
    api.unregister('test.a')
    api.unregister('test.b')
    assert not neo.nodes and [item.text for item in toolbar.children] == ['Tools', 'Macro', 'Ship']
    r.close()
    assert mapping == {'native-button': BaseButton} and InsiderService.Show is original_show


def test_python27_parse_and_node_delivery_composes_prior_wrapper(root, tmp_path):
    from lib2to3.pgen2 import driver
    from lib2to3 import pygram, pytree
    parser = driver.Driver(pygram.python_grammar, convert=pytree.convert)
    parser.parse_string(CLIENT.read_text(encoding='utf-8'))
    folder = install(root)
    material = capture_shared_menu(root, (folder.name,))
    stage_shared_menu(root, material)
    node = os.environ.get('EVEJS_TEST_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node is needed for actual JavaScript bridge execution')
    # An independently appended payload wraps the stock builder first.
    harness = tmp_path / 'harness.cjs'
    harness.write_text('''const Module = require('module');
const previous = Module.prototype._compile;
Module.prototype._compile = function(source, filename) {
 if (filename.endsWith('handshake.js')) source += `\nconst old = buildTidiSignedFunc;
 buildTidiSignedFunc = function(id) { const p = old(id); return buildMarshaledString('(lambda other: other)(' + p.toString('ascii',5) + ')'); };`;
 return previous.call(this, source, filename);
};
require(process.argv[2]);
const packet = require(process.argv[3]).packet();
console.log(JSON.stringify({head:packet[0], length:packet.readUInt32LE(1), expression:packet.toString('ascii',5)}));
''')
    result = subprocess.run([node, str(harness), str(shared_menu_path(root, material.digest)),
        str(root / 'server/src/network/tcp/handshake.js')], capture_output=True, text=True, check=True)
    packet = json.loads(result.stdout.splitlines()[-1])
    assert packet['head'] == 0x74 and packet['length'] == len(packet['expression'])
    assert '(lambda other: other)(' in packet['expression']
    assert '<evejs-shared-mod-menu>' in packet['expression']
    assert 'DELIVERY_READY:v1' in result.stdout
    parser.parse_string(packet['expression'] + '\n')
    import re
    encoded = re.search(r'b64decode\("([A-Za-z0-9+/=]+)"\)', packet['expression']).group(1)
    parser.parse_string(base64.b64decode(encoded).decode('utf-8'))
