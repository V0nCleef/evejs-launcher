"""Native shell-facing flags for the launcher's custom-chrome window."""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt


def launcher_startup_geometry(work_area: QRect) -> QRect:
    """Open at the roomy default size, centered inside the monitor work area.

    Qt supplies logical pixels here, so display scaling and the taskbar are
    already accounted for. Leave a little space around the window.
    """
    width = min(1389, max(1, work_area.width() - 48))
    height = min(995, max(1, work_area.height() - 48))
    geometry = QRect(0, 0, width, height)
    geometry.moveCenter(work_area.center())
    return geometry


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
