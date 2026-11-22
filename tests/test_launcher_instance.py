"""Exercise the real launcher entry point with two independent Qt processes."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_probe(mode: str, state: Path) -> int:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow

    import main
    from src import app, wizard
    from src.core import runtime_handoff
    from src.ui.tray_controller import LauncherTray

    def ready(on_exit):
        (state / "ready").touch()
        timer = QTimer(QApplication.instance())
        timer.setInterval(20)

        def stop_on_request():
            if (state / "exit").exists():
                timer.stop()
                on_exit()

        timer.timeout.connect(stop_on_request)
        timer.start()

    class ProbeWizard(QDialog):
        def __init__(self):
            super().__init__()
            QTimer.singleShot(0, lambda: ready(self.reject))

        def activateWindow(self):
            super().activateWindow()
            (state / "restored").touch()

    class ProbeWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self._cfg = {"runtime_backend": mode}
            with (state / "owner").open("x", encoding="utf-8") as owner:
                owner.write(str(os.getpid()))
            self._tray_controller = LauncherTray(self, available=lambda: False)
            restore = self._tray_controller.restore

            def record_restore():
                restore()
                assert self.isVisible()
                if (state / "ready").exists():
                    assert self.isMaximized()
                (state / "restored").touch()

            self._tray_controller.restore = record_restore
            QTimer.singleShot(0, self.hide_in_tray)

        def hide_in_tray(self):
            self.showMaximized()
            self._tray_controller._restore_maximized = self.isMaximized()
            self.hide()
            ready(QApplication.quit)

    wizard.SetupWizard = ProbeWizard
    app.MainWindow = ProbeWindow
    runtime_handoff.resume_runtime_handoff = lambda *_args: runtime_handoff.ResumeResult()
    main._initialize_ui_language = lambda _locale: {"evejs_root": "" if mode == "wizard" else str(state)}
    sys.argv = [sys.argv[0]]
    return main.main()


def _start(state: Path, mode: str) -> subprocess.Popen:
    python_path = os.pathsep.join((str(ROOT), *(path for path in sys.path if path)))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=python_path,
               PYTHONDONTWRITEBYTECODE="1")
    for key in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[key] = str(state / key.lower())
    # A Windows venv redirector can spawn a different process than the handle
    # owned by Popen. Run the actual interpreter so the crash test owns its PID.
    python = sys._base_executable if os.name == "nt" else sys.executable
    return subprocess.Popen([python, __file__, mode, str(state)], cwd=ROOT,
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _wait_for(path: Path, process: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + 20
    while not path.exists():
        if process is not None and process.poll() is not None:
            pytest.fail(f"Launcher exited while waiting for {path.name}: {process.communicate()}")
        assert time.monotonic() < deadline, f"Launcher did not create {path.name}"
        time.sleep(0.02)


def _assert_exit(process: subprocess.Popen) -> None:
    output = process.communicate(timeout=20)
    assert process.returncode == 0, output


def _clean_up(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize("mode", ["native", "docker_compose", "wizard"])
def test_repeat_launch_restores_the_first_window(tmp_path, mode):
    primary = _start(tmp_path, mode)
    processes = [primary]
    try:
        _wait_for(tmp_path / "ready", primary)
        secondaries = [_start(tmp_path, mode) for _ in range(3)]
        processes.extend(secondaries)
        for secondary in secondaries:
            _assert_exit(secondary)
        _wait_for(tmp_path / "restored", primary)
        logs = list(tmp_path.rglob("launcher.log"))
        assert len(logs) == 1
        assert logs[0].read_text(encoding="utf-8").count("Launcher startup version=") == 1
        if mode != "wizard":
            assert (tmp_path / "owner").read_text(encoding="utf-8") == str(primary.pid)
        (tmp_path / "exit").touch()
        _assert_exit(primary)
    finally:
        _clean_up(processes)


def test_another_settings_directory_and_crash_recovery(tmp_path):
    original = tmp_path / "original"
    independent = tmp_path / "independent"
    original.mkdir()
    independent.mkdir()
    first = _start(original, "native")
    processes = [first]
    try:
        _wait_for(original / "ready", first)
        other = _start(independent, "native")
        processes.append(other)
        _wait_for(independent / "ready", other)
        first.kill()
        first.communicate(timeout=10)
        (original / "ready").unlink()
        (original / "owner").unlink()
        replacement = _start(original, "native")
        processes.append(replacement)
        _wait_for(original / "ready", replacement)
        assert (original / "owner").read_text(encoding="utf-8") == str(replacement.pid)
        for state, process in ((original, replacement), (independent, other)):
            (state / "exit").touch()
            _assert_exit(process)
    finally:
        _clean_up(processes)


def test_simultaneous_startup_elects_one_launcher(tmp_path):
    processes = [_start(tmp_path, "native") for _ in range(3)]
    try:
        _wait_for(tmp_path / "ready")
        owner = int((tmp_path / "owner").read_text(encoding="utf-8"))
        primary = next(process for process in processes if process.pid == owner)
        for process in processes:
            if process is not primary:
                _assert_exit(process)
        _wait_for(tmp_path / "restored", primary)
        (tmp_path / "exit").touch()
        _assert_exit(primary)
    finally:
        _clean_up(processes)


if __name__ == "__main__":
    raise SystemExit(_run_probe(sys.argv[1], Path(sys.argv[2])))
