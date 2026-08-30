"""Focused contracts for title-bar music transport and live spectrum UI."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest

from src import i18n
from src.app import MainWindow
from src.audio.backends import MUSIC_SPECTRUM_BANDS
from src.widgets.title_bar import TitleBar, _MusicSpectrum
from src.widgets.ui_translation import retranslate_widget_tree


_NEW_AUDIO_PHRASES = {
    "Live 16-band music spectrum visualization.",
    "Live music spectrum",
    "Launcher music is muted. This control does not affect LYRA voice.",
    "Launcher music soundscape is active.",
    "Launcher music soundscape is off.",
    "Music spectrum is inactive.",
    "Next music track",
    "Play the next launcher music track.",
    "Play the previous launcher music track.",
    "Previous music track",
}


@pytest.fixture(autouse=True)
def reset_language() -> None:
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def _advance_until_settled(spectrum: _MusicSpectrum, limit: int = 300) -> None:
    for _ in range(limit):
        if not spectrum.is_animating():
            return
        spectrum._advance_animation()
    pytest.fail("music spectrum animation did not settle")


def test_transport_buttons_are_accessible_and_emit_for_mouse_and_keyboard(
    qapp,
) -> None:
    title_bar = TitleBar()
    title_bar.resize(1000, TitleBar.HEIGHT)
    title_bar.show()
    qapp.processEvents()
    previous: list[bool] = []
    following: list[bool] = []
    title_bar.previous_music_requested.connect(lambda: previous.append(True))
    title_bar.next_music_requested.connect(lambda: following.append(True))

    assert title_bar.audio_previous_btn.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert title_bar.audio_next_btn.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert title_bar.audio_previous_btn.accessibleName() == "Previous music track"
    assert title_bar.audio_next_btn.accessibleName() == "Next music track"
    assert title_bar.audio_previous_btn.accessibleDescription()
    assert title_bar.audio_next_btn.accessibleDescription()

    title_bar.audio_previous_btn.click()
    title_bar.audio_next_btn.setFocus()
    QTest.keyClick(title_bar.audio_next_btn, Qt.Key.Key_Space)

    assert previous == [True]
    assert following == [True]
    title_bar.close()


def test_spectrum_clamps_truncates_pads_and_inactive_targets_silence(qapp) -> None:
    title_bar = TitleBar()
    title_bar.set_audio_status(True)

    title_bar.set_music_spectrum([2, -1, float("nan"), 0.5])
    assert title_bar.music_spectrum.BAND_COUNT == MUSIC_SPECTRUM_BANDS == 16
    assert title_bar.music_spectrum.target_levels() == (
        1.0,
        0.0,
        0.0,
        0.5,
        *(0.0 for _ in range(12)),
    )

    title_bar.set_music_spectrum(range(40))
    assert len(title_bar.music_spectrum.target_levels()) == 16
    assert title_bar.music_spectrum.target_levels()[:2] == (0.0, 1.0)
    assert title_bar.music_spectrum.target_levels()[-1] == 1.0

    title_bar.set_audio_status(False)
    assert title_bar.music_spectrum.target_levels() == (0.0,) * 16
    title_bar.set_music_spectrum((1.0,) * 16)
    assert title_bar.music_spectrum.target_levels() == (0.0,) * 16
    title_bar.close()


def test_spectrum_frames_are_deterministic_and_attack_precedes_falloff(qapp) -> None:
    first = _MusicSpectrum()
    second = _MusicSpectrum()
    for spectrum in (first, second):
        spectrum.set_active(True)
        spectrum.set_levels((1.0, 0.5) + (0.0,) * 14)
        spectrum._advance_animation()

    assert first.levels() == second.levels()
    attacked = first.levels()[0]
    assert 0.0 < attacked < 1.0

    first.set_levels((0.0,) * 16)
    first._advance_animation()
    assert 0.0 < first.levels()[0] < attacked
    assert first.levels()[0] > attacked * 0.5

    first.set_active(False)
    second.set_active(False)
    first.close()
    second.close()


def test_spectrum_timer_stops_after_stable_nonzero_frame(qapp) -> None:
    spectrum = _MusicSpectrum()
    target = tuple((index + 1) / 16 for index in range(16))
    spectrum.set_active(True)
    spectrum.set_levels(target)

    assert spectrum.is_animating()
    _advance_until_settled(spectrum)

    assert not spectrum.is_animating()
    assert spectrum.levels() == pytest.approx(target, abs=spectrum.SETTLE_EPSILON)
    assert spectrum.peak_levels() == pytest.approx(
        spectrum.levels(),
        abs=spectrum.SETTLE_EPSILON,
    )
    spectrum.close()


def test_spectrum_deactivation_decays_to_exact_zero_and_stops(qapp) -> None:
    spectrum = _MusicSpectrum()
    spectrum.set_active(True)
    spectrum.set_levels((1.0,) * 16)
    _advance_until_settled(spectrum)
    assert any(spectrum.levels())

    spectrum.set_active(False)
    assert spectrum.is_animating()
    _advance_until_settled(spectrum)

    assert not spectrum.is_animating()
    assert spectrum.target_levels() == (0.0,) * 16
    assert spectrum.levels() == (0.0,) * 16
    assert spectrum.peak_levels() == (0.0,) * 16
    spectrum.close()


def test_transport_controls_collapse_before_essential_mute_control(qapp) -> None:
    title_bar = TitleBar()
    title_bar.show()

    for width in (1000, 650):
        title_bar.resize(width, TitleBar.HEIGHT)
        qapp.processEvents()
        assert title_bar.audio_previous_btn.isVisibleTo(title_bar)
        assert title_bar.audio_next_btn.isVisibleTo(title_bar)
        assert title_bar.music_spectrum.isVisibleTo(title_bar)

    title_bar.resize(540, TitleBar.HEIGHT)
    qapp.processEvents()
    assert not title_bar.audio_previous_btn.isVisibleTo(title_bar)
    assert not title_bar.audio_next_btn.isVisibleTo(title_bar)
    assert not title_bar.music_spectrum.isVisibleTo(title_bar)
    assert title_bar.audio_mute_btn.isVisibleTo(title_bar)
    title_bar.close()


def test_japanese_and_russian_live_switch_retranslate_audio_controls(qapp) -> None:
    title_bar = TitleBar()

    i18n.set_language("ja")
    title_bar.retranslate_ui()
    retranslate_widget_tree(title_bar, "ja")
    assert title_bar.audio_previous_btn.accessibleName() == "前の音楽トラック"
    assert title_bar.audio_next_btn.toolTip() == "次の音楽トラック"
    assert title_bar.music_spectrum.accessibleDescription() == (
        "音楽スペクトラムは停止中です。"
    )
    assert title_bar.audio_track_label.accessibleName() == (
        "現在のランチャー音楽：環境音オフ"
    )

    title_bar.set_audio_status(True)
    assert title_bar.audio_track_label.accessibleName() == (
        "現在のランチャー音楽：ステーション環境音"
    )
    assert title_bar.audio_speaker_glyph.accessibleDescription() == (
        "ランチャーの音楽サウンドスケープは再生中です。"
    )
    assert title_bar.audio_speaker_glyph.toolTip() == (
        title_bar.audio_speaker_glyph.accessibleDescription()
    )

    i18n.set_language("ru")
    title_bar.retranslate_ui()
    retranslate_widget_tree(title_bar, "ru")
    assert title_bar.audio_previous_btn.accessibleName() == (
        "Предыдущая музыкальная композиция"
    )
    assert title_bar.audio_next_btn.toolTip() == "Следующая музыкальная композиция"
    assert title_bar.music_spectrum.accessibleDescription() == (
        "Визуализация 16-полосного музыкального спектра в реальном времени."
    )
    assert "STATION SOUNDSCAPE" not in title_bar.audio_track_label.accessibleName()
    assert i18n.translate_ui_phrase(
        "STATION SOUNDSCAPE",
        "ru",
    ) in title_bar.audio_track_label.accessibleName()

    title_bar.set_music_muted(True)
    assert title_bar.audio_speaker_glyph.accessibleDescription() == (
        "Музыка лаунчера отключена. Этот элемент управления не влияет на голос LYRA."
    )
    assert title_bar.audio_speaker_glyph.toolTip() == (
        title_bar.audio_speaker_glyph.accessibleDescription()
    )
    title_bar.close()


def test_new_audio_phrases_are_complete_in_every_language_catalog() -> None:
    assert not any(i18n.missing_ui_phrase_translations().values())
    for option in i18n.LANGUAGES:
        for source in _NEW_AUDIO_PHRASES:
            translated = i18n.translate_ui_phrase(source, option.code)
            assert translated
            if option.code != "en":
                assert translated != source, (option.code, source)


class _RecordingAudioController(QObject):
    music_spectrum_changed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.previous_calls = 0
        self.next_calls = 0

    def previous_music(self) -> bool:
        self.previous_calls += 1
        return True

    def next_music(self) -> bool:
        self.next_calls += 1
        return True


def test_main_window_wiring_routes_spectrum_and_navigation_exactly_once(qapp) -> None:
    controller = _RecordingAudioController()
    title_bar = TitleBar()
    title_bar.set_audio_status(True)
    holder = SimpleNamespace(
        _audio_controller=controller,
        _title_bar=title_bar,
        _title_bar_music_controls_wired=False,
    )

    MainWindow._wire_title_bar_music_controls(holder)
    MainWindow._wire_title_bar_music_controls(holder)
    controller.music_spectrum_changed.emit((0.25,) * 16)
    title_bar.audio_previous_btn.click()
    title_bar.audio_next_btn.click()

    assert title_bar.music_spectrum.target_levels() == (0.25,) * 16
    assert controller.previous_calls == 1
    assert controller.next_calls == 1
    title_bar.close()
