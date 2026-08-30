"""Independent, branded update handoff run from the staged new launcher build."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import tempfile
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


_LAUNCHER_RUNTIME_DIR = "_internal"
_UPDATE_BACKUP_DIR = ".evejs-launcher-update-backup"
_UPDATE_CLEANUP_MARKER = Path(_LAUNCHER_RUNTIME_DIR) / ".evejs-update-cleanup.json"
_UPDATE_STAGING_PREFIX = "evejs_launcher_update_"


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
    """Replace only launcher-owned entries and retain a rollback until verified."""
    try:
        source_dir = handoff.source_dir.resolve()
        target_dir = handoff.target_dir.resolve()
    except (OSError, RuntimeError) as exc:
        return UpdateApplyResult(
            False,
            "The update paths could not be resolved. Your existing installation "
            f"has not been changed. Details: {exc}",
        )
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

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return UpdateApplyResult(
            False,
            "The launcher installation folder is unavailable. Your existing "
            f"installation has not been changed. Details: {exc}",
        )

    backup_dir = target_dir / _UPDATE_BACKUP_DIR
    _emit_stage(stage_callback, "install", "Creating a safe backup…")
    if _path_exists(backup_dir):
        return UpdateApplyResult(
            False,
            "A previous launcher update backup is still present. It was left "
            "untouched, and your existing installation has not been changed. "
            "Restart the launcher to retry cleanup before updating again.",
        )

    moved_old_install, backup_error = _backup_owned_install(
        target_dir,
        backup_dir,
        handoff.exe_name,
        retry_delay_seconds,
    )
    if backup_error:
        return UpdateApplyResult(
            False,
            backup_error,
        )

    _emit_stage(stage_callback, "install", "Copying new launcher files…")
    try:
        _copy_install_tree(
            source_dir,
            target_dir,
            handoff.exe_name,
            copy_progress_callback,
        )
        installed_exe = target_dir / handoff.exe_name
        if not installed_exe.is_file():
            raise OSError("The copied launcher executable was not found")
        if not (target_dir / _LAUNCHER_RUNTIME_DIR).is_dir():
            raise OSError("The copied launcher runtime directory was not found")
    except OSError as exc:
        restored = _restore_old_install(
            target_dir,
            backup_dir,
            handoff.exe_name,
            retry_delay_seconds,
        )
        recovery = (
            "The previous launcher was restored."
            if restored
            else f"The previous launcher files remain in {backup_dir}."
        )
        return UpdateApplyResult(
            False,
            "The new launcher files could not be copied. "
            f"{recovery} Other files in the folder were left untouched. Details: {exc}",
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


def schedule_update_cleanup(
    install_dir: Path,
    source_dir: Path,
    backup_dir: Path | None,
    exe_name: str,
) -> bool:
    """Record validated artifacts for cleanup by the restarted launcher.

    Keeping the cleanup inside the normal launcher process avoids spawning a
    detached shell whose only job is to delete directories after a delay.
    """
    try:
        install_dir = install_dir.resolve()
    except (OSError, RuntimeError):
        return False

    staging_root = _find_update_staging_root(source_dir)
    if (
        staging_root is None
        or _paths_overlap(staging_root, install_dir)
        or not install_dir.is_dir()
    ):
        return False

    if not _is_valid_exe_name(exe_name):
        return False

    payload: dict[str, str] = {
        "source_root": str(staging_root),
        "exe_name": exe_name,
    }
    if backup_dir is not None:
        if not _is_expected_backup_dir(
            backup_dir,
            install_dir,
        ) or not _is_launcher_backup_tree(backup_dir, exe_name):
            return False
        try:
            payload["backup_dir"] = str(backup_dir.resolve())
        except (OSError, RuntimeError):
            return False

    marker_path = install_dir / _UPDATE_CLEANUP_MARKER
    temporary_marker = marker_path.with_suffix(".tmp")
    try:
        temporary_marker.write_text(json.dumps(payload), encoding="utf-8")
        temporary_marker.replace(marker_path)
    except (OSError, RuntimeError):
        try:
            temporary_marker.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def cleanup_pending_update(install_dir: Path) -> bool:
    """Remove only the validated artifacts left by a completed update."""
    try:
        install_dir = install_dir.resolve()
    except (OSError, RuntimeError):
        return False

    marker_path = install_dir / _UPDATE_CLEANUP_MARKER
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return True
    except (OSError, json.JSONDecodeError):
        _remove_cleanup_marker(marker_path)
        return False

    source_value = payload.get("source_root") if isinstance(payload, dict) else None
    backup_value = payload.get("backup_dir") if isinstance(payload, dict) else None
    exe_name = payload.get("exe_name") if isinstance(payload, dict) else None
    if (
        not isinstance(source_value, str)
        or not isinstance(backup_value, (str, type(None)))
        or not isinstance(exe_name, str)
        or not _is_valid_exe_name(exe_name)
    ):
        _remove_cleanup_marker(marker_path)
        return False

    staging_root = _find_update_staging_root(Path(source_value))
    backup_dir = Path(backup_value) if backup_value else None
    if staging_root is None or _paths_overlap(staging_root, install_dir) or (
        backup_dir is not None and not _is_expected_backup_dir(backup_dir, install_dir)
    ):
        _remove_cleanup_marker(marker_path)
        return False
    if backup_dir is not None and not _is_launcher_backup_tree(backup_dir, exe_name):
        return False

    try:
        if staging_root.exists():
            shutil.rmtree(staging_root)
    except OSError:
        return False

    if backup_dir is not None and not _remove_launcher_backup(
        backup_dir,
        exe_name,
        retry_delay_seconds=0.1,
    ):
        return False

    _remove_cleanup_marker(marker_path)
    return not marker_path.exists()


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
            cleanup_scheduled = schedule_update_cleanup(
                self._handoff.target_dir,
                self._handoff.source_dir,
                result.backup_dir,
                self._handoff.exe_name,
            )
            launch_installed_launcher(result.installed_exe)
        except OSError as exc:
            self.completed.emit(
                False,
                "The new launcher was installed, but could not be restarted. "
                f"You can launch it manually. Details: {exc}",
            )
            return

        if not cleanup_scheduled:
            retained_path = result.backup_dir or self._handoff.source_dir
            self.completed.emit(
                False,
                "The new launcher was installed and restarted, but its rollback "
                f"cleanup could not be scheduled. Keep {retained_path} until "
                "the restarted launcher has been checked, then remove only that "
                "launcher backup before the next update.",
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
        self.set_handoff_mode()
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

        self.set_stage("restart", "Restarting EveJS Launcher…")
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
    if not _is_valid_exe_name(exe_name):
        return "The downloaded update has an invalid launcher name. The installation is unchanged."
    if not source_dir.is_dir():
        return "The downloaded update files are missing. Your existing installation is unchanged."
    source_exe = source_dir / exe_name
    source_runtime = source_dir / _LAUNCHER_RUNTIME_DIR
    if (
        not source_exe.is_file()
        or _is_reparse_point(source_exe)
        or not source_runtime.is_dir()
        or _is_reparse_point(source_runtime)
        or not _runtime_tree_is_complete(source_runtime)
    ):
        return "The downloaded update is incomplete. Your existing installation is unchanged."
    if _path_exists(target_dir) and not target_dir.is_dir():
        return "The launcher installation path is not a folder. The installation is unchanged."
    if (
        source_dir == target_dir
        or source_dir in target_dir.parents
        or target_dir in source_dir.parents
    ):
        return (
            "The update source overlaps the installation folder. "
            "Your existing installation is unchanged."
        )
    return ""


def _find_update_staging_root(source_dir: Path) -> Path | None:
    """Return the trusted temp root that contains a staged update, if any."""
    try:
        source_dir = source_dir.resolve()
        temp_dir = Path(tempfile.gettempdir()).resolve()
    except (OSError, RuntimeError):
        return None

    for candidate in (source_dir, *source_dir.parents):
        if candidate.parent == temp_dir and candidate.name.startswith(_UPDATE_STAGING_PREFIX):
            return candidate
    return None


def _is_expected_backup_dir(backup_dir: Path, install_dir: Path) -> bool:
    """Accept only the launcher-owned rollback directory inside the install root."""
    try:
        expected = install_dir / _UPDATE_BACKUP_DIR
        return backup_dir.resolve() == expected
    except (OSError, RuntimeError):
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    """Return whether either resolved directory contains the other."""
    return first == second or first in second.parents or second in first.parents


def _is_valid_exe_name(exe_name: str) -> bool:
    return Path(exe_name).name == exe_name and exe_name.lower().endswith(".exe")


def _launcher_owned_names(exe_name: str) -> tuple[str, str]:
    return (exe_name, _LAUNCHER_RUNTIME_DIR)


def _path_exists(path: Path) -> bool:
    """Like ``lexists``: include broken links so they are never ignored."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _is_reparse_point(path: Path) -> bool:
    """Reject links/junctions at launcher-owned paths without following them."""
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(attributes & 0x400)


