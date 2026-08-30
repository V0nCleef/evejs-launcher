"""Coordinator for launcher ambience and fixed prerecorded LYRA lines."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping
import math
import os
from pathlib import Path
import random
import re

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .assets import (
    MUSIC_EXTENSIONS,
    bundled_music_tracks,
    bundled_voice_clips,
    voice_catalog_ready,
)
from .backends import (
    MUSIC_SPECTRUM_BANDS,
    MULTIMEDIA_SUPPORTED,
    SILENT_MUSIC_SPECTRUM,
    VOICE_PLAYBACK_SUPPORTED,
    MusicBackend,
    SpeechBackend,
    create_music_backend,
    create_speech_backend,
)
from .events import VoiceEvent, VoiceLine, preview_announcement, render_announcement
from .music_catalog import discover_curated_music_tracks, curated_music_title
from .settings import AudioSettings

MusicFactory = Callable[[QObject], MusicBackend]
SpeechFactory = Callable[[AudioSettings, QObject], SpeechBackend]


class AudioController(QObject):
    """Own optional audio backends while keeping launcher actions independent."""

    MUSIC_SPECTRUM_BANDS = MUSIC_SPECTRUM_BANDS
    _MUSIC_HISTORY_LIMIT = 256

    master_muted_changed = pyqtSignal(bool)
    music_muted_changed = pyqtSignal(bool)
    music_playback_changed = pyqtSignal(bool, str)
    music_spectrum_changed = pyqtSignal(object)
    caption_requested = pyqtSignal(str)
    backend_availability_changed = pyqtSignal(bool, bool)

    def __init__(
        self,
        settings: Mapping[str, object] | None = None,
        parent: QObject | None = None,
        *,
        music_factory: MusicFactory = create_music_backend,
        speech_factory: SpeechFactory = create_speech_backend,
        voice_root: str | Path | None = None,
        music_root: str | Path | None = None,
        curated_music_roots: Iterable[str | os.PathLike[str]] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = AudioSettings.from_mapping(settings or {})
        self._music_factory = music_factory
        self._speech_factory = speech_factory
        self._voice_root = Path(voice_root) if voice_root is not None else None
        self._music_root = Path(music_root) if music_root is not None else None
        # A supplied music root is an isolated asset/test seam, so it must not
        # unexpectedly pull recordings from the developer's real Downloads.
        # Production leaves both arguments unset and gets automatic discovery;
        # callers can opt back in with explicit curated roots.
        self._curated_music_roots = (
            ()
            if curated_music_roots is None and music_root is not None
            else (
                None
                if curated_music_roots is None
                else tuple(Path(root) for root in curated_music_roots)
            )
        )
        self._music_rng = rng if rng is not None else random.Random()
        self._configured_music_paths = self._music_library_from_mapping(
            settings or {}
        )
        self._music_backend: MusicBackend | None = None
        self._speech_backend: SpeechBackend | None = None
        self._music_backend_reports_state = False
        self._music_backend_reports_finished = False
        self._music_backend_reports_failed = False
        self._music_backend_reports_spectrum = False
        self._music_source: Path | None = None
        self._music_source_generation = 0
        self._music_playlist: tuple[Path, ...] = ()
        self._music_shuffle_bag: list[Path] = []
        self._music_failed_sources: set[str] = set()
        self._music_last_source: Path | None = None
        self._music_history: list[Path] = []
        self._music_forward: list[Path] = []
        self._music_uses_library = True
        self._advancing_music = False
        self._music_operation_depth = 0
        self._pending_music_failure_generation: int | None = None
        self._music_requested = False
        self._music_active = False
        self._music_spectrum = SILENT_MUSIC_SPECTRUM
        self._voice_speaking = False
        self._voice_caption_queue: deque[tuple[Path, str]] = deque()

    @property
    def settings(self) -> AudioSettings:
        return self._settings

    @property
    def master_muted(self) -> bool:
        return self._settings.master_muted

    @property
    def music_muted(self) -> bool:
        """Return the independent music-only mute used by the title bar."""
        return self._settings.music_muted

    @property
    def _music_output_muted(self) -> bool:
        """Return whether either mute layer currently silences music."""
        return self.master_muted or self.music_muted

    @property
    def multimedia_supported(self) -> bool:
        return MULTIMEDIA_SUPPORTED

    @property
    def speech_supported(self) -> bool:
        return VOICE_PLAYBACK_SUPPORTED and voice_catalog_ready(self._voice_root)

    @property
    def music_active(self) -> bool:
        """Return whether the local backend reports the loop actively playing."""
        return self._music_active

    @property
    def music_spectrum(self) -> tuple[float, ...]:
        """Return the latest fixed normalized visualization frame."""
        return self._music_spectrum

    @property
    def music_track_name(self) -> str:
        """Return the current file's truthful display name when selected."""
        source = self._music_source
        return (
            self._music_display_name(source)
            if source is not None
            else "STATION SOUNDSCAPE"
        )

    def apply_settings(self, mapping: Mapping[str, object]) -> None:
        """Apply a complete config snapshot without initializing unused backends."""
        previous = self._settings
        updated = AudioSettings.from_mapping(mapping)
        configured_music_paths = self._music_library_from_mapping(mapping)
        music_library_changed = (
            configured_music_paths != self._configured_music_paths
        )
        self._configured_music_paths = configured_music_paths
        self._settings = updated

        if music_library_changed and self._music_uses_library:
            self._refresh_library_playlist()

        if self._speech_backend is not None:
            self._speech_backend.configure(updated)

        if self._music_backend is not None:
            music_output_muted = updated.master_muted or updated.music_muted
            previous_music_output_muted = (
                previous.master_muted or previous.music_muted
            )
            self._music_backend.set_muted(music_output_muted)
            self._apply_music_volume()
            if music_output_muted or not updated.music_enabled:
                self._music_backend.stop()
                self._set_music_active(False)
                self._reset_music_spectrum(force=True)
            elif self._music_requested and (
                previous_music_output_muted
                or not previous.music_enabled
                or not self._music_active
            ):
                self._request_music_playback()

        if updated.master_muted or not updated.voice_enabled:
            if self._speech_backend is not None:
                self._voice_caption_queue.clear()
                self._speech_backend.stop()
            self._set_voice_speaking(False)

        if previous.master_muted != updated.master_muted:
            self.master_muted_changed.emit(updated.master_muted)
        if previous.music_muted != updated.music_muted:
            self.music_muted_changed.emit(updated.music_muted)

    @pyqtSlot(bool)
    def set_master_muted(self, muted: bool) -> None:
        """Mute immediately; speech is stopped and no announcement is queued."""
        muted = bool(muted)
        if muted == self._settings.master_muted:
            return
        data = self._as_mapping()
        data["audio_master_muted"] = muted
        self._settings = AudioSettings.from_mapping(data)

        if self._music_backend is not None:
            self._music_backend.set_muted(muted or self.music_muted)
            if muted:
                self._music_backend.stop()
                self._set_music_active(False)
                self._reset_music_spectrum(force=True)
            elif (
                self._music_requested
                and self._settings.music_enabled
                and not self.music_muted
            ):
                self._request_music_playback()
        if self._speech_backend is not None and muted:
            self._voice_caption_queue.clear()
            self._speech_backend.stop()
        if muted:
            self._set_voice_speaking(False)
        self.master_muted_changed.emit(muted)

    @pyqtSlot()
    def toggle_master_muted(self) -> None:
        self.set_master_muted(not self.master_muted)

    @pyqtSlot(bool)
    def set_music_muted(self, muted: bool) -> None:
        """Mute or resume music without interrupting LYRA voice playback."""
        muted = bool(muted)
        if muted == self._settings.music_muted:
            return
        data = self._as_mapping()
        data["audio_music_muted"] = muted
        self._settings = AudioSettings.from_mapping(data)

        if self._music_backend is not None:
            self._music_backend.set_muted(self.master_muted or muted)
            if muted:
                self._music_backend.stop()
                self._set_music_active(False)
                self._reset_music_spectrum(force=True)
            elif (
                self._music_requested
                and self._settings.music_enabled
                and not self.master_muted
            ):
                self._request_music_playback()
        self.music_muted_changed.emit(muted)

    @pyqtSlot()
    def toggle_music_muted(self) -> None:
        self.set_music_muted(not self.music_muted)

    def start_music(self, path: str | Path | None = None) -> bool:
        """Start a shuffled playlist or one explicitly supplied local track.

        For state-reporting backends this method can return before audio starts;
        ``music_playback_changed`` is the authority for visible playback state.
        """
        self._music_requested = True
        self._music_uses_library = path is None
        if self._music_backend is not None:
            self._music_backend.stop()
        self._set_music_active(False)
        self._set_music_source(None)
        self._reset_music_spectrum(force=True)
        if path is None:
            playlist = self._available_music_tracks()
        else:
            source = self._validated_music_path(path)
            playlist = (source,) if source is not None else ()
        self._replace_music_playlist(playlist)
        if not self._music_playlist:
            self._set_music_active(False)
            self._reset_music_spectrum(force=True)
            return False

        backend = self._ensure_music_backend()
        if not backend.available:
            self._set_music_active(False)
            self._reset_music_spectrum(force=True)
            return False
        backend.set_muted(self._music_output_muted)
        self._apply_music_volume()
        if self._music_output_muted or not self._settings.music_enabled:
            self._set_music_active(False)
            self._reset_music_spectrum(force=True)
            return False
        return self._request_music_playback()

    def stop_music(self) -> None:
        self._music_requested = False
        if self._music_backend is not None:
            self._music_backend.stop()
        self._set_music_active(False)
        self._reset_music_spectrum(force=True)

    def next_music(self) -> bool:
        """Move forward through navigation history, then resume shuffle order."""
        backend = self._music_navigation_backend()
        if backend is None:
            return False
        self._music_operation_depth += 1
        try:
            return self._advance_music(backend, stop_current=True)
        finally:
            self._complete_music_operation()

    def previous_music(self) -> bool:
        """Play the previous successful track and retain a forward path."""
        backend = self._music_navigation_backend()
        if backend is None or not self._music_history:
            return False
        current = self._music_source
        self._set_music_active(False)
        self._reset_music_spectrum(force=True)
        backend.stop()

        self._music_operation_depth += 1
        try:
            while self._music_history:
                target = self._music_history.pop()
                if target == current:
                    continue
                if not self._play_specific_music_source(backend, target):
                    continue
                if current is not None and current != self._music_source:
                    self._music_forward.append(current)
                return True

            # A stale/deleted history entry should not turn Previous into an
            # unexpected Stop. Restore the known-good current item when able.
            if current is not None:
                self._play_specific_music_source(backend, current)
            return False
        finally:
            self._complete_music_operation()

    def prepare_voice_preview(self) -> bool:
        """Verify the complete local catalog and initialize its clip player."""
        if not self.speech_supported or self._voice_path(VoiceLine.PREVIEW) is None:
            return False
        return bool(self._ensure_speech_backend().available)

    @property
    def speech_identity(self) -> tuple[str, str]:
        """Return the active backend's truthful voice/locale when initialized."""
        backend = self._speech_backend
        identity = getattr(backend, "identity", None)
        if backend is None or identity is None:
            return "", ""
        try:
            voice_name, locale_name = identity
        except (TypeError, ValueError):
            return "", ""
        return str(voice_name), str(locale_name)

    def preview_voice(
        self,
        mapping: Mapping[str, object] | None = None,
        *,
        text: str = "",
    ) -> bool:
        """Play the fixed preview using unsaved volume without persisting it.

        ``text`` remains accepted for call-site compatibility but is ignored;
        callers can never make the prerecorded backend speak dynamic content.
        """
        del text
        preview = AudioSettings.from_mapping(mapping or self._as_mapping())
        if (
            preview.master_muted
            or not preview.voice_enabled
            or not self.speech_supported
        ):
            return False
        announcement = preview_announcement()
        path = self._voice_path(announcement.line)
        if path is None:
            return False
        backend = self._ensure_speech_backend()
        if not backend.available:
            return False
        # Preview is an explicit audition action: interrupt operational speech
        # and discard its pending FIFO before applying the draft volume.
        self._voice_caption_queue.clear()
        backend.stop()
        self._set_voice_speaking(False)
        backend.configure(preview)
        return self._queue_voice_clip(
            path,
            announcement.text,
            backend,
            caption_on_rejection=False,
        )

    def announce(self, event: VoiceEvent, **context: object) -> bool:
        """Caption an event and speak it locally when the configured backend allows."""
        announcement = render_announcement(
            event,
            announce_character_names=self._settings.announce_character_names,
            announce_results=self._settings.announce_results,
            **context,
        )
        if announcement is None:
            return False
        if (
            self.master_muted
            or not self._settings.voice_enabled
            or not self.speech_supported
        ):
            self.caption_requested.emit(announcement.text)
            return False
        path = self._voice_path(announcement.line)
        if path is None:
            self.caption_requested.emit(announcement.text)
            return False
        backend = self._ensure_speech_backend()
        if not backend.available:
            self.caption_requested.emit(announcement.text)
            return False
        backend.configure(self._settings)
        return self._queue_voice_clip(path, announcement.text, backend)

    def shutdown(self) -> None:
        """Stop all optional playback without creating either backend."""
        self._music_requested = False
        if self._music_backend is not None:
            self._music_backend.stop()
        self._set_music_active(False)
        self._reset_music_spectrum(force=True)
        if self._speech_backend is not None:
            self._voice_caption_queue.clear()
            self._speech_backend.stop()
        self._set_voice_speaking(False)

    def _ensure_music_backend(self) -> MusicBackend:
        if self._music_backend is None:
            self._music_backend = self._music_factory(self)
            register_state_callback = getattr(
                self._music_backend,
                "set_playback_state_callback",
                None,
            )
            if callable(register_state_callback):
                self._music_backend_reports_state = bool(
                    register_state_callback(self._on_music_backend_state)
                )
            register_finished_callback = getattr(
                self._music_backend,
                "set_track_finished_callback",
                None,
            )
            if callable(register_finished_callback):
                self._music_backend_reports_finished = bool(
                    register_finished_callback(self._on_music_track_finished)
                )
            register_failed_callback = getattr(
                self._music_backend,
                "set_track_failed_callback",
                None,
            )
            if callable(register_failed_callback):
                self._music_backend_reports_failed = bool(
                    register_failed_callback(self._on_music_track_failed)
                )
            register_spectrum_callback = getattr(
                self._music_backend,
                "set_spectrum_callback",
                None,
            )
            if callable(register_spectrum_callback):
                self._music_backend_reports_spectrum = bool(
                    register_spectrum_callback(self._on_music_backend_spectrum)
                )
            self.backend_availability_changed.emit(
                bool(self._music_backend.available),
                bool(
                    self.speech_supported
                    and self._speech_backend
                    and self._speech_backend.available
                ),
            )
        return self._music_backend

    def _ensure_speech_backend(self) -> SpeechBackend:
        backend = self._speech_backend
        if backend is not None and backend.available:
            return backend

        # The default factory degrades a transient Qt construction failure to a
        # no-op backend. Do not cache that sentinel: a later event-loop retry
        # may initialize QSoundEffect successfully once Windows audio is ready.
        backend = self._speech_factory(self._settings, self)
        backend.speaking_changed.connect(self._set_voice_speaking)
        backend.clip_started.connect(self._on_voice_clip_started)
        available = bool(backend.available)
        self._speech_backend = backend if available else None
        self.backend_availability_changed.emit(
            bool(self._music_backend and self._music_backend.available),
            bool(self.speech_supported and available),
        )
        if not available:
            backend.deleteLater()
        return backend

    def _queue_voice_clip(
        self,
        path: Path,
        caption: str,
        backend: SpeechBackend,
        *,
        caption_on_rejection: bool = True,
    ) -> bool:
        """Pair one fixed caption with the exact queued local clip path."""
        resolved = path.resolve()
        item = (resolved, caption)
        self._voice_caption_queue.append(item)
        accepted = bool(backend.play_clip(resolved))
        if accepted:
            return True
        # A rejected clip never starts, so remove only this newest request.
        if self._voice_caption_queue and self._voice_caption_queue[-1] == item:
            self._voice_caption_queue.pop()
        if caption_on_rejection:
            self.caption_requested.emit(caption)
        return False

    @pyqtSlot(object)
    def _on_voice_clip_started(self, path: object) -> None:
        """Release the caption paired with a clip when its audio begins."""
        try:
            started_path = Path(path).resolve()  # type: ignore[arg-type]
        except (OSError, TypeError, ValueError):
            return
        # If a queued asset failed before playback, skip its stale caption and
        # align at the next fixed path the backend actually started.
        while self._voice_caption_queue:
            expected_path, caption = self._voice_caption_queue.popleft()
            if expected_path == started_path:
                self.caption_requested.emit(caption)
                return

    def _voice_path(self, line: VoiceLine) -> Path | None:
        """Resolve one approved filename without accepting runtime path input."""
        return bundled_voice_clips(self._voice_root).get(line)

    def _request_music_playback(self, *, advance: bool = False) -> bool:
        self._music_operation_depth += 1
        try:
            return self._request_music_playback_impl(advance=advance)
        finally:
            self._complete_music_operation()

    def _complete_music_operation(self) -> None:
        self._music_operation_depth = max(0, self._music_operation_depth - 1)
        if (
            self._music_operation_depth != 0
            or self._pending_music_failure_generation is None
        ):
            return
        generation = self._pending_music_failure_generation
        self._pending_music_failure_generation = None
        # A backend double may report failure synchronously from
        # set_source()/play(). Keep recovery outside that call stack, matching
        # the queued contract of the real Qt backend.
        QTimer.singleShot(
            0,
            lambda generation=generation: self._handle_music_track_failed(
                generation
            ),
        )

    def _music_navigation_backend(self) -> MusicBackend | None:
        """Return a usable active backend without changing muted/disabled state."""
        if (
            not self._music_requested
            or self._music_output_muted
            or not self._settings.music_enabled
            or not self._music_playlist
        ):
            self._reset_music_spectrum(force=True)
            return None
        backend = self._music_backend
        if backend is None or not backend.available:
            self._reset_music_spectrum(force=True)
            return None
        return backend

    def _advance_music(
        self,
        backend: MusicBackend,
        *,
        stop_current: bool,
    ) -> bool:
        """Advance through forward history before consuming shuffled entries."""
        current = self._music_source
        self._set_music_active(False)
        self._reset_music_spectrum(force=True)
        if stop_current:
            backend.stop()

        while self._music_forward:
            target = self._music_forward.pop()
            if target == current:
                continue
            if not self._play_specific_music_source(backend, target):
                continue
            self._remember_music_history(current)
            return True

        selected = self._request_music_playback(advance=True)
        if selected:
            if self._music_source != current:
                self._remember_music_history(current)
            return True

        # Manual navigation should not strand a previously playable track just
        # because every other shuffled entry disappeared or failed to decode.
        if stop_current and current is not None:
            self._play_specific_music_source(backend, current)
        return False

    def _play_specific_music_source(
        self,
        backend: MusicBackend,
        source: Path,
    ) -> bool:
        """Validate, select, and play one history entry without touching shuffle."""
        identity = self._music_path_identity(source)
        if identity in self._music_failed_sources:
            return False
        validated = self._validated_music_path(source)
        if validated is None or not backend.set_source(validated):
            self._music_failed_sources.add(identity)
            return False
        self._set_music_source(validated)
        if self._play_selected_music_source(backend):
            return True
        self._retire_current_music_source()
        return False

    def _remember_music_history(self, source: Path | None) -> None:
        if source is None or source == self._music_source:
            return
        if self._music_history and self._music_history[-1] == source:
            return
        self._music_history.append(source)
        overflow = len(self._music_history) - self._MUSIC_HISTORY_LIMIT
        if overflow > 0:
            del self._music_history[:overflow]

    def _request_music_playback_impl(self, *, advance: bool = False) -> bool:
        backend = self._music_backend
        if backend is None:
            self._set_music_active(False)
            return False
        attempted: set[str] = set()

        if not advance and self._music_source is not None:
            attempted.add(self._music_path_identity(self._music_source))
            if self._play_selected_music_source(backend):
                return True
            self._retire_current_music_source()

        while len(attempted) < len(self._music_playlist):
            if not self._select_next_playable_track(backend, attempted):
                self._set_music_active(False)
                return False
            if self._play_selected_music_source(backend):
                return True
            self._retire_current_music_source()

        self._set_music_source(None)
        self._set_music_active(False)
        return False

    def _play_selected_music_source(self, backend: MusicBackend) -> bool:
        accepted = bool(backend.play())
        if not accepted:
            self._set_music_active(False)
            return False
        if not self._music_backend_reports_state:
            # Legacy/simple backends report acceptance synchronously. Real Qt
            # playback stays inactive until its state callback confirms it.
            self._set_music_active(True)
        return True

    def _on_music_track_finished(self) -> None:
        """Advance once after a backend-confirmed natural end of media."""
        if (
            self._advancing_music
            or not self._music_requested
            or self._music_output_muted
            or not self._settings.music_enabled
        ):
            return
        backend = self._music_backend
        if backend is None:
            return
        self._advancing_music = True
        try:
            self._set_music_active(False)
            self._reset_music_spectrum(force=True)
            self._music_operation_depth += 1
            try:
                self._advance_music(backend, stop_current=False)
            finally:
                self._complete_music_operation()
        finally:
            self._advancing_music = False

    def _on_music_track_failed(self) -> None:
        """Recover from a decoder/resource error, never from an ordinary stop."""
        generation = self._music_source_generation
        if self._music_operation_depth:
            self._pending_music_failure_generation = generation
            return
        self._handle_music_track_failed(generation)

    def _handle_music_track_failed(self, generation: int) -> None:
        if (
            generation != self._music_source_generation
            or self._advancing_music
            or not self._music_requested
            or self._music_source is None
            or self._music_output_muted
            or not self._settings.music_enabled
        ):
            return
        self._advancing_music = True
        try:
            self._retire_current_music_source()
            self._set_music_active(False)
            self._reset_music_spectrum(force=True)
            self._music_operation_depth += 1
            try:
                backend = self._music_backend
                if backend is not None:
                    self._advance_music(backend, stop_current=False)
            finally:
                self._complete_music_operation()
        finally:
            self._advancing_music = False

    def _on_music_backend_state(self, active: bool) -> None:
        allowed = (
            self._music_requested
            and self._music_source is not None
            and not self._music_output_muted
            and self._settings.music_enabled
        )
        self._set_music_active(bool(active) and allowed)

    def _on_music_backend_spectrum(self, values: object) -> None:
        allowed = (
            self._music_requested
            and self._music_source is not None
            and not self._music_output_muted
            and self._settings.music_enabled
        )
        if not allowed:
            self._reset_music_spectrum()
            return
        self._set_music_spectrum(values)

    @pyqtSlot(bool)
    def _set_voice_speaking(self, speaking: bool) -> None:
        speaking = bool(speaking)
        if self._voice_speaking == speaking:
            return
        self._voice_speaking = speaking
        self._apply_music_volume()

    def _set_music_active(self, active: bool) -> None:
        active = bool(active)
        if self._music_active == active:
            return
        self._music_active = active
        if not active:
            self._reset_music_spectrum()
        self.music_playback_changed.emit(active, self.music_track_name)

    def _set_music_spectrum(self, values: object, *, force: bool = False) -> None:
        """Normalize a backend frame before publishing the controller contract."""
        try:
            raw = tuple(values)  # type: ignore[arg-type]
            if len(raw) != MUSIC_SPECTRUM_BANDS:
                raise ValueError("unexpected spectrum size")
            frame: list[float] = []
            for value in raw:
                number = float(value)
                frame.append(
                    max(0.0, min(1.0, number))
                    if math.isfinite(number)
                    else 0.0
                )
            normalized = tuple(frame)
        except (TypeError, ValueError, OverflowError):
            normalized = SILENT_MUSIC_SPECTRUM
        if not force and normalized == self._music_spectrum:
            return
        self._music_spectrum = normalized
        self.music_spectrum_changed.emit(normalized)

    def _reset_music_spectrum(self, *, force: bool = False) -> None:
        self._set_music_spectrum(SILENT_MUSIC_SPECTRUM, force=force)

    def _set_music_source(self, source: Path | None) -> None:
        """Publish track identity changes even while playback remains stopped."""
        previous_name = self.music_track_name
        if source != self._music_source:
            self._music_source_generation += 1
        self._music_source = source
        if source is None:
            self._reset_music_spectrum()
        if self.music_track_name != previous_name:
            self.music_playback_changed.emit(
                self._music_active,
                self.music_track_name,
            )

    def _apply_music_volume(self) -> None:
        if self._music_backend is None:
            return
        volume = self._settings.music_volume
        if self._voice_speaking and self._settings.ducking_enabled:
            volume = round(volume * self._settings.ducking_level / 100)
        self._music_backend.set_volume(volume)

    @staticmethod
    def _music_library_from_mapping(
        mapping: Mapping[str, object],
    ) -> tuple[Path, ...]:
        """Read the normalized persisted music-library field defensively."""
        raw = mapping.get("audio_music_library", [])
        if not isinstance(raw, (list, tuple)):
            return ()
        paths: list[Path] = []
        for value in raw:
            if not isinstance(value, (str, os.PathLike)):
                continue
            try:
                paths.append(Path(value).expanduser())
            except (OSError, TypeError, ValueError):
                continue
        return tuple(paths)

    @staticmethod
    def _music_display_name(source: Path) -> str:
        """Turn a local filename into a faithful compact launcher label."""
        curated_title = curated_music_title(source)
        if curated_title is not None:
            return curated_title
        name = " ".join(source.stem.replace("_", " ").split())
        name = re.sub(
            r"^eve online(?: ost)?\s*-\s*(?:\d+\s*-\s*)?",
            "",
            name,
            flags=re.IGNORECASE,
        )
        name = re.sub(
            r"\s*-\s*ambient music$",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        return name or source.stem

    @staticmethod
    def _validated_music_path(value: str | os.PathLike[str] | Path) -> Path | None:
        """Return a readable supported local audio file, otherwise ``None``."""
        try:
            candidate = Path(value).expanduser()
            if candidate.suffix.casefold() not in MUSIC_EXTENSIONS:
                return None
            if not candidate.is_file():
                return None
            # The media decoder remains the format authority, but probing one
            # byte filters inaccessible library entries before shuffle time.
            with candidate.open("rb") as stream:
                stream.read(1)
            return candidate.resolve()
        except (OSError, TypeError, ValueError):
            return None

    def _available_music_tracks(self) -> tuple[Path, ...]:
        candidates = (
            *bundled_music_tracks(self._music_root),
            *discover_curated_music_tracks(self._curated_music_roots),
            *self._configured_music_paths,
        )
        tracks: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            path = self._validated_music_path(candidate)
            if path is None:
                continue
            identity = os.path.normcase(str(path)).casefold()
            if identity in seen:
                continue
            seen.add(identity)
            tracks.append(path)
        return tuple(tracks)

    def _replace_music_playlist(self, tracks: tuple[Path, ...]) -> None:
        self._music_playlist = tracks
        self._music_shuffle_bag.clear()
        self._music_failed_sources.clear()
        self._music_history.clear()
        self._music_forward.clear()

    def _refresh_library_playlist(self) -> None:
        """Apply a saved library change while preserving a still-valid track."""
        playlist = self._available_music_tracks()
        current = self._music_source
        self._replace_music_playlist(playlist)
        if current is None or current in playlist:
            return
        if self._music_backend is not None:
            self._music_backend.stop()
        self._set_music_active(False)
        self._set_music_source(None)

    def _select_next_playable_track(
        self,
        backend: MusicBackend,
        attempted: set[str],
    ) -> bool:
        """Try each playlist entry at most once for this one advancement.

        Validation can race with file deletion and backends may reject a
        nominally supported source. A bounded pass skips those entries without
        ever turning an invalid library into a GUI-thread retry loop.
        """
        candidate_count = len(self._music_playlist)
        # A partially consumed bag may refill during this pass. Twice the
        # library size is sufficient to encounter every distinct entry while
        # still providing a hard upper bound.
        for _ in range(candidate_count * 2):
            if len(attempted) >= candidate_count:
                break
            source = self._take_next_music_track()
            if source is None:
                break
            identity = self._music_path_identity(source)
            if identity in attempted or identity in self._music_failed_sources:
                continue
            attempted.add(identity)
            validated = self._validated_music_path(source)
            if validated is None or not backend.set_source(validated):
                self._music_failed_sources.add(identity)
                continue
            self._set_music_source(validated)
            return True
        self._set_music_source(None)
        return False

    @staticmethod
    def _music_path_identity(source: Path) -> str:
        return os.path.normcase(str(source)).casefold()

    def _retire_current_music_source(self) -> None:
        source = self._music_source
        if source is not None:
            self._music_failed_sources.add(self._music_path_identity(source))
        self._set_music_source(None)
        self._reset_music_spectrum(force=True)

    def _take_next_music_track(self) -> Path | None:
        if not self._music_playlist:
            return None
        if not self._music_shuffle_bag:
            bag = list(self._music_playlist)
            self._music_rng.shuffle(bag)
            if len(bag) > 1 and bag[0] == self._music_last_source:
                replacement = next(
                    (index for index, path in enumerate(bag[1:], 1) if path != bag[0]),
                    None,
                )
                if replacement is not None:
                    bag[0], bag[replacement] = bag[replacement], bag[0]
            self._music_shuffle_bag = bag
        source = self._music_shuffle_bag.pop(0)
        self._music_last_source = source
        return source

    def _as_mapping(self) -> dict[str, object]:
        settings = self._settings
        return {
            "audio_master_muted": settings.master_muted,
            "audio_music_muted": settings.music_muted,
            "audio_music_enabled": settings.music_enabled,
            "audio_music_volume": settings.music_volume,
            "audio_music_library": [
                str(path) for path in self._configured_music_paths
            ],
            "audio_voice_enabled": settings.voice_enabled,
            "audio_voice_volume": settings.voice_volume,
            "audio_voice_engine": settings.voice_engine,
            "audio_voice_locale": settings.voice_locale,
            "audio_voice_name": settings.voice_name,
            "audio_voice_rate": settings.voice_rate,
            "audio_voice_pitch": settings.voice_pitch,
            "audio_announce_character_names": settings.announce_character_names,
            "audio_announce_results": settings.announce_results,
            "audio_ducking_enabled": settings.ducking_enabled,
            "audio_ducking_level": settings.ducking_level,
        }
