"""Animated gold-pill update button for the title bar.

Three visual states:

* HIDDEN      – no update available, button invisible
* CHECKING    – small grey spinner text with reduced opacity
* AVAILABLE   – gold pill with version, pulsing opacity animation
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QPushButton

from src.constants import COLORS
from src.ui.visible_motion import VisibleMotion
from src.widgets.ui_translation import (
    set_translatable_text,
    set_translatable_text_template,
    set_translatable_tooltip,
    set_translatable_tooltip_template,
)


class UpdateButton(QPushButton):
    """Animated gold pill button for the title bar.

    Intended to sit between the expanding spacer and the min/max/close
    window-control buttons.  Parents can connect to :attr:`clicked` to
    show an ``UpdateDialog``.
    """

    def __init__(self, parent: QPushButton | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # --- Opacity effect (driven by QPropertyAnimation for pulse) --------
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(1.0)
        self.setGraphicsEffect(self._fx)

        self._pulse = QPropertyAnimation(self._fx, b"opacity", self)
        self._pulse.setDuration(2000)
        self._pulse.setStartValue(1.)
        self._pulse.setKeyValueAt(.5, .78)
        self._pulse.setEndValue(1.)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._available = False
        self._motion_gate = VisibleMotion(self, self._sync_motion)

        # Default: hidden
        self.set_up_to_date()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_update_available(self, version: str) -> None:
        """Switch to gold-pill state showing *version* and start pulsing."""
        self._cancel_pulse()

        # Clean, readable text — no unicode glyphs that might not render
        clean_version = version.lstrip("vV")
        set_translatable_text_template(self, f"Update v{clean_version}")
        set_translatable_tooltip_template(
            self,
            f"A new version is available: v{clean_version}",
        )

        font = QFont("Segoe UI")
        font.setPixelSize(13)
        font.setBold(True)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        self.setFixedHeight(28)
        self.setMinimumWidth(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setVisible(True)

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLORS["gold"]};
                color:            {COLORS["void_black"]};
                border:           none;
                border-radius:    14px;
                padding-left:     14px;
                padding-right:    14px;
                font-weight:      bold;
            }}
            QPushButton:hover {{
                background-color: #FFCC33;
            }}
            QPushButton:pressed {{
                background-color: #E6A500;
            }}
            """
        )

        self._start_pulse()

    def set_checking(self) -> None:
        """Show a muted spinner label while checking for updates."""
        self._cancel_pulse()

        set_translatable_text(self, "Checking...")
        set_translatable_tooltip(self, "Checking for updates...")

        font = QFont("Segoe UI")
        font.setPixelSize(13)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        self.setFixedHeight(28)
        self.setMinimumWidth(0)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setVisible(True)

        # Slightly transparent via the opacity effect
        self._fx.setOpacity(0.65)

        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color:       {COLORS["grey"]};
                border:      none;
                padding-left:  10px;
                padding-right: 10px;
                font-size:   13px;
            }}
            """
        )

    def set_up_to_date(self) -> None:
        """Hide the button — no update is available."""
        self._cancel_pulse()
        self._fx.setOpacity(1.0)
        self.setVisible(False)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Pulse animation
    # ------------------------------------------------------------------

    def _start_pulse(self) -> None:
        self._available = True
        self._motion_gate.sync()

    def _sync_motion(self, allowed):
        if allowed and self._available:
            if self._pulse.state() != QPropertyAnimation.State.Running:
                self._pulse.start()
        else:
            self._pulse.stop()
            self._fx.setOpacity(1.0)

    def _cancel_pulse(self) -> None:
        self._available = False
        self._sync_motion(False)
