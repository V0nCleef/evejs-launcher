"""Focused offscreen contracts for the Deep Signal visual foundation."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QPropertyAnimation, QTimer
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from src.constants import (
    MOTION_DURATIONS_MS,
    SEMANTIC_COLORS,
    STATUS_COLORS,
)
from src.theme import build_qss
from src.ui.motion import MotionController, resolve_motion_controller
from src.widgets.deep_signal_background import (
    DeepSignalBackground,
    operations_scene_path,
)
from src.widgets.glass_panel import GlassPanel
from src.widgets.page_header import PageHeader
from src.widgets.status_ring import StatusRing


def test_deep_signal_tokens_and_qss_roles_are_semantic_and_additive() -> None:
    assert SEMANTIC_COLORS["surface_elevated"]
    assert SEMANTIC_COLORS["text_primary"]
    assert STATUS_COLORS["starting"] == SEMANTIC_COLORS["warning"]
    assert STATUS_COLORS["online"] == SEMANTIC_COLORS["success"]
    assert MOTION_DURATIONS_MS["page"] == 180

    qss = build_qss(
        {"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"}
    )
    for role in (
        'QFrame[class="glassPanel"]',
        'QFrame[class="glassPanel"][variant="quiet"]',
        'QLabel[class="pageEyebrow"]',
        'QLabel[class="pageTitle"]',
        'QLabel[class="pageSubtitle"]',
        'QLabel[class="signalPill"]',
        'QPushButton[class="signalPrimary"]',
        'QPushButton[class="signalSecondary"]',
    ):
        assert role in qss


def test_motion_controller_resolves_tokens_and_settles_static_target(qapp) -> None:
    controller = MotionController()
    changes: list[bool] = []
    controller.reduced_motion_changed.connect(changes.append)

    assert controller.duration("state") == MOTION_DURATIONS_MS["state"]
    controller.set_reduced_motion(True)
    controller.set_reduced_motion(True)
    assert changes == [True]
    assert controller.duration("state") == 0

    target = QWidget()
    target.setWindowOpacity(0.0)
    animation = QPropertyAnimation(target, b"windowOpacity")
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    assert controller.start(animation) is False
    assert target.windowOpacity() == pytest.approx(1.0)

    with pytest.raises(ValueError, match="Unknown motion duration"):
        MotionController().duration("hyperspace")


def test_default_motion_controller_recovers_after_qobject_deletion(qapp) -> None:
    first = resolve_motion_controller(None)
    first.deleteLater()
    qapp.processEvents()

    # Explicit QObject deletion simulates the wrapper left behind when a test
    # QApplication is torn down and later recreated.
    from PyQt6 import sip

    sip.delete(first)
    replacement = resolve_motion_controller(None)
    assert replacement is not first
    assert replacement.animations_enabled is True


def test_glass_panel_and_page_header_expose_reusable_layout_contract(qapp) -> None:
    panel = GlassPanel(variant="quiet", interactive=True, padding=20)
    content = QLabel("Systems nominal")
    panel.add_widget(content)

    assert panel.property("class") == "glassPanel"
    assert panel.variant == "quiet"
    assert panel.interactive is True
    assert panel.content_layout.indexOf(content) == 0
    assert panel.content_layout.contentsMargins().left() == 20

    panel.set_selected(True)
    panel.set_accent("warning")
    assert panel.property("selected") is True
    assert panel.property("accent") == "warning"
    with pytest.raises(ValueError, match="Unknown glass panel variant"):
        panel.set_variant("opaque")

    header = PageHeader(
        "Operations",
        "Start services and monitor the cluster.",
        "Command network",
    )
    action = QPushButton("Launch stack")
    header.add_action(action)

    assert header.title_label.text() == "Operations"
    assert header.eyebrow_label.text() == "COMMAND NETWORK"
    assert header.subtitle_label.text().startswith("Start services")
    assert header.action_layout.indexOf(action) == 0
    assert header.accessibleName() == "Operations"


def test_status_ring_is_static_under_reduced_motion_and_accepts_enum_values(qapp) -> None:
    controller = MotionController(reduced_motion=True)
    ring = StatusRing(
        "Game",
        "Starting",
        "Waiting for port",
        "starting",
        motion_controller=controller,
    )
    ring.resize(180, 180)
    ring.show()
    qapp.processEvents()

    assert ring.state == "starting"
    assert ring.is_animating() is False
    assert not ring.grab().isNull()

    controller.set_reduced_motion(False)
    qapp.processEvents()
    assert ring.is_animating() is True

    ring.set_state("online", value="Online", detail="PID 123", progress=2.0)
    assert ring.state == "online"
    assert ring.value == "Online"
    assert ring.detail == "PID 123"
    assert ring.progress == 1.0
    assert ring.is_animating() is False
    assert "online" in ring.accessibleDescription()

    ring.set_state("ServiceState.FAILED")
    assert ring.state == "failed"
    ring.hide()
    assert ring.is_animating() is False


def test_background_is_permanently_static_under_every_motion_preference(qapp) -> None:
    controller = MotionController(reduced_motion=True)
    background = DeepSignalBackground(motion_controller=controller)
    background.resize(640, 360)
    background.show()
    qapp.processEvents()

    assert background.is_animating() is False
    assert not background.grab().isNull()
    assert background.cache_size.width() >= 640
    assert background.cache_size.height() >= 360

    controller.set_reduced_motion(False)
    qapp.processEvents()
    assert background.is_animating() is False
    background.set_motion_enabled(False)
    assert background.motion_enabled is False
    assert background.is_animating() is False
    background.set_motion_enabled(True)
    assert background.motion_enabled is True
    assert background.is_animating() is False


def test_background_static_cache_is_reused_until_resize_or_scene_reload(qapp) -> None:
    background = DeepSignalBackground(
        motion_controller=MotionController(reduced_motion=False)
    )
    background.resize(640, 360)
    background.show()
    qapp.processEvents()
    first_frame = background.grab().toImage()
    cache_key = background._cache.cacheKey()

    for _ in range(5):
        qapp.processEvents()
    second_frame = background.grab().toImage()

    assert background._cache.cacheKey() == cache_key
    assert first_frame == second_frame
    assert not hasattr(background, "_drift_timer")
    assert not hasattr(background, "_drift_clock")
    assert not hasattr(background, "_offset")
    assert background.findChildren(QTimer) == []


def test_background_optional_scene_is_cover_cropped_with_right_focal_bias(
    qapp,
    tmp_path,
) -> None:
    scene_path = tmp_path / "operations_orbital.png"
    scene = QImage(320, 180, QImage.Format.Format_ARGB32)
    scene.fill(QColor("#7B1515"))
    for x in range(160, scene.width()):
        for y in range(scene.height()):
            scene.setPixelColor(x, y, QColor("#1762D2"))
    assert scene.save(str(scene_path))

    controller = MotionController(reduced_motion=True)
    background = DeepSignalBackground(
        motion_controller=controller,
        scene_path=scene_path,
    )
    background.resize(160, 160)
    background.show()
    qapp.processEvents()
    captured = background.grab().toImage()

    assert background.scene_available is True
    assert background.scene_source_size.width() == 320
    assert background.cache_size.width() >= 160
    # The focal crop keeps the blue right-side subject visible. The readability
    # scrim may darken it, so compare channels rather than an exact colour.
    right_pixel = captured.pixelColor(150, 80)
    assert right_pixel.blue() > right_pixel.red()


def test_operations_scene_path_is_bundle_relative(tmp_path) -> None:
    module_file = tmp_path / "_internal" / "src" / "widgets" / "module.py"
    assert operations_scene_path(module_file) == (
        tmp_path
        / "_internal"
        / "assets"
        / "deep_signal"
        / "operations_orbital.png"
    )
