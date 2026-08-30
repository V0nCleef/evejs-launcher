"""Focused tests for decoded-PCM music visualization."""
from __future__ import annotations

from array import array
import math
from pathlib import Path

import pytest
from PyQt6.QtMultimedia import QAudioBuffer, QAudioFormat

from src.audio import backends
from src.audio.backends import (
    MUSIC_SPECTRUM_BANDS,
    SILENT_MUSIC_SPECTRUM,
    MusicBackend,
    _audio_buffer_pcm,
    _music_spectrum_from_pcm,
)
from src.audio.controller import AudioController


def _format(
    sample_format: QAudioFormat.SampleFormat,
    *,
    channels: int = 1,
    sample_rate: int = 48_000,
) -> QAudioFormat:
    audio_format = QAudioFormat()
    audio_format.setChannelCount(channels)
    audio_format.setSampleRate(sample_rate)
    audio_format.setSampleFormat(sample_format)
    return audio_format


def _buffer(
    sample_format: QAudioFormat.SampleFormat,
    values: list[int | float],
    *,
    channels: int = 1,
) -> QAudioBuffer:
    if sample_format == QAudioFormat.SampleFormat.UInt8:
        raw = bytes(int(value) for value in values)
    elif sample_format == QAudioFormat.SampleFormat.Int16:
        raw = array("h", (int(value) for value in values)).tobytes()
    elif sample_format == QAudioFormat.SampleFormat.Int32:
        raw = array("i", (int(value) for value in values)).tobytes()
    elif sample_format == QAudioFormat.SampleFormat.Float:
        raw = array("f", (float(value) for value in values)).tobytes()
    else:  # pragma: no cover - helper guard
        raise AssertionError("unsupported fixture format")
    return QAudioBuffer(raw, _format(sample_format, channels=channels))


@pytest.mark.parametrize(
    ("sample_format", "values", "expected"),
    [
        (
            QAudioFormat.SampleFormat.UInt8,
            [0, 128, 255],
            (-1.0, 0.0, 127.0 / 128.0),
        ),
        (
            QAudioFormat.SampleFormat.Int16,
            [-32768, 0, 32767],
            (-1.0, 0.0, 32767.0 / 32768.0),
        ),
        (
            QAudioFormat.SampleFormat.Int32,
            [-2147483648, 0, 2147483647],
            (-1.0, 0.0, 2147483647.0 / 2147483648.0),
        ),
        (
            QAudioFormat.SampleFormat.Float,
            [-1.0, 0.0, 1.0],
            (-1.0, 0.0, 1.0),
        ),
    ],
)
def test_pcm_conversion_supports_every_qt6_sample_format(
    sample_format: QAudioFormat.SampleFormat,
    values: list[int | float],
    expected: tuple[float, ...],
) -> None:
    samples, sample_rate = _audio_buffer_pcm(_buffer(sample_format, values))

    assert sample_rate == 48_000
    assert samples == pytest.approx(expected)


def test_pcm_conversion_downmixes_any_channel_count_and_sanitizes_float() -> None:
    buffer = _buffer(
        QAudioFormat.SampleFormat.Float,
        [0.25, 0.25, 0.25, -0.75, -0.75, float("nan")],
        channels=3,
    )

    samples, _sample_rate = _audio_buffer_pcm(buffer)

    assert samples == pytest.approx((0.25, -0.5))


def test_pcm_conversion_caps_work_to_newest_1024_frames() -> None:
    samples, _sample_rate = _audio_buffer_pcm(
        _buffer(QAudioFormat.SampleFormat.Int16, list(range(5000)))
    )

    assert len(samples) == 1024
    assert samples[0] == pytest.approx((5000 - 1024) / 32768.0)
    assert samples[-1] == pytest.approx(4999 / 32768.0)


