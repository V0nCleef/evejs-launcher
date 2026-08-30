"""Lazy Qt adapters for launcher ambience and fixed LYRA voice clips."""
from __future__ import annotations

from array import array
from collections import deque
from collections.abc import Callable
import math
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

try:  # Optional in source installs and intentionally graceful when excluded.
    from PyQt6.QtMultimedia import (
        QAudioFormat,
        QAudioOutput,
        QMediaPlayer,
        QSoundEffect,
    )
except (ImportError, OSError):  # pragma: no cover - depends on host Qt install
    QAudioFormat = None  # type: ignore[assignment,misc]
    QAudioOutput = None  # type: ignore[assignment,misc]
    QMediaPlayer = None  # type: ignore[assignment,misc]
    QSoundEffect = None  # type: ignore[assignment,misc]

try:  # Added in Qt 6.8; playback must remain usable on older Qt runtimes.
    from PyQt6.QtMultimedia import QAudioBufferOutput
except (ImportError, OSError):  # pragma: no cover - depends on host Qt install
    QAudioBufferOutput = None  # type: ignore[assignment,misc]

from .settings import AudioSettings

MULTIMEDIA_SUPPORTED = QAudioOutput is not None and QMediaPlayer is not None
VOICE_PLAYBACK_SUPPORTED = QSoundEffect is not None

MUSIC_SPECTRUM_BANDS = 16
SILENT_MUSIC_SPECTRUM = (0.0,) * MUSIC_SPECTRUM_BANDS
_SPECTRUM_MAX_FRAMES = 1024
_SPECTRUM_MIN_FFT_FRAMES = 64
_SPECTRUM_MAX_DECODED_SAMPLES = 8192

PlaybackStateCallback = Callable[[bool], None]
TrackFinishedCallback = Callable[[], None]
TrackFailedCallback = Callable[[], None]
SpectrumCallback = Callable[[tuple[float, ...]], None]


