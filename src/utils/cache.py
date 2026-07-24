"""Portrait image cache backed by QPixmapCache."""
from __future__ import annotations

from PyQt6.QtGui import QPixmap, QPixmapCache

_DEFAULT_LIMIT_MB = 32


class PortraitCache:
    """Thin wrapper around QPixmapCache for character portrait pixmaps.

    All methods are static; the cache is global to the QApplication.
    """

    @staticmethod
    def get(key: str) -> QPixmap | None:
        """Return the cached pixmap for *key*, or ``None`` if missing.

        Args:
            key: Unique cache key (e.g. ``"{char_id}_{size}"``).
        """
        pixmap = QPixmapCache.find(key)
        if pixmap is None or pixmap.isNull():
            return None
        return pixmap

    @staticmethod
    def put(key: str, pixmap: QPixmap) -> None:
        """Store *pixmap* in the cache under *key*.

        Args:
            key: Unique cache key.
            pixmap: The QPixmap to cache.
        """
        QPixmapCache.insert(key, pixmap)

    @staticmethod
    def clear() -> None:
        """Remove all entries from the cache."""
        QPixmapCache.clear()

    @staticmethod
    def set_limit(mb: int = _DEFAULT_LIMIT_MB) -> None:
        """Set the cache size limit in megabytes.

        Args:
            mb: Limit in MB (default 32).
        """
        QPixmapCache.setCacheLimit(mb * 1024)


# Apply the default limit on module import
PortraitCache.set_limit()
