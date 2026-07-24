"""QThread worker for loading character portraits.

Loads portrait images from the EveJS installation, scales them, optionally
applies a hexagonal mask, and caches results via QPixmapCache. All file I/O
and image processing happen off the GUI thread.

Signals:
    loaded(int, QPixmap): char_id and the loaded (or placeholder) pixmap.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap, QPixmapCache, QPainter, QPainterPath


# Standard EveJS portrait locations relative to evejs_root
_PORTRAIT_SEARCH_PATHS = [
    "server/src/_secondary/image/generated/Character/{char_id}_{size}.jpg",
    "server/src/_secondary/image/generated/Character/{char_id}_{size}.png",
    "server/src/_secondary/image/generated/Character/{char_id}_256.jpg",
    "server/src/_secondary/image/generated/Character/{char_id}_256.png",
    "server/src/_secondary/image/generated/Character/{char_id}_128.jpg",
    "server/src/_secondary/image/generated/Character/{char_id}_128.png",
    "server/src/_secondary/image/generated/Character/{char_id}_512.jpg",
    "server/src/_secondary/image/generated/Character/{char_id}_512.png",
    "server/src/_secondary/image/generated/Character/{char_id}_64.jpg",
    "server/src/_secondary/image/generated/Character/{char_id}_64.png",
    "server/src/_secondary/image/images/hi.jpg",
    "server/src/_secondary/image/images/hi.png",
]


def _placeholder_pixmap(size: int) -> QPixmap:
    """Return a simple gray placeholder pixmap."""
    pm = QPixmap(size, size)
    pm.fill(QColor(60, 60, 60))
    return pm


def _find_portrait_path(evejs_root: str, char_id: int, size: int = 128) -> Path | None:
    """Locate the portrait file for a character."""
    root = Path(evejs_root)
    for rel in _PORTRAIT_SEARCH_PATHS:
        candidate = root / rel.format(char_id=char_id, size=size)
        if candidate.exists():
            return candidate
    return None


def _apply_hex_mask(pixmap: QPixmap) -> QPixmap:
    """Apply a hexagonal alpha mask to a square pixmap."""
    size = pixmap.width()
    masked = QPixmap(size, size)
    masked.fill(Qt.GlobalColor.transparent)

    painter = QPainter(masked)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    w = float(size)
    h = float(size)
    # Flat-top hexagon
    path.moveTo(w * 0.25, 0.0)
    path.lineTo(w * 0.75, 0.0)
    path.lineTo(w, h * 0.5)
    path.lineTo(w * 0.75, h)
    path.lineTo(w * 0.25, h)
    path.lineTo(0.0, h * 0.5)
    path.closeSubpath()

    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return masked


class PortraitLoader(QThread):
    """Load and process a single character portrait.

    Args:
        evejs_root: Path to EveJS installation root.
        char_id:    Character ID to load portrait for.
        size:       Target width/height in pixels.
        hex_mask:   If True, apply a hexagonal alpha mask.
    """

    loaded = pyqtSignal(int, QPixmap)

    def __init__(
        self,
        evejs_root: str,
        char_id: int,
        size: int = 64,
        hex_mask: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._evejs_root = evejs_root
        self._char_id = int(char_id)
        self._size = int(size)
        self._hex_mask = bool(hex_mask)
        self._cache_key = f"{self._char_id}_{self._size}"

    def run(self) -> None:  # noqa: D401 - QThread entry point
        # Try cache first
        cached = QPixmapCache.find(self._cache_key)
        if cached is not None:
            self.loaded.emit(self._char_id, cached)
            return

        try:
            path = _find_portrait_path(self._evejs_root, self._char_id, self._size)
            if path is None:
                raise FileNotFoundError(f"No portrait found for {self._char_id}")

            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                raise RuntimeError(f"Failed to load image: {path}")

            # Scale to target size, keep aspect ratio by cropping to square
            if pixmap.width() != pixmap.height():
                side = min(pixmap.width(), pixmap.height())
                x = (pixmap.width() - side) // 2
                y = (pixmap.height() - side) // 2
                pixmap = pixmap.copy(x, y, side, side)

            pixmap = pixmap.scaled(
                self._size,
                self._size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

            if self._hex_mask:
                pixmap = _apply_hex_mask(pixmap)

            QPixmapCache.insert(self._cache_key, pixmap)
            self.loaded.emit(self._char_id, pixmap)

        except Exception:  # pragma: no cover - defensive
            placeholder = _placeholder_pixmap(self._size)
            if self._hex_mask:
                placeholder = _apply_hex_mask(placeholder)
            QPixmapCache.insert(self._cache_key, placeholder)
            self.loaded.emit(self._char_id, placeholder)
