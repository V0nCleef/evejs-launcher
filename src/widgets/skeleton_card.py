"""Skeleton loading placeholder card for EveJS Launcher V2.

A 220×280 frame with grey placeholder rectangles laid out like a
character card (128×128 centered portrait, name bar, info bar, button
bar). Visible cards gently breathe between two opacity levels. Hidden and
reduced-motion placeholders retain a static frame.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QGraphicsOpacityEffect

from src.constants import COLORS
from src.ui.visible_motion import VisibleMotion


class SkeletonCard(QFrame):
    """Pulsing placeholder used while character/portrait data loads."""

    WIDTH = 220
    HEIGHT = 280

    _OPACITY_LOW = 0.4
    _OPACITY_HIGH = 0.7

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

        self._pulsing_enabled = True
        self._pulse = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._pulse.setDuration(1600)
        self._pulse.setStartValue(self._OPACITY_HIGH)
        self._pulse.setKeyValueAt(.5, self._OPACITY_LOW)
        self._pulse.setEndValue(self._OPACITY_HIGH)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._motion_gate = VisibleMotion(self, self._sync_motion)

    def _sync_motion(self, allowed):
        if allowed and self._pulsing_enabled:
            if self._pulse.state() != QPropertyAnimation.State.Running:
                self._pulse.start()
        else:
            self._pulse.stop()
            self._opacity_effect.setOpacity(self._OPACITY_HIGH)

    def stop_pulsing(self) -> None:
        self._pulsing_enabled = False
        self._sync_motion(False)

    def start_pulsing(self) -> None:
        self._pulsing_enabled = True
        self._motion_gate.sync()
