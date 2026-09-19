"""Exercise authored UI logic with mocks, without reading or running game code."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ui(monkeypatch):
    pending = []

    class Control:
        def __init__(self, **kw):
            self.value = kw.get('setvalue', kw.get('checked'))
            self.text = kw.get('text', '')
            self.disabled = False

        def Disable(self): self.disabled = True
        def Enable(self): self.disabled = False
        def SetChecked(self, value, report=False): self.value = value
        def SetValue(self, value, docallback=False): self.value = value
        def GetValue(self): return self.value

    class Window:
        instance = None

        @classmethod
        def Open(cls):
            if cls.instance is None or cls.instance.destroyed:
                cls.instance = cls()
                cls.instance.destroyed = False
                cls.instance.content = object()
                cls.instance.ApplyAttributes(None)
            return cls.instance

        def ApplyAttributes(self, attributes): pass
        def Close(self): self.destroyed = True

    modules = {
        'uthread': {'new': lambda fn, *args: pending.append(lambda: fn(*args))},
        'carbonui': {'uiconst': SimpleNamespace(SCOPE_INGAME=1, TOTOP=2)},
        'carbonui.control.window': {'Window': Window},
        'carbonui.control.button': {'Button': Control},
        'carbonui.control.checkbox': {'Checkbox': Control},
        'carbonui.control.singlelineedits.singleLineEditInteger': {'SingleLineEditInteger': Control},
        'eve.client.script.ui.control.eveLabel': {'EveLabelMedium': Control},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location('example_window', ROOT/'examples/ingame-settings/window.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = {}
    session = SimpleNamespace(charid=1001)
    settings = dict(enabled=False, interval=60, revision=0)
    calls = []
    after_rpc = []

    def get():
        calls.append('get')
        if after_rpc:
            after_rpc.pop(0)()
        return json.dumps(dict(success=True, settings=settings))

    def save(payload):
        calls.append('save')
        settings.update(json.loads(payload))
        settings['revision'] += 1
        return json.dumps(dict(success=True, settings=settings))

    remote = SimpleNamespace(GetSettings=get, SaveSettings=save)
    sm = SimpleNamespace(
        RegisterForNotifyEvent=lambda obj, event: events.setdefault(event, []).append(obj),
        UnregisterForNotifyEvent=lambda obj, event: events[event].remove(obj),
        RemoteSvc=lambda name: remote if name == 'exampleModSettings' else None,
    )
    ready = [True]
    owner = module.install_window(sm, session, lambda: ready[0])

    def drain():
        while pending: pending.pop(0)()

    return SimpleNamespace(owner=owner, module=module, sm=sm, session=session,
                           settings=settings, calls=calls, remote=remote, ready=ready,
                           events=events, after_rpc=after_rpc, drain=drain)


def test_apply_reload_close_and_single_window(ui):
    ui.owner.OnExampleModSettingsOpen()
    window = ui.owner.window
    assert window.revision is None
    ui.drain()
    ui.owner.OnExampleModSettingsOpen()
    assert ui.owner.window is window
    window.enabled.value = True
    window.interval.value = 120
    window.Save()
    ui.drain()
    assert ui.settings == dict(enabled=True, interval=120, revision=1)
    window.interval.value = 999
    window.Close()
    ui.owner.OnExampleModSettingsOpen()
    ui.drain()
    window = ui.owner.window
    assert window.interval.value == 120
    window.interval.value = 888
    window.Reload()
    ui.drain()
    assert window.interval.value == 120


def test_rpc_failure_retains_draft_and_reenables_controls(ui):
    ui.owner.OnExampleModSettingsOpen()
    ui.drain()
    window = ui.owner.window
    window.interval.value = 120
    ui.remote.SaveSettings = lambda _: json.dumps(dict(success=False, message='stale'))
    window.Save()
    ui.drain()
    assert ui.settings['interval'] == 60
    assert window.interval.value == 120
    assert not window.busy and not window.interval.disabled
    assert 'Reload' in window.notice.text


@pytest.mark.parametrize('action', ['close', 'character', 'disconnect'])
def test_request_finishing_after_session_or_window_changes(ui, action):
    ui.owner.OnExampleModSettingsOpen()
    window = ui.owner.window

    def change():
        assert window.busy and window.enabled.disabled
        if action == 'close': window.Close()
        elif action == 'character': ui.session.charid = 1002
        else: ui.ready[0] = False

    ui.after_rpc.append(change)
    ui.drain()
    assert window.destroyed
    assert window.revision is None  # Old RPC result never reaches the screen.


def test_readiness_replacement_cleanup_and_character_event(ui):
    ui.ready[0] = False
    ui.owner.OnExampleModSettingsOpen()
    assert ui.owner.window is None
    ui.ready[0] = True
    ui.owner.OnExampleModSettingsOpen()
    ui.drain()
    window = ui.owner.window
    ui.owner.OnSessionChanged(False, SimpleNamespace(charid=1002), {})
    assert window.destroyed
    ui.owner.dispose()
    assert all(not handlers for handlers in ui.events.values())
    ui.owner.OnExampleModSettingsOpen()
    assert ui.owner.window is None
    new = ui.module.install_window(ui.sm, ui.session, lambda: True)
    assert all(handlers == [new] for handlers in ui.events.values())


def test_both_ai_handoffs_include_prompts_and_exported_examples(qapp, tmp_path):
    import zipfile
    from src.widgets.mod_authoring_guide import ModAuthoringGuide
    guide = ModAuthoringGuide(bundle_root=ROOT)
    try:
        guide._display(ROOT/'docs/how-to-make-a-mod/16-ingame-settings.md')
        assert not guide.export_examples.isHidden()
        handoff = guide._handoff_text()
        assert '# AI handoff: add an in-game settings window' in handoff
        assert 'def install_window(' in handoff
        assert 'function createSettingsHandlers(' in handoff
        assert 'Settings changed. Reload before saving.' in handoff
        assert 'has not itself' in handoff
        guide._display(ROOT/'docs/how-to-make-a-mod/12-client-files.md')
        assert not guide.export_examples.isHidden()
        migration = guide._handoff_text()
        assert '# AI handoff: migrate legacy client scripts to login delivery' in migration
        assert 'two server roots sharing a client' in migration
        assert 'client-script-delivery-v1' in migration
        archive = tmp_path/'examples.zip'
        guide._write_examples(archive)
        with zipfile.ZipFile(archive) as saved:
            for name in ('ingame-settings/AI-HANDOFF.md', 'ingame-settings/window.py',
                         'ingame-settings/settings-handlers.js', 'login-delivery/AI-HANDOFF.md'):
                assert 'examples/'+name in saved.namelist()
    finally:
        guide.close()
        guide.deleteLater()
