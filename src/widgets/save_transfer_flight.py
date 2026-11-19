"""The Save Migration flight illustration, driven by confirmed transfer stages."""
from __future__ import annotations

from math import sin
from random import Random

from PyQt6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient
from PyQt6.QtWidgets import QWidget

from src.ui.motion import window_motion_enabled


class SaveTransferFlight(QWidget):
    """Animate the ship toward the latest reported stage, without estimating time."""

    def __init__(self, parent, *, accent, position):
        super().__init__(parent)
        self.setMinimumHeight(152)
        self._accent = QColor(accent)
        self._position = self._target = position
        self._phase = 0.0
        random = Random(49176)
        self._stars = [(random.random(), random.random(), random.uniform(.35, 1.2))
                       for _ in range(55)]
        self._timer = QTimer(self, interval=32)
        self._timer.timeout.connect(self._advance)
        owner = self.window().parentWidget()
        self._motion_window = owner.window() if owner is not None else None
        if self._motion_window is not None:
            self._motion_window.installEventFilter(self)

    def set_position(self, position):
        self._target = position
        if not self._timer.isActive():
            self._position = position
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_motion()

    def _sync_motion(self):
        if self.isVisible() and window_motion_enabled(self._motion_window):
            self._timer.start()
        else:
            self._timer.stop()
            self._position = self._target
            self._phase = 0.0
            self.update()

    def eventFilter(self, watched, event):
        if (watched is self._motion_window and event.type() == QEvent.Type.DynamicPropertyChange
                and bytes(event.propertyName()) == b"animationsEnabled"):
            self._sync_motion()
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _advance(self):
        self._phase += .045
        self._position += (self._target - self._position) * .16
        self.update()

    @staticmethod
    def _color(color, opacity):
        result = QColor(color)
        result.setAlpha(opacity)
        return result

    def paintEvent(self, event):
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.setPen(Qt.PenStyle.NoPen)
        w, h = self.width(), self.height()
        edge = 42.0
        flight_y = h * .70
        distance = w - 2 * edge
        destination = QPointF(w - edge, flight_y)
        backdrop = QLinearGradient(0, 0, w, h)
        backdrop.setColorAt(0, QColor("#06111f"))
        backdrop.setColorAt(.56, QColor("#10233b"))
        backdrop.setColorAt(1, QColor("#251634"))
        paint.fillRect(self.rect(), backdrop)

        for x, y, radius, color in (
            (w * .70, h * .88, h * 1.48, QColor("#456dcb")),
            (w * .88, h * .19, h * .82, QColor("#ab54d8")),
            (destination.x(), destination.y(), 57, self._accent),
        ):
            haze = QRadialGradient(x, y, radius)
            haze.setColorAt(0, self._color(color, 99))
            haze.setColorAt(1, self._color(color, 0))
            paint.fillRect(self.rect(), haze)

        for x, y, size in self._stars:
            point = QPointF(((x - self._phase * size * .010) % 1) * w, y * h)
            opacity = int(46 + 47 * size)
            paint.setPen(QPen(QColor(190, 221, 252, opacity), max(.6, size)))
            streak = size * (1.4 + abs(self._target - self._position) * 30)
            paint.drawLine(point, point + QPointF(streak, 0))

        # A second layer of long streaks opens up only while the ship is moving.
        travel = abs(self._target - self._position)
        if travel > .006:
            for index, (sx, sy, size) in enumerate(self._stars[::4]):
                start = QPointF(((sx - self._phase * (.08 + size * .06)) % 1) * w,
                                h * (.47 + sy * .50))
                length = 13 + travel * (48 + index * 5)
                paint.setPen(QPen(self._color(self._accent, int(32 + travel * 105)), .75 + size / 3))
                paint.drawLine(start, start + QPointF(length, 0))

        origin = QPointF(edge, flight_y)
        paint.setPen(QPen(self._color("#93b7ef", 91), 1, Qt.PenStyle.DashLine))
        paint.drawLine(origin, destination)
        for fraction in (.25, .50, .75):
            x = edge + distance * fraction
            paint.setPen(QPen(self._color("#a6d4ff", 105), 1))
            paint.drawLine(QPointF(x, flight_y - 4), QPointF(x, flight_y + 4))

        for center in (origin, destination):
            paint.setBrush(Qt.BrushStyle.NoBrush)
            paint.setPen(QPen(self._color(self._accent, 110), 1))
            paint.drawEllipse(center, 19, 22)
            paint.drawEllipse(center, 14, 16)
            ring = QRectF(center.x() - 24, center.y() - 27, 48, 54)
            paint.setPen(QPen(self._color(self._accent, 210), 2))
            paint.drawArc(ring, int(self._phase * 46 * 16), 63 * 16)
            paint.drawArc(ring, int((self._phase * 46 + 180) * 16), 32 * 16)

        x = edge + distance * self._position
        y = flight_y + sin(self._phase * 1.6) * 1.05
        paint.setPen(Qt.PenStyle.NoPen)
        aura = QRadialGradient(x, y, 33 + 15 * travel)
        aura.setColorAt(0, self._color(self._accent, 105))
        aura.setColorAt(.45, self._color(self._accent, 42))
        aura.setColorAt(1, self._color(self._accent, 0))
        paint.fillRect(self.rect(), aura)
        length = 94 + travel * 128
        trail = QLinearGradient(x - length, y, x, y)
        trail.setColorAt(0, self._color(self._accent, 0))
        trail.setColorAt(.78, self._color(self._accent, 142))
        trail.setColorAt(1, QColor("#f1fdff"))
        paint.fillRect(QRectF(x - length, y - 1.35, length - 3, 2.7), trail)
        paint.fillRect(QRectF(x - length, y - 4, length - 8, .8), trail)
        paint.fillRect(QRectF(x - length, y + 3.2, length - 8, .8), trail)
        paint.setBrush(QColor("#def7ff"))
        paint.setPen(QPen(self._accent, 1.25))
        paint.drawPolygon(QPolygonF([
            QPointF(x + 15, y), QPointF(x - 8, y - 7), QPointF(x - 5, y - 2),
            QPointF(x - 13, y), QPointF(x - 5, y + 2), QPointF(x - 8, y + 7),
        ]))
        divider = QLinearGradient(0, h, w, h)
        divider.setColorAt(0, self._color(self._accent, 98))
        divider.setColorAt(1, self._color(self._accent, 11))
        paint.fillRect(QRectF(0, h - 1, w, 1), divider)
        paint.end()
