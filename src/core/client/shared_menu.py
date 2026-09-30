# -*- coding: utf-8 -*-
"""Public evejs_mod_menu API v1. Runs in the EVE Python 2.7 client only.

No file discovery, account elevation, keyboard automation or archive patching.
The server supplies the exact entrypoints captured in its Launcher runtime plan.
"""
import base64
import re
import traceback

API_VERSION = 1
_OPEN_ERROR = {'en': 'Unable to open mod window.', 'nl': 'Het modvenster kan niet worden geopend.',
    'de': u'Das Modfenster kann nicht ge\u00f6ffnet werden.', 'fr': u'Impossible d\u2019ouvrir la fen\u00eatre du mod.',
    'ru': u'\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u043e\u043a\u043d\u043e \u043c\u043e\u0434\u0430.',
    'ja': u'MOD \u306e\u30a6\u30a3\u30f3\u30c9\u30a6\u3092\u958b\u3051\u307e\u305b\u3093\u3002',
    'ko': u'\ubaa8\ub4dc \ucc3d\uc744 \uc5f4 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.',
    'zh_CN': u'\u65e0\u6cd5\u6253\u5f00\u6a21\u7ec4\u7a97\u53e3\u3002'}
_registry = None
_BUTTON_ID = 'evejs_launcher_shared_mods_v1'
try:
    _string_types = (basestring,)
    _text_type = unicode
except NameError:
    _string_types = (str,)
    _text_type = str


def _report(where):
    print('EVEJS_MOD_MENU:ERROR:' + where)
    traceback.print_exc()


def _id(value):
    if not isinstance(value, _string_types) or not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z', value):
        raise ValueError('Invalid shared menu mod ID')
    return value.lower()


def _labels(value):
    if not isinstance(value, dict) or not value or len(value) > 32 or 'en' not in value:
        raise ValueError('Labels need an English fallback and at most 32 translations')
    result = {}
    for key, text in value.items():
        if isinstance(text, _string_types) and not isinstance(text, _text_type):
            text = text.decode('utf-8')
        if (not isinstance(key, _string_types) or not re.match(r'^[a-z]{2}(?:_[A-Z]{2})?$', key)
                or not isinstance(text, _string_types) or not text.strip() or len(text) > 100
                or any(ord(char) < 32 for char in text) or '<' in text or '>' in text):
            raise ValueError('Invalid localized menu label')
        result[key] = text
    return result


def _localized(labels):
    language = str(getattr(session, 'languageID', 'en')).replace('-', '_').lower()
    language = {'zh': 'zh_CN', 'zh_cn': 'zh_CN', 'cn': 'zh_CN', 'jp': 'ja'}.get(language, language)
    return labels.get(language, labels.get(language.split('_')[0], labels['en']))


class Registration(object):
    """A generation-bound handle. A stale handle cannot remove a newer entry."""
    def __init__(self, registry, mod_id, token):
        self._registry, self.mod_id, self._token = registry, mod_id, token

    def update(self, label, opener, is_available=None):
        return self._registry.update(self.mod_id, label, opener, is_available, self._token)

    def close(self):
        return self._registry.unregister(self.mod_id, self._token)


