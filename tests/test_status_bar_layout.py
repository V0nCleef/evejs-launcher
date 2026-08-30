"""Geometry regressions for the launcher footer."""
from __future__ import annotations

import pytest

from PyQt6.QtCore import QAbstractAnimation, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.constants import APP_VERSION, COLORS
from src.core.service_status import ServiceState
from src.i18n import LANGUAGES, set_language
from src.theme import build_qss
from src.widgets.nav_panel import NavPanel
from src.widgets.status_bar import StatusBar


def test_footer_keeps_a_safe_baseline_and_intentional_text_fit_at_minimum_width(
    qapp: QApplication,
) -> None:
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})
    )
    bar = StatusBar()
    bar.resize(1000, StatusBar.HEIGHT)
    bar.set_server_state(ServiceState.ONLINE, pid=12345)
    bar.set_market_state(ServiceState.ONLINE, pid=67890)
    bar.set_client_count(12)
    bar.show()
    qapp.processEvents()

    try:
        assert bar.width() == 1000
        assert bar.height() == StatusBar.HEIGHT

        for section in (
            bar.server_section,
            bar.market_section,
            bar.clients_section,
        ):
            label = section.label
            assert label.height() >= label.fontMetrics().height() + 2
            assert label.geometry().right() <= section.contentsRect().right()
            assert label.text()
            assert (
                label.text() == label.toolTip()
                or (label.text().endswith("…") and label.toolTip().startswith(label.text()[:-1]))
            )

        assert bar.version_label.height() >= bar.version_label.fontMetrics().height() + 2
    finally:
        bar.close()
        bar.deleteLater()
        qapp.setStyleSheet(original_style)


def test_language_selector_lives_in_footer_and_retranslates_live(
    qapp: QApplication,
) -> None:
    set_language("en")
    bar = StatusBar()
    bar.resize(1000, StatusBar.HEIGHT)
    selected: list[str] = []
    bar.language_changed.connect(selected.append)
    bar.show()
    qapp.processEvents()

    try:
        combo = bar.language_combo
        assert [
            combo.itemData(index)
            for index in range(combo.count())
        ] == [option.code for option in LANGUAGES]
        assert [
            combo.itemText(index)
            for index in range(combo.count())
        ] == [
            "English",
            "简体中文",
            "日本語",
            "한국어",
            "Français",
            "Deutsch",
            "Nederlands",
            "Русский",
        ]
        assert all(
            not combo.itemIcon(index).isNull()
            for index in range(combo.count())
        )
        assert combo.height() == 28
        assert combo.geometry().left() < 220
        assert combo.geometry().right() < bar.server_section.geometry().left()
        assert combo.geometry().top() >= 0
        assert combo.geometry().bottom() < bar.height()

        combo.setCurrentIndex(combo.findData("zh_CN"))
        qapp.processEvents()

        assert selected == ["zh_CN"]
        assert combo.accessibleName() == "启动器语言"
        assert combo.toolTip() == "启动器语言"
    finally:
        set_language("en")
        bar.close()
        bar.deleteLater()


def test_footer_semantic_states_keep_logical_text_and_pulse_lifecycle(
    qapp: QApplication,
) -> None:
    bar = StatusBar()

    try:
        assert bar.objectName() == "statusBar"
        assert bar.height() == StatusBar.HEIGHT
        assert bar.build_label.text() == "BUILD //"
        assert bar.version_label.text() == f"v{APP_VERSION}"

        bar.set_server_state(ServiceState.STARTING)
        assert bar.server_section.property("telemetryState") == "warning"
        assert COLORS["gold"] in bar.server_section.dot.styleSheet()
        assert bar.server_section.label.text() == "Server: Starting..."
        assert bar.server_section.label.display_text() == "SERVER  •  STARTING..."
        assert bar.server_section._pulse.state() == QAbstractAnimation.State.Running

        bar.set_server_state(ServiceState.ONLINE, pid=12345)
        assert bar.server_section.property("telemetryState") == "online"
        assert COLORS["green"] in bar.server_section.dot.styleSheet()
        assert bar.server_section.label.text() == "Server: Online (PID 12345)"
        assert bar.server_section.label.display_text() == "SERVER  •  ONLINE"
        assert "PID 12345" in bar.server_section.label.toolTip()
        assert "PID 12345" in bar.server_section.accessibleDescription()
        assert bar.server_section._pulse.state() == QAbstractAnimation.State.Stopped

        bar.set_server_state(ServiceState.FAILED, error="boom")
        assert bar.server_section.property("telemetryState") == "danger"
        assert COLORS["red"] in bar.server_section.dot.styleSheet()
        assert bar.server_section.label.text() == "Server: Failed"
        assert bar.server_section.label.toolTip() == "boom"

        bar.set_client_count(3)
        assert bar.clients_section.property("telemetryState") == "online"
        assert bar.clients_section.label.text() == "3 clients running"
        bar.set_client_count(0)
        assert bar.clients_section.property("telemetryState") == "offline"
    finally:
        bar.close()
        bar.deleteLater()


