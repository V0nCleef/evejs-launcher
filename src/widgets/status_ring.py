"""Custom-painted Deep Signal lifecycle ring."""
from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPaintEvent, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from src.constants import MOTION_DURATIONS_MS, SEMANTIC_COLORS, STATUS_COLORS
from src.ui.motion import MotionController


# ``idle`` remains deliberately neutral, but an explicitly observed offline
# service is an actionable negative state on the Operations page.  Keep this
# presentation override local to the lifecycle instrument so the older global
# status palette can continue serving surfaces where "offline" is only idle
# telemetry.
_RING_COLORS = {
    **STATUS_COLORS,
    "offline": SEMANTIC_COLORS["danger"],
}


def _alpha(color: str, opacity: int) -> QColor:
    result = QColor(color)
    result.setAlpha(max(0, min(255, int(opacity))))
    return result


def _state_name(state: object) -> str:
    value = getattr(state, "value", state)
    name = str(value or "idle").lower().strip()
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name if name in STATUS_COLORS else "unknown"


class StatusRing(QWidget):
    """Display a truthful lifecycle state without owning runtime behavior."""

    state_changed = pyqtSignal(str)
    value_changed = pyqtSignal(str)
    _ACTIVE_STATES = {"starting", "launching", "stopping"}

    def __init__(
        self,
        label: str,
        value: str = "—",
        detail: str = "",
        state: object = "idle",
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        # Scale from the available square so this instrument can be reused in
        # compact telemetry rows as well as full dashboard tiles.
        self.setMinimumSize(56, 56)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self._label = str(label)
        self._value = str(value)
        self._detail = str(detail)
        self._state = _state_name(state)
        self._progress = 1.0
        self._phase = 0.0
        # Every ring owns its default policy. Retaining a module-global QObject
        # across QApplication teardown can leave Qt signal connections alive
        # after their receivers have been destroyed (notably in repeated GUI
        # tests and launcher restarts). Explicit controllers remain supported.
        self._motion = motion_controller or MotionController(parent=self)

        self._activity_animation = QVariantAnimation(self)
        self._activity_animation.setStartValue(0.0)
        self._activity_animation.setEndValue(1.0)
        self._activity_animation.setDuration(MOTION_DURATIONS_MS["ambient"])
        self._activity_animation.setLoopCount(-1)
        self._activity_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._activity_animation.valueChanged.connect(self._set_phase)
        self._motion.reduced_motion_changed.connect(self._on_motion_policy_changed)

        self._update_accessibility()

    @property
    def state(self) -> str:
        return self._state

    @property
    def value(self) -> str:
        return self._value

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def signal_color(self) -> str:
        """Return the semantic colour currently painted by the instrument."""
        return _RING_COLORS[self._state]

    @property
    def animations_enabled(self) -> bool:
        """Whether lifecycle activity arcs may animate while visible."""
        return self._motion.animations_enabled

    def set_animations_enabled(self, enabled: bool) -> None:
        """Apply reduced motion immediately and settle on the canonical frame.

        The controller may be shared by several rings on one page.  Publishing
        the preference through it keeps those instruments in lockstep without
        retaining a process-global QObject across QApplication lifetimes.
        """
        self._motion.set_reduced_motion(not bool(enabled))
        # ``set_reduced_motion`` emits only when the effective policy changes;
        # sync explicitly as well so this method remains a complete public seam.
        self._sync_animation()

    def set_state(
        self,
        state: object,
        *,
        value: str | int | None = None,
        detail: str | None = None,
        progress: float | None = None,
    ) -> None:
        state_name = _state_name(state)
        changed = state_name != self._state
        self._state = state_name
        if value is not None:
            self._value = str(value)
        if detail is not None:
            self._detail = str(detail)
        if progress is not None:
            self._progress = max(0.0, min(1.0, float(progress)))
        self._sync_animation()
        self._update_accessibility()
        self.update()
        if changed:
            self.state_changed.emit(self._state)

    def set_value(self, value: str | int) -> None:
        value = str(value)
        if value == self._value:
            return
        self._value = value
        self._update_accessibility()
        self.update()
        self.value_changed.emit(value)

    def set_detail(self, detail: str) -> None:
        self._detail = str(detail)
        self._update_accessibility()
        self.update()

    def set_progress(self, progress: float) -> None:
        self._progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def is_animating(self) -> bool:
        return (
            self._activity_animation.state()
            == QAbstractAnimation.State.Running
        )

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(164, 164)

    def _set_phase(self, value: object) -> None:
        self._phase = float(value)
        self.update()

    def _on_motion_policy_changed(self, _reduced: bool) -> None:
        self._sync_animation()

    def _sync_animation(self) -> None:
        should_run = (
            self._state in self._ACTIVE_STATES
            and self.isVisible()
            and self._motion.animations_enabled
        )
        if should_run:
            if not self.is_animating():
                self._activity_animation.start()
            return
        self._activity_animation.stop()
        self._phase = 0.0
        self.update()

    def _update_accessibility(self) -> None:
        self.setAccessibleName(f"{self._label} status")
        parts = [self._value, self._state]
        if self._detail:
            parts.append(self._detail)
        self.setAccessibleDescription(". ".join(parts))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = max(1.0, min(float(self.width()), float(self.height())) - 12.0)
        left = (self.width() - side) / 2.0
        top = (self.height() - side) / 2.0
        ring_rect = QRectF(left + 9.0, top + 9.0, side - 18.0, side - 18.0)
        color = self.signal_color

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_alpha(SEMANTIC_COLORS["background_raised"], 225))
        painter.drawEllipse(ring_rect.adjusted(5.0, 5.0, -5.0, -5.0))

        base_pen = QPen(_alpha(SEMANTIC_COLORS["border"], 220), 5.0)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(base_pen)
        painter.drawEllipse(ring_rect)

        glow_pen = QPen(_alpha(color, 42), 12.0)
        glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(glow_pen)
        if self._state in self._ACTIVE_STATES:
            start = int((90.0 - 360.0 * self._phase) * 16.0)
            painter.drawArc(ring_rect, start, int(104.0 * 16.0))
        else:
            painter.drawArc(ring_rect, 90 * 16, int(-360.0 * self._progress * 16.0))

        signal_pen = QPen(QColor(color), 4.0)
        signal_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(signal_pen)
        if self._state in self._ACTIVE_STATES:
            start = int((90.0 - 360.0 * self._phase) * 16.0)
            painter.drawArc(ring_rect, start, int(104.0 * 16.0))
        else:
            painter.drawArc(ring_rect, 90 * 16, int(-360.0 * self._progress * 16.0))

        value_font = QFont("Segoe UI")
        value_font.setPixelSize(max(16, int(side * 0.18)))
        value_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(value_font)
        painter.setPen(QColor(SEMANTIC_COLORS["text_primary"]))
        painter.drawText(
            QRectF(left, top + side * 0.27, side, side * 0.24),
            Qt.AlignmentFlag.AlignCenter,
            self._value,
        )

        label_font = QFont("Segoe UI")
        label_font.setPixelSize(max(9, int(side * 0.075)))
        label_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(label_font)
        painter.setPen(QColor(color))
        painter.drawText(
            QRectF(left, top + side * 0.51, side, side * 0.13),
            Qt.AlignmentFlag.AlignCenter,
            self._label.upper(),
        )

        if self._detail:
            detail_font = QFont("Segoe UI")
            detail_font.setPixelSize(max(8, int(side * 0.06)))
            painter.setFont(detail_font)
            painter.setPen(QColor(SEMANTIC_COLORS["text_muted"]))
            painter.drawText(
                QRectF(left + 10.0, top + side * 0.64, side - 20.0, side * 0.12),
                Qt.AlignmentFlag.AlignCenter,
                self._detail,
            )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._activity_animation.stop()
        self._phase = 0.0
        super().hideEvent(event)


__all__ = ["StatusRing"]
