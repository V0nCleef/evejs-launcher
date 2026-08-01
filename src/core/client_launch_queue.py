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


class AsyncClientLaunchQueue(QObject):
    """Serial queue whose current item completes through ``item_finished``.

    ``start_item`` must return quickly after starting asynchronous work. The
    owner calls ``item_finished`` exactly once when that work has fully torn
    down, ensuring that even a zero-millisecond stagger never overlaps two
    profile mutations or client process starts.
    """

    progress = pyqtSignal(int, int, int)  # attempted, total, successfully launched
    finished = pyqtSignal(int, int, bool)  # attempted, successfully launched, cancelled

    def __init__(
        self,
        items: Iterable[T],
        start_item: Callable[[T], bool],
        *,
        stagger_ms: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = list(items)
        self._start_item = start_item
        self._stagger_ms = max(0, int(stagger_ms))
        self._attempted = 0
        self._succeeded = 0
        self._active = False
        self._started = False
        self._waiting_for_item = False
        self._cancel_requested = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_next)

    @property
    def is_active(self) -> bool:
        """Return whether the queue owns an in-flight or future launch."""
        return self._active

    def start(self) -> None:
        """Start the first asynchronous launch without a stagger."""
        if self._started:
            return
        self._started = True
        if not self._items:
            self.finished.emit(0, 0, False)
            return
        self._active = True
        self._start_next()

    def cancel(self) -> None:
        """Cancel future launches while allowing an in-flight launch to finish."""
        if not self._active:
            return
        self._cancel_requested = True
        self._timer.stop()
        if not self._waiting_for_item:
            self._finish(cancelled=True)

    def item_finished(self, succeeded: bool) -> None:
        """Complete the current item and schedule the next eligible launch."""
        if not self._active or not self._waiting_for_item:
            return
        self._waiting_for_item = False
        if succeeded:
            self._succeeded += 1
        self.progress.emit(self._attempted, len(self._items), self._succeeded)

        if self._cancel_requested:
            self._finish(cancelled=True)
            return
        if self._attempted >= len(self._items):
            self._finish(cancelled=False)
            return
        self._timer.start(self._stagger_ms)

    @pyqtSlot()
    def _start_next(self) -> None:
        if not self._active or self._waiting_for_item:
            return
        item = self._items[self._attempted]
        self._attempted += 1
        self._waiting_for_item = True
        try:
            started = bool(self._start_item(item))
        except Exception:  # noqa: BLE001 - one failed item must not stop the queue
            log.exception("Unexpected asynchronous client-launch callback failure")
            started = False
        if not started:
            QTimer.singleShot(0, lambda: self.item_finished(False))

    def _finish(self, *, cancelled: bool) -> None:
        self._active = False
        self._waiting_for_item = False
        self._timer.stop()
        self.finished.emit(self._attempted, self._succeeded, cancelled)
