"""One-shot mod I/O worker; orchestration and widgets remain in the GUI thread."""
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from src.core.mod_contributions import RemovalReviewRequired


@dataclass(frozen=True)
class ModOperationResult:
    token: object
    success: bool
    value: object = None
    error: str = ""


class ModOperationWorker(QObject):
    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(self, token: object, operation: Callable[[], object]):
        super().__init__()
        self.token, self.operation = token, operation

    @pyqtSlot()
    def run(self):
        try:
            result = ModOperationResult(self.token, True, self.operation())
        except RemovalReviewRequired as exc:
            result = ModOperationResult(self.token, False, value=exc.review, error=str(exc))
        except Exception as exc:
            result = ModOperationResult(self.token, False, error=str(exc) or type(exc).__name__)
        try:
            self.completed.emit(result)
        finally:
            self.cleanup.emit()
