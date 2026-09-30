# -*- coding: utf-8 -*-
import evejs_mod_menu as mods


def open_window():
    sm.GetService('cmd').OpenNotepad()


registration = mods.register('launcher.menu-test.notepad',
    {'en': 'Menu test: Notepad', 'nl': 'Menutest: Notitieblok',
     'de': 'Menutest: Notizblock', 'fr': 'Test du menu : bloc-notes',
     'ru': u'\u0422\u0435\u0441\u0442 \u043c\u0435\u043d\u044e: \u0431\u043b\u043e\u043a\u043d\u043e\u0442',
     'ja': u'\u30e1\u30cb\u30e5\u30fc\u30c6\u30b9\u30c8: \u30e1\u30e2',
     'ko': u'\uba54\ub274 \ud14c\uc2a4\ud2b8: \uba54\ubaa8\uc7a5',
     'zh_CN': u'\u83dc\u5355\u6d4b\u8bd5: \u8bb0\u4e8b\u672c'}, open_window, api_version=1)


def cleanup():
    registration.close()
