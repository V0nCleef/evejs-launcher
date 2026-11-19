"""Optional mod cues: background cache verification/decoding and one PCM stream."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer

from .game_cues import CueSelection, prepare_cues
from .settings import _bool, _percent

log = logging.getLogger(__name__)


class GameCueController(QObject):
    def __init__(self, audio, settings, *, loader=prepare_cues, player_factory=None):
        super().__init__(audio)
        self.audio = audio
        self._loader = loader
        self._player_factory = player_factory
        self._player = None
        self._pool = None
        self._future = None
        self._loading_client = ''
        self._prepared_client = None
        self._client = CueSelection('')
        self._selection_settings = {}
        self._clips = {}
        self._closed = False
        self.error = ''
        self.enabled, self.volume = True, .65
        self._poll = QTimer(self, interval=40)
        self._poll.timeout.connect(self._collect)
        audio.master_muted_changed.connect(self._mute_changed)
        self.apply_settings(settings)

    def apply_settings(self, settings):
        self.enabled = _bool(settings, 'audio_sound_effects_enabled', True)
        self.volume = _percent(settings, 'audio_sound_effects_volume', 65) / 100
        for name in ('client_path', 'evejs_root', 'linux_setup_root', 'runtime_backend'):
            if name in settings:
                self._selection_settings[name] = settings[name]
        self._client = CueSelection.from_mapping(self._selection_settings)
        if self._prepared_client != self._client:
            self._clips = {}
            self.stop()
        if not self.enabled:
            self.stop()
        elif self._player is not None:
            self._player.set_volume(self.volume)
        self._ensure_prepared()

    def _ensure_prepared(self):
        if (self._closed or not self.enabled or not self._client.client
                or self._prepared_client == self._client or self._future is not None):
            return
        # Creating the pool never reads the client cache on the GUI thread.
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='game-cue-loader')
        from src import config
        scratch = Path(config.CONFIG_DIR) / 'cache' / 'sound-effects'
        self._loading_client = self._client
        self._future = self._pool.submit(self._loader, self._loading_client, scratch)
        self._poll.start()

    def _collect(self):
        if self._future is None or not self._future.done():
            return
        future, loaded = self._future, self._loading_client
        self._future = None
        self._poll.stop()
        try:
            clips, error = future.result(), ''
        except Exception as exc:
            clips, error = {}, str(exc) or type(exc).__name__
        if not self._closed and loaded == self._client:
            self._prepared_client = loaded
            self._clips = clips
            self.error = error
            if error:
                log.info('Optional mod sounds unavailable: %s', error)
        self._ensure_prepared()

    def _mute_changed(self, muted):
        if muted:
            self.stop()

    def play(self, cue):
        if self._closed or not self.enabled or self.audio.master_muted or cue not in self._clips:
            return False
        if self._player is None:
            factory = self._player_factory
            if factory is None:
                from .game_pcm import GameAudio
                factory = GameAudio
            try:
                self._player = factory(self, volume=self.volume)
            except Exception as exc:
                self.error = str(exc) or type(exc).__name__
                return False
        self._player.play(self._clips[cue])
        return True

    def stop(self):
        if self._player is not None:
            self._player.stop()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._poll.stop()
        self._clips = {}
        if self._player is not None:
            self._player.shutdown()
        if self._future is not None:
            self._future.cancel()
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