def test_footer_reduce_motion_settles_and_restores_requested_pulses(
    qapp: QApplication,
) -> None:
    bar = StatusBar()
    try:
        bar.set_server_state(ServiceState.STARTING)
        bar.set_market_state(ServiceState.STOPPING)
        assert bar.server_section.is_animating() is True
        assert bar.market_section.is_animating() is True

        bar.set_animations_enabled(False)
        assert bar.animations_enabled is False
        assert bar.server_section.is_animating() is False
        assert bar.market_section.is_animating() is False
        assert bar.server_section._opacity_effect.opacity() == 1.0
        assert bar.market_section._opacity_effect.opacity() == 1.0

        # Semantic state remains transient, so opting back into motion resumes
        # the existing request without requiring another runtime observation.
        bar.set_animations_enabled(True)
        assert bar.animations_enabled is True
        assert bar.server_section.is_animating() is True
        assert bar.market_section.is_animating() is True
    finally:
        bar.close()
        bar.deleteLater()


def test_footer_child_click_still_forwards_console_signal(qapp: QApplication) -> None:
    bar = StatusBar()
    toggled: list[str] = []
    bar.console_toggled.connect(toggled.append)
    bar.resize(1000, StatusBar.HEIGHT)
    bar.show()
    qapp.processEvents()

    try:
        QTest.mouseClick(bar.server_section.label, Qt.MouseButton.LeftButton)
        QTest.mouseClick(bar.market_section.dot, Qt.MouseButton.LeftButton)
        assert toggled == ["server", "market"]
    finally:
        bar.close()
        bar.deleteLater()


@pytest.mark.parametrize("window_size", ((1366, 768), (1000, 640)))
def test_footer_owns_the_full_bottom_edge_without_a_painted_shoulder(
    qapp: QApplication,
    window_size: tuple[int, int],
) -> None:
    """The nav ends above one continuous, footer-owned background.

    A layout spacer can reserve the quiet area before Server telemetry without
    inheriting the application's opaque ``QWidget`` background and painting a
    second dark rectangle over the footer gradient.
    """
    width, height = window_size
    shell = QWidget()
    root = QVBoxLayout(shell)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    title_placeholder = QLabel()
    title_placeholder.setFixedHeight(36)
    root.addWidget(title_placeholder)

    content = QHBoxLayout()
    content.setContentsMargins(0, 0, 0, 0)
    content.setSpacing(0)
    nav = NavPanel()
    content.addWidget(nav)
    content.addWidget(QWidget(), stretch=1)
    root.addLayout(content, stretch=1)

    bar = StatusBar()
    root.addWidget(bar)
    shell.resize(width, height)
    shell.show()
    qapp.processEvents()

    try:
        assert bar.geometry().getRect() == (
            0,
            height - StatusBar.HEIGHT,
            width,
            StatusBar.HEIGHT,
        )
        assert nav.geometry().bottom() + 1 == bar.geometry().top()
        assert nav.geometry().right() + 1 == 220

        language_item = bar.layout().itemAt(0)
        assert language_item.widget() is bar.language_combo
        assert bar.language_combo.geometry().left() < 220
        assert bar.language_combo.geometry().right() < (
            bar.server_section.geometry().left()
        )
        assert bar.server_section.geometry().left() >= 220

        for section in (
            bar.server_section,
            bar.market_section,
            bar.clients_section,
        ):
            assert section.geometry().top() == 1
            assert section.geometry().bottom() == StatusBar.HEIGHT - 1
            assert section.geometry().left() >= 0
            assert section.geometry().right() < width
    finally:
        shell.close()
        shell.deleteLater()
