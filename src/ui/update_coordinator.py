"""Update orchestration over the window's single retained operation state.

These functions keep worker/result ownership in the host window while separating
update dialogs, scheduling and persisted preferences from server/client logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import sys
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog

from .. import config
from ..core.platform import hard_exit
from ..updater.checker import UpdateChecker
from ..updater.dialog import UpdateDialog
from ..updater.installer import UpdateInstallWorker
from ..updater.progress_dialog import UpdateProgressDialog
from ..widgets.localized_dialogs import LocalizedMessageBox as QMessageBox

log = logging.getLogger(__name__)


def _on_update_available(
    self, version: str, changelog: str, download_url: str, published_at: str
) -> None:
    """Handler for when an update is found."""
    self._latest_version = version
    self._latest_changelog = changelog
    self._latest_download_url = download_url
    self._latest_published = published_at
    self._title_bar.show_update_available(version)
    self._settings_page.set_update_check_done(True)


def _on_update_clicked(self) -> None:
    """Show the update dialog and handle download/install or skip."""
    from ..constants import APP_VERSION

    dlg = UpdateDialog(
        current_version=APP_VERSION,
        new_version=self._latest_version,
        changelog=self._latest_changelog,
        download_url=self._latest_download_url,
        published_at=self._latest_published,
        parent=self,
    )
    dlg.exec()

    if dlg.result() == QDialog.DialogCode.Accepted:
        self._begin_update_install()

    elif dlg.skip_requested:
        # Keep the application-owned snapshot current so later saves cannot
        # silently resurrect a skipped version.
        skipped = set(self._cfg.get("update_skip_versions", []))
        skipped.add(self._latest_version)
        self._cfg["update_skip_versions"] = sorted(skipped)
        config.save(self._cfg)
        self._title_bar.set_update_up_to_date()


def _begin_update_install(self) -> None:
    """Show progress before downloading, then retain the worker through teardown."""
    from ..core.application_operations import update_active, update_blocked
    if update_active(self):
        return
    if update_blocked(self):
        QMessageBox.information(
            self, "Operation In Progress",
            "Wait for the active operation to finish before installing a launcher update.",
        )
        return
    if not self._latest_download_url:
        QMessageBox.warning(
            self,
            "Update Unavailable",
            "This release does not include a downloadable launcher package.",
        )
        return

    dialog = UpdateProgressDialog(self._latest_version, parent=self)
    worker = UpdateInstallWorker(
        self._latest_download_url,
        sys.executable,
        parent=self,
    )
    self._update_progress_dialog = dialog
    self._update_install_worker = worker
    self._update_install_result = None
    self._update_install_thread_finished = False
    self._set_operation_controls_busy(True)

    worker.stage_changed.connect(dialog.set_stage)
    worker.download_progress.connect(dialog.set_download_progress)
    worker.completed.connect(self._on_update_install_completed)
    worker.finished.connect(
        self._on_update_install_thread_finished,
        Qt.ConnectionType.QueuedConnection,
    )

    dialog.show()
    QApplication.processEvents()
    try:
        worker.start()
    except Exception as exc:
        self._update_install_worker = None
        worker.deleteLater()
        self._set_operation_controls_busy(False)
        dialog.show_error(str(exc) or "The update worker could not start.")


def _on_update_install_completed(self, success: bool, error: str) -> None:
    """Record the worker result without racing its QThread teardown."""
    if self._update_install_worker is None or self._update_install_result is not None:
        return
    self._update_install_result = (success, error)
    self._finish_update_install_if_ready()


def _on_update_install_thread_finished(self) -> None:
    """Wait for both the result and finished signal before releasing the worker."""
    self._update_install_thread_finished = True
    self._finish_update_install_if_ready()


def _finish_update_install_if_ready(self) -> None:
    """Surface preparation failures or exit only after the agent has started."""
    if self._update_install_result is None or not self._update_install_thread_finished:
        return

    success, error = self._update_install_result
    worker = self._update_install_worker
    # Keep the guard held during the delayed handoff after worker teardown.
    self._update_handoff_pending = success
    self._update_install_worker = None
    self._update_install_result = None
    self._update_install_thread_finished = False
    if worker is not None:
        worker.deleteLater()

    dialog = self._update_progress_dialog
    if not success:
        self._set_operation_controls_busy(False)
        if dialog is not None:
            dialog.show_error(error)
        return

    if dialog is not None:
        dialog.set_stage("install", "Switching to the standalone updater…")
    QTimer.singleShot(750, hard_exit)


def _create_update_checker(self) -> UpdateChecker:
    """Create an update worker whose lifetime is safe during window close."""
    checker = UpdateChecker(self)
    checker.update_available.connect(self._on_update_available)
    checker.up_to_date.connect(self._on_update_up_to_date)
    checker.check_failed.connect(
        lambda msg: log.warning("Update check failed: %s", msg)
    )
    checker.finished.connect(
        self._on_update_checker_finished,
        Qt.ConnectionType.QueuedConnection,
    )
    return checker


def _start_update_checker(self, checker: UpdateChecker) -> None:
    """Start one checker unless shutdown has begun or it already runs."""
    if self._close_in_progress or checker.isRunning():
        return
    if checker not in self._active_update_checkers:
        self._active_update_checkers.append(checker)
    checker.check()


def _start_automatic_update_check(self) -> None:
    """Run the reusable startup/periodic checker only while the window is open."""
    if not self._cfg.get("update_auto_check", True):
        return
    self._start_update_checker(self._update_checker)


def _apply_update_settings(self) -> None:
    """Reconfigure the timer from the current application-owned preferences."""
    timer = getattr(self, "_update_timer", None)
    if timer is None:
        return
    timer.stop()
    hours = self._cfg.get("update_check_interval_hours", 6)
    if self._cfg.get("update_auto_check", True) and hours > 0:
        timer.start(int(hours * 3600 * 1000))


def _has_running_update_checker(self) -> bool:
    """Return whether a retained update worker still owns a native thread."""
    return any(checker.isRunning() for checker in self._active_update_checkers)


def _on_update_checker_finished(self) -> None:
    """Release completed checker tracking and resume a deferred window close."""
    self._active_update_checkers = [
        checker
        for checker in self._active_update_checkers
        if checker.isRunning()
    ]
    if self._close_in_progress:
        QTimer.singleShot(0, self.close)


def _on_update_up_to_date(self, version: str = "") -> None:
    """Handler for when the app is already up to date."""
    self._title_bar.set_update_up_to_date()
    self._cfg["update_last_checked"] = datetime.now(timezone.utc).isoformat()
    config.save(self._cfg)
    self._settings_page.set_update_check_done(True)


def _on_manual_update_check(self) -> None:
    """Triggered by the Settings page's 'Check for Updates' button."""
    if self._close_in_progress:
        return
    # Visual feedback — title bar spinner + settings button shows checking
    self._title_bar.set_update_checking()
    self._settings_page.set_update_checking()

    # Create a fresh checker (QThread can only start once)
    checker = self._create_update_checker()
    checker.up_to_date.connect(lambda v="": self._settings_page.set_update_check_done(True))
    checker.check_failed.connect(lambda msg: self._on_check_failed_from_settings(msg))
    self._start_update_checker(checker)


def _on_check_failed_from_settings(self, error: str) -> None:
    """Handle a failed check triggered from Settings."""
    log.warning("Manual update check failed: %s", error)
    self._title_bar.set_update_up_to_date()
    self._settings_page.set_update_check_done(False)
