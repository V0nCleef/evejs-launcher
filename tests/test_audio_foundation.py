"""Focused coverage for the optional audio/LYRA foundation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from src import config
from src.app import MainWindow
from src.audio.assets import (
    audio_assets_root,
    bundled_music_tracks,
    bundled_voice_clips,
    missing_voice_lines,
    voice_catalog_ready,
)
from src.audio import backends
from src.audio.backends import MusicBackend, SpeechBackend
from src.audio.controller import AudioController
from src.audio.events import VOICE_LINE_TEXT, VoiceEvent, VoiceLine, render_announcement
from src.audio.settings import AudioSettings
from src.pages.settings_page import SettingsPage
from src.widgets.title_bar import TitleBar


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def _write_complete_voice_catalog(root: Path) -> Path:
    """Create a tiny provenance-matched catalog for backend-isolation tests."""
    root.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, str]] = {}
    for line in VoiceLine:
        path = root / line.filename
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(22_050)
            stream.writeframes(b"\x00\x00")
        entries[line.value] = {
            "text": line.text,
            "filename": line.filename,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "clips": entries}),
        encoding="utf-8",
    )
    return root


def test_audio_config_defaults_and_deep_signal_flag(isolated_config: Path) -> None:
    loaded = config.load()

    assert loaded["deep_signal_enabled"] is True
    assert loaded["audio_master_muted"] is False
    assert loaded["audio_music_muted"] is False
    assert loaded["audio_music_enabled"] is True
    assert loaded["audio_music_volume"] == 50
    assert loaded["audio_music_library"] == []
    assert "audio_ui_sounds_enabled" not in loaded
    assert "audio_ui_sounds_volume" not in loaded
    assert loaded["audio_voice_enabled"] is True
    assert loaded["audio_voice_volume"] == 100
    assert loaded["audio_voice_engine"] == ""
    assert loaded["audio_voice_locale"] == ""
    assert loaded["audio_voice_name"] == ""
    assert loaded["audio_voice_rate"] == 0.0
    assert loaded["audio_voice_pitch"] == 0.0
    assert loaded["audio_announce_character_names"] is True
    assert loaded["audio_announce_results"] is True
    assert loaded["audio_ducking_enabled"] is True
    assert loaded["audio_ducking_level"] == 100


def test_audio_settings_default_music_voice_and_explicit_ducking() -> None:
    assert not hasattr(AudioSettings(), "ui_sounds_enabled")
    assert not hasattr(AudioSettings(), "ui_sounds_volume")
    assert AudioSettings().music_volume == 50
    assert AudioSettings.from_mapping({}).music_volume == 50
    assert (
        AudioSettings.from_mapping({"audio_music_volume": "loud"}).music_volume
        == 50
    )
    assert AudioSettings().voice_volume == 100
    assert AudioSettings.from_mapping({}).voice_volume == 100
    assert (
        AudioSettings.from_mapping({"audio_voice_volume": None}).voice_volume
        == 100
    )
    assert AudioSettings().ducking_level == 100
    assert AudioSettings.from_mapping({}).ducking_level == 100
    assert AudioSettings.from_mapping({"audio_ducking_level": 20}).ducking_level == 20


def test_invalid_persisted_volume_types_use_product_defaults(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {
                "audio_music_volume": "loud",
                "audio_voice_volume": None,
            }
        ),
        encoding="utf-8",
    )

    loaded = config.load()

    assert loaded["audio_music_volume"] == 50
    assert loaded["audio_voice_volume"] == 100
    assert loaded["audio_ducking_level"] == 100


def test_audio_config_migrates_aliases_and_clamps_invalid_values(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {
                "master_muted": True,
                "music_volume": 151.2,
                "ui_sounds_enabled": False,
                "ui_sounds_volume": 44.6,
                "audio_ui_sounds_enabled": True,
                "audio_ui_sounds_volume": 12,
                "voice_volume": -4,
                "tts_engine": "  sapi  ",
                "tts_locale": "  en-US ",
                "tts_voice": "  Local Voice  ",
                "tts_rate": 3,
                "tts_pitch": -2,
                "voice_announce_names": False,
                "voice_announce_results": "yes",
                "voice_duck_music": False,
                "voice_ducking_percent": 25.6,
                "deep_signal_enabled": "yes",
            }
        ),
        encoding="utf-8",
    )

    loaded = config.load()

    assert loaded["audio_master_muted"] is False
    assert loaded["audio_music_muted"] is True
    assert loaded["audio_music_volume"] == 100
    assert "audio_ui_sounds_enabled" not in loaded
    assert "audio_ui_sounds_volume" not in loaded
    assert loaded["audio_voice_volume"] == 0
    assert loaded["audio_voice_engine"] == "sapi"
    assert loaded["audio_voice_locale"] == "en-US"
    assert loaded["audio_voice_name"] == "Local Voice"
    assert loaded["audio_voice_rate"] == 1.0
    assert loaded["audio_voice_pitch"] == -1.0
    assert loaded["audio_announce_character_names"] is False
    assert loaded["audio_announce_results"] is True
    assert loaded["audio_ducking_enabled"] is False
    assert loaded["audio_ducking_level"] == 26
    assert loaded["deep_signal_enabled"] is True


def test_legacy_master_mute_becomes_music_only_and_does_not_disable_lyra(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    isolated_config.write_text(
        json.dumps({"audio_master_muted": True}),
        encoding="utf-8",
    )

    loaded = config.load()
    assert loaded["audio_master_muted"] is False
    assert loaded["audio_music_muted"] is True

    voice_root = tmp_path / "voice"
    _write_complete_voice_catalog(voice_root)
    speech = _RecordingSpeech(None)
    controller = AudioController(
        loaded,
        voice_root=voice_root,
        speech_factory=lambda *_args: speech,
    )

    assert controller.music_muted is True
    assert controller.master_muted is False
    assert controller.preview_voice() is True
    assert [path.name for path in speech.played] == ["preview.wav"]
    for legacy_key in (
        "master_muted",
        "music_volume",
        "ui_sounds_enabled",
        "ui_sounds_volume",
        "audio_ui_sounds_enabled",
        "audio_ui_sounds_volume",
        "voice_volume",
        "tts_engine",
        "tts_locale",
        "tts_voice",
        "tts_rate",
        "tts_pitch",
        "voice_announce_names",
        "voice_announce_results",
        "voice_duck_music",
        "voice_ducking_percent",
    ):
        assert legacy_key not in loaded


def test_title_bar_mute_is_visible_accessible_and_emits_once(qapp) -> None:
    title_bar = TitleBar()
    observed: list[bool] = []
    title_bar.music_mute_changed.connect(observed.append)

    assert title_bar.audio_mute_btn.isVisibleTo(title_bar)
    assert title_bar.audio_mute_btn.isCheckable()
    assert title_bar.audio_mute_btn.accessibleName() == "Mute launcher music"
    assert title_bar.audio_mute_btn.toolTip() == "Mute launcher music"
    assert title_bar.is_music_muted() is False

    title_bar.audio_mute_btn.click()

    assert observed == [True]
    assert title_bar.is_music_muted() is True
    assert title_bar.audio_mute_btn.text() == "MUTED"
    assert title_bar.audio_mute_btn.accessibleName() == "Unmute launcher music"
    assert "does not affect lyra voice" in (
        title_bar.audio_mute_btn.accessibleDescription().lower()
    )

    title_bar.set_music_muted(False)
    assert observed == [True]
    assert title_bar.audio_mute_btn.text() == "MUTE"


def test_title_bar_audio_capsule_is_truthful_and_concept_sized(qapp) -> None:
    title_bar = TitleBar()
    title_bar.show()
    title_bar.resize(1366, TitleBar.HEIGHT)
    qapp.processEvents()

    assert title_bar.height() == 36
    assert title_bar.audio_capsule.width() == 410
    assert title_bar.audio_capsule.height() == 30
    assert title_bar.audio_note_label.isVisibleTo(title_bar)
    assert title_bar.audio_track_label.isVisibleTo(title_bar)
    assert title_bar.audio_waveform.isVisibleTo(title_bar)
    assert title_bar.audio_speaker_glyph.isVisibleTo(title_bar)
    assert title_bar.audio_track_label.text() == "SOUNDSCAPE OFF"
    assert title_bar.is_audio_active() is False
    assert title_bar.audio_waveform.is_active() is False
    assert "soundscape is off" in title_bar.audio_capsule.accessibleDescription().lower()

    title_bar.set_audio_status(True)

    assert title_bar.audio_track_label.text() == "STATION SOUNDSCAPE"
    assert title_bar.is_audio_active() is True
    assert title_bar.audio_waveform.is_active() is True
    assert "playing station soundscape" in (
        title_bar.audio_capsule.accessibleDescription().lower()
    )

    title_bar.set_audio_status(
        True,
        "Red Glowing Dust (Jukebox) With A Deliberately Long Track Title",
    )
    assert title_bar.audio_track_label.toolTip() == (
        "RED GLOWING DUST (JUKEBOX) WITH A DELIBERATELY LONG TRACK TITLE"
    )
    assert title_bar.audio_track_label.accessibleName().endswith(
        title_bar.audio_track_label.toolTip()
    )
    assert "…" in title_bar.audio_track_label.text()
    assert title_bar.audio_track_label.text() != title_bar.audio_track_label.toolTip()

    title_bar.set_audio_status(False, "Unwired placeholder")
    assert title_bar.audio_track_label.text() == "SOUNDSCAPE OFF"
    assert title_bar.audio_waveform.is_active() is False
    title_bar.close()


def test_title_bar_audio_capsule_collapses_before_mute_control(qapp) -> None:
    title_bar = TitleBar()
    title_bar.show()

    title_bar.resize(800, TitleBar.HEIGHT)
    qapp.processEvents()
    assert title_bar.audio_capsule.width() == 282
    assert not title_bar.audio_note_label.isVisibleTo(title_bar)
    assert title_bar.audio_track_label.isVisibleTo(title_bar)
    assert title_bar.audio_waveform.isVisibleTo(title_bar)
    assert title_bar.audio_speaker_glyph.isVisibleTo(title_bar)

    title_bar.resize(650, TitleBar.HEIGHT)
    qapp.processEvents()
    assert title_bar.audio_capsule.width() == 206
    assert not title_bar.audio_track_label.isVisibleTo(title_bar)
    assert title_bar.audio_waveform.isVisibleTo(title_bar)
    assert title_bar.audio_speaker_glyph.isVisibleTo(title_bar)

    title_bar.resize(540, TitleBar.HEIGHT)
    qapp.processEvents()
    assert title_bar.audio_capsule.width() == 124
    assert not title_bar.audio_waveform.isVisibleTo(title_bar)
    assert title_bar.audio_speaker_glyph.isVisibleTo(title_bar)

    title_bar.resize(480, TitleBar.HEIGHT)
    qapp.processEvents()
    assert title_bar.audio_capsule.width() == 88
    assert not title_bar.audio_speaker_glyph.isVisibleTo(title_bar)
    assert title_bar.audio_mute_btn.isVisibleTo(title_bar)
    assert title_bar.audio_mute_btn.width() == 78
    title_bar.close()


def test_title_bar_audio_glyph_tracks_music_only_mute(qapp) -> None:
    title_bar = TitleBar()

    assert title_bar.audio_speaker_glyph.is_muted() is False
    title_bar.set_music_muted(True)
    assert title_bar.audio_speaker_glyph.is_muted() is True
    description = title_bar.audio_speaker_glyph.accessibleDescription().lower()
    assert "music is muted" in description
    assert "does not affect lyra voice" in description

    title_bar.set_music_muted(False)
    assert title_bar.audio_speaker_glyph.is_muted() is False
    assert "off" in title_bar.audio_speaker_glyph.accessibleDescription().lower()


def test_audio_controller_is_lazy_and_unavailable_backend_stays_silent(qapp) -> None:
    calls = {"music": 0, "speech": 0}

    def music_factory(_parent):
        calls["music"] += 1
        return MusicBackend()

    def speech_factory(_settings, parent):
        calls["speech"] += 1
        return SpeechBackend(parent)

    controller = AudioController(
        {}, music_factory=music_factory, speech_factory=speech_factory
    )

    assert calls == {"music": 0, "speech": 0}
    assert controller.start_music() is False
    assert calls == {"music": 1, "speech": 0}
    controller.set_master_muted(True)
    assert calls == {"music": 1, "speech": 0}


def test_transient_unavailable_voice_backend_is_retried(
    qapp,
    tmp_path: Path,
) -> None:
    calls = 0
    ready = _RecordingSpeech(None)

    def speech_factory(_settings, parent):
        nonlocal calls
        calls += 1
        return SpeechBackend(parent) if calls == 1 else ready

    controller = AudioController(
        {},
        speech_factory=speech_factory,
        voice_root=_write_complete_voice_catalog(tmp_path / "voice"),
    )

    assert controller.prepare_voice_preview() is False
    assert controller._speech_backend is None
    assert controller.prepare_voice_preview() is True
    assert controller._speech_backend is ready
    assert calls == 2


def test_backend_factories_degrade_to_no_op_when_optional_qt_modules_are_missing(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends, "MULTIMEDIA_SUPPORTED", False)
    monkeypatch.setattr(backends, "VOICE_PLAYBACK_SUPPORTED", False)

    music = backends.create_music_backend(qapp)
    speech = backends.create_speech_backend(
        AudioController({}).settings,
        qapp,
    )

    assert music.available is False
    assert speech.available is False
    assert music.play() is False
    assert speech.say("Dynamic speech must remain disabled") is False


def test_fixed_voice_backend_never_exposes_dynamic_speech(qapp) -> None:
    backend = backends.create_speech_backend(AudioController({}).settings, qapp)

    assert backend.say("Speak an arbitrary private label") is False
    assert "QTextToSpeech" not in vars(backends)


class _RecordingMusic(MusicBackend):
    available = True

    def __init__(self) -> None:
        self.source: Path | None = None
        self.volumes: list[int] = []
        self.muted: list[bool] = []
        self.play_count = 0
        self.stop_count = 0

    def set_source(self, path: Path) -> bool:
        self.source = path
        return True

    def set_volume(self, percent: int) -> None:
        self.volumes.append(percent)

    def set_muted(self, muted: bool) -> None:
        self.muted.append(muted)

    def play(self) -> bool:
        self.play_count += 1
        return True

    def stop(self) -> None:
        self.stop_count += 1


class _AsyncRecordingMusic(_RecordingMusic):
    """Backend double whose play request completes through a later callback."""

    def __init__(self) -> None:
        super().__init__()
        self._state_callback = None

    def set_playback_state_callback(self, callback) -> bool:
        self._state_callback = callback
        callback(False)
        return True

    def report_playback(self, active: bool) -> None:
        assert self._state_callback is not None
        self._state_callback(active)


class _RecordingSpeech(SpeechBackend):
    available = True

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.played: list[Path] = []
        self.stop_count = 0

    def play_clip(self, path: Path) -> bool:
        self.played.append(path)
        self.clip_started.emit(path)
        return True

    def stop(self) -> None:
        self.stop_count += 1


class _DeferredRecordingSpeech(SpeechBackend):
    """FIFO double that publishes clip starts only when explicitly advanced."""

    available = True

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.pending: list[Path] = []
        self.started: list[Path] = []
        self.stop_count = 0
        self.speaking = False

    def play_clip(self, path: Path) -> bool:
        self.pending.append(path)
        return True

    def start_next(self) -> Path:
        path = self.pending.pop(0)
        self.started.append(path)
        if not self.speaking:
            self.speaking = True
            self.speaking_changed.emit(True)
        self.clip_started.emit(path)
        return path

    def finish_current(self) -> None:
        if not self.pending:
            self.speaking = False
            self.speaking_changed.emit(False)

    def stop(self) -> None:
        self.stop_count += 1
        self.pending.clear()
        if self.speaking:
            self.speaking = False
            self.speaking_changed.emit(False)


def test_controller_captions_ducks_music_and_master_mute_stops_both(
    qapp,
    tmp_path: Path,
) -> None:
    track = tmp_path / "ambient.ogg"
    track.write_bytes(b"fixture")
    music = _RecordingMusic()
    speech = _RecordingSpeech(None)
    voice_root = _write_complete_voice_catalog(tmp_path / "voice")
    controller = AudioController(
        {
            "audio_music_volume": 50,
            "audio_ducking_level": 20,
        },
        music_factory=lambda _parent: music,
        speech_factory=lambda _settings, _parent: speech,
        voice_root=voice_root,
    )
    captions: list[str] = []
    mute_states: list[bool] = []
    controller.caption_requested.connect(captions.append)
    controller.master_muted_changed.connect(mute_states.append)
    playback_states: list[tuple[bool, str]] = []
    controller.music_playback_changed.connect(
        lambda active, name: playback_states.append((active, name))
    )

    assert controller.start_music(track) is True
    assert controller.music_active is True
    assert playback_states == [(False, "ambient"), (True, "ambient")]
    assert music.volumes[-1] == 50
    assert controller.announce(
        VoiceEvent.CHARACTER_LAUNCHING,
        character_name="Pilot Example",
    ) is True
    assert captions == ["Launching selected character."]
    assert [path.name for path in speech.played] == ["character_launching.wav"]

    speech.speaking_changed.emit(True)
    assert music.volumes[-1] == 10
    speech.speaking_changed.emit(False)
    assert music.volumes[-1] == 50

    controller.set_master_muted(True)
    assert mute_states == [True]
    assert controller.music_active is False
    assert playback_states[-1] == (False, "ambient")
    assert music.stop_count == 1
    assert speech.stop_count == 1


def test_controller_voice_fifo_keeps_captions_and_ducking_aligned(
    qapp,
    tmp_path: Path,
) -> None:
    track = tmp_path / "soundscape.wav"
    track.write_bytes(b"fixture")
    music = _RecordingMusic()
    speech = _DeferredRecordingSpeech(None)
    controller = AudioController(
        {
            "audio_music_volume": 50,
            "audio_ducking_enabled": True,
            "audio_ducking_level": 20,
        },
        music_factory=lambda _parent: music,
        speech_factory=lambda _settings, _parent: speech,
        voice_root=_write_complete_voice_catalog(tmp_path / "voice"),
    )
    captions: list[str] = []
    controller.caption_requested.connect(captions.append)

    assert controller.start_music(track) is True
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATING) is True
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATED) is True
    assert captions == []
    assert [path.name for path in speech.pending] == [
        "clients_terminating.wav",
        "clients_terminated.wav",
    ]

    assert speech.start_next().name == "clients_terminating.wav"
    assert captions == ["Terminating all clients."]
    assert music.volumes[-1] == 10

    # The second caption appears only as the queued result begins, and the
    # soundscape never restores between the two clips.
    volume_events = len(music.volumes)
    assert speech.start_next().name == "clients_terminated.wav"
    assert captions == ["Terminating all clients.", "All clients terminated."]
    assert len(music.volumes) == volume_events
    assert music.volumes[-1] == 10

    speech.finish_current()
    assert music.volumes[-1] == 50


def test_controller_preview_interrupts_and_shutdown_clears_queued_voice(
    qapp,
    tmp_path: Path,
) -> None:
    speech = _DeferredRecordingSpeech(None)
    controller = AudioController(
        {},
        speech_factory=lambda _settings, _parent: speech,
        voice_root=_write_complete_voice_catalog(tmp_path / "voice"),
    )
    captions: list[str] = []
    controller.caption_requested.connect(captions.append)

    assert controller.announce(VoiceEvent.CLIENTS_TERMINATING) is True
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATED) is True
    assert controller.preview_voice() is True
    assert speech.stop_count == 1
    assert [path.name for path in speech.pending] == ["preview.wav"]

    speech.start_next()
    assert captions == ["LYRA online. Shipboard systems ready."]
    controller.shutdown()
    assert speech.stop_count == 2
    assert speech.pending == []


def test_controller_waits_for_async_playback_and_ignores_stale_resume_state(
    qapp,
    tmp_path: Path,
) -> None:
    track = tmp_path / "ambient.wav"
    track.write_bytes(b"fixture")
    music = _AsyncRecordingMusic()
    controller = AudioController({}, music_factory=lambda _parent: music)
    playback_states: list[tuple[bool, str]] = []
    controller.music_playback_changed.connect(
        lambda active, name: playback_states.append((active, name))
    )

    assert controller.start_music(track) is True
    assert music.play_count == 1
    assert controller.music_active is False
    assert playback_states == [(False, "ambient")]

    music.report_playback(True)
    assert controller.music_active is True
    assert playback_states[-1] == (True, "ambient")

    music.report_playback(False)
    assert controller.music_active is False
    assert playback_states[-1] == (False, "ambient")

    controller.set_master_muted(True)
    music.report_playback(True)
    assert controller.music_active is False

    controller.set_master_muted(False)
    assert music.play_count == 2
    assert controller.music_active is False
    music.report_playback(True)
    assert controller.music_active is True

    controller.apply_settings({"audio_music_enabled": False})
    assert controller.music_active is False
    music.report_playback(True)
    assert controller.music_active is False

    controller.apply_settings({"audio_music_enabled": True})
    assert music.play_count == 3
    assert controller.music_active is False
    music.report_playback(True)
    assert controller.music_active is True

    controller.stop_music()
    music.report_playback(True)
    assert controller.music_active is False


def test_controller_invalid_replacement_stops_previously_active_music(
    qapp,
    tmp_path: Path,
) -> None:
    track = tmp_path / "ambient.wav"
    track.write_bytes(b"fixture")
    music = _RecordingMusic()
    controller = AudioController({}, music_factory=lambda _parent: music)

    assert controller.start_music(track) is True
    assert controller.music_active is True
    assert controller.start_music(tmp_path / "missing.wav") is False
    assert controller.music_active is False
    assert music.stop_count == 1
    assert controller.music_track_name == "STATION SOUNDSCAPE"


def test_qt_music_backend_reports_player_state_media_status_and_errors(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self._slots = []

        def connect(self, slot) -> None:
            self._slots.append(slot)

        def emit(self, *args) -> None:
            for slot in tuple(self._slots):
                slot(*args)

    class FakeAudioOutput:
        def __init__(self, _parent) -> None:
            self.volume = 1.0
            self.muted = False

        def setVolume(self, value: float) -> None:
            self.volume = value

        def setMuted(self, value: bool) -> None:
            self.muted = value

    class FakePlaybackState:
        StoppedState = "stopped"
        PlayingState = "playing"

    class FakeMediaStatus:
        NoMedia = "none"
        LoadingMedia = "loading"
        LoadedMedia = "loaded"
        BufferingMedia = "buffering"
        BufferedMedia = "buffered"
        EndOfMedia = "ended"
        InvalidMedia = "invalid"

    class FakeError:
        NoError = "no-error"
        ResourceError = "resource-error"

    class FakeLoops:
        Once = "once"
        Infinite = -1

    class FakePlayer:
        PlaybackState = FakePlaybackState
        MediaStatus = FakeMediaStatus
        Error = FakeError
        Loops = FakeLoops
        last = None

        def __init__(self, _parent) -> None:
            type(self).last = self
            self.playbackStateChanged = FakeSignal()
            self.mediaStatusChanged = FakeSignal()
            self.errorOccurred = FakeSignal()
            self.errorChanged = FakeSignal()
            self._state = FakePlaybackState.StoppedState
            self._status = FakeMediaStatus.NoMedia
            self._error = FakeError.NoError
            self.loops = None

        def setAudioOutput(self, _output) -> None:
            pass

        def setLoops(self, loops) -> None:
            self.loops = loops

        def setSource(self, source) -> None:
            self._error = FakeError.NoError
            self._status = (
                FakeMediaStatus.NoMedia
                if source.isEmpty()
                else FakeMediaStatus.LoadingMedia
            )
            self.mediaStatusChanged.emit(self._status)

        def playbackState(self):
            return self._state

        def mediaStatus(self):
            return self._status

        def error(self):
            return self._error

        def play(self) -> None:
            self._state = FakePlaybackState.PlayingState
            self.playbackStateChanged.emit(self._state)

        def stop(self) -> None:
            self._state = FakePlaybackState.StoppedState
            self.playbackStateChanged.emit(self._state)

        def set_status(self, status) -> None:
            self._status = status
            self.mediaStatusChanged.emit(status)

        def fail(self) -> None:
            self._error = FakeError.ResourceError
            self.errorOccurred.emit(self._error, "fixture failure")

    monkeypatch.setattr(backends, "QAudioOutput", FakeAudioOutput)
    monkeypatch.setattr(backends, "QMediaPlayer", FakePlayer)
    monkeypatch.setattr(backends, "MULTIMEDIA_SUPPORTED", True)

    track = tmp_path / "ambient.wav"
    track.write_bytes(b"fixture")
    backend = backends.QtMusicBackend(qapp)
    assert FakePlayer.last is not None
    assert FakePlayer.last.loops == FakeLoops.Once
    observed: list[bool] = []
    finished: list[str] = []
    failures: list[str] = []
    assert backend.set_playback_state_callback(observed.append) is True
    assert backend.set_track_finished_callback(lambda: finished.append("end")) is True
    assert backend.set_track_failed_callback(lambda: failures.append("failed")) is True
    assert observed == [False]

    assert backend.set_source(track) is True
    assert backend.play() is True
    assert observed == [False]  # PlayingState alone is not decoded audio.

    player = FakePlayer.last
    assert player is not None
    player.set_status(FakeMediaStatus.LoadedMedia)
    assert observed[-1] is True

    player.fail()
    assert observed[-1] is False
    # Failure recovery is deliberately queued outside the Qt signal stack.
    assert failures == []
    qapp.processEvents()
    assert failures == ["failed"]

    assert backend.set_source(track) is True
    backend.play()
    player.set_status(FakeMediaStatus.BufferedMedia)
    assert observed[-1] is True
    player.set_status(FakeMediaStatus.InvalidMedia)
    assert observed[-1] is False
    assert finished == []
    qapp.processEvents()
    assert failures == ["failed", "failed"]

    assert backend.set_source(track) is True
    backend.play()
    player.set_status(FakeMediaStatus.LoadedMedia)
    player.set_status(FakeMediaStatus.EndOfMedia)
    player.set_status(FakeMediaStatus.EndOfMedia)
    assert finished == []
    qapp.processEvents()
    assert finished == ["end"]

    # An explicit stop invalidates an already queued decoder failure so it can
    # never be mistaken for a request to advance the playlist.
    assert backend.set_source(track) is True
    backend.play()
    player.fail()
    backend.stop()
    qapp.processEvents()
    assert failures == ["failed", "failed"]

    # Likewise, a replacement source invalidates the old source's queued
    # error even when the multimedia plugin clears it during setSource().
    assert backend.set_source(track) is True
    player.fail()
    assert backend.set_source(track) is True
    qapp.processEvents()
    assert failures == ["failed", "failed"]


def test_qt_voice_backend_queues_fifo_and_keeps_speaking_true_between_clips(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self._slots = []

        def connect(self, slot) -> None:
            self._slots.append(slot)

        def emit(self, *args) -> None:
            for slot in tuple(self._slots):
                slot(*args)

    class FakeStatus:
        Null = "null"
        Ready = "ready"
        Error = "error"

    class FakeEffect:
        Status = FakeStatus
        last = None

        def __init__(self, _parent) -> None:
            type(self).last = self
            self.playingChanged = FakeSignal()
            self.statusChanged = FakeSignal()
            self.sources: list[Path] = []
            self._playing = False
            self._status = FakeStatus.Null

        def setLoopCount(self, _count: int) -> None:
            return None

        def setVolume(self, _volume: float) -> None:
            return None

        def setSource(self, url) -> None:
            self.sources.append(Path(url.toLocalFile()))
            self._status = FakeStatus.Ready
            self.statusChanged.emit()

        def status(self):
            return self._status

        def play(self) -> None:
            self._playing = True
            self.playingChanged.emit()

        def stop(self) -> None:
            self._playing = False
            self.playingChanged.emit()

        def isPlaying(self) -> bool:
            return self._playing

        def finish(self) -> None:
            self._playing = False
            self.playingChanged.emit()

    monkeypatch.setattr(backends, "QSoundEffect", FakeEffect)
    monkeypatch.setattr(backends, "VOICE_PLAYBACK_SUPPORTED", True)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    backend = backends.QtVoiceClipBackend(AudioSettings(), qapp)
    states: list[bool] = []
    started: list[str] = []
    backend.speaking_changed.connect(states.append)
    backend.clip_started.connect(lambda path: started.append(Path(path).name))

    assert backend.play_clip(first) is True
    assert backend.play_clip(second) is True
    assert started == ["first.wav"]
    assert states == [True]

    effect = FakeEffect.last
    assert effect is not None
    effect.finish()
    assert started == ["first.wav", "second.wav"]
    assert states == [True]
    assert [path.name for path in effect.sources] == ["first.wav", "second.wav"]

    effect.finish()
    assert states == [True, False]


def test_qt_voice_backend_has_bounded_fifo_and_stop_discards_pending(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSignal:
        def __init__(self) -> None:
            self._slots = []

        def connect(self, slot) -> None:
            self._slots.append(slot)

        def emit(self, *args) -> None:
            for slot in tuple(self._slots):
                slot(*args)

    class FakeStatus:
        Ready = "ready"
        Error = "error"

    class FakeEffect:
        Status = FakeStatus
        last = None

        def __init__(self, _parent) -> None:
            type(self).last = self
            self.playingChanged = FakeSignal()
            self.statusChanged = FakeSignal()
            self._playing = False

        def setLoopCount(self, _count: int) -> None:
            return None

        def setVolume(self, _volume: float) -> None:
            return None

        def setSource(self, _url) -> None:
            return None

        def status(self):
            return FakeStatus.Ready

        def play(self) -> None:
            self._playing = True
            self.playingChanged.emit()

        def stop(self) -> None:
            self._playing = False
            self.playingChanged.emit()

        def isPlaying(self) -> bool:
            return self._playing

        def finish(self) -> None:
            self._playing = False
            self.playingChanged.emit()

    monkeypatch.setattr(backends, "QSoundEffect", FakeEffect)
    monkeypatch.setattr(backends, "VOICE_PLAYBACK_SUPPORTED", True)
    paths = []
    for index in range(backends.QtVoiceClipBackend.MAX_QUEUED_CLIPS + 2):
        path = tmp_path / f"clip-{index}.wav"
        path.write_bytes(b"fixture")
        paths.append(path)
    backend = backends.QtVoiceClipBackend(AudioSettings(), qapp)

    assert backend.play_clip(paths[0]) is True
    for path in paths[1:-1]:
        assert backend.play_clip(path) is True
    assert backend.play_clip(paths[-1]) is False

    backend.stop()
    effect = FakeEffect.last
    assert effect is not None
    effect.finish()
    assert not backend._queue


def test_voice_preview_uses_unsaved_settings_without_replacing_controller_state(
    qapp,
    tmp_path: Path,
) -> None:
    speech = _RecordingSpeech(None)
    configured = []
    speech.configure = configured.append  # type: ignore[method-assign]
    controller = AudioController(
        {"audio_voice_volume": 80, "audio_voice_rate": 0.0},
        speech_factory=lambda _settings, _parent: speech,
        voice_root=_write_complete_voice_catalog(tmp_path / "voice"),
    )
    captions: list[str] = []
    controller.caption_requested.connect(captions.append)

    assert controller.preview_voice(
        {"audio_voice_enabled": True, "audio_voice_volume": 37, "audio_voice_rate": 0.4}
    ) is True

    assert captions == ["LYRA online. Shipboard systems ready."]
    assert [path.name for path in speech.played] == ["preview.wav"]
    assert configured[-1].voice_volume == 37
    assert configured[-1].voice_rate == 0.4
    assert controller.settings.voice_volume == 80
    assert controller.settings.voice_rate == 0.0


def test_muted_or_unavailable_speech_still_provides_local_caption(qapp) -> None:
    captions: list[str] = []
    controller = AudioController({"audio_master_muted": True})
    controller.caption_requested.connect(captions.append)

    assert controller.announce(VoiceEvent.SERVER_STACK_LAUNCHING) is False
    assert captions == ["Launching server stack."]


def test_partial_voice_catalog_never_plays_while_captions_remain_available(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "partial-voice"
    root.mkdir()
    (root / VoiceLine.CHARACTER_LAUNCHING.filename).write_bytes(b"partial")
    speech = _RecordingSpeech(None)
    controller = AudioController(
        {},
        speech_factory=lambda _settings, _parent: speech,
        voice_root=root,
    )
    captions: list[str] = []
    controller.caption_requested.connect(captions.append)

    assert controller.speech_supported is False
    assert controller.announce(VoiceEvent.CHARACTER_LAUNCHING) is False
    assert captions == ["Launching selected character."]
    assert speech.played == []


def test_voice_event_privacy_and_result_preferences() -> None:
    private = render_announcement(
        VoiceEvent.CHARACTER_LAUNCHING,
        character_name="Secret\nPilot",
        announce_character_names=False,
    )
    suppressed = render_announcement(
        VoiceEvent.LAUNCH_SEQUENCE_COMPLETE,
        launched_count=5,
        announce_results=False,
    )

    assert private is not None
    assert private.text == "Launching selected character."
    assert suppressed is None


def test_title_mute_persistence_preserves_an_unsaved_settings_draft(
    qapp,
    isolated_config: Path,
) -> None:
    config.save(config.load())
    page = SettingsPage()
    page.proxy_url_edit.setText("http://127.0.0.1:29999")
    assert page.is_dirty()

    initial = config.load()
    window = SimpleNamespace(
        _cfg=initial,
        _audio_controller=AudioController(initial),
    )
    MainWindow._on_music_mute_changed(window, True)

    assert page.is_dirty()
    assert page.proxy_url_edit.text() == "http://127.0.0.1:29999"
    assert config.load()["audio_music_muted"] is True
    assert config.load()["audio_master_muted"] is False

    page.save_settings()
    saved = config.load()
    assert saved["proxy_url"] == "http://127.0.0.1:29999"
    assert saved["audio_music_muted"] is True
    assert saved["audio_master_muted"] is False


def test_audio_asset_paths_work_for_source_and_frozen_layouts(tmp_path: Path) -> None:
    source_module = tmp_path / "project" / "src" / "audio" / "assets.py"
    frozen_module = tmp_path / "app" / "_internal" / "src" / "audio" / "assets.py"

    assert audio_assets_root(source_module) == tmp_path / "project" / "assets" / "audio"
    assert audio_assets_root(frozen_module) == tmp_path / "app" / "_internal" / "assets" / "audio"
    assert bundled_music_tracks(tmp_path / "missing") == ()


def test_fixed_lyra_catalog_declares_missing_clips_and_preserves_existing_pcm16() -> None:
    clips = bundled_voice_clips()
    missing = missing_voice_lines()
    original_lines = {
        VoiceLine.PREVIEW,
        VoiceLine.SERVER_STACK_LAUNCHING,
        VoiceLine.SERVER_STACK_ONLINE,
        VoiceLine.SERVER_STACK_FAILED,
        VoiceLine.GAME_SERVER_LAUNCHING,
        VoiceLine.GAME_SERVER_ONLINE,
        VoiceLine.GAME_SERVER_LAUNCH_FAILED,
        VoiceLine.MARKET_SERVER_LAUNCHING,
        VoiceLine.MARKET_SERVER_ONLINE,
        VoiceLine.MARKET_SERVER_LAUNCH_FAILED,
        VoiceLine.CHARACTER_LAUNCHING,
        VoiceLine.CHARACTER_LAUNCH_FAILED,
        VoiceLine.GROUP_LAUNCHING,
        VoiceLine.LAUNCH_SEQUENCE_COMPLETE,
        VoiceLine.LAUNCH_SEQUENCE_PARTIAL,
        VoiceLine.LAUNCH_SEQUENCE_CANCELLED,
    }

    assert original_lines <= set(clips)
    assert set(clips).isdisjoint(missing)
    assert set(clips) | set(missing) == set(VoiceLine)
    assert voice_catalog_ready() is (not missing)
    assert len(VoiceLine) == 25
    assert all("fixture" not in line.text.casefold() for line in VoiceLine)
    assert all(VOICE_LINE_TEXT[line] == line.text for line in VoiceLine)
    for path in clips.values():
        with wave.open(str(path), "rb") as stream:
            assert stream.getnchannels() == 1
            assert stream.getsampwidth() == 2
            assert stream.getframerate() == 22_050
            assert stream.getnframes() > 0


def test_voice_catalog_readiness_requires_matching_manifest_hashes(
    tmp_path: Path,
) -> None:
    root = _write_complete_voice_catalog(tmp_path / "voice")

    assert missing_voice_lines(root) == ()
    assert voice_catalog_ready(root) is True

    with (root / VoiceLine.PREVIEW.filename).open("ab") as stream:
        stream.write(b"tampered")

    assert missing_voice_lines(root) == ()
    assert voice_catalog_ready(root) is False


def test_deferred_voice_probe_publishes_ready_state() -> None:
    calls: list[tuple[bool, str]] = []
    controller = SimpleNamespace(
        speech_supported=True,
        prepare_voice_preview=lambda: True,
    )
    page = SimpleNamespace(
        set_voice_preview_available=lambda available, reason: calls.append(
            (available, reason)
        )
    )
    window = SimpleNamespace(
        _close_in_progress=False,
        _audio_controller=controller,
        _settings_page=page,
    )

    MainWindow._prepare_shipboard_voice(window)

    assert calls == [(True, "Bundled LYRA voice catalog is ready.")]


def test_deferred_voice_probe_retries_only_a_bounded_number_of_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bool, str]] = []
    scheduled: list[tuple[int, object]] = []
    controller = SimpleNamespace(
        speech_supported=True,
        prepare_voice_preview=lambda: False,
    )
    page = SimpleNamespace(
        set_voice_preview_available=lambda available, reason: calls.append(
            (available, reason)
        )
    )
    window = SimpleNamespace(
        _close_in_progress=False,
        _audio_controller=controller,
        _settings_page=page,
        _voice_prepare_attempts=0,
        _VOICE_PREPARE_MAX_ATTEMPTS=3,
        _VOICE_PREPARE_RETRY_DELAY_MS=25,
    )
    window._prepare_shipboard_voice = lambda: MainWindow._prepare_shipboard_voice(
        window
    )
    monkeypatch.setattr(
        "src.app.QTimer",
        SimpleNamespace(
            singleShot=lambda delay, callback: scheduled.append((delay, callback))
        ),
    )

    MainWindow._prepare_shipboard_voice(window)
    assert calls == []
    assert [delay for delay, _callback in scheduled] == [25]
    scheduled.pop(0)[1]()
    assert calls == []
    assert [delay for delay, _callback in scheduled] == [25]
    scheduled.pop(0)[1]()

    assert scheduled == []
    assert calls == [(False, "Bundled LYRA voice asset unavailable.")]
    assert window._voice_prepare_attempts == 3


def test_failed_voice_audition_does_not_disable_verified_capability() -> None:
    availability_changes: list[tuple[bool, str]] = []
    page = SimpleNamespace(
        audio_preview_settings=lambda: {"audio_voice_enabled": True},
        set_voice_preview_available=lambda available, reason: (
            availability_changes.append((available, reason))
        ),
    )
    controller = SimpleNamespace(preview_voice=lambda _draft: False)
    window = SimpleNamespace(
        _close_in_progress=False,
        _audio_controller=controller,
        _settings_page=page,
    )

    MainWindow._preview_shipboard_voice(window)

    assert availability_changes == []


def test_release_bundles_only_approved_celestial_transit_track() -> None:
    tracks = bundled_music_tracks()
    names = [track.name for track in tracks]

    assert names == ["celestial_transit.wav"]
    approved = tracks[0]
    assert approved.stat().st_size > 1_000_000

    with wave.open(str(approved), "rb") as stream:
        assert stream.getnchannels() == 2
        assert stream.getsampwidth() == 2
        assert stream.getframerate() == 44_100
        assert stream.getnframes() == 2_419_200


def test_close_stops_optional_audio_before_other_lifecycle_checks() -> None:
    calls: list[str] = []

    class Audio:
        @staticmethod
        def shutdown() -> None:
            calls.append("audio")

    class Event:
        @staticmethod
        def accept() -> None:
            calls.append("accept")

    window = SimpleNamespace(
        _audio_controller=Audio(),
        _shutdown_audio_for_close=lambda: calls.append("audio"),
        _settings_page=None,
        _stack=None,
        _launch_queue=None,
        _client_launch_thread=None,
        _character_creation_thread=None,
        _character_creation_request=None,
        _character_deletion_thread=None,
        _character_deletion_request=None,
        _overview_patch_thread=None,
        _docker_preflight_thread=None,
        _update_install_worker=None,
        _docker_log_thread=None,
        _tracker=SimpleNamespace(running_count=0),
        _close_after_lifecycle=False,
        _server_proc=None,
        _market_proc=None,
        _close_in_progress=False,
        _docker_mode=lambda: False,
        _lifecycle_active=lambda: False,
        _server_process_alive=lambda: False,
        _stop_service_monitor=lambda: True,
        _has_running_update_checker=lambda: False,
        _release_mod_lifecycle_lease=lambda: None,
    )

    MainWindow.closeEvent(window, Event())

    assert calls == ["audio", "accept"]