def _runtime_tree_is_complete(runtime_dir: Path) -> bool:
    """Require at least one ordinary runtime file and no linked descendants."""
    found_file = False
    try:
        for path in runtime_dir.rglob("*"):
            if _is_reparse_point(path):
                return False
            if path.is_file():
                found_file = True
            elif not path.is_dir():
                return False
    except OSError:
        return False
    return found_file


def _is_launcher_backup_tree(backup_dir: Path, exe_name: str) -> bool:
    """Ensure cleanup can never recurse through user-owned backup content."""
    if not _path_exists(backup_dir):
        return True
    if not backup_dir.is_dir() or _is_reparse_point(backup_dir):
        return False

    owned_names = set(_launcher_owned_names(exe_name))
    try:
        children = list(backup_dir.iterdir())
    except OSError:
        return False
    return all(
        child.name in owned_names and not _is_reparse_point(child)
        for child in children
    )


def _remove_cleanup_marker(marker_path: Path) -> None:
    """Best-effort marker cleanup; a failed removal is retried on next launch."""
    try:
        marker_path.unlink(missing_ok=True)
    except OSError:
        pass


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
            if _is_reparse_point(path):
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            elif _path_exists(path):
                path.unlink()
        except OSError:
            time.sleep(max(0.05, retry_delay_seconds))
            continue
        return not _path_exists(path)
    return not _path_exists(path)


