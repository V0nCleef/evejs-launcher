"""One-shot worker for launcher-native mod removal."""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.mod_management import (
    ManagedModRemovalRequest,
    ManagedModRemovalResult,
    remove_managed_mod,
)


class ManagedModRemovalWorker(QObject):
    """Run a verified external uninstaller away from the Qt GUI thread."""

    completed = pyqtSignal(object)

    def __init__(
        self,
        request: ManagedModRemovalRequest,
        *,
        executor: Callable[[ManagedModRemovalRequest], ManagedModRemovalResult]
        = remove_managed_mod,
    ) -> None:
        super().__init__()
        self._request = request
        self._executor = executor

    @property
    def request(self) -> ManagedModRemovalRequest:
        return self._request

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._executor(self._request)
            if not isinstance(result, ManagedModRemovalResult):
                raise TypeError("The mod removal executor returned an invalid result.")
        except Exception as exc:
            result = ManagedModRemovalResult(
                request=self._request,
                success=False,
                message=str(exc) or "The registered mod uninstaller failed.",
            )
        self.completed.emit(result)


__all__ = ["ManagedModRemovalWorker"]
