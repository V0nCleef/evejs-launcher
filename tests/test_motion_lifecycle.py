"""Motion work must stop without altering the action or audio state."""
from PyQt6.QtCore import QAbstractAnimation, QPropertyAnimation
from PyQt6.QtWidgets import QWidget, QVBoxLayout
from src.widgets.skeleton_card import SkeletonCard
from src.widgets.update_button import UpdateButton
from src.widgets.toggle_switch import ToggleSwitch
from src.widgets.title_bar import _MusicSpectrum
from src.ui.page_reveal import PageReveal
from src.widgets.console_panel import ConsolePanel
from src.ui.motion import MotionController
from src.ui.visible_motion import VisibleMotion


def test_controller_and_visibility_gate_share_window_preference(qapp):
    host = QWidget()
    child = QWidget(host)
    controller = MotionController(parent=child)
    gate = VisibleMotion(child, lambda _allowed: None)
    host.show()
    qapp.processEvents()
    assert controller.animations_enabled and gate.allowed
    host.setProperty("animationsEnabled", False)
    assert controller.reduced_motion and controller.duration(100) == 0
    assert not gate.allowed
    host.setProperty("animationsEnabled", True)
    assert controller.duration(100) == 100 and gate.allowed
    controller.set_reduced_motion(True)
    assert not controller.animations_enabled
    host.close()


def test_decorations_pause_for_hidden_minimized_and_reduced_motion(qapp):
    host = QWidget()
    layout = QVBoxLayout(host)
    skeleton = SkeletonCard()
    update = UpdateButton()
    spectrum = _MusicSpectrum()
    toggle = ToggleSwitch()
    for widget in (skeleton, update, spectrum, toggle):
        layout.addWidget(widget)
    update.set_update_available("1.0.53")
    assert skeleton._pulse.state() == QAbstractAnimation.State.Stopped
    assert update._pulse.state() == QAbstractAnimation.State.Stopped
    host.show()
    qapp.processEvents()
    spectrum.set_active(True)
    spectrum.set_levels([.7]*16)
    assert spectrum.is_animating()
    assert update._pulse.state() == QAbstractAnimation.State.Running
    assert skeleton._pulse.state() == QAbstractAnimation.State.Running
    try:
        for pause, resume in (
            (host.hide, host.show),
            (host.showMinimized, host.showNormal),
            (lambda: host.setProperty("animationsEnabled", False),
             lambda: host.setProperty("animationsEnabled", True)),
        ):
            pause()
            qapp.processEvents()
            assert not spectrum.is_animating()
            assert skeleton._pulse.state() == QAbstractAnimation.State.Stopped
            assert update._pulse.state() == QAbstractAnimation.State.Stopped
            toggle.setChecked(not toggle.isChecked())
            assert toggle._get_thumb_pos() == float(toggle.isChecked())
            assert spectrum.is_active()  # visual preference never stops music
            resume()
            qapp.processEvents()
            assert skeleton._pulse.state() == QAbstractAnimation.State.Running
        for _ in range(8):
            update.set_update_available("1.0.53")
        assert len(update.findChildren(QPropertyAnimation)) == 1
    finally:
        host.close()
        host.deleteLater()


def test_repeated_page_reveal_clears_prior_effect_and_reduced_motion(qapp):
    host = QWidget()
    a, b = QWidget(host), QWidget(host)
    host.show()
    qapp.processEvents()
    reveal = PageReveal(host)
    try:
        reveal.start(a)
        reveal.start(b)
        assert a.graphicsEffect() is None
        assert b.graphicsEffect() is not None
        reveal.finish()
        assert b.graphicsEffect() is None
        host.setProperty("animationsEnabled", False)
        reveal.start(a)
        assert a.graphicsEffect() is None
    finally:
        host.close()


def test_console_preserves_reading_position_and_can_return_to_live(qapp):
    host = QWidget()
    host.resize(900, 700)
    panel = ConsolePanel(host)
    host.show()
    panel.begin_stream("Test stream")
    panel._append_lines([f"line {i}" for i in range(200)])
    qapp.processEvents()
    bar = panel._log.verticalScrollBar()
    try:
        assert bar.value() == bar.maximum()
        bar.setValue(30)
        panel._append_lines(["new output"])
        assert bar.value() == 30
        assert panel._live_btn.isVisible()
        panel._live_btn.click()
        assert bar.value() == bar.maximum()
        panel._append_lines(["following again"])
        assert bar.value() == bar.maximum()
    finally:
        host.close()
