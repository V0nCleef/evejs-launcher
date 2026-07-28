"""Independent, branded update handoff run from the staged new launcher build."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from src.updater.progress_dialog import UpdateProgressDialog


StageCallback = Callable[[str, str], None]
CopyProgressCallback = Callable[[int, int], None]
ProcessRunningCheck = Callable[[int], bool]
SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class UpdateHandoff:
    """Arguments passed from the old launcher to the staged new build."""

    target_dir: Path
    source_dir: Path
    exe_name: str
    parent_pid: int


@dataclass(frozen=True)
class UpdateApplyResult:
    """Outcome of safely copying a staged onedir build into place."""

    success: bool
    error: str = ""
    installed_exe: Path | None = None
    backup_dir: Path | None = None


def parse_update_handoff_args(argv: Sequence[str]) -> UpdateHandoff:
    """Parse the private ``--apply-update`` command line used by the updater."""
    if "--apply-update" not in argv:
        raise ValueError("Missing --apply-update flag")

    values: dict[str, str] = {}
    for flag in ("--target-dir", "--source-dir", "--exe-name", "--parent-pid"):
        try:
            index = argv.index(flag)
            values[flag] = argv[index + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Missing value for {flag}") from exc

    exe_name = values["--exe-name"]
    if Path(exe_name).name != exe_name or not exe_name.lower().endswith(".exe"):
        raise ValueError("Invalid update executable name")

    try:
        parent_pid = int(values["--parent-pid"])
    except ValueError as exc:
        raise ValueError("Invalid update parent process ID") from exc

    return UpdateHandoff(
        target_dir=Path(values["--target-dir"]),
        source_dir=Path(values["--source-dir"]),
        exe_name=exe_name,
        parent_pid=parent_pid,
    )


def apply_staged_update(
    handoff: UpdateHandoff,
    *,
    is_process_running: ProcessRunningCheck | None = None,
    stage_callback: StageCallback | None = None,
    copy_progress_callback: CopyProgressCallback | None = None,
    wait_timeout_seconds: float = 25.0,
    retry_delay_seconds: float = 0.5,
    settle_seconds: int = 15,
    sleep_func: SleepFunction | None = None,
) -> UpdateApplyResult:
    """Swap the install directory while preserving a rollback copy until verified."""
    source_dir = handoff.source_dir.resolve()
    target_dir = handoff.target_dir.resolve()
    validation_error = _validate_handoff(source_dir, target_dir, handoff.exe_name)
    if validation_error:
        return UpdateApplyResult(False, validation_error)

    _emit_stage(stage_callback, "install", "Waiting for the launcher to close…")
    process_check = is_process_running or _is_process_running
    if not _wait_for_process_exit(
        handoff.parent_pid,
        process_check,
        wait_timeout_seconds,
        retry_delay_seconds,
    ):
        return UpdateApplyResult(
            False,
            "The previous launcher did not close in time. Your existing installation "
            "has not been changed.",
        )

    _wait_for_file_locks_to_settle(
        settle_seconds,
        stage_callback,
        sleep_func or time.sleep,
    )

    backup_dir = target_dir.with_name(f"{target_dir.name}.old")
    _emit_stage(stage_callback, "install", "Creating a safe backup…")
    if not _remove_path_with_retry(backup_dir, retry_delay_seconds):
        return UpdateApplyResult(
            False,
            "A previous update backup is still in use. Your existing installation "
            "has not been changed.",
        )

    if target_dir.exists() and not _rename_with_retry(
        target_dir,
        backup_dir,
        retry_delay_seconds,
    ):
        return UpdateApplyResult(
            False,
            "The existing launcher files are still in use. Your existing installation "
            "has not been changed.",
        )

    moved_old_install = backup_dir.exists()
    _emit_stage(stage_callback, "install", "Copying new launcher files…")
    try:
        _copy_install_tree(source_dir, target_dir, copy_progress_callback)
        installed_exe = target_dir / handoff.exe_name
        if not installed_exe.is_file():
            raise OSError("The copied launcher executable was not found")
    except OSError as exc:
        _restore_old_install(target_dir, backup_dir, retry_delay_seconds)
        return UpdateApplyResult(
            False,
            "The new files could not be copied. The previous launcher was restored. "
            f"Details: {exc}",
        )

    _emit_stage(stage_callback, "install", "Verifying installed launcher…")
    return UpdateApplyResult(
        True,
        installed_exe=installed_exe,
        backup_dir=backup_dir if moved_old_install else None,
    )


def launch_installed_launcher(exe_path: Path) -> None:
    """Launch through Explorer to isolate the fresh PyInstaller process."""
    subprocess.Popen(
        ["explorer.exe", str(exe_path)],
        creationflags=subprocess.DETACHED_PROCESS,
        close_fds=True,
    )


def schedule_update_cleanup(source_dir: Path, backup_dir: Path | None) -> None:
    """Remove the staged package and old backup after the fresh launcher starts."""
    paths = [source_dir]
    if backup_dir is not None:
        paths.append(backup_dir)
    commands = [f'rmdir /s /q "{path}"' for path in paths if path.exists()]
    if not commands:
        return

    command = "timeout /t 8 /nobreak > nul & " + " & ".join(commands)
    subprocess.Popen(
        ["cmd.exe", "/d", "/c", command],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


class UpdateHandoffWorker(QThread):
    """Perform the blocking directory swap without freezing the update window."""

    stage_changed = pyqtSignal(str, str)
    copy_progress = pyqtSignal(int, int)
    completed = pyqtSignal(bool, str)

    def __init__(self, handoff: UpdateHandoff, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._handoff = handoff

    def run(self) -> None:
        """Safely copy, restart, and schedule cleanup of the staged package."""
        result = apply_staged_update(
            self._handoff,
            stage_callback=self.stage_changed.emit,
            copy_progress_callback=self.copy_progress.emit,
        )
        if not result.success or result.installed_exe is None:
            self.completed.emit(False, result.error or "The update could not be completed.")
            return

        try:
            launch_installed_launcher(result.installed_exe)
            schedule_update_cleanup(self._handoff.source_dir, result.backup_dir)
        except OSError as exc:
            self.completed.emit(
                False,
                "The new launcher was installed, but could not be restarted. "
                f"You can launch it manually. Details: {exc}",
            )
            return

        self.completed.emit(True, "")


class UpdateHandoffWindow(UpdateProgressDialog):
    """Standalone update surface that remains visible after the old app exits."""

    def __init__(self, handoff: UpdateHandoff, version: str) -> None:
        super().__init__(version)
        self._handoff = handoff
        self._worker: UpdateHandoffWorker | None = None
        self._result: tuple[bool, str] | None = None
        self._thread_finished = False
        self._started = False
        self.set_stage("install", "Waiting for the launcher to close…")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._start_handoff)

    def _start_handoff(self) -> None:
        """Start once the standalone window has painted its first frame."""
        worker = UpdateHandoffWorker(self._handoff, self)
        worker.stage_changed.connect(self.set_stage)
        worker.copy_progress.connect(self.set_copy_progress)
        worker.completed.connect(self._on_handoff_completed)
        worker.finished.connect(self._on_handoff_thread_finished)
        self._worker = worker
        worker.start()

    def _on_handoff_completed(self, success: bool, error: str) -> None:
        self._result = (success, error)
        self._finish_handoff_if_ready()

    def _on_handoff_thread_finished(self) -> None:
        self._thread_finished = True
        self._finish_handoff_if_ready()

    def _finish_handoff_if_ready(self) -> None:
        if self._result is None or not self._thread_finished:
            return

        success, error = self._result
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()

        if not success:
            self.show_error(error)
            return

        self.set_stage("install", "Restarting EveJS Launcher…")
        QTimer.singleShot(850, self._close_after_restart)

    def _close_after_restart(self) -> None:
        """Leave only after Explorer has begun the clean launcher restart."""
        self.allow_close()
        QApplication.quit()


# ── File-swap helpers ─────────────────────────────────────────────────────


def _emit_stage(callback: StageCallback | None, stage: str, detail: str) -> None:
    """Publish a visible phase without coupling the swap logic to Qt."""
    if callback is not None:
        callback(stage, detail)


def _validate_handoff(source_dir: Path, target_dir: Path, exe_name: str) -> str:
    if not source_dir.is_dir():
        return "The downloaded update files are missing. Your existing installation is unchanged."
    if not (source_dir / exe_name).is_file():
        return "The downloaded update is incomplete. Your existing installation is unchanged."
    if source_dir == target_dir or source_dir in target_dir.parents or target_dir in source_dir.parents:
        return "The update source overlaps the installation folder. Your existing installation is unchanged."
    return ""


def _wait_for_process_exit(
    process_id: int,
    process_check: ProcessRunningCheck,
    timeout_seconds: float,
    retry_delay_seconds: float,
) -> bool:
    if process_id <= 0:
        return True

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while process_check(process_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, retry_delay_seconds))
    return True


def _wait_for_file_locks_to_settle(
    settle_seconds: int,
    stage_callback: StageCallback | None,
    sleep_func: SleepFunction,
) -> None:
    """Retain the proven post-exit pause while reporting each visible second."""
    for seconds_remaining in range(max(0, settle_seconds), 0, -1):
        unit = "second" if seconds_remaining == 1 else "seconds"
        _emit_stage(
            stage_callback,
            "install",
            f"Releasing old launcher files ({seconds_remaining} {unit} remaining)…",
        )
        sleep_func(1.0)


def _is_process_running(process_id: int) -> bool:
    """Return whether a Windows process is still alive without signalling it."""
    if process_id <= 0:
        return False

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
        False,
        process_id,
    )
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _remove_path_with_retry(path: Path, retry_delay_seconds: float, attempts: int = 20) -> bool:
    for _attempt in range(attempts):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError:
            time.sleep(max(0.05, retry_delay_seconds))
            continue
        return not path.exists()
    return not path.exists()


def _rename_with_retry(source: Path, destination: Path, retry_delay_seconds: float, attempts: int = 20) -> bool:
    for _attempt in range(attempts):
        try:
            source.rename(destination)
            return True
        except OSError:
            time.sleep(max(0.05, retry_delay_seconds))
    return False


def _copy_install_tree(
    source_dir: Path,
    target_dir: Path,
    progress_callback: CopyProgressCallback | None,
) -> None:
    files: list[Path] = []
    for source_path in source_dir.rglob("*"):
        relative_path = source_path.relative_to(source_dir)
        target_path = target_dir / relative_path
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        elif source_path.is_file():
            files.append(source_path)

    total = len(files)
    if total == 0:
        raise OSError("The update package has no files")

    for completed, source_path in enumerate(files, start=1):
        target_path = target_dir / source_path.relative_to(source_dir)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        if progress_callback is not None:
            progress_callback(completed, total)


def _restore_old_install(target_dir: Path, backup_dir: Path, retry_delay_seconds: float) -> None:
    _remove_path_with_retry(target_dir, retry_delay_seconds)
    if backup_dir.exists() and not target_dir.exists():
        _rename_with_retry(backup_dir, target_dir, retry_delay_seconds)
