"""Subprocess regressions for Qt Multimedia failures that can abort pythonw."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_real_qsoundeffect_preview_uses_native_zero_argument_status_signal(
    tmp_path: Path,
) -> None:
    """A real status transition must not cross a mismatched Python slot.

    ``QSoundEffect.statusChanged`` has no payload. PyQt terminates the process
    on Windows when its callback requires one argument, so this must remain a
    subprocess smoke instead of an in-process unit test.
    """
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        from PyQt6.QtCore import QCoreApplication, QTimer
        from src.audio.controller import AudioController

        app = QCoreApplication([])
        controller = AudioController({
            "audio_master_muted": False,
            "audio_voice_enabled": True,
            "audio_voice_volume": 80,
        })
        assert controller.prepare_voice_preview() is True
        captions = []
        controller.caption_requested.connect(captions.append)
        QTimer.singleShot(50, lambda: controller.preview_voice())
        QTimer.singleShot(3500, app.quit)
        result = app.exec()
        controller.shutdown()
        assert captions == ["LYRA online. Shipboard systems ready."]
        raise SystemExit(result)
        """
    )
    env = os.environ.copy()
    env["APPDATA"] = str(tmp_path / "appdata")
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, (
        f"native LYRA preview exited {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_real_qsoundeffect_loads_new_market_start_recording(
    tmp_path: Path,
) -> None:
    """The expanded catalog must load and start through real Qt Multimedia."""
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        from PyQt6.QtCore import QCoreApplication, QTimer
        from src.audio.controller import AudioController
        from src.audio.events import VoiceEvent

        app = QCoreApplication([])
        controller = AudioController({
            "audio_master_muted": False,
            "audio_voice_enabled": True,
            "audio_voice_volume": 100,
        })
        captions = []
        controller.caption_requested.connect(captions.append)
        QTimer.singleShot(
            50,
            lambda: controller.announce(VoiceEvent.MARKET_SERVER_LAUNCHING),
        )
        QTimer.singleShot(3500, app.quit)
        result = app.exec()
        controller.shutdown()
        assert captions == ["Launching market server."]
        raise SystemExit(result)
        """
    )
    env = os.environ.copy()
    env["APPDATA"] = str(tmp_path / "appdata")
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, (
        f"native LYRA Market announcement exited {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_real_qmediaplayer_emits_pcm_spectrum_and_lifecycle_zero(
    tmp_path: Path,
) -> None:
    """The shipped Qt runtime must decode one real track into spectrum frames."""
    repo_root = Path(__file__).resolve().parents[1]
    track = repo_root / "assets" / "audio" / "music" / "celestial_transit.wav"
    assert track.is_file()
    script = textwrap.dedent(
        f"""
        from pathlib import Path

        from PyQt6.QtCore import QCoreApplication, QTimer
        from PyQt6.QtMultimedia import QAudioBufferOutput

        from src.audio.backends import (
            MUSIC_SPECTRUM_BANDS,
            SILENT_MUSIC_SPECTRUM,
            QtMusicBackend,
        )
        from src.audio.controller import AudioController

        assert QAudioBufferOutput is not None
        app = QCoreApplication([])
        controller = AudioController({{
            "audio_master_muted": False,
            "audio_music_muted": False,
            "audio_music_enabled": True,
            "audio_music_volume": 5,
        }})
        frames = []
        errors = []
        state = {{"nonzero": False, "zero_after_stop": False, "timed_out": False}}

        def silence_and_finish():
            try:
                controller.set_music_muted(True)
                controller.stop_music()
                state["zero_after_stop"] = bool(
                    frames and frames[-1] == SILENT_MUSIC_SPECTRUM
                )
            except Exception as exc:
                errors.append(f"silence failed: {{exc!r}}")
            app.quit()

        def observe_spectrum(values):
            try:
                frame = tuple(values)
                if len(frame) != MUSIC_SPECTRUM_BANDS:
                    errors.append(f"invalid frame length: {{len(frame)}}")
                    app.quit()
                    return
                if not all(0.0 <= float(value) <= 1.0 for value in frame):
                    errors.append(f"out-of-range frame: {{frame!r}}")
                    app.quit()
                    return
                frames.append(frame)
                if any(value > 0.0 for value in frame) and not state["nonzero"]:
                    state["nonzero"] = True
                    # Never stop QMediaPlayer from inside its decoded-buffer
                    # signal stack; queue the lifecycle edge through Qt.
                    QTimer.singleShot(0, silence_and_finish)
            except Exception as exc:
                errors.append(f"spectrum callback failed: {{exc!r}}")
                app.quit()

        def start_playback():
            try:
                accepted = controller.start_music(
                    Path({str(track)!r})
                )
                if not accepted:
                    errors.append("controller rejected celestial_transit.wav")
                    app.quit()
                    return
                backend = controller._music_backend
                if not isinstance(backend, QtMusicBackend):
                    errors.append(f"unexpected backend: {{type(backend)!r}}")
                    app.quit()
                    return
                if backend._buffer_output is None:
                    errors.append("QAudioBufferOutput was not attached")
                    app.quit()
                    return
                if not controller._music_backend_reports_spectrum:
                    errors.append("controller did not register the backend spectrum callback")
                    app.quit()
                    return
                if backend._player.audioBufferOutput() is not backend._buffer_output:
                    errors.append("buffer output is not attached to the playback player")
                    app.quit()
            except Exception as exc:
                errors.append(f"startup failed: {{exc!r}}")
                app.quit()

        def deadline():
            state["timed_out"] = True
            app.quit()

        controller.music_spectrum_changed.connect(observe_spectrum)
        QTimer.singleShot(50, start_playback)
        QTimer.singleShot(8000, deadline)
        result = app.exec()
        controller.shutdown()

        assert result == 0
        assert not state["timed_out"], "no nonzero PCM spectrum within 8 seconds"
        assert not errors, errors
        assert state["nonzero"] is True
        assert state["zero_after_stop"] is True
        assert frames[-1] == SILENT_MUSIC_SPECTRUM
        raise SystemExit(result)
        """
    )
    env = os.environ.copy()
    env["APPDATA"] = str(tmp_path / "appdata")
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, (
        f"native music spectrum smoke exited {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
