"""One retained, mouse-transparent viewport layer for verified mod changes."""
from dataclasses import dataclass, field
from math import pi, sin
from random import Random
from time import perf_counter

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen, QRegion
from PyQt6.QtWidgets import QWidget, QLabel, QPushButton

WARP_APPROACH_MS = 580

from src.ui.visible_motion import VisibleMotion


def tint(color, alpha):
    rgb = QColor(color)
    rgb.setAlpha(max(0, min(255, round(alpha))))
    return rgb


def paint_departing_row(row):
    """Paint a literal silhouette; never synchronously grab a live widget."""
    density = row.devicePixelRatioF()
    image = QImage(round(row.width() * density), round(row.height() * density),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(density)
    image.fill(0)
    paint = QPainter(image)
    paint.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint.setBrush(QColor('#151e29'))
    paint.setPen(QPen(QColor('#34536a'), 1))
    paint.drawRoundedRect(QRectF(row.rect()).adjusted(.5, .5, -.5, -.5), 5, 5)
    for widget in row.findChildren(QWidget):
        if not isinstance(widget, (QLabel, QPushButton)) or not widget.isVisibleTo(row):
            continue
        rect = QRect(widget.mapTo(row, QPoint()), widget.size())
        color = '#aac7da'
        if isinstance(widget, QPushButton) or widget is row.state_label or widget is row.kind_badge:
            paint.setBrush(QColor('#242c38'))
            paint.setPen(QPen(QColor('#3d5566'), 1))
            paint.drawRoundedRect(QRectF(rect).adjusted(.5, .5, -.5, -.5), 4, 4)
        if widget is row.remove_btn:
            color = '#ff9090'
        paint.setFont(widget.font())
        paint.setPen(QColor(color))
        alignment = (Qt.AlignmentFlag.AlignCenter if isinstance(widget, QPushButton)
                     else Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeading)
        value = widget.fontMetrics().elidedText(widget.text(), Qt.TextElideMode.ElideRight, rect.width())
        paint.drawText(rect, alignment, value)
    paint.end()
    return image


class DepletionMask:
    """Seeded, multiscale erosion field rendered with alpha and heat lookups."""

    width = 390
    height = 38

    def __init__(self):
        random = Random(5391)
        scales = ((14, 4, .55), (44, 11, .28), (96, 23, .17))
        layers = [
            (columns, rows, weight, [[random.random() for _ in range(columns + 1)] for _ in range(rows + 1)])
            for columns, rows, weight in scales
        ]
        values = [
            sum(self._sample(grid, x * columns / self.width, y * rows / self.height) * weight
                for columns, rows, weight, grid in layers)
            for y in range(self.height) for x in range(self.width)
        ]
        lower, upper = min(values), max(values)
        field = bytes(round(255 * (value - lower) / (upper - lower)) for value in values)
        self._frames = tuple(self._render(field, step / 36) for step in range(37))

    @staticmethod
    def _sample(grid, x, y):
        left, top = int(x), int(y)
        u, v = x - left, y - top
        u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
        near = grid[top][left] + (grid[top][left + 1] - grid[top][left]) * u
        far = grid[top + 1][left] + (grid[top + 1][left + 1] - grid[top + 1][left]) * u
        return near + (far - near) * v

    def frame(self, progress):
        return self._frames[round(progress * (len(self._frames) - 1))]

    def _render(self, field, progress):
        threshold = 255 * progress
        coverage, glow = [], []
        for value in range(256):
            distance = value - threshold
            alpha = round(255 * min(1, max(0, (distance + 1) / 3)))
            heat = max(0.0, 1.0 - abs(distance - .3) / 14)
            edge = max(0.0, 1.0 - abs(distance - .4) / 4)
            coverage.append(bytes((255, 255, 255, alpha)))
            glow.append(bytes((255, round(82 + 143 * edge), round(33 + 128 * edge),
                               round(102 * heat + 150 * edge))))
        alpha_pixels = b"".join(coverage[value] for value in field)
        hot_pixels = b"".join(glow[value] for value in field)
        stride = self.width * 4
        return (
            QImage(alpha_pixels, self.width, self.height, stride, QImage.Format.Format_RGBA8888).copy(),
            QImage(hot_pixels, self.width, self.height, stride, QImage.Format.Format_RGBA8888).copy(),
        )


LANDING_FLASH_MS = 370
EFFECT_DURATION_MS = {"arrival": WARP_APPROACH_MS + LANDING_FLASH_MS, "departure": 1160, "power": 210}


@dataclass
class RowEffect:
    row: QWidget
    kind: str
    photo: QImage | None
    enabled: bool
    original_height: int
    started: float = field(default_factory=perf_counter)
    fraction: float = 0.0

    @property
    def landing_fraction(self):
        elapsed_ms = self.fraction * EFFECT_DURATION_MS["arrival"]
        return (elapsed_ms - WARP_APPROACH_MS) / LANDING_FLASH_MS


class RowEffectsLayer(QWidget):
    """Paint the active rows on one clock and one mouse-transparent layer."""

    def __init__(self, viewport, finish_effect):
        super().__init__(viewport)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()
        self.effects = {}
        self._finish_effect = finish_effect
        # Precompute once off the GUI thread, including the cached alpha frames.
        if _MaskJob.cached is None and not _MaskJob.pending:
            _MaskJob.pending = True
            QThreadPool.globalInstance().start(_MaskJob())
        self._previous_damage = QRegion()
        seeded = Random(723)
        self._sparks = [(seeded.random(), seeded.uniform(-1, 1), seeded.uniform(.4, 1.0))
                        for _ in range(45)][::3]
        self._timer = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def bounds(viewport, row):
        return QRect(row.mapTo(viewport, QPoint()), row.size())

    def add(self, key, row, kind, *, photo=None, enabled=True):
        if len(self.effects) >= 64:
            self._finish_effect(next(iter(self.effects)))
        self.effects[key] = RowEffect(row, kind, photo, enabled, row.height())
        if not self._timer.isActive():
            self.setGeometry(self.parentWidget().rect())
            self.show()
            self.raise_()
            self._timer.start()

    def remove(self, key):
        effect = self.effects.pop(key, None)
        if effect is not None and effect.photo is not None and not sip.isdeleted(effect.row):
            layout = effect.row.parentWidget().layout()
            if layout is not None:
                layout.removeWidget(effect.row)
            effect.row.hide()
            effect.row.deleteLater()
        if self._timer.isActive() and not self.effects:
            self._timer.stop()
            self._previous_damage = QRegion()
            self.hide()
        return effect

    def _tick(self):
        viewport = self.parentWidget()
        if self.geometry() != viewport.rect():
            self.setGeometry(viewport.rect())
        now = perf_counter()
        damage = QRegion()
        for key, effect in tuple(self.effects.items()):
            elapsed_ms = (now - effect.started) * 1000
            effect.fraction = min(1, elapsed_ms / EFFECT_DURATION_MS[effect.kind])
            if effect.fraction >= 1 or sip.isdeleted(effect.row):
                self._finish_effect(key)
                continue
            area = self.bounds(viewport, effect.row)
            extent = QRect(0, area.y(), viewport.width(), max(area.height(), effect.original_height))
            damage |= QRegion(extent.intersected(viewport.rect()))
        if self.effects:
            self.update(self._previous_damage.united(damage))
            self._previous_damage = damage

    def paintEvent(self, event):
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        viewport = self.parentWidget()
        for effect in self.effects.values():
            if sip.isdeleted(effect.row):
                continue
            bounds = self.bounds(viewport, effect.row)
            area = QRect(0, bounds.y(), self.width(), bounds.height())
            if not event.region().intersects(area) or not self.rect().intersects(area):
                continue
            paint.save()
            paint.setClipRect(area)
            self._paint_effect(paint, effect, QRectF(bounds.x(), bounds.y(), bounds.width(), effect.original_height))
            paint.restore()
        paint.end()

    def _paint_effect(self, paint, effect, bounds):
        t = effect.fraction
        accent = "#8bffc8" if effect.kind == "power" and effect.enabled else "#76e4ff"
        center = bounds.center()
        left, width = bounds.left(), bounds.width()
        amplitude = (1 - t) ** 1.5
        lines = Qt.PenStyle.NoPen
        paint.setPen(lines)

        if effect.kind == "power":
            band = width * (1 - t)
            light = QLinearGradient(left + band - 85, 0, left + band + 85, 0)
            light.setColorAt(0, tint(accent, 0))
            light.setColorAt(.50, tint(accent, 140 * amplitude))
            light.setColorAt(1, tint(accent, 0))
            paint.fillRect(QRectF(left, bounds.bottom() - 2, width, 2), light)
        elif effect.kind == "arrival":
            landing = effect.landing_fraction
            shade = .54 * (1 - min(1, max(0, landing) * 1.44))
            paint.fillRect(bounds, tint("#101820", 255 * shade))
            if landing < 0:
                approach = 1 + landing * LANDING_FLASH_MS / WARP_APPROACH_MS
                self._paint_approach(paint, bounds, approach)
                return
            t = landing
            amplitude = (1 - t) ** 1.5
            reach = width * (.16 + .84 * (1 - (1 - t) ** 3))
            flash = QLinearGradient(center.x() - reach / 2, 0, center.x() + reach / 2, 0)
            flash.setColorAt(0, tint(accent, 0))
            flash.setColorAt(.5, tint(accent, 173 * sin(pi * min(1, t + .15)) * amplitude))
            flash.setColorAt(1, tint(accent, 0))
            paint.fillRect(bounds, flash)
            paint.setBrush(Qt.BrushStyle.NoBrush)
            paint.setPen(QPen(tint(accent, 230 * amplitude), 1.7))
            rim = QRectF(center.x() - reach / 2, bounds.top() + (1 - t) * 7,
                         reach, bounds.height() - (1 - t) * 14)
            paint.drawRoundedRect(rim, 5, 5)
            paint.setPen(QPen(tint("#a3faff", 170 * amplitude), 1.2))
            for index, (x, y, speed) in enumerate(self._sparks):
                px = left + x * width
                py = center.y() + y * bounds.height() * .50
                streak = (35 + speed * 96) * (1 - t)
                direction = 1 if index % 2 else -1
                paint.drawLine(QPointF(px, py), QPointF(px + streak * direction, py))
        else:
            self._deplete(paint, bounds, t, effect.photo)

    def _paint_approach(self, paint, bounds, approach):
        center = bounds.center()
        for index, (position, height, speed) in enumerate(self._sparks):
            direction = -1 if index % 2 else 1
            distance = bounds.width() * (.20 + .45 * position) * (1 - approach ** 1.7)
            x = center.x() + direction * distance
            y = center.y() + height * bounds.height() * .50
            trail = direction * (12 + 115 * speed * approach)
            paint.setPen(QPen(tint("#a3faff", 153 * approach * speed), 1.2))
            paint.drawLine(QPointF(x, y), QPointF(x + trail, y))

    def _deplete(self, paint, bounds, t, photo):
        burn = max(0.0, min(1.0, (t - .055) / .88))
        if not burn:
            paint.drawImage(bounds, photo, QRectF(photo.rect()))
        elif burn < 1:
            # Keep the photographed row in place while its surface burns off.
            if _MaskJob.cached is None:
                paint.setOpacity(1 - burn)
                paint.drawImage(bounds, photo, QRectF(photo.rect()))
                return
            alpha, heat = _MaskJob.cached.frame(burn)
            row = photo.copy()
            density = row.devicePixelRatio()
            row.setDevicePixelRatio(1)
            layer = QPainter(row)
            layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            physical = QRectF(row.rect())
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            layer.drawImage(physical, alpha, QRectF(alpha.rect()))
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            layer.setOpacity(min(1.0, (1 - burn) * 4))
            layer.drawImage(physical, heat, QRectF(heat.rect()))
            layer.end()
            row.setDevicePixelRatio(density)
            paint.drawImage(bounds, row, physical)


class _MaskJob(QRunnable):
    cached = None
    pending = False

    def run(self):
        try:
            type(self).cached = DepletionMask()
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Optional mod dissolve mask could not be prepared')
        finally:
            type(self).pending = False


class ModFeedback(QObject):
    """Decorate confirmed inventory differences, never button intentions."""
    cue_requested = pyqtSignal(str)
    effect_finished = pyqtSignal(object)

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.layer = RowEffectsLayer(page._scroll.viewport(), self.finish)
        self._gate = VisibleMotion(page, self._motion_changed)

    def _motion_changed(self, allowed):
        if not allowed:
            for key in tuple(self.layer.effects):
                self.finish(key)

    def visible(self, row):
        viewport = self.page._scroll.viewport()
        return (not sip.isdeleted(row) and self.page.isVisible()
                and not self.page.window().isMinimized()
                and row.isVisibleTo(self.page)
                and RowEffectsLayer.bounds(viewport, row).intersects(viewport.rect()))

    def finish(self, key):
        if self.layer.remove(key) is not None:
            self.effect_finished.emit(key)

    def forget_row(self, row):
        for key, effect in tuple(self.layer.effects.items()):
            if effect.row is row:
                self.finish(key)

    def departure(self, row):
        if not self.visible(row):
            return None
        self.cue_requested.emit('departure')
        if not self._gate.allowed:
            return None
        image = paint_departing_row(row)
        placeholder = QWidget(self.page._list_container)
        placeholder.setObjectName('departingModSpace')
        placeholder.setFixedHeight(row.height())
        placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        placeholder.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        placeholder._departure_image = image
        return placeholder

    def present(self, row, kind):
        if not self.visible(row):
            return
        if kind == 'arrival':
            self.cue_requested.emit(kind)
        if self._gate.allowed:
            self.layer.add(object(), row, kind, enabled=row.mod.active)

    def start_departure(self, placeholder):
        if not sip.isdeleted(placeholder):
            if self._gate.allowed:
                self.layer.add(object(), placeholder, 'departure', photo=placeholder._departure_image)
            else:
                placeholder.hide()
                placeholder.deleteLater()
