"""Qt workers for backend-selected account and character-detail loading."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.db import Account
from src.core.runtime.data import DataSourceError, RuntimeDataSelection
from src.core.runtime.docker_cli import redact_docker_diagnostic


DataSelectionFactory = Callable[[], RuntimeDataSelection]


@dataclass(frozen=True)
class AccountLoadResult:
    """One immutable account result attributed to an exact runtime target."""

    selection: RuntimeDataSelection
    accounts: tuple[Account, ...]
    token: object | None = None


@dataclass(frozen=True)
class CharacterDetailResult:
    """One detail result attributed to an exact runtime target."""

    selection: RuntimeDataSelection
    character_id: int
    detail: dict | None
    token: object | None = None


@dataclass(frozen=True)
class DataLoadFailure:
    """Bounded diagnostic that is safe to cross into the GUI thread."""

    code: str
    message: str
    token: object | None = None


class _DataLoader(QObject):
    """Shared cancellation and private-safe error delivery for one-shot workers."""

    failed = pyqtSignal(object)
    progress = pyqtSignal(str)
    cleanup = pyqtSignal()

    def __init__(
        self,
        selection_factory: DataSelectionFactory,
        *,
        token: object | None = None,
    ) -> None:
        super().__init__()
        self._selection_factory = selection_factory
        self._token = token
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def _cancelled(self) -> bool:
        return self._cancel.is_set()

    def _emit_failure(self, error: BaseException) -> None:
        if self._cancelled():
            return
        if isinstance(error, DataSourceError):
            code = error.code
            message = redact_docker_diagnostic(error, limit=160)
        else:
            code = "unexpected_error"
            message = "Character data could not be loaded safely."
        self.failed.emit(DataLoadFailure(code, message, self._token))


class AccountLoader(_DataLoader):
    """Load accounts off the GUI thread through the selected runtime source."""

    completed = pyqtSignal(object)

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self._cancelled():
                return
            self.progress.emit("Loading accounts...")
            selection = self._selection_factory()
            accounts = selection.data_source.load_accounts()
            if not self._cancelled():
                self.completed.emit(
                    AccountLoadResult(selection, tuple(accounts), self._token)
                )
        except (DataSourceError, OSError, ValueError) as exc:
            self._emit_failure(exc)
        except Exception as exc:  # noqa: BLE001 - privacy boundary for worker faults
            self._emit_failure(exc)
        finally:
            self.cleanup.emit()


class CharacterDetailLoader(_DataLoader):
    """Load one character detail off the GUI thread through the same seam."""

    completed = pyqtSignal(object)

    def __init__(
        self,
        selection_factory: DataSelectionFactory,
        char_id: int,
        *,
        token: object | None = None,
    ) -> None:
        super().__init__(selection_factory, token=token)
        self._char_id = int(char_id)

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self._cancelled():
                return
            self.progress.emit("Loading character details...")
            selection = self._selection_factory()
            detail = selection.data_source.get_character_detail(self._char_id)
            if not self._cancelled():
                self.completed.emit(
                    CharacterDetailResult(
                        selection,
                        self._char_id,
                        detail,
                        self._token,
                    )
                )
        except (DataSourceError, OSError, ValueError) as exc:
            self._emit_failure(exc)
        except Exception as exc:  # noqa: BLE001 - privacy boundary for worker faults
            self._emit_failure(exc)
        finally:
            self.cleanup.emit()
