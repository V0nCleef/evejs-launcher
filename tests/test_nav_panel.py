"""Visual contract tests for left-navigation actions."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from src.constants import COLORS, CONTROL_HEIGHTS, Page
from src.widgets.nav_panel import NavPanel


def test_nav_kill_all_is_solid_red_and_keeps_room_for_its_label(
    qapp: QApplication,
) -> None:
    panel = NavPanel()
    panel.resize(220, 500)
    panel.show()
    qapp.processEvents()

    try:
        button = panel.btn_kill_all
        style = button.styleSheet()
        normal_rule = style.split("QPushButton:hover", maxsplit=1)[0]

        assert f"background-color: {COLORS['red']};" in normal_rule
        assert f"color: {COLORS['white']};" in normal_rule
        assert "margin:" not in style
        assert button.height() == CONTROL_HEIGHTS["compact"]
        assert button.contentsRect().width() >= (
            button.fontMetrics().horizontalAdvance(button.text()) + 24
        )
    finally:
        panel.close()
        panel.deleteLater()


def test_tools_navigation_sits_between_mods_and_settings(
    qapp: QApplication,
) -> None:
    panel = NavPanel()

    try:
        assert [
            panel.nav_group.button(int(page)).text()
            for page in Page
        ] == ["Home", "Characters", "Mods", "Tools", "Settings"]
        assert panel.nav_group.id(panel.btn_tools) == int(Page.TOOLS)
        assert panel.nav_group.id(panel.btn_settings) == int(Page.SETTINGS)
    finally:
        panel.close()
        panel.deleteLater()
