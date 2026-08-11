"""One-shot Qt worker for client overview patch installation and restore."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.overview_patch import (
    OverviewPatchStatus,
    patch_overview_client,
    restore_overview_client,
)


log = logging.getLogger(__name__)


class OverviewPatchAction(Enum):
    PATCH = "patch"
    RESTORE = "restore"


@dataclass(frozen=True)
class OverviewPatchResult:
    action: OverviewPatchAction
    client_path: Path
    status: OverviewPatchStatus


@dataclass(frozen=True)
class OverviewPatchFailure:
    action: OverviewPatchAction
    client_path: Path
    error_type: str
    message: str


class OverviewPatchWorker(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        action: OverviewPatchAction,
        client_path: str | Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._action = action
        self._client_path = Path(client_path)

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self._action is OverviewPatchAction.PATCH:
                status = patch_overview_client(self._client_path)
            else:
                status = restore_overview_client(self._client_path)
        except Exception as exc:  # noqa: BLE001 - archive/OS failures vary
            log.exception("Overview client patch operation failed")
            self.failed.emit(
                OverviewPatchFailure(
                    self._action,
                    self._client_path,
                    type(exc).__name__,
                    str(exc).strip() or type(exc).__name__,
                )
            )
        else:
            self.completed.emit(
                OverviewPatchResult(self._action, self._client_path, status)
            )
        finally:
            self.cleanup.emit()
