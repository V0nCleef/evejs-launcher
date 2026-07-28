"""Interaction regressions for the custom settings toggle."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.widgets.toggle_switch import ToggleSwitch


def test_toggle_switch_accepts_clicks_across_its_painted_pill_and_space(
    qapp: QApplication,
) -> None:
    toggle = ToggleSwitch()
    toggled: list[bool] = []
    toggle.toggled.connect(toggled.append)
    toggle.show()
    qapp.processEvents()

    try:
        QTest.mouseClick(
            toggle,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(2, toggle.height() // 2),
        )
        assert toggle.isChecked() is True
        assert toggled == [True]

        QTest.mouseClick(
            toggle,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(toggle.width() - 2, toggle.height() // 2),
        )
        assert toggle.isChecked() is False
        assert toggled == [True, False]

        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        assert toggle.isChecked() is True
        assert toggled == [True, False, True]
    finally:
        toggle.deleteLater()
