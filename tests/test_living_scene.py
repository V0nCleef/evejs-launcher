"""Composed scene regressions: effects must survive real Home layering."""
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QPoint, QRectF
from PyQt6.QtGui import QRegion
import pytest
from src.pages.home_page import HomePage
from src.theme import build_qss


def _pixels(widget):
    image = widget.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return image.constBits().asstring(image.sizeInBytes())


@pytest.mark.parametrize("size", [(756, 568), (1146, 688), (2335, 1308), (2200, 850)])
def test_home_composition_exposes_traffic_and_keeps_artwork_fixed(qapp, monkeypatch, size):
    old_style = qapp.styleSheet()
    qapp.setStyleSheet(build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"}))
    page = HomePage()
    page.resize(*size)
    page.show()
    qapp.processEvents()
    overlay = page.traffic_overlay
    overlay._pause_clock()
    try:
        monkeypatch.setattr(overlay, "_current_scene_time_ms", lambda: 0)
        overlay.update()
        qapp.processEvents()
        first = _pixels(page)
        art = _pixels(page.signal_background)
        monkeypatch.setattr(overlay, "_current_scene_time_ms", lambda: 4000)
        overlay.update()
        qapp.processEvents()
        second = _pixels(page)
        assert second != first, "Traffic is hidden in the composed Home page"
        assert _pixels(page.signal_background) == art
        allowed = QRegion()
        geometry = overlay.scene_geometry
        for time in (0, 4000):
            for sample in overlay.sample_frame(time):
                center = geometry.point(sample.x, sample.y)
                allowed |= QRegion(QRectF(center.x()-120, center.y()-120, 240, 240).toAlignedRect())
        for rect, _ in overlay._lights.patches:
            allowed |= QRegion(geometry.transform.mapRect(rect).adjusted(-3, -3, 3, 3).toAlignedRect())
        for x, y in overlay._lights.beacons:
            center = geometry.point(x, y)
            allowed |= QRegion(QRectF(center.x()-12, center.y()-12, 24, 24).toAlignedRect())
        dpr = page.devicePixelRatioF()
        physical_width = page.grab().width()  # Qt rounds half pixels upward.
        changed = [i//4 for i in range(0, len(first), 4) if first[i:i+4] != second[i:i+4]]
        assert changed
        assert all(allowed.contains(QPoint(int((i % physical_width)/dpr), int((i//physical_width)/dpr))) for i in changed)
    finally:
        page.close()
        page.deleteLater()
        qapp.setStyleSheet(old_style)


def test_optional_scene_manifest_failure_keeps_static_fallback(qapp, monkeypatch, tmp_path):
    import src.ui.scene_timeline as timeline
    timeline.scene_definition.cache_clear()
    monkeypatch.setattr(timeline, "SCENE_MANIFEST", tmp_path / "absent.json")
    try:
        model = timeline.SceneTimeline()
        assert model.sample(15000) == ()
    finally:
        timeline.scene_definition.cache_clear()


def test_station_lights_change_locally_and_scene_schedules_stay_bounded():
    from src.ui.scene_timeline import SceneTimeline
    model = SceneTimeline()
    levels = [model.light_level(0, time) for time in range(0, 60000, 100)]
    assert min(levels) < .15 and max(levels) == 1.
    assert any(model.sample(time) for time in range(0, 3000, 100))
    for time in range(0, 3600000, 500):
        assert len(model.sample(time)) <= 5
    assert len(model._schedules) <= 5
