"""Small, interruptible hover/focus light for primary command buttons."""
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, pyqtProperty, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient
from PyQt6.QtWidgets import QPushButton
from src.ui.visible_motion import VisibleMotion


class SignalButton(QPushButton):
    def __init__(self, text, parent=None, *, accent="#00D9F5"):
        super().__init__(text, parent)
        self._accent = QColor(accent)
        self._light = 0.
        self._busy = False
        self._scan = 0.
        self._sweep = QVariantAnimation(self)
        self._sweep.setDuration(1500)
        self._sweep.setStartValue(0.)
        self._sweep.setEndValue(1.)
        self._sweep.setLoopCount(-1)
        self._sweep.valueChanged.connect(self._set_scan)
        self._animation = QPropertyAnimation(self, b"light", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion_gate = VisibleMotion(self, self._motion_changed)

    def _set_light(self, value):
        self._light = float(value)
        self.update()

    light = pyqtProperty(float, fget=lambda self: self._light, fset=_set_light)

    def _motion_changed(self, allowed):
        if not allowed:
            self._animation.stop()
            self._set_light(0.)
        if allowed and self._busy:
            if self._sweep.state() != QVariantAnimation.State.Running:
                self._sweep.start()
        else:
            self._sweep.stop()
            self.update()

    def set_busy(self, busy):
        """A continuing sweep indicates an actual pending operation, not progress."""
        self._busy = bool(busy)
        self._motion_gate.sync()

    def _set_scan(self, value):
        self._scan = float(value)
        self.update()

    def event(self, event):
        result = super().event(event)
        from PyQt6.QtCore import QEvent
        if hasattr(self, "_animation") and event.type() in (
            QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.FocusIn,
            QEvent.Type.FocusOut, QEvent.Type.EnabledChange,
        ):
            self._animation.stop()
            target = float(self.isEnabled() and (self.underMouse() or self.hasFocus()))
            if self._motion_gate.allowed:
                self._animation.setStartValue(self._light)
                self._animation.setEndValue(target)
                self._animation.start()
            else:
                self._set_light(target)
        return result

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._light <= 0. and self._sweep.state() != QVariantAnimation.State.Running:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._accent)
        color.setAlpha(int(160 * self._light))
        painter.setPen(QPen(color, 1.5))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 4, 4)
        if self._sweep.state() == QVariantAnimation.State.Running:
            x = (self.width()+100)*self._scan-100
            gradient = QLinearGradient(x, 0, x+100, 0)
            clear = QColor(self._accent)
            clear.setAlpha(0)
            gradient.setColorAt(0., clear)
            gradient.setColorAt(.5, self._accent)
            gradient.setColorAt(1., clear)
            painter.fillRect(QRectF(5, self.height()-4, self.width()-10, 2), gradient)
