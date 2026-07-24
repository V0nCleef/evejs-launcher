"""iOS-style animated toggle switch for EveJS Launcher V2.

A QCheckBox subclass that paints itself as a 40×20 pill. The thumb
slides between its two rest positions with a 150ms QPropertyAnimation
driven by a custom ``_thumb_pos`` property. The track is teal when
checked, steel when unchecked.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    Qt,
    QRectF,
    QPropertyAnimation,
    QEasingCurve,
    pyqtProperty,
)
from PyQt6.QtGui import QPainter, QColor, QPaintEvent
from PyQt6.QtWidgets import QCheckBox

from src.constants import COLORS


class ToggleSwitch(QCheckBox):
    """Animated iOS-style pill toggle (40×20 logical size)."""

    WIDTH = 40
    HEIGHT = 20
    _MARGIN = 2  # padding between track edge and thumb

    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Hide the default indicator/text — we paint everything ourselves.
        self.setText("")
        self.setStyleSheet("QCheckBox { spacing: 0px; }")

        # Storage for the animated thumb position (0.0 → off, 1.0 → on).
        self._thumb_pos_value: float = 1.0 if checked else 0.0
        self.setChecked(checked)

        self._anim = QPropertyAnimation(self, b"_thumb_pos", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.toggled.connect(self._on_toggled)

    # ── Animated thumb position property ─────────────────────────────────
    def _get_thumb_pos(self) -> float:
        return self._thumb_pos_value

    def _set_thumb_pos(self, value: float) -> None:
        self._thumb_pos_value = float(value)
        self.update()

    _thumb_pos = pyqtProperty(float, fget=_get_thumb_pos, fset=_set_thumb_pos)

    # ── Slots ────────────────────────────────────────────────────────────
    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._get_thumb_pos())
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    # ── Painting ─────────────────────────────────────────────────────────
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_rect = QRectF(0, 0, self.WIDTH, self.HEIGHT)

        # Track
        track_color = QColor(COLORS["teal"] if self.isChecked() else COLORS["steel"])
        if not self.isEnabled():
            track_color = track_color.darker(140)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track_rect, self.HEIGHT / 2, self.HEIGHT / 2)

        # Thumb — interpolated along the track by _thumb_pos in [0, 1].
        thumb_d = self.HEIGHT - 2 * self._MARGIN
        x_off = self._MARGIN + self._get_thumb_pos() * (
            self.WIDTH - thumb_d - 2 * self._MARGIN
        )
        thumb_rect = QRectF(x_off, self._MARGIN, thumb_d, thumb_d)
        thumb_color = QColor(COLORS["white"])
        if not self.isEnabled():
            thumb_color = thumb_color.darker(120)
        painter.setBrush(thumb_color)
        painter.drawEllipse(thumb_rect)

        painter.end()

    def sizeHint(self):  # noqa: N802
        from PyQt6.QtCore import QSize
        return QSize(self.WIDTH, self.HEIGHT)
