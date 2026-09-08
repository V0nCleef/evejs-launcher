"""Station traffic and local lighting above a permanently fixed orbital scene."""
from __future__ import annotations
import math
from PyQt6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, QTimer, Qt
from PyQt6.QtGui import (QColor, QLinearGradient, QPaintEvent, QPainter,
                        QPainterPath, QPen, QRadialGradient, QRegion)
from PyQt6.QtWidgets import QWidget
from src.ui.motion import MotionController
from src.ui.scene_geometry import SceneGeometry
from src.ui.scene_timeline import SceneTimeline, TrafficSample
from src.widgets.deep_signal_background import operations_scene_path
from src.widgets.station_lights import StationLights


class DockingTrafficOverlay(QWidget):
    """One elapsed-time scene clock; controls never receive its mouse events."""

    TICK_INTERVAL_MS = 40
    TRAFFIC_CYCLE_SECONDS = SceneTimeline.CYCLE_SECONDS

    def __init__(self, parent=None, *, motion_controller: MotionController | None = None,
                 motion_enabled: bool = True, seed: int = 31407):
        super().__init__(parent)
        self.setObjectName("deepSignalDockingTraffic")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._motion_controller = motion_controller
        self._motion_enabled = bool(motion_enabled)
        if motion_controller is not None:
            self._motion_enabled &= motion_controller.animations_enabled
            motion_controller.reduced_motion_changed.connect(self._on_reduced_motion_changed)
        self._timeline = SceneTimeline(seed)
        self._lights = StationLights(operations_scene_path())
        self._background = None
        self._reserved_left_px = 0
        self._scene_time_ms = 0
        self._clock = QElapsedTimer()
        self._watched_window = None
        self._last_damage = QRegion()
        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._tick_timer.setInterval(self.TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self._on_tick)

    def set_background(self, background):
        self._background = background

    @property
    def scene_geometry(self):
        if self._background is not None:
            return self._background.scene_geometry
        width, height = self._timeline.definition["reference_size"]
        return SceneGeometry(width, height, self.width(), self.height())

    @property
    def motion_enabled(self):
        return self._motion_enabled

    @property
    def reserved_left_px(self):
        return self._reserved_left_px

    @property
    def scene_time_ms(self):
        return self._current_scene_time_ms()

    @property
    def timer_interval_ms(self):
        return self._tick_timer.interval()

    def is_animating(self):
        return self._tick_timer.isActive()

    def set_reserved_left_px(self, pixels):
        # Retain the command boundary for diagnostics; translucent controls
        # compose naturally over the scene instead of clipping ships away.
        self._reserved_left_px = max(0, int(pixels))

    def set_motion_enabled(self, enabled):
        self._motion_enabled = bool(enabled)
        self._sync_timer()
        self.update()

    def sample_frame(self, elapsed_ms=None):
        return self._timeline.sample(self._current_scene_time_ms() if elapsed_ms is None else elapsed_ms)

    def traffic_rect(self):
        return QRectF(self.rect()).adjusted(8., 8., -8., -8.)

    def _has_drawable_area(self):
        if self._background is not None:
            size = self._background.scene_source_size
            if not self._background.scene_available or [size.width(), size.height()] != self._timeline.definition["reference_size"]:
                return False
        return bool(self._timeline.definition["routes"]) and self.width() >= 320 and self.height() >= 180

    def _current_scene_time_ms(self):
        return self._scene_time_ms + (max(0, self._clock.elapsed()) if self._clock.isValid() else 0)

    def _pause_clock(self):
        if self._clock.isValid():
            self._scene_time_ms += max(0, self._clock.elapsed())
            self._clock.invalidate()
        self._tick_timer.stop()

    def _resume_clock(self):
        if not self._clock.isValid():
            self._clock.start()
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def _should_animate(self):
        return (self._motion_enabled and self.isVisible() and self.window().isVisible()
                and not self.window().isMinimized() and self._has_drawable_area())

    def _sync_timer(self):
        if self._should_animate():
            self._resume_clock()
        else:
            self._pause_clock()

    def _on_tick(self):
        if not self._should_animate():
            self._pause_clock()
            return
        geometry = self.scene_geometry
        damage = QRegion()
        for sample in self.sample_frame():
            point = geometry.point(sample.x, sample.y)
            radius = 120 if sample.warp_alpha else (55 if sample.kind == "freighter" else 22)
            damage |= QRegion(QRectF(point.x()-radius, point.y()-radius,
                                    radius*2, radius*2).toAlignedRect())
        for rect, _ in self._lights.patches:
            damage |= QRegion(geometry.transform.mapRect(rect).adjusted(-2, -2, 2, 2).toAlignedRect())
        for x, y in self._lights.beacons:
            point = geometry.point(x, y)
            damage |= QRegion(QRectF(point.x()-10, point.y()-10, 20, 20).toAlignedRect())
        self.update(damage | self._last_damage)
        self._last_damage = damage

    def _on_reduced_motion_changed(self, reduced):
        self.set_motion_enabled(not reduced)

    def _bind_window_events(self):
        window = self.window()
        if window is self._watched_window:
            return
        if self._watched_window is not None:
            self._watched_window.removeEventFilter(self)
        self._watched_window = window
        if window is not self:
            window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._watched_window and event.type() in {
            QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Close, QEvent.Type.WindowStateChange
        }:
            self._sync_timer()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self._bind_window_events()
        self._sync_timer()

    def hideEvent(self, event):
        self._pause_clock()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._last_damage = QRegion()
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
        geometry = self.scene_geometry
        width = float(geometry.source_width)
        height = float(geometry.source_height)
        position = geometry.point(sample.x, sample.y)
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

        # A tiny lit hull gives nearby vessels a direction even between flares.
        hull = QPainterPath(position + QPointF(direction_x*5, direction_y*5))
        hull.lineTo(position + QPointF(-direction_x*2+perpendicular_x*2,
                                      -direction_y*2+perpendicular_y*2))
        hull.lineTo(position + QPointF(-direction_x*2-perpendicular_x*2,
                                      -direction_y*2-perpendicular_y*2))
        hull.closeSubpath()
        painter.setPen(QPen(QColor(151, 183, 207, int(alpha*.65)), .6))
        painter.setBrush(QColor(18, 34, 46, int(alpha*.9)))
        painter.drawPath(hull)

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
        geometry = self.scene_geometry
        width = float(geometry.source_width)
        height = float(geometry.source_height)
        position = geometry.point(sample.x, sample.y)
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

    def paintEvent(self, event: QPaintEvent):
        if not self._motion_enabled or not self._has_drawable_area():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setClipRect(self.traffic_rect())
        geometry = self.scene_geometry
        # Far traffic is occluded by the fixed hull, including its docking rim.
        scene = QPainterPath()
        scene.addRect(self.traffic_rect())
        hull = QPainterPath()
        vertices = self._timeline.definition["hull"]
        hull.moveTo(geometry.point(*vertices[0]))
        for point in vertices[1:]:
            hull.lineTo(geometry.point(*point))
        hull.closeSubpath()
        painter.save()
        painter.setClipPath(scene.subtracted(hull))
        for sample in self.sample_frame():
            if sample.kind == "freighter":
                self._paint_silhouette(painter, sample)
            else:
                self._paint_light(painter, sample)
        painter.restore()
        self._lights.paint(painter, geometry, self._current_scene_time_ms())


__all__ = ["DockingTrafficOverlay", "TrafficSample"]
