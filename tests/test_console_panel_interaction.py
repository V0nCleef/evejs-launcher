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
    close_main_window,
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
    close_main_window(window)


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


def test_append_lines_bounds_large_bursts(qapp):
    panel = ConsolePanel()
    panel.begin_stream("Test")
    burst_size = panel.MAX_LINES + 50_000
    panel._append_lines([f"line-{index}" for index in range(burst_size)])
    assert panel._log.document().blockCount() <= panel.MAX_LINES
    assert f"line-{burst_size-1}" in panel._log.toPlainText()
    assert "line-0\n" not in panel._log.toPlainText()
    panel.close()


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


def test_footer_controls_switch_and_close_console_without_losing_access(
    console_window: MainWindow, qapp: QApplication, tmp_path,
) -> None:
    window = console_window
    panel = window._console_panel
    panel.stop()
    window._cfg["evejs_root"] = str(tmp_path)
    footer = window._status_bar

    QTest.mouseClick(footer.server_section.label, Qt.MouseButton.LeftButton)
    assert panel.isVisible()
    assert panel._title_label.text() == "Server"
    assert panel.height() == panel.DEFAULT_HEIGHT
    assert panel.geometry().bottom() < footer.geometry().top()
    assert footer.server_section.property("consoleOpen")

    QTest.mouseClick(footer.market_section.dot, Qt.MouseButton.LeftButton)
    assert panel.isVisible()
    assert panel._title_label.text() == "Market"
    assert footer.market_section.property("consoleOpen")
    assert not footer.server_section.property("consoleOpen")

    footer.setFixedHeight(56)
    window.resize(1000, 640)
    qapp.processEvents()
    assert panel.geometry().bottom() < footer.geometry().top()

    footer.market_section.setFocus()
    QTest.keyClick(footer.market_section, Qt.Key.Key_Space)
    assert not panel.isVisible()
    assert not footer.market_section.property("consoleOpen")

    QTest.mouseClick(footer.server_section.label, Qt.MouseButton.LeftButton)
    QTest.mouseClick(footer.server_section.label, Qt.MouseButton.LeftButton)
    assert not panel.isVisible()


@pytest.mark.parametrize("launcher_hidden", [False, True])
def test_native_console_rebinds_when_switching_installations(
    console_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch, launcher_hidden: bool,
) -> None:
    from src.core import server_launcher

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(server_launcher, "_LOGS_DIR", tmp_path / "logs")
    first_console = server_launcher.get_server_console_log(first)
    second_console = server_launcher.get_server_console_log(second)
    first_console.parent.mkdir(parents=True)
    first_console.write_text("first world\n", encoding="utf-8")

    window = console_window
    panel = window._console_panel
    window._cfg["evejs_root"] = str(first)
    assert first_console != second_console
    assert server_launcher.get_server_console_log(first / ".." / "first") == first_console

    window._on_console_toggled("server")
    assert panel._log.toPlainText().strip() == "first world"
    if launcher_hidden:
        window.hide()
    window._on_settings_saved({"evejs_root": str(second)})
    window.show()
    assert not panel.isVisible()

    window._on_console_toggled("server")
    assert panel._log.toPlainText() == ""
    assert panel._log_path == second_console
    assert not panel._notepad_btn.isEnabled()

    second_console.parent.mkdir(parents=True)
    second_console.write_text("second world\n", encoding="utf-8")
    panel._poll_log()
    assert panel._log.toPlainText().strip() == "second world"
    assert panel._notepad_btn.isEnabled()
