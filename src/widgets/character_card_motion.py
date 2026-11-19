"""Hold-to-drag and landing feedback without changing reorder persistence."""
from PyQt6 import sip
from PyQt6.QtCore import QMimeData, QPoint, QPointF, QRectF, QTimer, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QDrag, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect

from src.ui.visible_motion import VisibleMotion



def tone(color, opacity):
    tint = QColor(color)
    tint.setAlpha(max(0, min(255, round(opacity))))
    return tint


class CharacterCardMotion:
    """Optional feedback around the stock authenticated character reorder gesture."""
    HOLD_MS = 360

    def _init_card_motion(self):
        self._armed = False
        self._hold_context = None
        self._early_movement = False
        self._charge = 0.0
        self._settle = 1.0
        self._hold = QTimer(self, singleShot=True, interval=self.HOLD_MS)
        self._hold.setTimerType(Qt.TimerType.PreciseTimer)
        self._hold.timeout.connect(self._arm)
        self._arming_animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=self.HOLD_MS)
        self._arming_animation.valueChanged.connect(self._draw_charge)
        self._landing_animation = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=490)
        self._landing_animation.valueChanged.connect(self._draw_landing)
        self._motion_gate = VisibleMotion(self, self._update_motion)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._drag_start = event.position().toPoint()
        self._dragged = False
        self._clear_hold()
        self._early_movement = False
        if self._reorder_context is not None:
            self._hold_context = self._reorder_context
            self._hold.start()
            if self._motion_gate.allowed:
                self._arming_animation.start()
        event.accept()

    def _arm(self):
        if self._drag_start is None or self._hold_context is not self._reorder_context:
            return
        self._armed = True
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._draw_charge(1.0 if self._motion_gate.allowed else 0.0)

    def mouseMoveEvent(self, event):
        if self._drag_start is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return super().mouseMoveEvent(event)
        distance = (event.position().toPoint() - self._drag_start).manhattanLength()
        if distance < QApplication.startDragDistance():
            return
        self._early_movement = True
        if self._armed and self._hold_context is self._reorder_context:
            self._drag()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        self._dragged = self._dragged or self._armed or self._early_movement
        self._clear_hold()
        if (self._drag_start is not None and not self._dragged
                and self.rect().contains(event.position().toPoint())):
            self.selected.emit(self.username, self.char_name, self.char_id)
        self._drag_start = None
        event.accept()

    def _cancel_card_gesture(self):
        self._drag_start = None
        self._drop_after = None
        self._clear_hold()
        self._landing_animation.stop()
        self._draw_landing(1.0)
        self.drag_finished.emit()

    def hideEvent(self, event):
        self._cancel_card_gesture()
        super().hideEvent(event)

    def focusOutEvent(self, event):
        self._cancel_card_gesture()
        super().focusOutEvent(event)

    def _clear_hold(self):
        self._hold.stop()
        self._arming_animation.stop()
        self._hold_context = None
        self._armed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._draw_charge(0.0)

    def _drag(self):
        self._dragged = True
        self._active_drag_context = self._reorder_context
        self._drag_start, hotspot = None, self._drag_start
        self._clear_hold()
        flight = QDrag(self)
        from src.widgets.character_card import CHARACTER_CARD_MIME
        mime = QMimeData()
        mime.setData(CHARACTER_CARD_MIME, b"1")
        flight.setMimeData(mime)
        portrait, padding = self._flight_image()
        flight.setPixmap(portrait)
        flight.setHotSpot(hotspot + QPoint(padding, padding))
        opacity = None
        if self._motion_gate.allowed:
            opacity = QGraphicsOpacityEffect(self, opacity=.38)
            self.setGraphicsEffect(opacity)
        try:
            self._exec_drag(flight)
        finally:
            if not sip.isdeleted(self):
                self._active_drag_context = None
                if opacity is not None and self.graphicsEffect() is opacity:
                    self.setGraphicsEffect(None)
                self.drag_finished.emit()

    def _exec_drag(self, flight):
        flight.exec(Qt.DropAction.MoveAction)

    def _flight_image(self):
        card = self.grab()
        if not self._motion_gate.allowed:
            return card, 0
        logical = card.deviceIndependentSize()
        pad = 10
        density = card.devicePixelRatio()
        result = QPixmap(round((logical.width() + 2 * pad) * density),
                         round((logical.height() + 2 * pad) * density))
        result.setDevicePixelRatio(density)
        result.fill(Qt.GlobalColor.transparent)
        paint = QPainter(result)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QRectF(pad - 1, pad - 1, logical.width() + 2, logical.height() + 2)
        paint.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((15, 20), (10, 39), (4, 105)):
            paint.setPen(QPen(tone("#5ceaff", alpha), width))
            paint.drawRoundedRect(outline, 6, 6)
        paint.drawPixmap(pad, pad, card)
        paint.setPen(QPen(tone("#8cecff", 235), 2))
        paint.drawRoundedRect(outline, 5, 5)
        paint.end()
        return result, pad

    def land(self):
        if self._motion_gate.allowed:
            self._landing_animation.stop()
            self._draw_landing(0.0)
            self._landing_animation.start()

    def _update_motion(self, allowed):
        if not allowed:
            if not self.isVisible() or self.window().isMinimized():
                self._cancel_card_gesture()
            self._arming_animation.stop()
            self._landing_animation.stop()
            self._draw_charge(0.0)
            self._draw_landing(1.0)

    def _draw_charge(self, value):
        self._charge = value
        self.update()

    def _draw_landing(self, value):
        self._settle = value
        self.update()

    def _paint_reorder_feedback(self):
        if not self._motion_gate.allowed:
            # Keep a static, functional insertion marker without decorative
            # glow, arming charge or landing motion.
            if self._drop_after is not None:
                paint = QPainter(self)
                right = self._drop_after != (self.layoutDirection() == Qt.LayoutDirection.RightToLeft)
                x = self.width() - 2 if right else 2
                paint.setPen(QPen(tone('#bcfaff', 246), 2))
                paint.drawLine(x, 5, x, self.height() - 5)
                paint.end()
            return
        if not (self._charge > 0 or self._settle < 1 or self._drop_after is not None):
            return
        paint = QPainter(self)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._charge > 0:
            length = (w - 26) * self._charge
            y = h - 4
            glow = QLinearGradient(13, y, 13 + length, y)
            glow.setColorAt(0, tone("#2394eb", 140))
            glow.setColorAt(1, tone("#99f8ff", 255))
            paint.fillRect(QRectF(13, y - 2, length, 3), glow)
            if self._armed:
                paint.setPen(QPen(tone("#8cf5ff", 207), 1.6))
                paint.setBrush(Qt.BrushStyle.NoBrush)
                paint.drawRoundedRect(QRectF(2, 2, w - 4, h - 4), 6, 6)

        if self._drop_after is not None:
            right = self._drop_after != (self.layoutDirection() == Qt.LayoutDirection.RightToLeft)
            edge = w - 3 if right else 3
            direction = -1 if right else 1
            marker = QLinearGradient(edge, 0, edge + direction * 38, 0)
            marker.setColorAt(0, tone("#50d7ff", 103))
            marker.setColorAt(1, tone("#50d7ff", 0))
            paint.fillRect(QRectF(min(edge, edge + direction * 38), 4, 38, h - 8), marker)
            paint.setPen(QPen(tone("#bcfaff", 246), 2))
            for y in (14, h - 14):
                paint.drawLine(QPointF(edge, y), QPointF(edge + direction * 15, y))

        if self._settle < 1:
            t = self._settle
            glow = QRadialGradient(QPointF(w / 2, h * .52), h * .68)
            glow.setColorAt(0, tone("#8bffdc", 63 * (1 - t)))
            glow.setColorAt(1, tone("#42d6f0", 0))
            paint.fillRect(self.rect(), glow)
            # Four braces close on the card and fade after the slot locks.
            offset = 20 * (1 - min(1, t * 2.8)) ** 2 + 5
            paint.setPen(QPen(tone("#adffdc", 250 * (1 - t) ** .67), 2.4))
            for x, dx in ((offset, 1), (w - offset, -1)):
                for y, dy in ((offset, 1), (h - offset, -1)):
                    origin = QPointF(x, y)
                    paint.drawLine(origin, QPointF(x + 24 * dx, y))
                    paint.drawLine(origin, QPointF(x, y + 24 * dy))
            wave = 5 + (h - 10) * t
            paint.setPen(QPen(tone("#6aeedc", 180 * (1 - t)), 1.3))
            paint.drawLine(QPointF(6, wave), QPointF(w - 6, wave))
        paint.end()
