"""Frame presses must reach native resizing before child-widget handlers."""
from copy import deepcopy

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QWindow
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from src import config
from src.app import MainWindow


@pytest.fixture
def resize_window(qapp, monkeypatch):
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(evejs_root="", client_path="", update_auto_check=False,
        auto_start_server=False, auto_start_market=False,
        audio_music_enabled=False, audio_voice_enabled=False, animations_enabled=False)
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    window.setGeometry(100, 100, 1200, 800)
    window.show()
    qapp.processEvents()
    calls = []
    monkeypatch.setattr(QWindow, "startSystemResize", lambda handle, edge: calls.append(edge) or True)
    yield window, calls
    qapp.removeEventFilter(window)
    if window._cursor_override_active:
        QApplication.restoreOverrideCursor()
        window._cursor_override_active = False
    window.close()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("x,y,edge", [
    (600, 2, Qt.Edge.TopEdge),
    (2, 2, Qt.Edge.TopEdge | Qt.Edge.LeftEdge),
    (1197, 2, Qt.Edge.TopEdge | Qt.Edge.RightEdge),
    (2, 400, Qt.Edge.LeftEdge),
    (1197, 400, Qt.Edge.RightEdge),
    (600, 797, Qt.Edge.BottomEdge),
    (2, 797, Qt.Edge.BottomEdge | Qt.Edge.LeftEdge),
    (1197, 797, Qt.Edge.BottomEdge | Qt.Edge.RightEdge),
])
def test_frame_press_takes_priority_over_title_bar_and_other_children(resize_window, x, y, edge):
    window, calls = resize_window
    pos = QPoint(x, y)
    target = window.childAt(pos) or window
    local = target.mapFrom(window, pos)
    before = window.geometry()
    QTest.mousePress(target, Qt.MouseButton.LeftButton, pos=local)
    assert calls == [edge]
    assert window._title_bar._drag_pos is None
    assert window._resizing
    assert window.geometry() == before
    # Child controls can accept releases too; they must not leave a stuck resize.
    QTest.mouseRelease(target, Qt.MouseButton.LeftButton, pos=local)
    assert not window._resizing


def test_title_bar_drag_still_moves_below_resize_margin(resize_window):
    window, calls = resize_window
    title = window._title_bar
    pos = QPoint(350, 18)
    before = window.pos()
    QTest.mousePress(title, Qt.MouseButton.LeftButton, pos=pos)
    moved = pos + QPoint(30, 25)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(moved),
        QPointF(title.mapToGlobal(moved)), Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(title, event)
    QTest.mouseRelease(title, Qt.MouseButton.LeftButton, pos=moved)
    assert not calls
    assert window.pos() == before + QPoint(30, 25)
    assert title._drag_pos is None


def test_title_bar_buttons_still_work_below_resize_margin(resize_window, qapp):
    window, calls = resize_window
    QTest.mouseClick(window._title_bar.btn_max, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert window.isMaximized()
    assert window._edge_at(QPoint(2, 2)) is None
    QTest.mouseClick(window._title_bar.btn_max, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not window.isMaximized()
    assert not calls


def test_native_resize_failure_does_not_start_a_title_drag_or_stick(resize_window, monkeypatch):
    window, calls = resize_window
    monkeypatch.setattr(QWindow, "startSystemResize", lambda *_: False)
    QTest.mousePress(window._title_bar, Qt.MouseButton.LeftButton, pos=QPoint(350, 2))
    assert window._title_bar._drag_pos is None
    assert not window._resizing
    QTest.mouseRelease(window._title_bar, Qt.MouseButton.LeftButton, pos=QPoint(350, 2))


def test_dialog_and_outside_positions_do_not_resize_main_window(resize_window):
    window, calls = resize_window
    dialog = QDialog(window)
    dialog.resize(200, 100)
    dialog.show()
    QTest.mouseClick(dialog, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert not calls
    for pos in (QPoint(-1, 20), QPoint(20, -1), QPoint(window.width(), 20)):
        assert window._edge_at(pos) is None
    dialog.close()
    dialog.deleteLater()


def test_non_mouse_events_do_not_query_a_widgets_window(resize_window, monkeypatch):
    window, calls = resize_window
    def unexpected_window_query():
        raise AssertionError("A widget lifecycle event must not query its native window")
    monkeypatch.setattr(window._title_bar, "window", unexpected_window_query)
    assert not window.eventFilter(window._title_bar, QEvent(QEvent.Type.Polish))
    assert not calls


def test_hover_recovers_after_native_resize_consumes_release(resize_window):
    window, calls = resize_window
    window._resizing = True
    pos = QPoint(350, 18)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos),
        QPointF(window._title_bar.mapToGlobal(pos)), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window._title_bar, event)
    assert not window._resizing
    assert not window._cursor_override_active
    assert not calls


def test_resize_cursor_is_cleared_when_mouse_moves_to_a_dialog(resize_window):
    window, calls = resize_window
    window._update_cursor_for_edge(QPoint(350, 2))
    assert window._cursor_override_active
    dialog = QDialog(window)
    dialog.resize(200, 100)
    pos = QPoint(20, 20)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(pos),
        QPointF(dialog.mapToGlobal(pos)), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(dialog, event)
    assert not window._cursor_override_active
    assert not calls
    dialog.deleteLater()
