"""One-shot mod I/O worker; orchestration and widgets remain in the GUI thread."""
from dataclasses import dataclass
import logging
import time
import uuid
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from src.core.mod_contributions import RemovalReviewRequired

log = logging.getLogger(__name__)


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
        operation_id = uuid.uuid4().hex
        started = time.monotonic()
        log.info("Mod operation started operation_id=%s", operation_id)
        try:
            result = ModOperationResult(self.token, True, self.operation())
        except RemovalReviewRequired as exc:
            log.warning("Mod operation requires review operation_id=%s elapsed_seconds=%.3f: %s",
                        operation_id, time.monotonic() - started, exc)
            result = ModOperationResult(self.token, False, value=exc.review, error=str(exc))
        except Exception as exc:
            log.exception("Mod operation failed operation_id=%s elapsed_seconds=%.3f",
                          operation_id, time.monotonic() - started)
            result = ModOperationResult(self.token, False, error=str(exc) or type(exc).__name__)
        else:
            log.info("Mod operation completed operation_id=%s elapsed_seconds=%.3f",
                     operation_id, time.monotonic() - started)
        try:
            self.completed.emit(result)
        finally:
            self.cleanup.emit()
