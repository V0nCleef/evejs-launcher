"""Regression contract: navigation and selection have no interface cues."""

from __future__ import annotations

import inspect

from src.app import MainWindow
from src.audio import backends
from src.audio.controller import AudioController


def test_audio_controller_has_no_interface_cue_seam() -> None:
    parameters = inspect.signature(AudioController.__init__).parameters

    assert "ui_sound_factory" not in parameters
    assert "ui_sound_root" not in parameters
    assert not hasattr(AudioController, "play_ui_sound")
    assert not hasattr(backends, "UiSoundBackend")
    assert not hasattr(backends, "QtUiSoundBackend")
    assert not hasattr(backends, "create_ui_sound_backend")


def test_main_window_has_no_interface_cue_dispatch() -> None:
    assert not hasattr(MainWindow, "_play_ui_sound")
