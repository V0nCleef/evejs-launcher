"""One-shot worker for preparing and starting an EVE client off the GUI thread."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.launcher import ClientLaunchContext


log = logging.getLogger(__name__)


class LaunchedProcess(Protocol):
    """Minimal process surface retained by the launcher's process tracker."""

    pid: int

    def poll(self) -> int | None: ...


@dataclass(frozen=True)
class ClientLaunchRequest:
    """Immutable inputs for one profile preparation and client spawn."""

    username: str
    character_name: str
    evejs_root: str
    client_path: str
    profiles_root: Path
    launch_context: ClientLaunchContext


@dataclass(frozen=True)
class ClientLaunchResult:
    """Successful process creation attributed to its exact request."""

    request: ClientLaunchRequest
    process: LaunchedProcess


@dataclass(frozen=True)
class ClientLaunchFailure:
    """Bounded launch failure safe to deliver back to the GUI thread."""

    request: ClientLaunchRequest
    error_type: str
    message: str


ClientLaunchOperation = Callable[[ClientLaunchRequest], LaunchedProcess]


class ClientLaunchWorker(QObject):
    """Run profile filesystem work and process creation outside Qt's GUI thread."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        request: ClientLaunchRequest,
        operation: ClientLaunchOperation,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._operation = operation

    @pyqtSlot()
    def run(self) -> None:
        try:
            process = self._operation(self._request)
        except Exception as exc:  # noqa: BLE001 - OS/process failures vary
            log.exception(
                "Client launch worker failed for account %s",
                self._request.username,
            )
            message = str(exc).strip() or type(exc).__name__
            self.failed.emit(
                ClientLaunchFailure(
                    request=self._request,
                    error_type=type(exc).__name__,
                    message=message,
                )
            )
        else:
            self.completed.emit(ClientLaunchResult(self._request, process))
        finally:
            self.cleanup.emit()
