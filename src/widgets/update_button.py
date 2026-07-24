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

        self._pulse: QPropertyAnimation | None = None

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
        self.setText(f"Update v{clean_version}")
        self.setToolTip(f"A new version is available: v{clean_version}")

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

        self.setText("Checking...")
        self.setToolTip("Checking for updates...")

        font = QFont("Segoe UI")
        font.setPixelSize(12)
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
                font-size:   12px;
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
        """Begin looping opacity pulse (0.85 → 1.0, 1.5 s, InOutSine)."""
        self._fx.setOpacity(1.0)

        self._pulse = QPropertyAnimation(self._fx, b"opacity", self)
        self._pulse.setDuration(1500)
        self._pulse.setStartValue(0.85)
        self._pulse.setEndValue(1.0)
        self._pulse.setLoopCount(-1)  # infinite
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.start()

    def _cancel_pulse(self) -> None:
        """Stop and discard any running pulse animation."""
        if self._pulse is not None:
            self._pulse.stop()
            self._pulse = None
