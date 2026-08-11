"""One-shot Qt worker for offline EveJS account and character creation."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.character_creation import (
    CharacterCreationRequest,
    CharacterCreationResult,
    create_character,
)


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CharacterCreationFailure:
    request: CharacterCreationRequest
    error_type: str
    message: str


CharacterCreationOperation = Callable[
    [CharacterCreationRequest], CharacterCreationResult
]


class CharacterCreationWorker(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        request: CharacterCreationRequest,
        operation: CharacterCreationOperation = create_character,
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
            log.exception("Character creation worker failed")
            self.failed.emit(
                CharacterCreationFailure(
                    self._request,
                    type(exc).__name__,
                    str(exc).strip() or type(exc).__name__,
                )
            )
        else:
            self.completed.emit(result)
        finally:
            self.cleanup.emit()
