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
from src.widgets.console_panel import ConsolePanel


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


def test_stream_mode_disables_notepad_preserves_ring_buffer_and_emits_closed(
    qapp: QApplication,
) -> None:
    panel = ConsolePanel()
    closed: list[bool] = []
    panel.closed.connect(lambda: closed.append(True))

    panel.begin_stream("Docker Compose — Server logs")
    panel.append_stream_line("first")
    panel._append_lines([str(index) for index in range(panel.MAX_LINES + 10)])

    assert not panel._notepad_btn.isEnabled()
    assert "first" not in panel._log.toPlainText()
    assert panel._log.document().maximumBlockCount() == panel.MAX_LINES
    assert panel._log.document().blockCount() <= panel.MAX_LINES
    panel.stop()
    assert closed == [True]


def test_console_deep_signal_header_reports_stream_completion(
    qapp: QApplication,
) -> None:
    panel = ConsolePanel()

    panel.begin_stream("Fixture service logs")

    assert "LIVE" in panel._activity_label.text()
    assert panel._copy_btn.accessibleName() == "Copy visible console output"
    assert panel._header.height() == panel.HEADER_HEIGHT

    panel.finish_stream("Fixture stream complete")

    assert "COMPLETE" in panel._activity_label.text()
    assert "Fixture stream complete" in panel._log.toPlainText()
    panel.stop()


def test_stream_mode_notepad_is_disabled_and_never_calls_platform_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = ConsolePanel()
    opened: list[object] = []
    monkeypatch.setattr("src.core.platform.open_text_editor", opened.append)

    panel.begin_stream("Docker Compose — Server logs")
    panel._open_in_notepad()

    assert not panel._notepad_btn.isEnabled()
    assert opened == []


def test_tail_after_stream_restores_native_file_state_and_notepad(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("native line\n", encoding="utf-8")
    panel = ConsolePanel()
    opened: list[object] = []
    monkeypatch.setattr("src.core.platform.open_text_editor", opened.append)

    panel.begin_stream("Docker Compose — Server logs")
    panel.tail(log_path)
    panel._open_in_notepad()

    assert panel._streaming is False
    assert panel._notepad_btn.isEnabled()
    assert panel._log_path == log_path
    assert panel._log_offset == log_path.stat().st_size
    assert panel._poll_timer.isActive()
    assert opened == [log_path]
    panel.stop()


def test_copy_after_bounded_stream_writes_visible_ring_buffer_to_clipboard(qapp: QApplication) -> None:
    panel = ConsolePanel()
    panel.begin_stream("Docker Compose — Server logs")
    panel._append_lines([f"line-{index}" for index in range(panel.MAX_LINES + 10)])

    panel._copy_to_clipboard()

    assert QApplication.clipboard().text() == panel._log.toPlainText()
    assert "line-0" not in QApplication.clipboard().text()
    assert "line-2009" in QApplication.clipboard().text()


def test_append_lines_bounds_large_burst_before_one_qt_insert(
    qapp: QApplication,
) -> None:
    panel = ConsolePanel()
    inserted: list[str] = []
    cursor_moves: list[object] = []
    ensure_visible: list[bool] = []

    class _Cursor:
        def movePosition(self, operation) -> None:  # noqa: N802
            cursor_moves.append(operation)

        def insertText(self, text: str) -> None:  # noqa: N802
            inserted.append(text)

    class _Document:
        @staticmethod
        def blockCount() -> int:  # noqa: N802
            return 1

    class _Log:
        @staticmethod
        def textCursor() -> _Cursor:  # noqa: N802
            return _Cursor()

        @staticmethod
        def document() -> _Document:
            return _Document()

        @staticmethod
        def ensureCursorVisible() -> None:  # noqa: N802
            ensure_visible.append(True)

    panel._log = _Log()
    burst_size = panel.MAX_LINES + 50_000

    panel._append_lines([f"line-{index}" for index in range(burst_size)])

    assert len(cursor_moves) == 1
    assert len(inserted) == 1
    assert inserted[0].splitlines() == [
        f"line-{index}"
        for index in range(burst_size - panel.MAX_LINES, burst_size)
    ]
    assert ensure_visible == [True]


def test_poll_log_caps_large_unread_burst_discards_partial_line_and_advances(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    log_path = tmp_path / "server_console.log"
    existing = b"already-consumed\n"
    oversized_by = 123
    cut_line = b"x" * 500 + b"\n"
    latest = b"latest-one\nlatest-two\n"
    middle_size = (
        ConsolePanel.MAX_POLL_BYTES
        + oversized_by
        - len(cut_line)
        - len(latest)
        - 1  # newline following the middle line
    )
    burst = cut_line + (b"y" * middle_size) + b"\n" + latest
    log_path.write_bytes(existing + burst)
    panel = ConsolePanel()
    panel._log_path = log_path
    panel._log_offset = len(existing)
    appended: list[list[str]] = []
    monkeypatch.setattr(panel, "_append_lines", appended.append)

    panel._poll_log()

    assert panel._log_offset == log_path.stat().st_size
    assert len(appended) == 1
    assert appended[0][-2:] == ["latest-one", "latest-two"]
    assert not any(line.startswith("x") for line in appended[0])
    assert len("\n".join(appended[0]).encode("utf-8")) < panel.MAX_POLL_BYTES


def test_poll_log_preserves_small_delta_and_utf8_replacement(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    log_path = tmp_path / "market_console.log"
    existing = b"already-consumed\n"
    delta = "normal\nsnowman: \u2603\n".encode("utf-8") + b"invalid: \xff\n"
    log_path.write_bytes(existing + delta)
    panel = ConsolePanel()
    panel._log_path = log_path
    panel._log_offset = len(existing)
    appended: list[list[str]] = []
    monkeypatch.setattr(panel, "_append_lines", appended.append)

    panel._poll_log()

    assert appended == [["normal", "snowman: \u2603", "invalid: \ufffd"]]
    assert panel._log_offset == log_path.stat().st_size
