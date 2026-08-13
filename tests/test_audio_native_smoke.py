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