def _rename_with_retry(
    source: Path,
    destination: Path,
    retry_delay_seconds: float,
    attempts: int = 20,
) -> bool:
    for _attempt in range(attempts):
        try:
            source.rename(destination)
            return True
        except OSError:
            time.sleep(max(0.05, retry_delay_seconds))
    return False


def _backup_owned_install(
    target_dir: Path,
    backup_dir: Path,
    exe_name: str,
    retry_delay_seconds: float,
) -> tuple[bool, str]:
    """Move only the EXE and ``_internal`` into the rollback directory."""
    try:
        backup_dir.mkdir()
    except OSError:
        return False, (
            "The launcher rollback directory could not be created. "
            "Your existing installation and all neighboring files were left untouched."
        )

    moved_names: list[str] = []
    for name in _launcher_owned_names(exe_name):
        source_path = target_dir / name
        if not _path_exists(source_path):
            continue
        if _is_reparse_point(source_path) or not _rename_with_retry(
            source_path,
            backup_dir / name,
            retry_delay_seconds,
        ):
            restored = _restore_backup_entries(
                target_dir,
                backup_dir,
                moved_names,
                retry_delay_seconds,
            )
            if restored:
                return False, (
                    "The existing launcher files are still in use. The original "
                    "launcher was restored, and all neighboring files were left untouched."
                )
            return False, (
                "The existing launcher files could not be moved or fully restored. "
                f"Recovery files remain in {backup_dir}. All neighboring files "
                "were left untouched."
            )
        moved_names.append(name)

    if not moved_names:
        try:
            backup_dir.rmdir()
        except OSError:
            return False, (
                "The empty launcher rollback directory could not be removed. "
                f"It was left at {backup_dir}; neighboring files were not changed."
            )
        return False, ""
    return True, ""