class Registry(object):
    __notifyevents__ = ['OnSessionChanged', 'OnSessionReset', 'OnUIRefresh']

    def __init__(self, delivery, menus_factory=None):
        self.delivery = tuple(delivery)
        self.allowed = frozenset(_id(item['id']) for item in delivery)
        self.entries = {}
        self.namespaces = []
        self.serial = 0
        self.closed = False
        self.clearing = False
        self.last_session = None
        self.wait_for_session = False
        self.loading_owner = None
        self.signature = None
        self.menus = menus_factory(self) if menus_factory else None
        self.menu_error_reported = False

    def register(self, mod_id, label, opener, is_available=None):
        mod_id = _id(mod_id)
        if self.closed or self.clearing or mod_id not in self.allowed:
            raise ValueError('This mod is not selected in the Launcher runtime plan')
        if self.loading_owner is not None and self.loading_owner != mod_id:
            raise ValueError('An entrypoint can register only its own manifest ID')
        label = _labels(label)
        if not callable(opener) or (is_available is not None and not callable(is_available)):
            raise ValueError('Menu opener and availability must be callbacks')
        self.serial += 1
        self.entries[mod_id] = (label, opener, is_available, self.serial, False)
        self.refresh()
        return Registration(self, mod_id, self.serial)

    def update(self, mod_id, label, opener, is_available=None, token=None):
        mod_id = _id(mod_id)
        entry = self.entries.get(mod_id)
        if entry is None or (token is not None and token != entry[3]):
            raise ValueError('The shared menu registration is no longer current')
        # Preserve the handle's token while replacing callbacks and clearing failure.
        label = _labels(label)
        if not callable(opener) or (is_available is not None and not callable(is_available)):
            raise ValueError('Menu opener and availability must be callbacks')
        self.entries[mod_id] = (label, opener, is_available, entry[3], False)
        self.refresh()
        return Registration(self, mod_id, entry[3])

    def unregister(self, mod_id, token=None):
        mod_id = _id(mod_id)
        entry = self.entries.get(mod_id)
        if entry is None or (token is not None and token != entry[3]):
            return False
        del self.entries[mod_id]
        self.refresh()
        return True

    def usable(self):
        if self.closed or self.wait_for_session or not getattr(session, 'charid', None):
            return []
        result = []
        for mod_id, entry in sorted(self.entries.items()):
            if entry[4]:
                continue
            try:
                if entry[2] is None or entry[2]():
                    result.append((_localized(entry[0]), mod_id, entry[3]))
            except BaseException:
                # Faulty readiness is hidden; other entries remain usable.
                self.entries[mod_id] = entry[:4] + (True,)
                _report('availability:' + mod_id)
        return sorted(result, key=lambda item: (item[0].lower(), item[1]))

    def menu_entries(self):
        return [(label, self.open, (mod_id, token)) for label, mod_id, token in self.usable()]

    def open(self, mod_id, token):
        if (mod_id, token) not in [(item[1], item[2]) for item in self.usable()]:
            return
        entry = self.entries[mod_id]
        try:
            entry[1]()
        except BaseException:
            if self.entries.get(mod_id) == entry:
                self.entries[mod_id] = entry[:4] + (True,)
            _report('opener:' + mod_id)
            self.refresh()
            try:
                import eve
                eve.Message('CustomNotify', {'notify': _localized(entry[0]) + ': ' + _localized(_OPEN_ERROR)})
            except Exception:
                pass

    def refresh(self):
        if self.closed:
            return
        signature = tuple(self.usable())
        changed = signature != self.signature
        self.signature = signature
        try:
            # GPS executes before the UI is ready. Import native UI only after
            # a character has entered its session and a usable entry exists.
            if self.menus is None and signature:
                self.menus = NativeMenus(self)
            if self.menus is not None:
                self.menus.sync(bool(signature), changed)
            self.menu_error_reported = False
        except Exception:
            if not self.menu_error_reported:
                _report('native-menus')
                self.menu_error_reported = True

    def _clear(self):
        self.clearing = True
        self.entries.clear()
        namespaces, self.namespaces = self.namespaces, []
        for namespace in namespaces:
            cleanup = namespace.get('cleanup')
            if callable(cleanup):
                try:
                    cleanup()
                except BaseException:
                    _report('entrypoint-cleanup')
        self.entries.clear()
        self.clearing = False
        self.refresh()

    def tick(self):
        if self.closed or self.clearing or self.wait_for_session:
            return
        key = (getattr(session, 'userid', None), getattr(session, 'charid', None))
        if key != self.last_session:
            self._clear()
            self.last_session = key
            if key[1]:
                for item in self.delivery:
                    self.loading_owner = _id(item['id'])
                    namespace = {'__name__': 'evejs_menu_' + self.loading_owner.replace('.', '_'),
                                 'sm': sm, 'session': session}
                    self.namespaces.append(namespace)
                    try:
                        source = base64.b64decode(item['source'])
                        eval(compile(source, '<mod-menu:' + self.loading_owner + '>', 'exec'), namespace)
                    except BaseException:
                        self.entries.pop(self.loading_owner, None)
                        _report('entrypoint:' + self.loading_owner)
                    finally:
                        self.loading_owner = None
                print('EVEJS_MOD_MENU:REGISTERED:%s' % len(self.entries))
        self.refresh()

    def OnSessionChanged(self, is_remote, sess, change):
        self.wait_for_session = False
        self.tick()

    def OnSessionReset(self, *args):
        self.wait_for_session = True
        self.last_session = None
        self._clear()

    def OnUIRefresh(self, *args):
        self.signature = None
        self.refresh()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._clear()
        if self.menus is not None:
            self.menus.close()


