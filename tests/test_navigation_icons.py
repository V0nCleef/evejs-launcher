"""Offline navigation glyphs keep their size, state and bundled provenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QIcon

from src.constants import COLORS
from src.widgets.nav_panel import NavPanel


ROOT = Path(__file__).resolve().parents[1]


def _opaque_colors(pixmap):
    image = pixmap.toImage()
    return [
        image.pixelColor(x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() >= 200
    ]


def test_navigation_has_compact_icons_with_explicit_checked_colors(qapp):
    panel = NavPanel()
    try:
        for button in panel.nav_group.buttons():
            assert button.iconSize() == QSize(19, 19)
            for state, expected in (
                (QIcon.State.Off, QColor("#B7C9D6")),
                (QIcon.State.On, QColor(COLORS["teal"])),
            ):
                image = button.icon().pixmap(QSize(19, 19), QIcon.Mode.Normal, state)
                colors = _opaque_colors(image)
                assert len(colors) >= 10
                assert all(
                    # Unpremultiplication of antialiased 8-bit pixels may round
                    # by two levels where strokes overlap.
                    abs(color.red() - expected.red()) <= 2
                    and abs(color.green() - expected.green()) <= 2
                    and abs(color.blue() - expected.blue()) <= 2
                    for color in colors
                )
    finally:
        panel.close()
        panel.deleteLater()


@pytest.mark.parametrize("kind", ["home", "characters", "save_migration", "mods", "tools", "settings"])
def test_bundled_icons_render_offline_from_another_working_directory(qapp, monkeypatch, tmp_path, kind):
    from src.ui.navigation_icons import navigation_icon

    monkeypatch.chdir(tmp_path)
    icon = navigation_icon(kind)
    for ratio in (1.0, 2.0, 3.0):
        pixmap = icon.pixmap(QSize(19, 19), ratio, QIcon.Mode.Normal, QIcon.State.On)
        assert not pixmap.isNull()
        assert pixmap.width() == round(19 * ratio)
        assert pixmap.devicePixelRatio() == ratio
        assert _opaque_colors(pixmap)


def test_vendored_glyphs_are_static_and_match_reviewed_sources():
    folder = ROOT / "assets" / "icons" / "navigation"
    manifest = json.loads((folder / "sources.json").read_text(encoding="utf-8"))
    assert len(manifest["icons"]) == 6
    allowed = {"svg", "path", "line", "polyline", "polygon", "circle", "rect", "ellipse"}
    for record in manifest["icons"]:
        payload = (folder / record["file"]).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload).hexdigest() == record["git_blob_sha"]
        assert len(payload) < 4096
        root = ElementTree.fromstring(payload)
        assert root.attrib["viewBox"] == "0 0 24 24"
        for element in root.iter():
            assert element.tag.rsplit("}", 1)[-1] in allowed
            assert not any("href" in key or key.startswith("on") for key in element.attrib)
        assert b"<!" not in payload
    license_text = (ROOT / "licenses" / "LUCIDE.txt").read_text(encoding="utf-8")
    assert "Lucide" in license_text and "Permission" in license_text
