"""Window-scoped motion gate shared by optional decorative widgets."""
from __future__ import annotations

from collections.abc import Callable
from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QWidget
from PyQt6 import sip
from .motion import window_motion_enabled


class VisibleMotion(QObject):
    """Reconcile motion when visibility, window state or preferences change.

    The launcher publishes ``animationsEnabled`` on its window. Standalone
    widgets default to motion enabled, without a process-global preference.
    """

    def __init__(self, widget: QWidget, changed: Callable[[bool], None]):
        super().__init__(widget)
        self.widget = widget
        self.changed = changed
        self._window = None
        widget.installEventFilter(self)

    @property
    def allowed(self) -> bool:
        window = self.widget.window()
        return (self.widget.isVisible() and not window.isMinimized()
                and window_motion_enabled(self.widget))

    def sync(self):
        self.changed(self.allowed)

    def eventFilter(self, watched, event):
        # QWidget teardown can deliver Hide after SIP marks its wrapper dead.
        # Never call back into that widget from its remaining child filter.
        if sip.isdeleted(self.widget):
            return False
        if event.type() == QEvent.Type.DynamicPropertyChange:
            # Qt also changes private animation properties during destruction.
            # Only the explicit window preference belongs to this gate.
            if bytes(event.propertyName()) != b"animationsEnabled":
                return False
        if event.type() == QEvent.Type.Show:
            window = self.widget.window()
            if window is not self._window:
                if self._window is not None and self._window is not self.widget and not sip.isdeleted(self._window):
                    self._window.removeEventFilter(self)
                self._window = window
                if window is not self.widget:
                    window.installEventFilter(self)
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide,
                            QEvent.Type.WindowStateChange, QEvent.Type.DynamicPropertyChange):
            self.sync()
        return super().eventFilter(watched, event)