def _audio_buffer_pcm(buffer: object) -> tuple[tuple[float, ...], int]:
    """Return bounded mono PCM and its sample rate from one ``QAudioBuffer``.

    Qt supplies native-endian, interleaved decoded samples here. Keeping this
    conversion independent from ``QtMusicBackend`` makes every supported Qt 6
    sample format deterministic to unit test without starting a second decoder.
    Invalid, truncated, or unknown data fails closed to an empty sample tuple.
    """
    if QAudioFormat is None:
        return (), 0
    try:
        if not bool(buffer.isValid()):  # type: ignore[attr-defined]
            return (), 0
        audio_format = buffer.format()  # type: ignore[attr-defined]
        channels = int(audio_format.channelCount())
        sample_rate = int(audio_format.sampleRate())
        byte_count = int(buffer.byteCount())  # type: ignore[attr-defined]
        sample_format = audio_format.sampleFormat()
        if channels <= 0 or channels > 64 or sample_rate <= 0 or byte_count <= 0:
            return (), 0
        data = buffer.constData()  # type: ignore[attr-defined]
    except (AttributeError, BufferError, MemoryError, TypeError, ValueError):
        return (), 0

    formats = QAudioFormat.SampleFormat
    if sample_format == formats.UInt8:
        width = 1
        kind = "B"
        scale = 128.0
        midpoint = 128.0
    elif sample_format == formats.Int16:
        width = 2
        kind = "h"
        scale = 32768.0
        midpoint = 0.0
    elif sample_format == formats.Int32:
        width = 4
        kind = "i"
        scale = 2147483648.0
        midpoint = 0.0
    elif sample_format == formats.Float:
        width = 4
        kind = "f"
        scale = 1.0
        midpoint = 0.0
    else:
        return (), 0

    complete_samples = byte_count // width
    complete_frames = complete_samples // channels
    frame_count = complete_frames
    if frame_count <= 0:
        return (), 0
    frame_limit = min(
        _SPECTRUM_MAX_FRAMES,
        max(_SPECTRUM_MIN_FFT_FRAMES, _SPECTRUM_MAX_DECODED_SAMPLES // channels),
    )
    frame_count = min(frame_count, frame_limit)
    kept_samples = frame_count * channels
    # Prefer the newest decoded frames if a platform plugin delivers an
    # unusually large buffer. This also places a strict ceiling on GUI work.
    start_sample = complete_frames * channels - kept_samples
    start_byte = start_sample * width
    end_byte = start_byte + kept_samples * width
    try:
        set_size = getattr(data, "setsize", None)
        if callable(set_size):
            set_size(byte_count)
            raw = memoryview(data)[start_byte:end_byte].tobytes()
        else:
            raw = bytes(data.asstring(byte_count))[start_byte:end_byte]
    except (AttributeError, BufferError, MemoryError, OverflowError, TypeError, ValueError):
        return (), 0
    values = array(kind)
    try:
        values.frombytes(raw)
    except (BufferError, MemoryError, ValueError):
        return (), 0
    if len(values) < kept_samples:
        return (), 0

    mono: list[float] = []
    append = mono.append
    for frame_start in range(0, kept_samples, channels):
        mixed = 0.0
        for channel in range(channels):
            value = (float(values[frame_start + channel]) - midpoint) / scale
            if not math.isfinite(value):
                value = 0.0
            mixed += max(-1.0, min(1.0, value))
        append(mixed / channels)
    return tuple(mono), sample_rate


def _fft_magnitudes(samples: tuple[float, ...]) -> tuple[list[float], int]:
    """Return Hann-windowed positive-bin magnitudes using a bounded radix-2 FFT."""
    available = min(len(samples), _SPECTRUM_MAX_FRAMES)
    if available <= 0:
        return [], 0
    frame_count = 1
    while frame_count * 2 <= available:
        frame_count *= 2
    frame_count = max(_SPECTRUM_MIN_FFT_FRAMES, frame_count)
    selected = list(samples[-min(available, frame_count) :])
    if len(selected) < frame_count:
        selected.extend([0.0] * (frame_count - len(selected)))

    if not any(abs(value) > 1.0e-8 for value in selected):
        return [0.0] * (frame_count // 2 + 1), frame_count

    denominator = max(1, frame_count - 1)
    window = [
        0.5 - 0.5 * math.cos(2.0 * math.pi * index / denominator)
        for index in range(frame_count)
    ]
    values = [
        complex(sample * window[index], 0.0)
        for index, sample in enumerate(selected)
    ]

    # In-place bit-reversal permutation.
    target = 0
    for index in range(1, frame_count):
        bit = frame_count >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target ^= bit
        if index < target:
            values[index], values[target] = values[target], values[index]

    length = 2
    while length <= frame_count:
        angle = -2.0 * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for offset in range(0, frame_count, length):
            twiddle = 1.0 + 0.0j
            for inner in range(half):
                even = values[offset + inner]
                odd = values[offset + inner + half] * twiddle
                values[offset + inner] = even + odd
                values[offset + inner + half] = even - odd
                twiddle *= step
        length *= 2

    window_gain = max(sum(window), 1.0)
    scale = 2.0 / window_gain
    magnitudes = [
        abs(values[index]) * scale for index in range(frame_count // 2 + 1)
    ]
    return magnitudes, frame_count


def _music_spectrum_from_pcm(
    samples: tuple[float, ...],
    sample_rate: int,
    *,
    bands: int = MUSIC_SPECTRUM_BANDS,
) -> tuple[float, ...]:
    """Map decoded mono PCM into fixed logarithmic frequency-band levels."""
    if bands <= 0:
        return ()
    silence = (0.0,) * bands
    if sample_rate <= 0 or not samples:
        return silence
    magnitudes, frame_count = _fft_magnitudes(samples)
    if frame_count <= 0 or not magnitudes or max(magnitudes, default=0.0) <= 1.0e-8:
        return silence

    nyquist = sample_rate / 2.0
    bin_width = sample_rate / frame_count
    low_hz = max(30.0, bin_width)
    if nyquist <= low_hz:
        peak = max(magnitudes[1:], default=0.0)
        value = min(1.0, math.sqrt(max(0.0, peak)))
        return tuple(round(value, 6) for _ in range(bands))

    ratio = nyquist / low_hz
    edges = [low_hz * ratio ** (index / bands) for index in range(bands + 1)]
    last_bin = len(magnitudes) - 1
    result: list[float] = []
    for index in range(bands):
        first = max(1, min(last_bin, int(math.floor(edges[index] / bin_width))))
        after = max(
            first + 1,
            int(math.ceil(edges[index + 1] / bin_width)),
        )
        after = min(last_bin + 1, after)
        peak = max(magnitudes[first:after], default=0.0)
        # A square-root response makes quiet ambience visible while preserving
        # the ordering and a hard normalized ceiling for the renderer.
        level = min(1.0, math.sqrt(max(0.0, peak)))
        result.append(round(level, 6))
    return tuple(result)


class MusicBackend:
    """Small interface shared by the Qt backend and controller test doubles."""

    available = False

    def set_source(self, path: Path) -> bool:
        return False

    def set_volume(self, percent: int) -> None:
        return None

    def set_muted(self, muted: bool) -> None:
        return None

    def set_playback_state_callback(
        self,
        callback: PlaybackStateCallback,
    ) -> bool:
        """Register actual playback-state reports when the backend supports them.

        ``False`` preserves compatibility with simple synchronous test doubles:
        their ``play()`` return value remains the controller's state authority.
        """
        return False

    def set_track_finished_callback(
        self,
        callback: TrackFinishedCallback,
    ) -> bool:
        """Register a callback for a natural end-of-media event.

        This is deliberately separate from playback-state reporting: stops,
        mutes, errors, and source replacements must never advance a playlist.
        """
        del callback
        return False

    def set_track_failed_callback(
        self,
        callback: TrackFailedCallback,
    ) -> bool:
        """Register a callback for a decoder/resource failure.

        Failure is a distinct lifecycle edge from pause, stop, mute, and a
        natural end. Controllers can therefore skip a broken playlist item
        without interpreting every inactive playback report as an error.
        """
        del callback
        return False

    def set_spectrum_callback(
        self,
        callback: SpectrumCallback,
    ) -> bool:
        """Register fixed normalized PCM spectrum reports when supported.

        The no-op default is also the test-double seam: lightweight backends
        can implement this one method without pretending to expose Qt objects.
        """
        del callback
        return False

    def play(self) -> bool:
        return False

    def stop(self) -> None:
        return None


class QtMusicBackend(MusicBackend):
    """QMediaPlayer adapter constructed only when playback is requested."""

    available = True

    def __init__(self, parent: QObject) -> None:
        if not MULTIMEDIA_SUPPORTED:
            raise RuntimeError("QtMultimedia is unavailable")
        self._output = QAudioOutput(parent)  # type: ignore[operator]
        self._player = QMediaPlayer(parent)  # type: ignore[operator]
        self._playback_state_callback: PlaybackStateCallback | None = None
        self._track_finished_callback: TrackFinishedCallback | None = None
        self._track_failed_callback: TrackFailedCallback | None = None
        self._spectrum_callback: SpectrumCallback | None = None
        self._last_reported_spectrum = SILENT_MUSIC_SPECTRUM
        self._smoothed_spectrum = SILENT_MUSIC_SPECTRUM
        self._last_reported_active: bool | None = None
        self._end_reported = False
        self._source_generation = 0
        self._failure_reported_generation: int | None = None
        self._player.setAudioOutput(self._output)
        self._buffer_output = self._create_buffer_output(parent)
        once = getattr(QMediaPlayer.Loops, "Once", 1)  # type: ignore[union-attr]
        self._player.setLoops(once)
        self._player.playbackStateChanged.connect(self._on_player_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_player_error)
        error_changed = getattr(self._player, "errorChanged", None)
        if error_changed is not None:
            error_changed.connect(self._on_player_state_changed)
        self._has_source = False

    def set_source(self, path: Path) -> bool:
        self._source_generation += 1
        self._end_reported = False
        self._failure_reported_generation = None
        self._has_source = False
        self._reset_spectrum(force=True)
        if not path.is_file():
            self._player.stop()
            self._player.setSource(QUrl())
            self._publish_playback_state()
            return False
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self._has_source = True
        self._publish_playback_state()
        if self._source_has_failed():
            self._queue_track_failed()
            return False
        return True

    def set_track_finished_callback(
        self,
        callback: TrackFinishedCallback,
    ) -> bool:
        self._track_finished_callback = callback
        return True

    def set_track_failed_callback(
        self,
        callback: TrackFailedCallback,
    ) -> bool:
        self._track_failed_callback = callback
        return True

    def set_spectrum_callback(
        self,
        callback: SpectrumCallback,
    ) -> bool:
        self._spectrum_callback = callback
        self._publish_spectrum(SILENT_MUSIC_SPECTRUM, force=True)
        return self._buffer_output is not None

    def set_volume(self, percent: int) -> None:
        self._output.setVolume(max(0.0, min(1.0, percent / 100.0)))

    def set_muted(self, muted: bool) -> None:
        muted = bool(muted)
        self._output.setMuted(muted)
        if muted:
            self._reset_spectrum(force=True)

    def set_playback_state_callback(
        self,
        callback: PlaybackStateCallback,
    ) -> bool:
        self._playback_state_callback = callback
        self._last_reported_active = None
        self._publish_playback_state()
        return True

    def play(self) -> bool:
        if not self._has_source:
            return False
        if self._source_has_failed():
            self._publish_playback_state()
            self._queue_track_failed()
            return False
        self._player.play()
        # Some platform plugins update synchronously without first delivering
        # a signal. The queried QMediaPlayer state remains the authority.
        self._publish_playback_state()
        if self._source_has_failed():
            self._queue_track_failed()
            return False
        return True

    def stop(self) -> None:
        # Invalidate any queued end/failure notification before an explicit
        # lifecycle stop. The source itself remains loaded so unmute can resume
        # it without selecting a different playlist entry.
        self._source_generation += 1
        self._end_reported = False
        self._failure_reported_generation = None
        self._player.stop()
        self._publish_playback_state()
        self._reset_spectrum(force=True)

    def _create_buffer_output(self, parent: QObject) -> object | None:
        """Attach decoded-buffer observation to this same media player."""
        setter = getattr(self._player, "setAudioBufferOutput", None)
        if QAudioBufferOutput is None or not callable(setter):
            return None
        try:
            output = QAudioBufferOutput(parent)  # type: ignore[operator]
            output.audioBufferReceived.connect(self._on_audio_buffer_received)
            setter(output)
            return output
        except (AttributeError, OSError, RuntimeError, TypeError):
            # Visualization is optional. Never sacrifice working playback
            # because an older or incomplete platform plugin lacks this tap.
            return None

    def _on_audio_buffer_received(self, buffer: object) -> None:
        try:
            samples, sample_rate = _audio_buffer_pcm(buffer)
            spectrum = _music_spectrum_from_pcm(samples, sample_rate)
        except Exception:
            # A malformed platform buffer must not escape a Qt signal callback
            # and abort pythonw. Playback remains independent of visualization.
            self._reset_spectrum()
            return
        if not self._has_source or not samples or not any(spectrum):
            self._reset_spectrum()
            return
        smoothed = []
        for previous, current in zip(self._smoothed_spectrum, spectrum):
            response = 0.72 if current >= previous else 0.28
            smoothed.append(previous + (current - previous) * response)
        self._smoothed_spectrum = tuple(
            round(max(0.0, min(1.0, value)), 6) for value in smoothed
        )
        self._publish_spectrum(self._smoothed_spectrum)

    def _reset_spectrum(self, *, force: bool = False) -> None:
        self._smoothed_spectrum = SILENT_MUSIC_SPECTRUM
        self._publish_spectrum(SILENT_MUSIC_SPECTRUM, force=force)

    def _publish_spectrum(
        self,
        values: tuple[float, ...],
        *,
        force: bool = False,
    ) -> None:
        values = tuple(
            max(0.0, min(1.0, float(value)))
            for value in values[:MUSIC_SPECTRUM_BANDS]
        )
        if len(values) < MUSIC_SPECTRUM_BANDS:
            values += (0.0,) * (MUSIC_SPECTRUM_BANDS - len(values))
        if not force and values == self._last_reported_spectrum:
            return
        self._last_reported_spectrum = values
        callback = self._spectrum_callback
        if callback is not None:
            try:
                callback(values)
            except Exception:
                # Visualization subscribers are optional and must never be
                # allowed to unwind through Qt's decoded-audio signal stack.
                return

    def _on_player_state_changed(self, _state: object = None) -> None:
        self._publish_playback_state()

    def _on_media_status_changed(self, status: object = None) -> None:
        self._publish_playback_state()
        invalid_media = getattr(
            QMediaPlayer.MediaStatus,  # type: ignore[union-attr]
            "InvalidMedia",
            None,
        )
        if invalid_media is not None and status == invalid_media:
            self._reset_spectrum(force=True)
            self._queue_track_failed()
            return
        end_of_media = getattr(
            QMediaPlayer.MediaStatus,  # type: ignore[union-attr]
            "EndOfMedia",
            None,
        )
        if (
            end_of_media is None
            or status != end_of_media
            or not self._has_source
            or self._end_reported
        ):
            return
        self._end_reported = True
        self._reset_spectrum(force=True)
        generation = self._source_generation
        # Replacing QMediaPlayer's source from inside mediaStatusChanged can
        # re-enter platform multimedia plugins. Queue the controller edge and
        # bind it to this exact source generation instead.
        QTimer.singleShot(
            0,
            lambda generation=generation: self._deliver_track_finished(
                generation
            ),
        )

    def _on_player_error(self, _error: object, _message: str = "") -> None:
        self._publish_playback_state()
        self._reset_spectrum(force=True)
        self._queue_track_failed()

    def _source_has_failed(self) -> bool:
        if not self._has_source:
            return False
        player = self._player
        invalid_media = (  # type: ignore[union-attr]
            QMediaPlayer.MediaStatus.InvalidMedia
        )
        return (
            player.error() != QMediaPlayer.Error.NoError  # type: ignore[union-attr]
            or player.mediaStatus() == invalid_media
        )

    def _queue_track_failed(self) -> None:
        """Queue one failure edge for the current source, never re-entrantly."""
        if not self._has_source or self._end_reported:
            return
        generation = self._source_generation
        if self._failure_reported_generation == generation:
            return
        self._failure_reported_generation = generation
        QTimer.singleShot(
            0,
            lambda generation=generation: self._deliver_track_failed(
                generation
            ),
        )

    def _deliver_track_failed(self, generation: int) -> None:
        if (
            generation != self._source_generation
            or not self._has_source
            or self._end_reported
            or not self._source_has_failed()
        ):
            return
        callback = self._track_failed_callback
        if callback is not None:
            callback()

    def _deliver_track_finished(self, generation: int) -> None:
        if (
            generation != self._source_generation
            or not self._has_source
            or not self._end_reported
        ):
            return
        callback = self._track_finished_callback
        if callback is not None:
            callback()

    def _actual_playback_active(self) -> bool:
        """Return true only after Qt reports usable media actively playing."""
        player = self._player
        if not self._has_source:
            return False
        if player.error() != QMediaPlayer.Error.NoError:  # type: ignore[union-attr]
            return False
        if (
            player.playbackState()
            != QMediaPlayer.PlaybackState.PlayingState  # type: ignore[union-attr]
        ):
            return False
        ready_statuses = {
            QMediaPlayer.MediaStatus.LoadedMedia,  # type: ignore[union-attr]
            QMediaPlayer.MediaStatus.BufferingMedia,  # type: ignore[union-attr]
            QMediaPlayer.MediaStatus.BufferedMedia,  # type: ignore[union-attr]
        }
        return player.mediaStatus() in ready_statuses

    def _publish_playback_state(self) -> None:
        active = self._actual_playback_active()
        if active == self._last_reported_active:
            return
        self._last_reported_active = active
        callback = self._playback_state_callback
        if callback is not None:
            callback(active)


class VoiceClipBackend(QObject):
    """Signal-bearing interface for one fixed prerecorded voice clip."""

    speaking_changed = pyqtSignal(bool)
    clip_started = pyqtSignal(object)
    available = False

    def configure(self, settings: AudioSettings) -> None:
        return None

    def play_clip(self, path: Path) -> bool:
        return False

    def say(self, text: str) -> bool:
        """Reject dynamic speech retained only for migration-safe callers."""
        del text
        return False

    def stop(self) -> None:
        return None


class QtVoiceClipBackend(VoiceClipBackend):
    """Low-latency FIFO player for the bounded prerecorded LYRA catalog."""

    # Reject the newest request once full. Accepted clips always retain FIFO
    # order, while a broken event storm can never grow memory without bound.
    MAX_QUEUED_CLIPS = 8

    def __init__(self, settings: AudioSettings, parent: QObject) -> None:
        super().__init__(parent)
        if not VOICE_PLAYBACK_SUPPORTED:
            raise RuntimeError("QtMultimedia sound effects are unavailable")
        self._effect = QSoundEffect(parent)  # type: ignore[operator]
        self._effect.setLoopCount(1)
        self._effect.playingChanged.connect(self._on_playing_changed)
        status_changed = getattr(self._effect, "statusChanged", None)
        if status_changed is not None:
            status_changed.connect(self._on_status_changed)
        self._queue: deque[Path] = deque()
        self._current_path: Path | None = None
        self._current_started = False
        self._active = False
        self._transitioning = False
        self._speaking_reported = False
        self.configure(settings)

    @property
    def available(self) -> bool:
        """Reflect an initialized Qt effect player without claiming an asset."""
        return VOICE_PLAYBACK_SUPPORTED

    def configure(self, settings: AudioSettings) -> None:
        self._effect.setVolume(
            max(0.0, min(1.0, settings.voice_volume / 100.0))
        )

    @property
    def identity(self) -> tuple[str, str]:
        """Return the fixed catalog identity, never a system TTS claim."""
        return "LYRA prerecorded voice", "en_GB"

    def play_clip(self, path: Path) -> bool:
        """Accept one fixed local WAV without interrupting an active line."""
        if not path.is_file():
            return False
        resolved = path.resolve()
        if self._active:
            if len(self._queue) >= self.MAX_QUEUED_CLIPS:
                return False
            self._queue.append(resolved)
            return True
        return self._start_clip(resolved)

    def _start_clip(self, path: Path) -> bool:
        """Start one validated path while suppressing transition false edges."""
        self._active = True
        self._current_path = path
        self._current_started = False
        self._transitioning = True
        try:
            self._effect.setSource(QUrl.fromLocalFile(str(path)))
            error_status = QSoundEffect.Status.Error  # type: ignore[union-attr]
            if self._effect.status() == error_status:
                self._active = False
                self._current_path = None
                return False
            self._effect.play()
            if self._effect.status() == error_status:
                self._active = False
                self._current_path = None
                return False
            return True
        finally:
            self._transitioning = False
            # Test doubles and some platform plugins can promote playback
            # synchronously inside ``play()`` while transition edges are
            # suppressed. Publish that true state once the source swap ends.
            if self._active and bool(self._effect.isPlaying()):
                self._mark_current_started()

    def _finish_current_and_advance(self) -> None:
        """Advance synchronously so speaking stays true between queued clips."""
        self._active = False
        self._current_path = None
        self._current_started = False
        while self._queue:
            if self._start_clip(self._queue.popleft()):
                return
        self._publish_speaking(False)

    def _publish_speaking(self, speaking: bool) -> None:
        speaking = bool(speaking)
        if speaking == self._speaking_reported:
            return
        self._speaking_reported = speaking
        self.speaking_changed.emit(speaking)

    def _mark_current_started(self) -> None:
        """Publish one fixed path exactly once when its audio starts."""
        path = self._current_path
        if path is None:
            return
        if not self._current_started:
            self._current_started = True
            self.clip_started.emit(path)
        self._publish_speaking(True)

    def stop(self) -> None:
        """Stop the current line and discard every pending line."""
        self._queue.clear()
        self._active = False
        self._current_path = None
        self._current_started = False
        self._transitioning = True
        try:
            self._effect.stop()
        finally:
            self._transitioning = False
        self._publish_speaking(False)

    def _on_playing_changed(self) -> None:
        if self._transitioning:
            return
        if bool(self._effect.isPlaying()):
            self._mark_current_started()
            return
        if self._active:
            self._finish_current_and_advance()
        else:
            self._publish_speaking(False)

    def _on_status_changed(self) -> None:
        # QSoundEffect.statusChanged is a zero-argument Qt signal.  Query the
        # authoritative status here instead of expecting a signal payload;
        # a mismatched Python slot signature can abort a pythonw process when
        # Qt emits the first source-status transition.
        status = self._effect.status()
        error_status = QSoundEffect.Status.Error  # type: ignore[union-attr]
        if (
            self._transitioning
            or status != error_status
            or not self._active
            or bool(self._effect.isPlaying())
        ):
            return
        self._finish_current_and_advance()


# Compatibility names keep existing controller/test integrations source-safe
# while the runtime implementation is now fixed-clip playback, not TTS.
SpeechBackend = VoiceClipBackend
QtSpeechBackend = QtVoiceClipBackend


def create_music_backend(parent: QObject) -> MusicBackend:
    """Create a backend or a no-op object when Qt media cannot initialize."""
    if not MULTIMEDIA_SUPPORTED:
        return MusicBackend()
    try:
        return QtMusicBackend(parent)
    except (RuntimeError, OSError, TypeError):
        return MusicBackend()


def create_speech_backend(settings: AudioSettings, parent: QObject) -> SpeechBackend:
    """Create fixed-clip playback or a safe no-op when Qt audio is absent."""
    if not VOICE_PLAYBACK_SUPPORTED:
        return VoiceClipBackend(parent)
    try:
        return QtVoiceClipBackend(settings, parent)
    except (RuntimeError, OSError, TypeError):
        return VoiceClipBackend(parent)
