# Author-owned Python 2.7-compatible example. No client archive patching.
# Run through your reviewed login delivery after character readiness succeeds.
import json
import uthread
from carbonui import uiconst as C
from carbonui.control.window import Window
from carbonui.control.button import Button
from carbonui.control.checkbox import Checkbox
from carbonui.control.singlelineedits.singleLineEditInteger import SingleLineEditInteger
from eve.client.script.ui.control.eveLabel import EveLabelMedium


def install_window(sm, session, is_ready):
    # Keep the returned controller and call dispose() before installing again.
    class Controller(object):
        __notifyevents__ = ['OnExampleModSettingsOpen', 'OnSessionChanged']

        def __init__(self):
            self.active = True
            self.session = session
            self.window = None

        def usable(self):
            return self.active and is_ready() and bool(getattr(self.session, 'charid', None))

        def OnExampleModSettingsOpen(self):
            if self.usable():
                self.window = SettingsWindow.Open()

        def OnSessionChanged(self, *args):
            if len(args) > 1 and hasattr(args[1], 'charid'):
                self.session = args[1]
            if self.window is not None and not self.window.ValidCharacter():
                self.window = None

        def dispose(self):
            self.active = False
            if self.window is not None and not self.window.destroyed:
                self.window.Close()
            for event in self.__notifyevents__:
                sm.UnregisterForNotifyEvent(self, event)

    owner = Controller()

    class SettingsWindow(Window):
        default_windowID = 'EveJSExampleModSettings'
        default_caption = 'Example mod settings'
        default_width = 360
        default_height = 240
        default_minSize = (360, 240)
        default_scope = C.SCOPE_INGAME

        def ApplyAttributes(self, attributes):
            self.character = owner.session.charid
            self.busy = False
            self.revision = None
            Window.ApplyAttributes(self, attributes)
            owner.window = self
            self.notice = EveLabelMedium(parent=self.content, align=C.TOTOP, text='Loading...')
            self.enabled = Checkbox(parent=self.content, align=C.TOTOP, text='Enable example feature', checked=False)
            EveLabelMedium(parent=self.content, align=C.TOTOP, text='Interval in seconds')
            self.interval = SingleLineEditInteger(parent=self.content, align=C.TOTOP,
                                                 setvalue=60, minValue=6, maxValue=86400)
            Button(parent=self.content, align=C.TOTOP, label='Apply', func=self.Save)
            Button(parent=self.content, align=C.TOTOP, label='Reload', func=self.Reload)
            self.Reload()

        def ValidCharacter(self):
            if self.destroyed:
                return False
            if not owner.usable() or self.character != getattr(owner.session, 'charid', None):
                self.Close()
                return False
            return True

        def SetBusy(self, value):
            self.busy = value
            self.enabled.Disable() if value else self.enabled.Enable()
            self.interval.Disable() if value else self.interval.Enable()

        def Request(self, method, payload=None):
            if self.busy or not self.ValidCharacter():
                return
            self.SetBusy(True)
            try:
                remote = sm.RemoteSvc('exampleModSettings')
                response = json.loads(getattr(remote, method)(*(() if payload is None else (payload,))))
                # The RPC yields. Never update a window for a different character.
                if not self.ValidCharacter():
                    return
                if not response.get('success'):
                    raise RuntimeError(response.get('message', 'Settings request failed.'))
                settings = response['settings']
                self.revision = settings['revision']
                self.enabled.SetChecked(settings['enabled'], report=False)
                self.interval.SetValue(settings['interval'], docallback=False)
                self.notice.text = 'Saved.' if method == 'SaveSettings' else 'Settings loaded.'
            except Exception:
                if self.ValidCharacter():
                    self.notice.text = 'Could not load/save. Reload and try again.'
            finally:
                if not self.destroyed:
                    self.SetBusy(False)

        def Reload(self, *args):
            uthread.new(self.Request, 'GetSettings')

        def Save(self, *args):
            if self.busy or self.revision is None or not self.ValidCharacter():
                return
            payload = json.dumps({'enabled': bool(self.enabled.GetValue()),
                                  'interval': self.interval.GetValue(), 'revision': self.revision})
            uthread.new(self.Request, 'SaveSettings', payload)

    registered = []
    try:
        for event in owner.__notifyevents__:
            sm.RegisterForNotifyEvent(owner, event)
            registered.append(event)
    except Exception:
        owner.active = False
        for event in registered:
            sm.UnregisterForNotifyEvent(owner, event)
        raise
    return owner
