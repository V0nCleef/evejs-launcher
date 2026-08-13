"""Sparse, deterministic docking traffic for the Deep Signal scene.

The approved orbital artwork remains a cached, permanently static raster.  This
widget is a separate transparent layer that paints only a few tiny navigation
lights, short warp-arrival streaks, and one rare dark capital silhouette.

Animation is intentionally modest: one coarse timer samples a monotonic elapsed
clock at 12.5 FPS, and the timer is stopped while the page is hidden, covered by
another page, minimized, reduced-motion, or too narrow to expose the station.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import random

from PyQt6.QtCore import (
    QElapsedTimer,
    QEvent,
    QPointF,
    QRectF,
    QTimer,
    Qt,
)
from PyQt6.QtGui import (
    QColor,
    QHideEvent,
    QLinearGradient,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWidgets import QWidget

from src.ui.motion import MotionController


@dataclass(frozen=True, slots=True)
class _TrafficRoute:
    """One normalized, cyclic approach route."""

    kind: str
    starts_at_s: float
    duration_s: float
    start: tuple[float, float]
    control_a: tuple[float, float]
    control_b: tuple[float, float]
    dock: tuple[float, float]
    scale: float
    tone: str


@dataclass(frozen=True, slots=True)
class TrafficSample:
    """A deterministic normalized traffic sample exposed for focused tests."""

    kind: str
    x: float
    y: float
    dx: float
    dy: float
    progress: float
    opacity: float
    warp_alpha: float
    scale: float
    tone: str


def _cubic(
    start: tuple[float, float],
    control_a: tuple[float, float],
    control_b: tuple[float, float],
    end: tuple[float, float],
    progress: float,
) -> tuple[float, float, float, float]:
    """Return cubic position and derivative without allocating a painter path."""
    t = min(1.0, max(0.0, float(progress)))
    inverse = 1.0 - t
    x = (
        inverse**3 * start[0]
        + 3.0 * inverse * inverse * t * control_a[0]
        + 3.0 * inverse * t * t * control_b[0]
        + t**3 * end[0]
    )
    y = (
        inverse**3 * start[1]
        + 3.0 * inverse * inverse * t * control_a[1]
        + 3.0 * inverse * t * t * control_b[1]
        + t**3 * end[1]
    )
    dx = 3.0 * (
        inverse * inverse * (control_a[0] - start[0])
        + 2.0 * inverse * t * (control_b[0] - control_a[0])
        + t * t * (end[0] - control_b[0])
    )
    dy = 3.0 * (
        inverse * inverse * (control_a[1] - start[1])
        + 2.0 * inverse * t * (control_b[1] - control_a[1])
        + t * t * (end[1] - control_b[1])
    )
    return x, y, dx, dy


class DockingTrafficOverlay(QWidget):
    """Low-cost station traffic painted above a static scene and below controls."""

    TICK_INTERVAL_MS = 80
    TRAFFIC_CYCLE_SECONDS = 96.0
    MIN_EXPOSED_WIDTH = 150
    _EDGE_INSET = 14
    _LIGHT_STARTS = (1.2, 12.0, 23.5, 35.0, 47.5, 60.0, 72.0, 84.5)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
        motion_enabled: bool = True,
        seed: int = 31_407,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("deepSignalDockingTraffic")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._motion_controller = motion_controller
        self._motion_enabled = bool(motion_enabled)
        if motion_controller is not None:
            self._motion_enabled = (
                self._motion_enabled and motion_controller.animations_enabled
            )
            motion_controller.reduced_motion_changed.connect(
                self._on_reduced_motion_changed
            )

        self._seed = int(seed)
        self._routes = self._build_routes(self._seed)
        self._reserved_left_px = 0
        self._scene_time_ms = 0
        self._clock = QElapsedTimer()
        self._watched_window: QWidget | None = None

        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._tick_timer.setInterval(self.TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self._on_tick)

    @staticmethod
    def _build_routes(seed: int) -> tuple[_TrafficRoute, ...]:
        rng = random.Random(seed)
        routes: list[_TrafficRoute] = []
        tones = ("cyan", "warm", "cyan", "cool")
        for index, starts_at in enumerate(DockingTrafficOverlay._LIGHT_STARTS):
            # All ships converge on the station's right-hand approach beacon.
            # Sources alternate between the upper-right and deep right edge so
            # the scene feels inhabited without becoming a screensaver.
            if index % 3 == 0:
                start = (rng.uniform(0.80, 0.91), rng.uniform(0.04, 0.18))
            elif index % 3 == 1:
                start = (rng.uniform(1.01, 1.08), rng.uniform(0.22, 0.46))
            else:
                start = (rng.uniform(0.85, 0.99), rng.uniform(0.13, 0.34))
            dock = (rng.uniform(0.902, 0.928), rng.uniform(0.565, 0.615))
            control_a = (
                start[0] + (dock[0] - start[0]) * rng.uniform(0.24, 0.34),
                start[1] + (dock[1] - start[1]) * rng.uniform(0.12, 0.26),
            )
            control_b = (
                start[0] + (dock[0] - start[0]) * rng.uniform(0.62, 0.76),
                start[1] + (dock[1] - start[1]) * rng.uniform(0.68, 0.84),
            )
            routes.append(
                _TrafficRoute(
                    kind="light",
                    starts_at_s=starts_at,
                    duration_s=rng.uniform(7.0, 8.6),
                    start=start,
                    control_a=control_a,
                    control_b=control_b,
                    dock=dock,
                    scale=rng.uniform(0.82, 1.14),
                    tone=tones[index % len(tones)],
                )
            )

        # A single large, nearly black hull crosses the approach once per
        # 96-second cycle.  It appears early on the first visit, then remains
        # genuinely rare instead of looping conspicuously.
        routes.append(
            _TrafficRoute(
                kind="silhouette",
                starts_at_s=8.6,
                duration_s=14.2,
                start=(1.055, 0.285),
                control_a=(1.018, 0.325),
                control_b=(0.962, 0.492),
                dock=(0.923, 0.594),
                scale=1.0,
                tone="dark",
            )
        )
        return tuple(routes)

    @property
    def motion_enabled(self) -> bool:
        return self._motion_enabled

    @property
    def reserved_left_px(self) -> int:
        return self._reserved_left_px

    @property
    def scene_time_ms(self) -> int:
        return self._current_scene_time_ms()

    @property
    def timer_interval_ms(self) -> int:
        return self._tick_timer.interval()

    def is_animating(self) -> bool:
        """Return whether the single paint timer is currently consuming work."""
        return self._tick_timer.isActive()

    def set_reserved_left_px(self, pixels: int) -> None:
        """Reserve the command column; traffic never paints left of this edge."""
        pixels = max(0, int(pixels))
        if pixels == self._reserved_left_px:
            return
        self._reserved_left_px = pixels
        self._sync_timer()
        self.update()

    def set_motion_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._motion_enabled:
            return
        self._motion_enabled = enabled
        self._sync_timer()
        if not enabled:
            self.update()

    def sample_frame(self, elapsed_ms: int | None = None) -> tuple[TrafficSample, ...]:
        """Sample the cyclic traffic model at an exact elapsed time.

        Passing a time makes visual behavior deterministic in tests without
        altering the live elapsed clock or relying on timer delivery timing.
        """
        if elapsed_ms is None:
            elapsed_ms = self._current_scene_time_ms()
        elapsed_s = max(0.0, float(elapsed_ms) / 1000.0)
        samples: list[TrafficSample] = []
        for route in self._routes:
            age_s = (elapsed_s - route.starts_at_s) % self.TRAFFIC_CYCLE_SECONDS
            if age_s > route.duration_s:
                continue
            progress = age_s / route.duration_s
            # Smoothstep keeps tiny ships from visibly snapping as they arrive.
            travel = progress * progress * (3.0 - 2.0 * progress)
            x, y, dx, dy = _cubic(
                route.start,
                route.control_a,
                route.control_b,
                route.dock,
                travel,
            )
            fade_in = min(1.0, age_s / 0.32)
            fade_out = min(1.0, max(0.0, (route.duration_s - age_s) / 1.15))
            opacity = fade_in * fade_out
            warp_alpha = 0.0
            if route.kind == "light" and age_s <= 0.48:
                warp_alpha = max(0.0, 1.0 - age_s / 0.48)
            samples.append(
                TrafficSample(
                    kind=route.kind,
                    x=x,
                    y=y,
                    dx=dx,
                    dy=dy,
                    progress=progress,
                    opacity=opacity,
                    warp_alpha=warp_alpha,
                    scale=route.scale,
                    tone=route.tone,
                )
            )
        return tuple(samples)

    def traffic_rect(self) -> QRectF:
        """Return the station-side clip rectangle used for all live painting."""
        left = max(float(self._reserved_left_px), self.width() * 0.58)
        top = float(self._EDGE_INSET)
        right = max(left, float(self.width() - self._EDGE_INSET))
        bottom = max(top, float(self.height() - self._EDGE_INSET))
        return QRectF(left, top, right - left, bottom - top)

    def _has_drawable_area(self) -> bool:
        area = self.traffic_rect()
        return area.width() >= self.MIN_EXPOSED_WIDTH and area.height() >= 180.0

    def _current_scene_time_ms(self) -> int:
        if self._clock.isValid():
            return self._scene_time_ms + max(0, self._clock.elapsed())
        return self._scene_time_ms

    def _pause_clock(self) -> None:
        if self._clock.isValid():
            self._scene_time_ms += max(0, self._clock.elapsed())
            self._clock.invalidate()
        self._tick_timer.stop()

    def _resume_clock(self) -> None:
        if not self._clock.isValid():
            self._clock.start()
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def _window_can_animate(self) -> bool:
        window = self.window()
        if window is None or not window.isVisible():
            return False
        return not bool(window.windowState() & Qt.WindowState.WindowMinimized)

    def _should_animate(self) -> bool:
        return (
            self._motion_enabled
            and self.isVisible()
            and self._window_can_animate()
            and self._has_drawable_area()
        )

    def _sync_timer(self) -> None:
        if self._should_animate():
            self._resume_clock()
        else:
            self._pause_clock()

    def _on_tick(self) -> None:
        # Visibility can change between queued events; fail closed so a covered
        # or minimized launcher never retains a paint loop.
        if not self._should_animate():
            self._pause_clock()
            return
        self.update(self.traffic_rect().toAlignedRect())

    def _on_reduced_motion_changed(self, reduced: bool) -> None:
        self.set_motion_enabled(not bool(reduced))

    def _bind_window_events(self) -> None:
        window = self.window()
        if window is self._watched_window:
            return
        if self._watched_window is not None:
            try:
                self._watched_window.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched_window = window
        if window is not None and window is not self:
            window.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._watched_window and event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.Close,
            QEvent.Type.WindowStateChange,
        }:
            # Window-state flags are authoritative by the time the filter runs.
            self._sync_timer()
        return super().eventFilter(watched, event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._bind_window_events()
        self._sync_timer()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._pause_clock()
        super().hideEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_timer()

    @staticmethod
    def _unit_direction(sample: TrafficSample, width: float, height: float) -> tuple[float, float]:
        dx = sample.dx * width
        dy = sample.dy * height
        length = math.hypot(dx, dy)
        if length <= 0.001:
            return 1.0, 0.0
        return dx / length, dy / length

    @staticmethod
    def _tone_color(tone: str, alpha: int) -> QColor:
        colors = {
            "cyan": (42, 221, 245),
            "warm": (255, 194, 102),
            "cool": (148, 190, 255),
        }
        red, green, blue = colors.get(tone, colors["cyan"])
        return QColor(red, green, blue, max(0, min(255, int(alpha))))

    def _paint_light(self, painter: QPainter, sample: TrafficSample) -> None:
        width = float(self.width())
        height = float(self.height())
        position = QPointF(sample.x * width, sample.y * height)
        direction_x, direction_y = self._unit_direction(sample, width, height)
        perpendicular_x, perpendicular_y = -direction_y, direction_x
        alpha = max(0, min(255, int(255 * sample.opacity)))

        if sample.warp_alpha > 0.0:
            streak_length = (48.0 + 44.0 * sample.scale) * sample.warp_alpha
            tail = QPointF(
                position.x() - direction_x * streak_length,
                position.y() - direction_y * streak_length,
            )
            streak = QLinearGradient(tail, position)
            streak.setColorAt(0.0, self._tone_color(sample.tone, 0))
            streak.setColorAt(
                0.72,
                self._tone_color(sample.tone, int(70 * sample.warp_alpha)),
            )
            streak.setColorAt(
                1.0,
                self._tone_color(sample.tone, int(225 * sample.warp_alpha)),
            )
            pen = QPen(streak, 1.4 + sample.scale)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(tail, position)

        trail_length = 7.0 + 7.0 * sample.scale
        tail = QPointF(
            position.x() - direction_x * trail_length,
            position.y() - direction_y * trail_length,
        )
        trail_pen = QPen(self._tone_color(sample.tone, int(alpha * 0.26)), 1.0)
        trail_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(trail_pen)
        painter.drawLine(tail, position)

        glow_radius = 3.4 + 2.8 * sample.scale
        glow = QRadialGradient(position, glow_radius)
        glow.setColorAt(0.0, self._tone_color(sample.tone, int(alpha * 0.82)))
        glow.setColorAt(0.35, self._tone_color(sample.tone, int(alpha * 0.34)))
        glow.setColorAt(1.0, self._tone_color(sample.tone, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(position, glow_radius, glow_radius)

        core_radius = 0.75 + sample.scale * 0.38
        painter.setBrush(self._tone_color(sample.tone, alpha))
        painter.drawEllipse(position, core_radius, core_radius)

        # A second warm pin-light makes the speck read as a vessel rather than
        # another background star, even when it occupies only three pixels.
        nav = QPointF(
            position.x() + perpendicular_x * (1.3 + sample.scale),
            position.y() + perpendicular_y * (1.3 + sample.scale),
        )
        painter.setBrush(QColor(255, 156, 86, int(alpha * 0.86)))
        painter.drawEllipse(nav, 0.55, 0.55)

    def _paint_silhouette(self, painter: QPainter, sample: TrafficSample) -> None:
        width = float(self.width())
        height = float(self.height())
        position = QPointF(sample.x * width, sample.y * height)
        direction_x, direction_y = self._unit_direction(sample, width, height)
        perpendicular_x, perpendicular_y = -direction_y, direction_x
        growth = 0.45 + sample.progress * 0.85
        hull_length = (15.0 + 11.0 * sample.scale) * growth
        hull_width = (5.0 + 5.5 * sample.scale) * growth

        tip = QPointF(
            position.x() + direction_x * hull_length * 0.52,
            position.y() + direction_y * hull_length * 0.52,
        )
        rear_center = QPointF(
            position.x() - direction_x * hull_length * 0.48,
            position.y() - direction_y * hull_length * 0.48,
        )
        shoulder_a = QPointF(
            position.x() + perpendicular_x * hull_width,
            position.y() + perpendicular_y * hull_width,
        )
        shoulder_b = QPointF(
            position.x() - perpendicular_x * hull_width,
            position.y() - perpendicular_y * hull_width,
        )
        rear_a = QPointF(
            rear_center.x() + perpendicular_x * hull_width * 0.42,
            rear_center.y() + perpendicular_y * hull_width * 0.42,
        )
        rear_b = QPointF(
            rear_center.x() - perpendicular_x * hull_width * 0.42,
            rear_center.y() - perpendicular_y * hull_width * 0.42,
        )
        path = QPainterPath(tip)
        path.lineTo(shoulder_a)
        path.lineTo(rear_a)
        path.lineTo(rear_center)
        path.lineTo(rear_b)
        path.lineTo(shoulder_b)
        path.closeSubpath()

        alpha = max(0, min(255, int(255 * sample.opacity)))
        rim = QPen(QColor(72, 121, 137, int(alpha * 0.42)), 0.8)
        painter.setPen(rim)
        painter.setBrush(QColor(0, 3, 7, int(alpha * 0.90)))
        painter.drawPath(path)

        engine = QPointF(
            rear_center.x() - direction_x * 1.1,
            rear_center.y() - direction_y * 1.1,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(40, 195, 224, int(alpha * 0.58)))
        painter.drawEllipse(engine, 0.75, 0.75)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if not self._motion_enabled or not self._has_drawable_area():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(self.traffic_rect())
        for sample in self.sample_frame():
            if sample.kind == "silhouette":
                self._paint_silhouette(painter, sample)
            else:
                self._paint_light(painter, sample)


__all__ = ["DockingTrafficOverlay", "TrafficSample"]
