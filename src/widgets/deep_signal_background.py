"""Cached Deep Signal starfield and nebula background."""
from __future__ import annotations

import random
from pathlib import Path

from PyQt6.QtCore import (
    QPointF,
    QRectF,
    QSize,
    Qt,
)
from PyQt6.QtGui import (
    QColor,
    QLinearGradient,
    QPaintEvent,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QResizeEvent,
)
from PyQt6.QtWidgets import QWidget

from src.constants import SEMANTIC_COLORS
from src.ui.motion import MotionController


def operations_scene_path(module_file: str | Path | None = None) -> Path:
    """Resolve the optional bundled Operations scene in source and frozen builds."""
    source_file = Path(module_file) if module_file is not None else Path(__file__)
    return (
        source_file.resolve().parent.parent.parent
        / "assets"
        / "deep_signal"
        / "operations_orbital.png"
    )


class DeepSignalBackground(QWidget):
    """Low-cost cached, permanently static Operations background.

    ``motion_enabled`` remains a compatibility setting for callers that apply
    one global animation preference to multiple widgets.  It is intentionally
    inert here: the orbital scene never moves, while other UI animations may
    continue to honor that preference independently.
    """

    # Preserve the approved zero-offset crop from the former drift renderer.
    # The extra edge is static and never traversed.
    _STATIC_CACHE_PADDING = 8

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
        motion_enabled: bool = True,
        seed: int = 7_319,
        scene_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        # Background motion was permanently retired. Do not retain the shared
        # process-wide MotionController QObject: it serves no runtime purpose
        # here and can outlive/re-enter QApplication teardown in test and
        # launcher restart paths. Keep only whether an explicit policy was
        # supplied for introspection-compatible construction.
        self._motion_controller_supplied = motion_controller is not None
        self._motion_enabled = bool(motion_enabled)
        self._seed = int(seed)
        self._cache = QPixmap()
        self._scene_path = (
            Path(scene_path) if scene_path is not None else operations_scene_path()
        )
        self._scene_source = QPixmap(str(self._scene_path))

    @property
    def motion_enabled(self) -> bool:
        return self._motion_enabled

    @property
    def cache_size(self) -> QSize:
        return QSize(self._cache.size())

    @property
    def scene_path(self) -> Path:
        return self._scene_path

    @property
    def scene_available(self) -> bool:
        return not self._scene_source.isNull()

    @property
    def scene_source_size(self) -> QSize:
        return QSize(self._scene_source.size())

    def reload_scene(self) -> bool:
        """Reload the optional scene and invalidate the rendered cache."""
        self._scene_source = QPixmap(str(self._scene_path))
        self._cache = QPixmap()
        self.update()
        return self.scene_available

    def set_motion_enabled(self, enabled: bool) -> None:
        """Retain the caller preference without ever animating the background."""
        self._motion_enabled = bool(enabled)

    def is_animating(self) -> bool:
        """The Operations background is static by design."""
        return False

    def _ensure_cache(self) -> None:
        cache_size = QSize(
            max(1, self.width() + self._STATIC_CACHE_PADDING),
            max(1, self.height() + self._STATIC_CACHE_PADDING),
        )
        if not self._cache.isNull() and self._cache.size() == cache_size:
            return

        self._cache = QPixmap(cache_size)
        self._cache.fill(QColor(SEMANTIC_COLORS["background"]))
        painter = QPainter(self._cache)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(0.0, 0.0, float(cache_size.width()), float(cache_size.height()))

        if self.scene_available:
            self._paint_scene(painter, bounds)
        else:
            self._paint_procedural_fallback(painter, bounds, cache_size)

        self._paint_readability_gradient(painter, bounds)

    def _paint_scene(self, painter: QPainter, bounds: QRectF) -> None:
        """Cover-crop the scene while preserving its right-side focal subject."""
        target_size = bounds.size().toSize()
        scaled = self._scene_source.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        excess_x = max(0, scaled.width() - target_size.width())
        excess_y = max(0, scaled.height() - target_size.height())
        # The approved composition reserves the right side for the orbital
        # station.  A 70% horizontal focal crop preserves that subject across
        # 16:9, ultrawide, and minimum-size windows.
        source = QRectF(
            float(round(excess_x * 0.70)),
            float(round(excess_y * 0.48)),
            float(target_size.width()),
            float(target_size.height()),
        )
        painter.drawPixmap(bounds, scaled, source)

    def _paint_procedural_fallback(
        self,
        painter: QPainter,
        bounds: QRectF,
        cache_size: QSize,
    ) -> None:
        """Render the deterministic low-cost fallback when no scene is bundled."""

        base = QLinearGradient(0.0, 0.0, 0.0, float(cache_size.height()))
        base.setColorAt(0.0, QColor(SEMANTIC_COLORS["background_raised"]))
        base.setColorAt(0.58, QColor(SEMANTIC_COLORS["background"]))
        base.setColorAt(1.0, QColor("#020509"))
        painter.fillRect(bounds, base)

        cyan_haze = QRadialGradient(
            QPointF(cache_size.width() * 0.78, cache_size.height() * 0.14),
            max(cache_size.width(), cache_size.height()) * 0.72,
        )
        cyan_haze.setColorAt(0.0, QColor(0, 200, 224, 34))
        cyan_haze.setColorAt(0.45, QColor(0, 153, 184, 13))
        cyan_haze.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(bounds, cyan_haze)

        gold_haze = QRadialGradient(
            QPointF(cache_size.width() * 0.15, cache_size.height() * 0.88),
            max(cache_size.width(), cache_size.height()) * 0.54,
        )
        gold_haze.setColorAt(0.0, QColor(255, 184, 0, 18))
        gold_haze.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(bounds, gold_haze)

        rng = random.Random(
            self._seed + cache_size.width() * 31 + cache_size.height() * 17
        )
        star_count = max(
            28,
            min(170, (cache_size.width() * cache_size.height()) // 14_000),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        for _ in range(star_count):
            radius = rng.choice((0.55, 0.65, 0.8, 1.0, 1.25))
            opacity = rng.randint(38, 145)
            color = QColor(
                117 if rng.random() < 0.2 else 220,
                226 if rng.random() < 0.2 else 235,
                238,
                opacity,
            )
            painter.setBrush(color)
            painter.drawEllipse(
                QPointF(
                    rng.uniform(0.0, cache_size.width()),
                    rng.uniform(0.0, cache_size.height()),
                ),
                radius,
                radius,
            )

        grid_pen = QPen(QColor(52, 88, 106, 18), 1.0)
        painter.setPen(grid_pen)
        grid_step = 96
        for x in range(0, cache_size.width(), grid_step):
            painter.drawLine(x, 0, x, cache_size.height())
        for y in range(0, cache_size.height(), grid_step):
            painter.drawLine(0, y, cache_size.width(), y)

        # Cached orbital traces keep the fallback recognisably Deep Signal
        # without introducing live blur or per-frame geometry work.
        orbit_pen = QPen(QColor(0, 200, 224, 25), 1.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(orbit_pen)
        orbit_center = QPointF(
            cache_size.width() * 0.79,
            cache_size.height() * 0.73,
        )
        for width_ratio, height_ratio in (
            (0.22, 0.09),
            (0.34, 0.15),
            (0.48, 0.22),
            (0.64, 0.30),
        ):
            width = cache_size.width() * width_ratio
            height = cache_size.height() * height_ratio
            painter.drawEllipse(
                QRectF(
                    orbit_center.x() - width / 2.0,
                    orbit_center.y() - height / 2.0,
                    width,
                    height,
                )
            )

    @staticmethod
    def _paint_readability_gradient(painter: QPainter, bounds: QRectF) -> None:
        """Protect foreground contrast while allowing the right scene to breathe."""
        left_fade = QLinearGradient(
            bounds.left(),
            bounds.top(),
            bounds.left() + bounds.width() * 0.78,
            bounds.top(),
        )
        left_fade.setColorAt(0.0, QColor(3, 8, 14, 247))
        left_fade.setColorAt(0.32, QColor(3, 9, 16, 222))
        left_fade.setColorAt(0.58, QColor(3, 10, 17, 128))
        left_fade.setColorAt(0.82, QColor(3, 10, 17, 34))
        left_fade.setColorAt(1.0, QColor(3, 10, 17, 0))
        painter.fillRect(bounds, left_fade)

        bottom_fade = QLinearGradient(
            bounds.left(),
            bounds.top() + bounds.height() * 0.55,
            bounds.left(),
            bounds.bottom(),
        )
        bottom_fade.setColorAt(0.0, QColor(2, 6, 11, 0))
        bottom_fade.setColorAt(1.0, QColor(2, 6, 11, 112))
        painter.fillRect(bounds, bottom_fade)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        self._ensure_cache()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        source = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        painter.drawPixmap(QRectF(self.rect()), self._cache, source)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._cache = QPixmap()
        super().resizeEvent(event)


__all__ = ["DeepSignalBackground", "operations_scene_path"]
