"""Small, offline Lucide glyphs shared by the public navigation buttons."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from src.constants import COLORS


NAV_ICON_SIZE = 19
_GLYPHS = {
    "home": "house",
    "characters": "scan-face",
    "save_migration": "folder-sync",
    "mods": "boxes",
    "tools": "wrench",
    "settings": "sliders-horizontal",
}
_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "icons" / "navigation"


def navigation_icon(kind: str) -> QIcon:
    """Render bundled paths with explicit idle/checked colors at desktop scales.

    SVG source and notices are vendored; navigation never downloads assets or
    depends on a desktop icon theme. Each raster has its own device pixel ratio
    so the icon occupies the same logical space on Windows and Linux.
    """
    source = (_ASSETS / f"{_GLYPHS[kind]}.svg").read_text(encoding="utf-8")
    source = source.replace('stroke-width="2"', 'stroke-width="1.6"')
    icon = QIcon()
    for state, color in (
        (QIcon.State.Off, "#B7C9D6"),
        (QIcon.State.On, COLORS["teal"]),
    ):
        svg = source.replace('stroke="currentColor"', f'stroke="{QColor(color).name()}"')
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            raise ValueError(f"Invalid bundled navigation glyph: {kind}")
        for ratio in (1.0, 1.25, 1.5, 1.75, 2.0, 3.0):
            edge = round(NAV_ICON_SIZE * ratio)
            pixmap = QPixmap(edge, edge)
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter, QRectF(0, 0, NAV_ICON_SIZE, NAV_ICON_SIZE))
            painter.end()
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
                icon.addPixmap(pixmap, mode, state)
    return icon
