"""Page scrolling must never edit a setting, even after the field has focus."""
from copy import deepcopy

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QSpinBox

from src import config
from src.pages.settings_page import SettingsPage


def _wheel(widget, amount):
    point = widget.rect().center()
    event = QWheelEvent(
        QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(),
        QPoint(0, amount), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    # Exercise the real receiver and its containing QScrollArea. Sending a
    # synthetic event to QWindow does not perform native wheel hit testing in
    # the offscreen platform; it can silently bypass the control altogether.
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("field", [
    "runtime_backend_combo", "docker_policy_combo", "server_script_combo",
    "stagger_delay_spin", "update_interval_spin", "music_volume_slider",
    "voice_volume_slider", "ducking_level_slider",
])
@pytest.mark.parametrize("focused", [False, True])
def test_wheel_scrolls_settings_without_changing_values(qapp, monkeypatch, field, focused):
    cfg = deepcopy(config.DEFAULT_CONFIG)
    if field == "docker_policy_combo":
        cfg["runtime_backend"] = "docker_compose"
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    page = SettingsPage()
    page.resize(1000, 640)
    page.show()
    widget = getattr(page, field)
    if isinstance(widget, QComboBox) and widget.count() < 2:
        widget.addItem("Another test option", "fixture")
    page.settings_scroll.ensureWidgetVisible(widget)
    if focused:
        widget.setFocus()
    else:
        page.evejs_root_edit.setFocus()
        page.settings_scroll.ensureWidgetVisible(widget)
    qapp.processEvents()
    try:
        assert widget.hasFocus() is focused
        assert widget.isEnabled()
        before = page._form_state()
        bar = page.settings_scroll.verticalScrollBar()
        position = bar.value()
        delta = -120 if position < bar.maximum() else 120
        _wheel(widget, delta)
        qapp.processEvents()
        assert page._form_state() == before
        assert bar.value() != position, "The wheel was swallowed instead of scrolling the page"
        first_scroll = bar.value()
        _wheel(widget, -delta)
        assert page._form_state() == before
        assert bar.value() != first_scroll, "Scrolling back was swallowed"
        # A spinbox's editable child must leave scrolling alone as well.
        if isinstance(widget, QSpinBox):
            position = bar.value()
            _wheel(widget.lineEdit(), delta)
            assert page._form_state() == before
            assert bar.value() != position, "The spinbox editor swallowed the wheel"
    finally:
        page.close()
        page.deleteLater()


def test_settings_keep_deliberate_keyboard_and_mouse_edits(qapp, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: deepcopy(config.DEFAULT_CONFIG))
    page = SettingsPage()
    page.resize(1000, 640)
    page.show()
    qapp.processEvents()
    try:
        spin = page.stagger_delay_spin
        spin.setFocus()
        old = spin.value()
        QTest.keyClick(spin, Qt.Key.Key_Up)
        assert spin.value() == old + 1
        spin.lineEdit().selectAll()
        QTest.keyClicks(spin.lineEdit(), "12")
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Return)
        assert spin.value() == 12
        page.settings_scroll.ensureWidgetVisible(spin)
        QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(spin.width()-5, 5))
        assert spin.value() == 13

        slider = page.music_volume_slider
        slider.setFocus()
        old = slider.value()
        QTest.keyClick(slider, Qt.Key.Key_Right)
        assert slider.value() == old + slider.singleStep()
        page.settings_scroll.ensureWidgetVisible(slider)
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width()-4, slider.height()//2))
        assert slider.value() > old + slider.singleStep()

        combo = page.runtime_backend_combo
        combo.setFocus()
        old = combo.currentIndex()
        QTest.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() != old
    finally:
        page.close()
        page.deleteLater()
