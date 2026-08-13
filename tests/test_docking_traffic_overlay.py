"""Lifecycle and performance contracts for station-side docking traffic."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget

from src.ui.motion import MotionController
from src.widgets.docking_traffic_overlay import DockingTrafficOverlay


def test_traffic_schedule_is_sparse_deterministic_and_contains_arrivals(qapp) -> None:
    first = DockingTrafficOverlay(seed=118)
    second = DockingTrafficOverlay(seed=118)
    different = DockingTrafficOverlay(seed=119)

    sample_times = range(0, 96_000, 100)
    first_frames = tuple(first.sample_frame(elapsed) for elapsed in sample_times)
    second_frames = tuple(second.sample_frame(elapsed) for elapsed in sample_times)

    assert first_frames == second_frames
    assert first.sample_frame(1_300) != different.sample_frame(1_300)
    assert max(map(len, first_frames)) <= 2
    assert any(
        sample.kind == "light" and sample.warp_alpha > 0.0
        for frame in first_frames
        for sample in frame
    )
    assert any(
        sample.kind == "silhouette"
        for frame in first_frames
        for sample in frame
    )
    assert all(
        0.0 <= sample.progress <= 1.0 and 0.0 <= sample.opacity <= 1.0
        for frame in first_frames
        for sample in frame
    )


def test_overlay_uses_one_bounded_low_cadence_timer(qapp) -> None:
    overlay = DockingTrafficOverlay()

    timers = overlay.findChildren(QTimer)
    assert timers == [overlay._tick_timer]
    assert 60 <= overlay.timer_interval_ms <= 125
    assert overlay._tick_timer.timerType() == Qt.TimerType.CoarseTimer
    # Structural performance guard: no frame ever asks QPainter to render an
    # unbounded particle field, even when sampled at fine granularity.
    assert max(
        len(overlay.sample_frame(elapsed))
        for elapsed in range(0, 96_000, 25)
    ) <= 2


def test_overlay_stops_when_covered_minimized_or_reduce_motion(qapp) -> None:
    controller = MotionController()
    stack = QStackedWidget()
    stack.resize(900, 500)
    traffic_page = QWidget()
    cover_page = QWidget()
    overlay = DockingTrafficOverlay(
        traffic_page,
        motion_controller=controller,
    )
    overlay.setGeometry(traffic_page.rect())
    overlay.set_reserved_left_px(400)
    stack.addWidget(traffic_page)
    stack.addWidget(cover_page)
    stack.setCurrentWidget(traffic_page)
    stack.show()
    qapp.processEvents()

    try:
        assert overlay.is_animating() is True

        stack.setCurrentWidget(cover_page)
        qapp.processEvents()
        covered_time = overlay.scene_time_ms
        assert overlay.isVisible() is False
        assert overlay.is_animating() is False
        qapp.processEvents()
        assert overlay.scene_time_ms == covered_time

        stack.setCurrentWidget(traffic_page)
        qapp.processEvents()
        assert overlay.is_animating() is True

        stack.showMinimized()
        qapp.processEvents()
        assert stack.isMinimized() is True
        assert overlay.is_animating() is False

        stack.showNormal()
        qapp.processEvents()
        assert overlay.is_animating() is True

        controller.set_reduced_motion(True)
        qapp.processEvents()
        assert overlay.motion_enabled is False
        assert overlay.is_animating() is False

        controller.set_reduced_motion(False)
        qapp.processEvents()
        assert overlay.motion_enabled is True
        assert overlay.is_animating() is True
    finally:
        stack.close()
        stack.deleteLater()


def test_overlay_clips_every_pixel_away_from_the_command_surface(qapp) -> None:
    host = QWidget()
    host.resize(900, 500)
    overlay = DockingTrafficOverlay(host, seed=11)
    overlay.setGeometry(host.rect())
    overlay.set_reserved_left_px(400)
    # Freeze on an active tiny arrival without altering the live traffic model.
    overlay._scene_time_ms = 1_500
    host.show()
    qapp.processEvents()

    try:
        clip = overlay.traffic_rect()
        image = overlay.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
        left_alpha = max(
            image.pixelColor(x, y).alpha()
            for y in range(image.height())
            for x in range(int(clip.left()))
        )
        station_alpha = max(
            image.pixelColor(x, y).alpha()
            for y in range(image.height())
            for x in range(int(clip.left()), image.width())
        )

        assert left_alpha == 0
        assert station_alpha > 0
        assert overlay.testAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        assert overlay.focusPolicy() == Qt.FocusPolicy.NoFocus
    finally:
        host.close()
        host.deleteLater()


def test_home_traffic_disables_itself_when_no_station_side_is_exposed(qapp) -> None:
    from src.pages.home_page import HomePage

    page = HomePage()
    page.resize(756, 568)
    page.show()
    qapp.processEvents()

    try:
        layers = page.layout()
        assert layers.indexOf(page.signal_background) == 0
        assert layers.indexOf(page.traffic_overlay) == 1
        assert layers.currentWidget() is page._foreground
        assert page.signal_background.is_animating() is False
        assert page.traffic_overlay.reserved_left_px >= 744
        assert page.traffic_overlay.is_animating() is False

        page.resize(1_122, 696)
        qapp.processEvents()
        assert page.traffic_overlay.traffic_rect().width() >= 150
        assert page.traffic_overlay.is_animating() is True

        page.set_animations_enabled(False)
        assert page.traffic_overlay.is_animating() is False
        assert page.signal_background.is_animating() is False
    finally:
        page.close()
        page.deleteLater()
