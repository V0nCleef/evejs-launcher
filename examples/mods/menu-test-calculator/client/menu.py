# -*- coding: utf-8 -*-
import evejs_mod_menu as mods


def open_window():
    sm.GetService('cmd').OpenCalculator()


registration = mods.register('launcher.menu-test.calculator',
    {'en': 'Menu test: Calculator', 'nl': 'Menutest: Rekenmachine',
     'de': 'Menutest: Rechner', 'fr': 'Test du menu : calculatrice',
     'ru': u'\u0422\u0435\u0441\u0442 \u043c\u0435\u043d\u044e: \u043a\u0430\u043b\u044c\u043a\u0443\u043b\u044f\u0442\u043e\u0440',
     'ja': u'\u30e1\u30cb\u30e5\u30fc\u30c6\u30b9\u30c8: \u96fb\u5353',
     'ko': u'\uba54\ub274 \ud14c\uc2a4\ud2b8: \uacc4\uc0b0\uae30',
     'zh_CN': u'\u83dc\u5355\u6d4b\u8bd5: \u8ba1\u7b97\u5668'}, open_window, api_version=1)


def cleanup():
    registration.close()
