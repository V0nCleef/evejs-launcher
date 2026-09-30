# -*- coding: utf-8 -*-
# Complete, self-contained Python 2.7 client entrypoint for author.menu-demo.
import evejs_mod_menu as mods
from carbonui import uiconst
from carbonui.control.window import Window
from eve.client.script.ui.control.eveLabel import EveLabelMedium


class ModWindow(Window):
    default_windowID = 'AuthorMenuDemoV1'
    default_caption = 'Example mod'
    default_width = 320
    default_height = 180
    default_scope = uiconst.SCOPE_INGAME

    def ApplyAttributes(self, attributes):
        Window.ApplyAttributes(self, attributes)
        EveLabelMedium(parent=self.content, align=uiconst.TOTOP,
                       text='This window belongs to your mod.')


def open_window():
    ModWindow.Open()


registration = mods.register('author.menu-demo',
    {'en': 'Example mod', 'nl': 'Voorbeeldmod'}, open_window, api_version=1)


def cleanup():
    registration.close()
    ModWindow.CloseIfOpen()
