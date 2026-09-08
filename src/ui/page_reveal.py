"""Interruptible page entrance; the Operations artwork stays untouched."""
from PyQt6.QtCore import QObject, QVariantAnimation, QEasingCurve
from PyQt6.QtWidgets import QGraphicsOpacityEffect
from src.ui.visible_motion import VisibleMotion


class PageReveal(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self._target = None
        self._effect = None
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(220)
        self._animation.setStartValue(.55)
        self._animation.setEndValue(1.)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._frame)
        self._animation.finished.connect(self.finish)
        self._motion_gate = VisibleMotion(parent, lambda allowed: None if allowed else self.finish())

    def start(self, target):
        self.finish()
        if target is None or target.graphicsEffect() is not None:
            return
        window = target.window()
        if not target.isVisible() or window.isMinimized() or window.property("animationsEnabled") is False:
            return
        self._target = target
        self._effect = QGraphicsOpacityEffect(target)
        self._effect.setOpacity(.55)
        target.setGraphicsEffect(self._effect)
        self._animation.start()

    def _frame(self, value):
        if self._effect is not None:
            self._effect.setOpacity(float(value))

    def finish(self):
        self._animation.stop()
        if self._target is not None:
            self._target.setGraphicsEffect(None)
        self._target = self._effect = None
