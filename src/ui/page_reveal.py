"""Interruptible cyan sweep over the destination page."""
from PyQt6.QtCore import QEasingCurve, QObject, QPointF, QRectF, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget
from PyQt6 import sip

from src.ui.visible_motion import VisibleMotion


class _ScanLayer(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.fraction = 0.0

    def paintEvent(self, event):
        paint = QPainter(self)
        w, h, position = self.width(), self.height(), self.fraction
        front = w * position
        glow = QLinearGradient(front - 103, 0, front + 1, 0)
        glow.setColorAt(0, QColor(43, 127, 223, 0))
        glow.setColorAt(1, QColor(73, 223, 255, round(58 * (1 - position))))
        paint.fillRect(QRectF(front - 103, 0, 103, h), glow)
        paint.setPen(QPen(QColor(123, 234, 255, round(185 * (1 - position))), 1.4))
        paint.drawLine(QPointF(front, 0), QPointF(front, h))
        for y in range(28, h, 27):
            paint.drawLine(QPointF(front - 10, y), QPointF(front + 5, y))
        paint.end()


class PageReveal(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self._layer = None
        self._animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=245)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._frame)
        self._animation.finished.connect(self.finish)
        self._motion_gate = VisibleMotion(parent, lambda allowed: None if allowed else self.finish())

    def start(self, target):
        self.finish()
        if target is None or not target.isVisible() or not self._motion_gate.allowed:
            return
        self._layer = _ScanLayer(target)
        self._layer.setGeometry(target.rect())
        self._layer.raise_()
        self._layer.show()
        self._animation.start()

    def _frame(self, value):
        if self._layer is not None and not sip.isdeleted(self._layer):
            self._layer.setGeometry(self._layer.parentWidget().rect())
            self._layer.fraction = value
            self._layer.update()

    def finish(self):
        self._animation.stop()
        if self._layer is not None and not sip.isdeleted(self._layer):
            self._layer.hide()
            self._layer.deleteLater()
        self._layer = None
