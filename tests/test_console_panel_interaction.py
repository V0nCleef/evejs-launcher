"""Regression tests for console-overlay dismissal and header resizing."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton

from src import app as app_module
from src import config
from src.app import MainWindow


def _window_config() -> dict:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "",
            "client_path": "",
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    return cfg


@pytest.fixture
def console_window(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> MainWindow:
    cfg = _window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    monkeypatch.setattr(window, "_schedule_service_monitor_start", lambda: None)
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()

    window._console_panel.show()
    window._console_panel.raise_()
    qapp.processEvents()

    yield window

    window._console_panel.stop()
    window.close()
    qapp.processEvents()
    window.deleteLater()


def _outside_button(window: MainWindow) -> QPushButton:
    """Return an accepting child event target with no production side effects."""
    button = QPushButton("Outside test target", window._home_page)
    button.setGeometry(8, 8, 140, 28)
    button.show()
    return button


def test_console_dismisses_when_clicking_another_main_window_control(
    console_window: MainWindow,
    qapp: QApplication,
) -> None:
    panel = console_window._console_panel
    target = _outside_button(console_window)

    assert panel.isVisible()

    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    qapp.processEvents()

    assert panel.isHidden()


def test_console_header_resize_remains_active_when_an_outside_press_arrives(
    console_window: MainWindow,
    qapp: QApplication,
) -> None:
    panel = console_window._console_panel
    target = _outside_button(console_window)

    QTest.mousePress(panel._header, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    qapp.processEvents()

    assert panel._resizing is True
    assert panel.isVisible()

    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    qapp.processEvents()

    assert panel.isVisible()
    assert panel._resizing is True

    QTest.mouseRelease(panel._header, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    qapp.processEvents()

    assert panel._resizing is False