def test_spectrum_is_fixed_normalized_frequency_responsive_and_silent() -> None:
    sample_rate = 48_000
    frame_count = 1024

    def tone(frequency: float, amplitude: float = 0.8) -> tuple[float, ...]:
        return tuple(
            amplitude * math.sin(2.0 * math.pi * frequency * index / sample_rate)
            for index in range(frame_count)
        )

    low = _music_spectrum_from_pcm(tone(220.0), sample_rate)
    high = _music_spectrum_from_pcm(tone(6000.0), sample_rate)
    quiet = _music_spectrum_from_pcm(tone(1000.0, 0.05), sample_rate)
    loud = _music_spectrum_from_pcm(tone(1000.0, 0.8), sample_rate)
    silence = _music_spectrum_from_pcm((0.0,) * frame_count, sample_rate)

    assert len(low) == len(high) == MUSIC_SPECTRUM_BANDS
    assert all(0.0 <= value <= 1.0 for value in (*low, *high))
    assert max(range(len(low)), key=low.__getitem__) < max(
        range(len(high)), key=high.__getitem__
    )
    assert max(loud) > max(quiet) > 0.0
    assert silence == SILENT_MUSIC_SPECTRUM


class _Signal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self, *args) -> None:
        for slot in tuple(self._slots):
            slot(*args)


class _PlaybackState:
    StoppedState = "stopped"
    PlayingState = "playing"


class _MediaStatus:
    NoMedia = "none"
    LoadingMedia = "loading"
    LoadedMedia = "loaded"
    BufferingMedia = "buffering"
    BufferedMedia = "buffered"
    InvalidMedia = "invalid"
    EndOfMedia = "end"


class _Error:
    NoError = "ok"
    ResourceError = "resource"


class _Loops:
    Once = 1


class _AudioOutput:
    def __init__(self, _parent) -> None:
        self.muted = False

    def setVolume(self, _volume: float) -> None:
        return None

    def setMuted(self, muted: bool) -> None:
        self.muted = bool(muted)


class _BufferOutput:
    last = None

    def __init__(self, _parent) -> None:
        type(self).last = self
        self.audioBufferReceived = _Signal()


class _Player:
    PlaybackState = _PlaybackState
    MediaStatus = _MediaStatus
    Error = _Error
    Loops = _Loops
    instances = 0
    last = None

    def __init__(self, _parent) -> None:
        type(self).instances += 1
        type(self).last = self
        self.playbackStateChanged = _Signal()
        self.mediaStatusChanged = _Signal()
        self.errorOccurred = _Signal()
        self.errorChanged = _Signal()
        self._state = _PlaybackState.StoppedState
        self._status = _MediaStatus.NoMedia
        self._error = _Error.NoError
        self.buffer_output = None

    def setAudioOutput(self, _output) -> None:
        return None

    def setAudioBufferOutput(self, output) -> None:
        self.buffer_output = output

    def setLoops(self, _loops) -> None:
        return None

    def setSource(self, source) -> None:
        self._error = _Error.NoError
        self._status = _MediaStatus.NoMedia if source.isEmpty() else _MediaStatus.LoadingMedia

    def playbackState(self):
        return self._state

    def mediaStatus(self):
        return self._status

    def error(self):
        return self._error

    def play(self) -> None:
        self._state = _PlaybackState.PlayingState
        self._status = _MediaStatus.LoadedMedia

    def stop(self) -> None:
        self._state = _PlaybackState.StoppedState

    def fail(self) -> None:
        self._error = _Error.ResourceError
        self.errorOccurred.emit(self._error, "fixture")


