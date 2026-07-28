"""Worker and compatibility wrapper for the launcher self-update flow."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal


_logger = logging.getLogger(__name__)


class UpdateInstallWorker(QThread):
    """Prepare an update off the GUI thread and report live progress."""

    stage_changed = pyqtSignal(str, str)
    download_progress = pyqtSignal(int, int)
    completed = pyqtSignal(bool, str)

    def __init__(
        self,
        download_url: str,
        current_exe_path: str | Path,
        parent=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent)
        self._download_url = download_url
        self._current_exe_path = Path(current_exe_path)

    def run(self) -> None:
        """Download, validate, and stage the new launcher build."""
        from src.core.platform import run_updater

        try:
            success = run_updater(
                self._download_url,
                self._current_exe_path,
                progress_callback=self.download_progress.emit,
                status_callback=self.stage_changed.emit,
            )
        except Exception:  # noqa: BLE001 — retain the running launcher on failure.
            _logger.exception("Failed to prepare launcher update")
            self.completed.emit(
                False,
                "The update could not be prepared. The launcher is still running; "
                "please check your connection and try again.",
            )
            return

        if success:
            self.completed.emit(True, "")
            return

        self.completed.emit(
            False,
            "The update package could not be downloaded or verified. "
            "The launcher is still running, so you can try again.",
        )


def download_and_install(
    download_url: str,
    current_exe_path: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bool:
    """Download the staged build and hand off to its independent updater mode.

    Delegates to :func:`src.core.platform.run_updater` which is the
    platform-specific staging implementation.

    Returns
    -------
    bool
        *True* when the download succeeds and the helper has been launched.
        *False* when the download itself fails (caller should report the
        error to the user rather than exiting).
    """
    from src.core.platform import run_updater
    return run_updater(
        download_url,
        current_exe_path,
        progress_callback=progress_callback,
    )
