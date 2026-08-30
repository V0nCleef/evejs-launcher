"""Native shell-facing flags for the launcher's custom-chrome window."""

from __future__ import annotations

from PyQt6.QtCore import Qt


def launcher_window_flags() -> Qt.WindowType:
    """Keep custom chrome while exposing normal Windows shell controls.

    ``FramelessWindowHint`` alone creates a popup-style native window.  The
    explicit system-menu and caption-button hints preserve the launcher's
    borderless title bar while giving the Windows taskbar the minimize/restore
    capabilities it expects from an ordinary top-level application window.
    """
    return (
        Qt.WindowType.Window
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowMinimizeButtonHint
        | Qt.WindowType.WindowMaximizeButtonHint
        | Qt.WindowType.WindowCloseButtonHint
    )
