"""Synthetic checks for the launcher's Windows shell-facing window flags."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from src.widgets.window_behavior import launcher_window_flags


def test_launcher_window_flags_keep_custom_chrome_and_shell_controls() -> None:
    flags = launcher_window_flags()

    for required in (
        Qt.WindowType.Window,
        Qt.WindowType.FramelessWindowHint,
        Qt.WindowType.WindowSystemMenuHint,
        Qt.WindowType.WindowMinimizeButtonHint,
        Qt.WindowType.WindowMaximizeButtonHint,
        Qt.WindowType.WindowCloseButtonHint,
    ):
        assert flags & required


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shell contract")
def test_launcher_flags_map_to_native_minimize_capability_without_caption() -> None:
    probe = textwrap.dedent(
        """
        import ctypes
        import os

        os.environ["QT_QPA_PLATFORM"] = "windows"

        from PyQt6.QtWidgets import QApplication, QMainWindow
        from src.widgets.window_behavior import launcher_window_flags

        app = QApplication([])
        window = QMainWindow()
        window.setWindowFlags(launcher_window_flags())
        hwnd = int(window.winId())

        get_style = ctypes.windll.user32.GetWindowLongPtrW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        style = int(get_style(hwnd, -16))

        WS_SYSMENU = 0x00080000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        WS_CAPTION = 0x00C00000
        assert style & WS_SYSMENU
        assert style & WS_MINIMIZEBOX
        assert style & WS_MAXIMIZEBOX
        assert not style & WS_CAPTION
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
