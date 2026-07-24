"""Skeleton loading placeholder card for EveJS Launcher V2.

A 220×280 frame with grey placeholder rectangles laid out like a
character card (128×128 centered portrait, name bar, info bar, button
bar). The whole card pulses by toggling its opacity between 0.4 and 0.7
on an 800ms QTimer.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QGraphicsOpacityEffect

from src.constants import COLORS


class SkeletonCard(QFrame):
    """Pulsing placeholder used while character/portrait data loads."""

    WIDTH = 220
    HEIGHT = 280

    _OPACITY_LOW = 0.4
    _OPACITY_HIGH = 0.7
    _PULSE_INTERVAL_MS = 800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setObjectName("SkeletonCard")
        self.setStyleSheet(
            f"""
            #SkeletonCard {{
                background: {COLORS["carbon"]};
                border: 1px solid {COLORS["steel"]};
                border-radius: 8px;
            }}
            QFrame#SkeletonPlaceholder {{
                background: {COLORS["steel"]};
                border: none;
                border-radius: 4px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Portrait placeholder (128×128, centered) ─────────────────────
        self.portrait = QFrame(self)
        self.portrait.setObjectName("SkeletonPlaceholder")
        self.portrait.setFixedSize(128, 128)
        layout.addWidget(self.portrait, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ── Name bar ─────────────────────────────────────────────────────
        self.name_bar = QFrame(self)
        self.name_bar.setObjectName("SkeletonPlaceholder")
        self.name_bar.setFixedSize(160, 18)
        layout.addWidget(self.name_bar, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ── Info bar ─────────────────────────────────────────────────────
        self.info_bar = QFrame(self)
        self.info_bar.setObjectName("SkeletonPlaceholder")
        self.info_bar.setFixedSize(120, 12)
        layout.addWidget(self.info_bar, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

        # ── Button bar ───────────────────────────────────────────────────
        self.button_bar = QFrame(self)
        self.button_bar.setObjectName("SkeletonPlaceholder")
        self.button_bar.setFixedSize(180, 28)
        layout.addWidget(self.button_bar, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ── Pulsing opacity effect ───────────────────────────────────────
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(self._OPACITY_HIGH)
        self.setGraphicsEffect(self._opacity_effect)

        self._pulse_high = False  # next tick drives toward LOW
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(self._PULSE_INTERVAL_MS)
        self._pulse_timer.timeout.connect(self._pulse)
        self._pulse_timer.start()

    def _pulse(self) -> None:
        self._pulse_high = not self._pulse_high
        self._opacity_effect.setOpacity(
            self._OPACITY_HIGH if self._pulse_high else self._OPACITY_LOW
        )

    # ── Lifecycle helpers ────────────────────────────────────────────────
    def stop_pulsing(self) -> None:
        """Stop the pulse timer (e.g. when the card is being removed)."""
        self._pulse_timer.stop()

    def start_pulsing(self) -> None:
        """Resume the pulse timer if it was stopped."""
        if not self._pulse_timer.isActive():
            self._pulse_timer.start()
