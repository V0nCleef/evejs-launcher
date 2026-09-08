"""Cached emissive-only station masks; hull texture and the base art stay fixed."""
from __future__ import annotations
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QRadialGradient
from src.ui.scene_geometry import SceneGeometry
from src.ui.scene_timeline import SceneTimeline, scene_definition


class StationLights:
    """Prepare tiny authored window-bank masks once, never inside a paint tick."""

    def __init__(self, scene_path):
        source = QImage(str(scene_path)).convertToFormat(QImage.Format.Format_ARGB32)
        definition = scene_definition()
        self.patches: list[tuple[QRectF, QPixmap]] = []
        self.beacons = definition["beacons"]
        if source.isNull() or (source.width(), source.height()) != tuple(definition["reference_size"]):
            return
        for x, y, width, height in definition["lights"]:
            patch = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            patch.fill(Qt.GlobalColor.transparent)
            for py in range(height):
                for px in range(width):
                    color = source.pixelColor(x+px, y+py)
                    red, green, blue = color.red(), color.green(), color.blue()
                    # Only warm luminous pixels enter the mask, preserving the
                    # surrounding dark hull and cool structural rim lighting.
                    warmth = min(red-blue, green-blue)
                    if red > 70 and green > 48 and warmth > 12:
                        strength = min(1., warmth/48.) * min(1., (red-55)/80.)
                        patch.setPixelColor(px, py, QColor(
                            int(red*.15), int(green*.15), int(blue*.23), int(245*strength)))
            self.patches.append((QRectF(x, y, width, height), QPixmap.fromImage(patch)))

    def paint(self, painter: QPainter, geometry: SceneGeometry, elapsed_ms: int):
        painter.save()
        painter.setTransform(geometry.transform, True)
        for index, (rect, patch) in enumerate(self.patches):
            painter.setOpacity(1.-SceneTimeline.light_level(index, elapsed_ms))
            painter.drawPixmap(rect, patch, QRectF(patch.rect()))
        painter.restore()
        for index, (x, y) in enumerate(self.beacons):
            level = SceneTimeline.beacon_level(index, elapsed_ms)
            point = geometry.point(x, y)
            radius = 3. + level*5.
            glow = QRadialGradient(point, radius)
            tint = (94, 213, 255) if index > 1 else (255, 209, 144)
            glow.setColorAt(0., QColor(*tint, int(level*135)))
            glow.setColorAt(1., QColor(*tint, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(point, radius, radius)