def _restore_backup_entries(
    target_dir: Path,
    backup_dir: Path,
    names: Sequence[str],
    retry_delay_seconds: float,
) -> bool:
    restored = True
    for name in reversed(names):
        backup_path = backup_dir / name
        if _path_exists(backup_path) and not _rename_with_retry(
            backup_path,
            target_dir / name,
            retry_delay_seconds,
        ):
            restored = False
    if restored:
        try:
            backup_dir.rmdir()
        except OSError:
            restored = False
    return restored


def _copy_install_tree(
    source_dir: Path,
    target_dir: Path,
    exe_name: str,
    progress_callback: CopyProgressCallback | None,
) -> None:
    """Copy exactly the two launcher-owned roots; ignore every other sibling."""
    source_runtime = source_dir / _LAUNCHER_RUNTIME_DIR
    target_runtime = target_dir / _LAUNCHER_RUNTIME_DIR
    target_runtime.mkdir(parents=True, exist_ok=True)

    files: list[Path] = [source_dir / exe_name]
    for source_path in sorted(source_runtime.rglob("*")):
        if _is_reparse_point(source_path):
            raise OSError(f"Update package contains a linked path: {source_path.name}")
        relative_path = source_path.relative_to(source_dir)
        target_path = target_dir / relative_path
        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
        elif source_path.is_file():
            files.append(source_path)
        else:
            raise OSError(f"Update package contains an unsupported path: {source_path.name}")

    total = len(files)
    for completed, source_path in enumerate(files, start=1):
        target_path = target_dir / source_path.relative_to(source_dir)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        if progress_callback is not None:
            progress_callback(completed, total)


def _restore_old_install(
    target_dir: Path,
    backup_dir: Path,
    exe_name: str,
    retry_delay_seconds: float,
) -> bool:
    """Remove/restore only launcher-owned entries; never delete the install root."""
    restored = True
    for name in _launcher_owned_names(exe_name):
        target_path = target_dir / name
        if _path_exists(target_path) and not _remove_path_with_retry(
            target_path,
            retry_delay_seconds,
        ):
            restored = False

    for name in reversed(_launcher_owned_names(exe_name)):
        backup_path = backup_dir / name
        target_path = target_dir / name
        if not _path_exists(backup_path):
            continue
        if _path_exists(target_path) or not _rename_with_retry(
            backup_path,
            target_path,
            retry_delay_seconds,
        ):
            restored = False

    if _path_exists(backup_dir):
        try:
            backup_dir.rmdir()
        except OSError:
            restored = False
    return restored


def _remove_launcher_backup(
    backup_dir: Path,
    exe_name: str,
    retry_delay_seconds: float,
) -> bool:
    """Delete only validated launcher-owned backup entries, then the empty shell."""
    if not _is_launcher_backup_tree(backup_dir, exe_name):
        return False
    if not _path_exists(backup_dir):
        return True

    for name in _launcher_owned_names(exe_name):
        backup_path = backup_dir / name
        if _path_exists(backup_path) and not _remove_path_with_retry(
            backup_path,
            retry_delay_seconds,
        ):
            return False
    try:
        backup_dir.rmdir()
    except OSError:
        return False
    return not _path_exists(backup_dir)