def test_qt_backend_taps_same_player_and_zeros_spectrum_on_lifecycle_edges(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Player.instances = 0
    _Player.last = None
    _BufferOutput.last = None
    monkeypatch.setattr(backends, "QAudioOutput", _AudioOutput)
    monkeypatch.setattr(backends, "QAudioBufferOutput", _BufferOutput)
    monkeypatch.setattr(backends, "QMediaPlayer", _Player)
    monkeypatch.setattr(backends, "MULTIMEDIA_SUPPORTED", True)

    track = tmp_path / "visualized.wav"
    track.write_bytes(b"fixture")
    backend = backends.QtMusicBackend(qapp)
    observed: list[tuple[float, ...]] = []
    assert backend.set_spectrum_callback(observed.append) is True
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    assert _Player.instances == 1
    assert _Player.last is not None
    assert _Player.last.buffer_output is _BufferOutput.last

    assert backend.set_source(track) is True
    assert backend.play() is True
    tone = [
        int(24_000 * math.sin(2.0 * math.pi * 1000.0 * index / 48_000))
        for index in range(1024)
    ]
    assert _BufferOutput.last is not None
    _BufferOutput.last.audioBufferReceived.emit(
        _buffer(QAudioFormat.SampleFormat.Int16, tone)
    )
    assert len(observed[-1]) == MUSIC_SPECTRUM_BANDS
    assert max(observed[-1]) > 0.0

    backend.set_muted(True)
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    backend.set_muted(False)
    _BufferOutput.last.audioBufferReceived.emit(
        _buffer(QAudioFormat.SampleFormat.Int16, tone)
    )
    assert max(observed[-1]) > 0.0

    assert _Player.last is not None
    _Player.last._status = _MediaStatus.EndOfMedia
    _Player.last.mediaStatusChanged.emit(_MediaStatus.EndOfMedia)
    assert observed[-1] == SILENT_MUSIC_SPECTRUM

    assert backend.set_source(track) is True
    assert backend.play() is True
    _BufferOutput.last.audioBufferReceived.emit(
        _buffer(QAudioFormat.SampleFormat.Int16, tone)
    )
    assert max(observed[-1]) > 0.0
    _Player.last.fail()
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    backend.stop()
    assert observed[-1] == SILENT_MUSIC_SPECTRUM


def test_qt_backend_keeps_playback_when_buffer_output_is_unavailable(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backends, "QAudioOutput", _AudioOutput)
    monkeypatch.setattr(backends, "QAudioBufferOutput", None)
    monkeypatch.setattr(backends, "QMediaPlayer", _Player)
    monkeypatch.setattr(backends, "MULTIMEDIA_SUPPORTED", True)

    backend = backends.QtMusicBackend(qapp)
    observed: list[tuple[float, ...]] = []

    assert backend.available is True
    assert backend.set_spectrum_callback(observed.append) is False
    assert observed == [SILENT_MUSIC_SPECTRUM]


class _SpectrumMusic(MusicBackend):
    available = True

    def __init__(self) -> None:
        self.source: Path | None = None
        self.spectrum_callback = None
        self.finished_callback = None
        self.failed_callback = None

    def set_source(self, path: Path) -> bool:
        self.source = path
        return True

    def set_spectrum_callback(self, callback) -> bool:
        self.spectrum_callback = callback
        return True

    def set_track_finished_callback(self, callback) -> bool:
        self.finished_callback = callback
        return True

    def set_track_failed_callback(self, callback) -> bool:
        self.failed_callback = callback
        return True

    def set_volume(self, _percent: int) -> None:
        return None

    def set_muted(self, _muted: bool) -> None:
        return None

    def play(self) -> bool:
        return self.source is not None

    def stop(self) -> None:
        return None

    def emit_spectrum(self, values: object) -> None:
        assert self.spectrum_callback is not None
        self.spectrum_callback(values)

    def fail(self) -> None:
        assert self.failed_callback is not None
        self.failed_callback()

    def finish(self) -> None:
        assert self.finished_callback is not None
        self.finished_callback()


def test_controller_publishes_fixed_frames_and_zeros_on_mute_stop_and_failure(
    qapp,
    tmp_path: Path,
) -> None:
    track = tmp_path / "spectrum.wav"
    track.write_bytes(b"fixture")
    backend = _SpectrumMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
    )
    observed: list[tuple[float, ...]] = []
    controller.music_spectrum_changed.connect(observed.append)

    assert controller.start_music(track) is True
    frame = tuple((index + 1) / MUSIC_SPECTRUM_BANDS for index in range(MUSIC_SPECTRUM_BANDS))
    backend.emit_spectrum(frame)
    assert observed[-1] == frame
    assert controller.music_spectrum == frame

    controller.set_music_muted(True)
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    backend.emit_spectrum(frame)
    assert controller.music_spectrum == SILENT_MUSIC_SPECTRUM

    controller.set_music_muted(False)
    backend.emit_spectrum(frame)
    assert observed[-1] == frame
    backend.finish()
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    backend.emit_spectrum(frame)
    assert observed[-1] == frame
    backend.fail()
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    assert controller.music_active is False

    controller.stop_music()
    assert observed[-1] == SILENT_MUSIC_SPECTRUM
    assert all(len(values) == MUSIC_SPECTRUM_BANDS for values in observed)
