"""Rotating hero banner for EveJS Launcher V2.

A 176px-tall widget that pre-loads every ``hero_*.png`` under
``assets/hero/``, then cycles through them with cross-fade and
Ken Burns zoom. Images dynamically scale to fill the widget width.

Call ``stop()`` when the page is hidden and ``start()`` when it becomes
visible again.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QTimer,
    QRect,
    QPropertyAnimation,
    QEasingCurve,
    QVariantAnimation,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QGraphicsOpacityEffect,
)

from src.constants import COLORS

_ASSETS_HERO_DIR = (
    Path(__file__).resolve().parent.parent.parent / "assets" / "hero"
)


class HeroBanner(QWidget):
    """Rotating, cross-fading, Ken-Burns hero banner (176px tall, full width)."""

    HEIGHT = 176

    ROTATION_INTERVAL_MS = 6_000
    FADE_DURATION_MS = 1_200
    ZOOM_DURATION_MS = 6_000
    ZOOM_MIN = 1.0
    ZOOM_MAX = 1.05

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(0)
        self.setObjectName("HeroBanner")
        self.setStyleSheet(
            f"#HeroBanner {{ background: {COLORS['void_black']}; }}"
        )

        # ── Pre-load hero images (raw, not pre-cropped) ──────────────────
        self._sources: list[QPixmap] = self._load_sources()
        self._current_index: int = 0
        self._animations_enabled = True
        self._rotation_interval_ms = self.ROTATION_INTERVAL_MS

        # ── Two labels as direct children (NO layout — manually positioned) ─
        self._label_front = QLabel(self)
        self._label_back = QLabel(self)
        for lbl in (self._label_front, self._label_back):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setScaledContents(True)

        # Front label: NO opacity effect — always at full opacity.
        # (QGraphicsOpacityEffect can interfere with scaledContents on some Qt/Windows configs.)

        # Back label: has opacity effect for cross-fade transitions.
        self._opacity_back = QGraphicsOpacityEffect(self)
        self._opacity_back.setOpacity(0.0)
        self._label_back.setGraphicsEffect(self._opacity_back)

        # Z-order: front is on top initially (back starts hidden behind it).
        self._label_front.raise_()

        # ── Cross-fade animation ─────────────────────────────────────────
        self._fade_anim: QPropertyAnimation | None = None

        # ── Ken Burns zoom on the front label ────────────────────────────
        self._zoom_value_storage: float = self.ZOOM_MIN
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setDuration(self.ZOOM_DURATION_MS)
        self._zoom_anim.setStartValue(self.ZOOM_MIN)
        self._zoom_anim.setEndValue(self.ZOOM_MAX)
        self._zoom_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._zoom_anim.valueChanged.connect(self._on_zoom_frame)

        # ── Rotation timer ───────────────────────────────────────────────
        self._rotate_timer = QTimer(self)
        self._rotate_timer.setInterval(self._rotation_interval_ms)
        self._rotate_timer.timeout.connect(self._advance)

        # Initial paint and animation both wait for showEvent().  Keeping
        # hidden banners inactive prevents invisible repaint/timer work.

    # ── Width helper ────────────────────────────────────────────────────
    def _w(self) -> int:
        """Current usable width. Never returns 0 — Qt may report 0 before
        first layout, so fall back to a sensible minimum."""
        return max(1, self.width())

    # ── Asset loading ────────────────────────────────────────────────────
    def _load_sources(self) -> list[QPixmap]:
        """Load every hero_*.png as raw QPixmap — no pre-cropping."""
        pixmaps: list[QPixmap] = []
        if not _ASSETS_HERO_DIR.is_dir():
            return pixmaps
        for path in sorted(_ASSETS_HERO_DIR.glob("hero_*.png")):
            src = QPixmap(str(path))
            if not src.isNull():
                pixmaps.append(src)
        return pixmaps

    def _make_pixmap(self, index: int | None = None) -> QPixmap:
        """Produce a pixmap at EXACTLY the current widget dimensions.

        Uses KeepAspectRatioByExpanding + center-crop to preserve aspect
        ratio, then forces an explicit IgnoreAspectRatio scale to guarantee
        the pixmap matches the label geometry pixel-for-pixel.  This avoids
        any dependency on QLabel.scaledContents (which can misbehave when a
        QGraphicsOpacityEffect is on the widget).
        """
        idx = self._current_index if index is None else index
        src = self._sources[idx]
        w = self._w()
        h = self.HEIGHT

        # Step 1: scale to fill (preserve aspect ratio)
        scaled = src.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Step 2: center-crop
        x = max(0, (scaled.width() - w) // 2)
        y = max(0, (scaled.height() - h) // 2)
        crop_w = min(w, scaled.width() - x)
        crop_h = min(h, scaled.height() - y)
        cropped = scaled.copy(QRect(x, y, crop_w, crop_h))

        # Step 3: force exact widget size (belt and suspenders — ignore
        # any sub-pixel rounding from step 1 that might leave us 1px short)
        if cropped.width() != w or cropped.height() != h:
            cropped = cropped.scaled(
                w, h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return cropped

    # ── Ken Burns zoom ─────────────────────────────────────────────────
    def _on_zoom_frame(self, value: float) -> None:
        self._zoom_value_storage = float(value)
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        """Scale the front label around its center for a stable Ken Burns zoom.

        The source frame stays unchanged for the entire zoom cycle.  Re-cropping
        and re-scaling a pixmap on every animation frame causes visible shimmer
        as integer crop boundaries alternate, so only the display geometry moves.
        """
        if not self._sources:
            return

        w = self._w()
        zoom = max(self.ZOOM_MIN, self._zoom_value_storage)
        zoomed_width = max(w, round(w * zoom))
        zoomed_height = max(self.HEIGHT, round(self.HEIGHT * zoom))
        self._label_front.setGeometry(
            (w - zoomed_width) // 2,
            (self.HEIGHT - zoomed_height) // 2,
            zoomed_width,
            zoomed_height,
        )

    def _reset_front_geometry(self) -> None:
        """Return the front frame to its unzoomed, full-banner geometry."""
        self._label_front.setGeometry(0, 0, self._w(), self.HEIGHT)

    # ── Rotation / cross-fade ────────────────────────────────────────────
    def _advance(self) -> None:
        """Cross-fade to the next hero image and restart the Ken Burns."""
        if not self._animations_enabled or len(self._sources) < 2:
            return

        w = self._w()
        next_index = (self._current_index + 1) % len(self._sources)

        # Render the NEXT image onto the back label
        self._label_back.setPixmap(self._make_pixmap(index=next_index))

        # Bring back label to front (it starts at opacity 0)
        self._label_back.raise_()
        self._opacity_back.setOpacity(0.0)

        # Cancel any in-flight fade
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim.deleteLater()
            self._fade_anim = None

        # Fade the back label IN (front stays fully visible underneath;
        # front has no opacity effect so its scaledContents always works)
        fade = QPropertyAnimation(self._opacity_back, b"opacity", self)
        fade.setDuration(self.FADE_DURATION_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.InOutQuad)

        def _on_finished() -> None:
            # Promote back → front
            self._current_index = next_index
            self._label_front.setPixmap(self._make_pixmap())
            self._opacity_back.setOpacity(0.0)
            # Put front back on top (covers the now-transparent back)
            self._label_front.raise_()
            self._start_zoom()

        fade.finished.connect(_on_finished)
        fade.start()
        self._fade_anim = fade

    def _start_zoom(self) -> None:
        """Restart the Ken Burns zoom on the now-current image."""
        if not self._animations_enabled:
            return
        self._zoom_anim.stop()
        self._zoom_value_storage = self.ZOOM_MIN
        self._reset_front_geometry()
        self._zoom_anim.setStartValue(self.ZOOM_MIN)
        self._zoom_anim.setEndValue(self.ZOOM_MAX)
        self._zoom_anim.start()

    # ── Public controls ──────────────────────────────────────────────────
    @property
    def animations_enabled(self) -> bool:
        """Whether the banner is allowed to animate while visible."""
        return self._animations_enabled

    @property
    def rotation_interval_ms(self) -> int:
        """The configured rotation interval in milliseconds."""
        return self._rotation_interval_ms

    def set_rotation_interval(self, seconds: int) -> None:
        """Apply one rotation interval to both the cross-fade and zoom cycle."""
        self._rotation_interval_ms = max(1, int(seconds)) * 1_000
        self._rotate_timer.setInterval(self._rotation_interval_ms)
        self._zoom_anim.setDuration(self._rotation_interval_ms)

    def set_animations_enabled(self, enabled: bool) -> None:
        """Enable or disable motion immediately without losing the current image."""
        self._animations_enabled = bool(enabled)
        if not self._animations_enabled:
            self.stop()
            self._show_static_frame()
        elif self.isVisible():
            self.start()

    def _show_static_frame(self) -> None:
        """Render one stable frame after motion is disabled or interrupted."""
        self._zoom_value_storage = self.ZOOM_MIN
        self._opacity_back.setOpacity(0.0)
        self._reset_front_geometry()
        self._label_front.raise_()
        if self._sources and self.width() > 0:
            self._label_front.setPixmap(self._make_pixmap())

    def start(self) -> None:
        """Start rotation + Ken Burns. Safe to call repeatedly."""
        if not self._animations_enabled or not self.isVisible() or not self._sources:
            return
        if not self._rotate_timer.isActive():
            self._rotate_timer.start()
        self._start_zoom()

    def stop(self) -> None:
        """Stop rotation and restore the front image as one stable frame."""
        self._rotate_timer.stop()
        self._zoom_anim.stop()
        if self._fade_anim is not None:
            self._fade_anim.stop()
            self._fade_anim.deleteLater()
            self._fade_anim = None
        self._opacity_back.setOpacity(0.0)
        self._reset_front_geometry()
        self._label_front.raise_()

    def is_running(self) -> bool:
        return self._rotate_timer.isActive()

    # ── Qt overrides ─────────────────────────────────────────────────────
    def resizeEvent(self, event) -> None:  # noqa: N802
        """Re-render hero images and force labels to fill the widget.

        Because we do NOT use a layout manager (QStackedLayout was
        reporting 0×0 geometry on Windows), labels are manually
        positioned here.
        """
        super().resizeEvent(event)
        if not self._sources or self.width() <= 0:
            return
        w = self._w()
        # Force both labels to fill the entire widget
        self._label_front.setGeometry(0, 0, w, self.HEIGHT)
        self._label_back.setGeometry(0, 0, w, self.HEIGHT)
        # Re-render at the new size
        self._label_front.setPixmap(self._make_pixmap())
        self._apply_zoom()

    def showEvent(self, event) -> None:  # noqa: N802
        """Render and resume only when this visible banner permits animation."""
        super().showEvent(event)
        if self._sources and self.width() > 0:
            w = self._w()
            self._label_front.setGeometry(0, 0, w, self.HEIGHT)
            self._label_back.setGeometry(0, 0, w, self.HEIGHT)
            self._label_front.setPixmap(self._make_pixmap())
            if self._animations_enabled:
                self.start()
            else:
                self._show_static_frame()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)
