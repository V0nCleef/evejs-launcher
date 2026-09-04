"""One-shot, GUI-independent DLSS5 rollback worker."""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.dlss5_uninstall import (
    DLSS5UninstallRequest,
    DLSS5UninstallResult,
    uninstall_dlss5_client_mod,
)


class DLSS5UninstallWorker(QObject):
    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        request: DLSS5UninstallRequest,
        *,
        executor: Callable[[DLSS5UninstallRequest], DLSS5UninstallResult]
        = uninstall_dlss5_client_mod,
    ) -> None:
        super().__init__()
        self._request = request
        self._executor = executor

    @property
    def request(self) -> DLSS5UninstallRequest:
        return self._request

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._executor(self._request)
            if not isinstance(result, DLSS5UninstallResult):
                raise TypeError("The DLSS5 uninstaller returned an invalid result.")
        except Exception as exc:
            result = DLSS5UninstallResult(
                request=self._request, success=False,
                message=str(exc) or "DLSS5 uninstall failed; inspect retained rollback state.",
            )
        try:
            self.completed.emit(result)
        finally:
            self.cleanup.emit()
