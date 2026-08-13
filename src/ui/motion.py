"""Central motion policy for animated launcher widgets.

Widgets own their animations and timers, while this controller supplies one
application-wide reduced-motion decision and the canonical duration scale.
This keeps presentation concerns out of runtime/configuration modules and lets
each widget settle into a deterministic static frame immediately.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    pyqtSignal,
)

from src.constants import MOTION_DURATIONS_MS


class MotionController(QObject):
    """Publish motion preference and configure Qt animations consistently."""

    reduced_motion_changed = pyqtSignal(bool)

    def __init__(
        self,
        reduced_motion: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._reduced_motion = bool(reduced_motion)

    @property
    def reduced_motion(self) -> bool:
        return self._reduced_motion

    @property
    def animations_enabled(self) -> bool:
        return not self._reduced_motion

    def set_reduced_motion(self, reduced: bool) -> None:
        """Update the policy, emitting only when the effective value changes."""
        reduced = bool(reduced)
        if reduced == self._reduced_motion:
            return
        self._reduced_motion = reduced
        self.reduced_motion_changed.emit(reduced)

    def duration(self, token_or_ms: str | int) -> int:
        """Resolve a duration token, returning zero for reduced motion."""
        if self._reduced_motion:
            return 0
        if isinstance(token_or_ms, str):
            try:
                return MOTION_DURATIONS_MS[token_or_ms]
            except KeyError as exc:
                known = ", ".join(sorted(MOTION_DURATIONS_MS))
                raise ValueError(
                    f"Unknown motion duration {token_or_ms!r}; expected {known}"
                ) from exc
        return max(0, int(token_or_ms))

    def configure(
        self,
        animation: QAbstractAnimation,
        duration: str | int,
        easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    ) -> QAbstractAnimation:
        """Apply the shared duration/easing policy and return ``animation``."""
        resolved = self.duration(duration)
        set_duration = getattr(animation, "setDuration", None)
        if callable(set_duration):
            set_duration(resolved)
        set_easing = getattr(animation, "setEasingCurve", None)
        if callable(set_easing):
            set_easing(easing)
        return animation

    def start(self, animation: QAbstractAnimation) -> bool:
        """Start an animation, or settle its target when motion is reduced."""
        if not self._reduced_motion:
            animation.start()
            return True

        animation.stop()
        if isinstance(animation, QPropertyAnimation):
            target = animation.targetObject()
            property_name = bytes(animation.propertyName()).decode(
                "utf-8", errors="ignore"
            )
            if target is not None and property_name:
                target.setProperty(property_name, animation.endValue())
        return False


_default_motion_controller: MotionController | None = None


def default_motion_controller() -> MotionController:
    """Return a live process default, recreating it after QApplication teardown.

    PyQt test suites may destroy and recreate ``QApplication``. A module-level
    ``QObject`` can then leave a valid Python wrapper around a deleted C++
    instance, so the shared controller is created lazily and health-checked.
    """
    global _default_motion_controller
    if _default_motion_controller is not None:
        try:
            _default_motion_controller.objectName()
            return _default_motion_controller
        except RuntimeError:
            _default_motion_controller = None
    _default_motion_controller = MotionController()
    return _default_motion_controller


# Compatibility export for callers that import the original symbol directly.
# Runtime widgets should prefer an explicitly owned controller; this legacy
# object must never be parented into a QApplication or widget tree.
DEFAULT_MOTION_CONTROLLER = default_motion_controller()


def resolve_motion_controller(
    controller: MotionController | None,
) -> MotionController:
    """Return an explicit controller or the application default."""
    return controller or default_motion_controller()


__all__ = [
    "DEFAULT_MOTION_CONTROLLER",
    "default_motion_controller",
    "MotionController",
    "resolve_motion_controller",
]
