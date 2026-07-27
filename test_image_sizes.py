"""Regression tests for bundled hero image assets."""

from pathlib import Path

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication


def test_hero_images_load_with_dimensions() -> None:
    app = QApplication.instance() or QApplication([])
    images = sorted(Path("assets/hero").glob("hero_*.png"))
    assert images, "No bundled hero images were found"

    for image in images:
        pixmap = QPixmap(str(image))
        assert not pixmap.isNull(), f"Failed to load {image}"
        assert pixmap.width() > 0 and pixmap.height() > 0

    assert app is not None
