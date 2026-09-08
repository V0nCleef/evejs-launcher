"""One source-art transform shared by the fixed scene and its live details."""
from __future__ import annotations
from dataclasses import dataclass
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QTransform


@dataclass(frozen=True)
class SceneGeometry:
    source_width: int
    source_height: int
    width: int
    height: int
    padding: int = 8

    @property
    def scale(self) -> float:
        return max((self.width + self.padding) / self.source_width,
                   (self.height + self.padding) / self.source_height)

    @property
    def offset(self) -> tuple[float, float]:
        return ((self.source_width * self.scale - self.width - self.padding) * .70,
                (self.source_height * self.scale - self.height - self.padding) * .48)

    @property
    def transform(self) -> QTransform:
        x, y = self.offset
        return QTransform(self.scale, 0., 0., self.scale, -x, -y)

    def point(self, x: float, y: float) -> QPointF:
        return self.transform.map(QPointF(x * self.source_width, y * self.source_height))

    @property
    def source_rect(self) -> QRectF:
        x, y = self.offset
        return QRectF(x / self.scale, y / self.scale,
                      (self.width + self.padding) / self.scale,
                      (self.height + self.padding) / self.scale)
