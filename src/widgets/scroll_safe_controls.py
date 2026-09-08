"""Form controls that reserve the wheel for scrolling their containing page."""
from PyQt6.QtCore import QEvent, QPoint, QPointF
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QApplication, QComboBox, QDoubleSpinBox, QSlider, QSpinBox,
)


class _ScrollPageOnWheel:
    def wheelEvent(self, event: QWheelEvent) -> None:
        # Focus is deliberately irrelevant: a recently edited field must not
        # change when the user resumes scrolling. Forward to the viewport once
        # instead of relying on native event propagation through editor children.
        ancestor = self.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QAbstractScrollArea):
                viewport = ancestor.viewport()
                forwarded = QWheelEvent(
                    event.globalPosition() - QPointF(viewport.mapToGlobal(QPoint())),
                    event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                    event.buttons(), event.modifiers(), event.phase(),
                    event.inverted(), device=event.pointingDevice(),
                )
                QApplication.sendEvent(viewport, forwarded)
                # Prevent the native delivery path from scrolling a second time.
                event.accept()
                return
            ancestor = ancestor.parentWidget()
        event.ignore()


class _ScrollSafeSpinEditor(_ScrollPageOnWheel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.Wheel:
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)


class ScrollSafeSpinBox(_ScrollSafeSpinEditor, QSpinBox):
    """Number field edited by typing, buttons or keyboard, never the wheel."""


class ScrollSafeDoubleSpinBox(_ScrollSafeSpinEditor, QDoubleSpinBox):
    """Decimal field with the same page-scrolling policy as integer fields."""


class ScrollSafeSlider(_ScrollPageOnWheel, QSlider):
    """Slider edited by clicks, dragging or keyboard, never the wheel."""


class ScrollSafeComboBox(_ScrollPageOnWheel, QComboBox):
    """Dropdown whose closed selection never changes on a wheel event."""
