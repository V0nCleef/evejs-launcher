"""Serial Qt scheduler for launching EVE clients without blocking the UI."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
from typing import TypeVar

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot


T = TypeVar("T")
log = logging.getLogger(__name__)


class ClientLaunchQueue(QObject):
    """Launch queued items one at a time, yielding to the Qt event loop between them."""

    progress = pyqtSignal(int, int, int)  # attempted, total, successfully launched
    finished = pyqtSignal(int, int, bool)  # attempted, successfully launched, cancelled

    def __init__(
        self,
        items: Iterable[T],
        launch_item: Callable[[T], bool],
        *,
        stagger_ms: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._launch_item = launch_item
        self._stagger_ms = max(0, int(stagger_ms))
        self._attempted = 0
        self._succeeded = 0
        self._active = False
        self._started = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._launch_next)

    @property
    def is_active(self) -> bool:
        """Return whether the queue still owns future launch attempts."""
        return self._active

    def start(self) -> None:
        """Start the queue, launching the first item without a delay."""
        if self._started:
            return
        self._started = True
        if not self._items:
            self.finished.emit(0, 0, False)
            return
        self._active = True
        self._launch_next()

    def cancel(self) -> None:
        """Cancel future launches without touching clients already started."""
        if not self._active:
            return
        self._timer.stop()
        self._finish(cancelled=True)

    @pyqtSlot()
    def _launch_next(self) -> None:
        if not self._active:
            return

        item = self._items[self._attempted]
        self._attempted += 1
        try:
            launched = bool(self._launch_item(item))
        except Exception:  # noqa: BLE001 - one failed item must not stop the queue
            log.exception("Unexpected client-launch queue callback failure")
            launched = False
        if launched:
            self._succeeded += 1

        self.progress.emit(self._attempted, len(self._items), self._succeeded)
        if self._attempted >= len(self._items):
            self._finish(cancelled=False)
            return
        self._timer.start(self._stagger_ms)

    def _finish(self, *, cancelled: bool) -> None:
        self._active = False
        self._timer.stop()
        self.finished.emit(self._attempted, self._succeeded, cancelled)