class NativeMenus(object):
    def __init__(self, registry):
        from eve.client.script.ui.shared.neocom.neocom.fixedButtonExtension import FixedButtonExtension
        from eve.client.script.ui.shared.neocom.neocom.btnData.btnDataNodeGroup import BtnDataNodeGroup
        from eve.client.script.ui.shared.neocom.neocom.buttons.baseNeocomButton import BaseNeocomButton
        from eve.client.script.ui.control.eveLabel import EveLabelSmall
        from eve.client.script.ui.shared.neocom.neocom import neocomConst, neocomButtonClasses
        from carbonui.control.contextMenu.contextMenu import ShowMenu
        from carbonui import uiconst
        self.registry = registry
        self.neocom = None
        self.mapping = neocomButtonClasses.NEOCOM_BUTTON_CLASSES_BY_ID
        self.previous_button = self.mapping.get(_BUTTON_ID)
        self.insider_class = self.insider_previous = self.insider_wrapper = None
        self.insider_item = None

        class ModsButton(BaseNeocomButton):
            def ConstructIcon(self):
                BaseNeocomButton.ConstructIcon(self)
                # Keep native sprite/hover machinery, with an owned text mark.
                # A disabled label lets clicks and tooltips reach the button.
                self.mods_label = EveLabelSmall(parent=self, name='sharedModsMark',
                    state=uiconst.UI_DISABLED, align=uiconst.CENTER, text='MODS',
                    fontsize=9 if self.width < 40 else 10, bold=True, letterspace=0)

            def SetTexturePath(self, texturePath):
                # Both native sprites stay textureless, including blink updates.
                BaseNeocomButton.SetTexturePath(self, '')

            def UpdateIconColor(self, duration=None):
                BaseNeocomButton.UpdateIconColor(self, duration)
                self.mods_label.color = self.GetIconColor()

            def OnClickCommand(self):
                ShowMenu(self)

            def GetMenu(self):
                return registry.menu_entries()

            def GetMenuPosition(self, element):
                return (self.absoluteRight, self.absoluteTop)

        class ModsExtension(FixedButtonExtension):
            @property
            def is_visible(self):
                return bool(registry.usable())

            def create_button_data(self, parent):
                return BtnDataNodeGroup(parent=parent, btnType=neocomConst.BTNTYPE_GROUP,
                    btnID=_BUTTON_ID, label=_localized({'en': 'Mods', 'de': 'Mods', 'fr': 'Mods',
                        'nl': 'Mods', 'ru': u'\u041c\u043e\u0434\u044b', 'ja': u'\u30e2\u30c3\u30c9',
                        'ko': u'\ubaa8\ub4dc', 'zh_CN': u'\u6a21\u7ec4'}), iconPath=neocomConst.ICONPATH_GROUP,
                    isRemovable=False, isDraggable=False, analyticID=_BUTTON_ID)

        self.button_class = ModsButton
        self.extension = ModsExtension()
        self.mapping[_BUTTON_ID] = ModsButton

    def sync(self, visible, changed):
        neocom = sm.GetService('neocom')
        if self.neocom is not None and self.neocom is not neocom:
            self._detach_neocom()
        self.neocom = neocom
        extensions = getattr(neocom, '_fixedButtonExtensions', ())
        if visible and self.extension not in extensions:
            # Native OnSessionReset discards its list without disconnecting
            # every signal. Use a fresh owned extension after such a reset.
            self.extension = type(self.extension)()
            neocom.RegisterFixedButtonExtension(self.extension)
            print('EVEJS_MOD_MENU:NEOCOM_READY:v1')
        elif not visible and self.extension in extensions:
            neocom.UnregisterFixedButtonExtension(self.extension)
        elif visible and changed:
            self.extension.on_visible_changed(self.extension)
        self._sync_insider(visible)

    def _sync_insider(self, visible):
        # Never start Insider or bypass its role check. Only augment an open
        # native window after Show has run with its original guard intact.
        from carbon.common.script.sys import serviceConst
        if not getattr(session, 'role', 0) & serviceConst.ROLEMASK_ELEVATEDPLAYER:
            self._remove_insider_item()
            return
        from eve.devtools.script.insider import InsiderService, InsiderWnd
        from eve.devtools.script.menu_bar import MenuBar, MenuBarItem
        from carbonui import uiconst
        if self.insider_class is None:
            self.insider_class = InsiderService
            self.insider_previous = InsiderService.Show
            previous, owner = self.insider_previous, self

            def show(service, *args, **kwargs):
                result = previous(service, *args, **kwargs)
                if not owner.registry.closed:
                    try:
                        owner._sync_insider(bool(owner.registry.usable()))
                    except Exception:
                        _report('insider-show')
                return result

            self.insider_wrapper = show
            InsiderService.Show = show
        wnd = InsiderWnd.GetIfOpen()
        if not visible or wnd is None or getattr(wnd, 'destroyed', False):
            self._remove_insider_item()
            return
        for toolbar in wnd.header.extra_content.children:
            if isinstance(toolbar, MenuBar):
                if (self.insider_item is not None and not self.insider_item.destroyed
                        and self.insider_item.parent is toolbar):
                    return
                self._remove_insider_item()
                self.insider_item = MenuBarItem(parent=toolbar, align=uiconst.TOLEFT,
                    text='MODS', callback=self.registry.menu_entries)
                print('EVEJS_MOD_MENU:INSIDER_READY:v1')
                return

    def _remove_insider_item(self):
        item, self.insider_item = self.insider_item, None
        if item is not None and not getattr(item, 'destroyed', False):
            item.Close()

    def _detach_neocom(self):
        if self.neocom is not None and self.extension in getattr(self.neocom, '_fixedButtonExtensions', ()):
            self.neocom.UnregisterFixedButtonExtension(self.extension)

    def close(self):
        self._detach_neocom()
        self._remove_insider_item()
        if self.mapping.get(_BUTTON_ID) is self.button_class:
            if self.previous_button is None:
                self.mapping.pop(_BUTTON_ID, None)
            else:
                self.mapping[_BUTTON_ID] = self.previous_button
        if self.insider_class is not None and self.insider_class.Show is self.insider_wrapper:
            self.insider_class.Show = self.insider_previous


def register(mod_id, label, opener, is_available=None, api_version=1):
    if api_version != API_VERSION or type(api_version) is not int:
        raise ValueError('Unsupported shared menu API version')
    if _registry is None:
        raise RuntimeError('Shared Mods menu has not initialized')
    return _registry.register(mod_id, label, opener, is_available)


def update(mod_id, label, opener, is_available=None):
    if _registry is None:
        raise RuntimeError('Shared Mods menu has not initialized')
    return _registry.update(mod_id, label, opener, is_available)


def unregister(mod_id):
    return False if _registry is None else _registry.unregister(mod_id)


def shutdown():
    global _registry
    old, _registry = _registry, None
    if old is not None:
        try:
            sm.UnregisterNotify(old)
        finally:
            old.close()


def bootstrap(delivery):
    global _registry
    shutdown()
    _registry = Registry(delivery)
    sm.RegisterNotify(_registry)
    registry = _registry

    def watch():
        import blue
        while not registry.closed:
            try:
                registry.tick()
            except Exception:
                _report('initialization')
            blue.pyos.synchro.SleepWallclock(1000)

    import uthread
    uthread.new(watch)
    print('EVEJS_MOD_MENU:BOOTSTRAP_READY:v1')
