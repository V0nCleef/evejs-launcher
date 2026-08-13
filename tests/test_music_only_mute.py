"""Focused contracts for the persistent title-bar music-only mute."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src import config
from src.audio.backends import MusicBackend, SpeechBackend
from src.audio.controller import AudioController
from src.audio.events import VoiceEvent, VoiceLine
from src.audio.settings import AudioSettings


class _MusicRecorder(MusicBackend):
    available = True

    def __init__(self) -> None:
        self.muted: list[bool] = []
        self.play_count = 0
        self.stop_count = 0

    def set_source(self, path: Path) -> bool:
        return path.is_file()

    def set_volume(self, percent: int) -> None:
        del percent

    def set_muted(self, muted: bool) -> None:
        self.muted.append(bool(muted))

    def play(self) -> bool:
        self.play_count += 1
        return True

    def stop(self) -> None:
        self.stop_count += 1


class _VoiceRecorder(SpeechBackend):
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


def _voice_catalog(root: Path) -> Path:
    root.mkdir()
    clips: dict[str, dict[str, str]] = {}
    for line in VoiceLine:
        path = root / line.filename
        path.write_bytes(b"fixed voice fixture")
        clips[line.value] = {
            "text": line.text,
            "filename": line.filename,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "clips": clips}),
        encoding="utf-8",
    )
    return root


def test_music_mute_is_normalized_independently_from_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)

    assert config.load()["audio_music_muted"] is False
    assert AudioSettings.from_mapping({}).music_muted is False
    assert AudioSettings.from_mapping(
        {"audio_master_muted": True}
    ).music_muted is False

    config_file.write_text(json.dumps({"music_muted": True}), encoding="utf-8")
    migrated = config.load()
    assert migrated["audio_music_muted"] is True
    assert migrated["audio_master_muted"] is False
    assert "music_muted" not in migrated

    config_file.write_text(
        json.dumps({"audio_master_muted": True}),
        encoding="utf-8",
    )
    transferred = config.load()
    assert transferred["audio_music_muted"] is True
    assert transferred["audio_master_muted"] is False

    config_file.write_text(
        json.dumps(
            {
                "audio_master_muted": True,
                "audio_music_muted": False,
            }
        ),
        encoding="utf-8",
    )
    explicit = config.load()
    assert explicit["audio_music_muted"] is False
    assert explicit["audio_master_muted"] is False

    config_file.write_text(
        json.dumps({"audio_music_muted": "yes"}),
        encoding="utf-8",
    )
    assert config.load()["audio_music_muted"] is False


def test_music_only_mute_stops_and_resumes_music_without_interrupting_lyra(
    qapp,
    tmp_path: Path,
) -> None:
    del qapp
    track = tmp_path / "soundscape.wav"
    track.write_bytes(b"music fixture")
    music = _MusicRecorder()
    voice = _VoiceRecorder(None)
    controller = AudioController(
        {},
        music_factory=lambda _parent: music,
        speech_factory=lambda _settings, _parent: voice,
        voice_root=_voice_catalog(tmp_path / "voice"),
    )
    mute_states: list[bool] = []
    controller.music_muted_changed.connect(mute_states.append)

    assert controller.start_music(track) is True
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATING) is True
    assert [path.name for path in voice.played] == ["clients_terminating.wav"]
    assert voice.stop_count == 0

    controller.set_music_muted(True)

    assert controller.music_muted is True
    assert controller.master_muted is False
    assert mute_states == [True]
    assert music.muted[-1] is True
    assert music.stop_count == 1
    assert controller.music_active is False
    assert voice.stop_count == 0
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATED) is True
    assert [path.name for path in voice.played] == [
        "clients_terminating.wav",
        "clients_terminated.wav",
    ]

    controller.set_music_muted(False)

    assert controller.music_muted is False
    assert mute_states == [True, False]
    assert music.muted[-1] is False
    assert music.play_count == 2
    assert controller.music_active is True
    assert voice.stop_count == 0


def test_master_mute_remains_the_stronger_music_and_voice_safety_switch(
    qapp,
    tmp_path: Path,
) -> None:
    del qapp
    track = tmp_path / "soundscape.wav"
    track.write_bytes(b"music fixture")
    music = _MusicRecorder()
    voice = _VoiceRecorder(None)
    controller = AudioController(
        {},
        music_factory=lambda _parent: music,
        speech_factory=lambda _settings, _parent: voice,
        voice_root=_voice_catalog(tmp_path / "voice"),
    )

    assert controller.start_music(track) is True
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATING) is True
    controller.set_master_muted(True)

    assert controller.master_muted is True
    assert controller.music_muted is False
    assert music.muted[-1] is True
    assert voice.stop_count == 1

    controller.set_music_muted(True)
    controller.set_master_muted(False)
    assert music.muted[-1] is True
    assert controller.music_active is False
    assert controller.announce(VoiceEvent.CLIENTS_TERMINATED) is True
