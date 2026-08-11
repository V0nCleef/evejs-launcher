"""One-shot Qt worker for offline EveJS character and account deletion."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.character_deletion import (
    CharacterDeletionRequest,
    CharacterDeletionResult,
    delete_character_or_account,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CharacterDeletionFailure:
    request: CharacterDeletionRequest
    error_type: str
    message: str


CharacterDeletionOperation = Callable[
    [CharacterDeletionRequest], CharacterDeletionResult
]


class CharacterDeletionWorker(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        request: CharacterDeletionRequest,
        operation: CharacterDeletionOperation = delete_character_or_account,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._operation = operation

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self._operation(self._request)
        except Exception as exc:  # noqa: BLE001 - Node/SQLite failures vary
            log.exception("Character deletion worker failed")
            self.failed.emit(
                CharacterDeletionFailure(
                    self._request,
                    type(exc).__name__,
                    str(exc).strip() or type(exc).__name__,
                )
            )
        else:
            self.completed.emit(result)
        finally:
            self.cleanup.emit()
