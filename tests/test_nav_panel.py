"""Visual contract tests for left-navigation actions."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.constants import COLORS, Page
from src.widgets.nav_panel import NavPanel


def test_nav_kill_all_is_compact_danger_outline_and_keeps_room_for_its_label(
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

        assert "background-color: rgba(224, 79, 79, 0.10);" in normal_rule
        assert f"color: {COLORS['red']};" in normal_rule
        assert "margin:" not in style
        assert button.height() == 28
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


def test_deep_signal_navigation_preserves_public_actions_and_keyboard_focus(
    qapp: QApplication,
) -> None:
    panel = NavPanel()
    changed_pages: list[int] = []
    service_actions: list[str] = []
    panel.page_changed.connect(changed_pages.append)
    panel.server_toggled.connect(lambda: service_actions.append("server"))
    panel.market_toggled.connect(lambda: service_actions.append("market"))
    panel.kill_all_clicked.connect(lambda: service_actions.append("kill"))

    try:
        assert panel.objectName() == "navPanel"
        assert panel.width() == 220
        assert panel.command_label.text() == "COMMAND DECK"
        assert panel.systems_label.text() == "SYSTEM CONTROL"
        assert panel.orbital_emblem.accessibleName() == "Deep Signal orbital emblem"
        assert panel.orbital_emblem.size().width() == 88

        buttons = [
            panel.btn_home,
            panel.btn_characters,
            panel.btn_mods,
            panel.btn_tools,
            panel.btn_settings,
        ]
        assert [button.text() for button in buttons] == [
            "Home",
            "Characters",
            "Mods",
            "Tools",
            "Settings",
        ]
        assert all(button.focusPolicy() == Qt.FocusPolicy.StrongFocus for button in buttons)
        assert all(button.height() == 56 for button in buttons)
        assert all(not button.icon().isNull() for button in buttons)

        panel.btn_characters.click()
        panel.btn_server.click()
        panel.btn_market.click()
        panel.btn_kill_all.click()
        assert changed_pages == [int(Page.CHARACTERS)]
        assert service_actions == ["server", "market", "kill"]
    finally:
        panel.close()
        panel.deleteLater()


def test_service_toggle_text_remains_logical_while_semantic_state_changes(
    qapp: QApplication,
) -> None:
    panel = NavPanel()

    try:
        states = (
            ("Starting Server...", "warning"),
            ("Stop Server", "online"),
            ("Server: Failed", "danger"),
            ("Retry Server", "danger"),
            ("Start Server", "idle"),
        )
        for text, expected_state in states:
            panel.btn_server.setText(text)
            assert panel.btn_server.text() == text
            assert panel.btn_server.property("telemetryState") == expected_state
    finally:
        panel.close()
        panel.deleteLater()
