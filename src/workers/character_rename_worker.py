"""Retained one-shot worker for the offline character rename."""
from collections.abc import Callable
import logging

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.character_rename import CharacterRenameError, CharacterRenameResult

log = logging.getLogger(__name__)


class CharacterRenameWorker(QObject):
    cleanup = pyqtSignal()

    def __init__(self, operation: Callable[[], CharacterRenameResult]) -> None:
        super().__init__()
        self._operation = operation
        self.outcome: CharacterRenameResult | CharacterRenameError | None = None

    @pyqtSlot()
    def run(self) -> None:
        try:
            self.outcome = self._operation()
        except CharacterRenameError as exc:
            self.outcome = exc
        except Exception:  # noqa: BLE001 - failed runtime discovery must fail closed
            log.exception("Unable to complete the character rename")
            self.outcome = CharacterRenameError("Unable to verify the character rename. See launcher logs.")
        finally:
            self.cleanup.emit()
