"""Lazy Qt adapters for launcher ambience and fixed LYRA voice clips."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

try:  # Optional in source installs and intentionally graceful when excluded.
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
except (ImportError, OSError):  # pragma: no cover - depends on host Qt install
    QAudioOutput = None  # type: ignore[assignment,misc]
    QMediaPlayer = None  # type: ignore[assignment,misc]
    QSoundEffect = None  # type: ignore[assignment,misc]

from .settings import AudioSettings

MULTIMEDIA_SUPPORTED = QAudioOutput is not None and QMediaPlayer is not None
VOICE_PLAYBACK_SUPPORTED = QSoundEffect is not None

PlaybackStateCallback = Callable[[bool], None]
TrackFinishedCallback = Callable[[], None]
TrackFailedCallback = Callable[[], None]


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
        self._last_reported_active: bool | None = None
        self._end_reported = False
        self._source_generation = 0
        self._failure_reported_generation: int | None = None
        self._player.setAudioOutput(self._output)
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

    def set_volume(self, percent: int) -> None:
        self._output.setVolume(max(0.0, min(1.0, percent / 100.0)))

    def set_muted(self, muted: bool) -> None:
        self._output.setMuted(bool(muted))

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
