"""Serial Qt scheduler for launching EVE clients without blocking the UI."""
from __future__ import annotations

from collections.abc import Callable, Iterable
import logging
from queue import Empty, SimpleQueue
import threading
import time
from typing import TypeVar

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot


T = TypeVar("T")
log = logging.getLogger(__name__)


class ClientWindowReadinessGate(QObject):
    """Wait for one launched process to own a usable window without blocking Qt."""

    finished = pyqtSignal(bool, str)  # ready, terminal reason

    def __init__(
        self,
        pid: int,
        process_poll: Callable[[], int | None],
        window_probe: Callable[[int], bool],
        *,
        timeout_ms: int,
        poll_interval_ms: int,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("Client readiness requires a positive process ID.")
        self._pid = pid
        self._process_poll = process_poll
        self._window_probe = window_probe
        self._timeout_ms = max(0, int(timeout_ms))
        self._poll_interval_ms = max(1, int(poll_interval_ms))
        self._deadline = 0.0
        self._active = False
        self._started = False
        self._probe_generation = 0
        self._probe_in_flight = False
        self._probe_results: SimpleQueue[
            tuple[int, bool, str | None, float]
        ] = SimpleQueue()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        """Begin bounded polling and perform the first cheap observation now."""
        if self._started:
            return
        self._started = True
        self._active = True
        self._deadline = time.monotonic() + (self._timeout_ms / 1_000)
        self._poll()

    def stop(self) -> None:
        """Stop polling without emitting a terminal result."""
        self._active = False
        self._timer.stop()
        self._invalidate_probe()

    @pyqtSlot()
    def _poll(self) -> None:
        if not self._active:
            return

        try:
            return_code = self._process_poll()
        except Exception:  # noqa: BLE001 - observation failure is retried until timeout
            log.debug(
                "Unable to poll client process pid=%s while awaiting its window",
                self._pid,
                exc_info=True,
            )
        else:
            if return_code is not None:
                self._complete(False, f"process-exited:{return_code}")
                return

        if self._consume_probe_result():
            return

        remaining_ms = self._remaining_ms()
        if remaining_ms <= 0:
            self._complete(False, "window-timeout")
            return

        if not self._probe_in_flight:
            self._start_window_probe()
        if not self._active:
            return
        self._timer.start(min(self._poll_interval_ms, remaining_ms))

    def _start_window_probe(self) -> None:
        """Run one potentially blocking window observation on a daemon thread."""
        if not self._active or self._probe_in_flight:
            return

        self._probe_generation += 1
        generation = self._probe_generation
        self._probe_in_flight = True
        pid = self._pid
        probe = self._window_probe
        results = self._probe_results

        def run_probe() -> None:
            error: str | None = None
            try:
                visible = bool(probe(pid))
            except Exception as exc:  # noqa: BLE001 - reported to the Qt owner
                visible = False
                error = f"{type(exc).__name__}: {exc}"
            results.put((generation, visible, error, time.monotonic()))

        worker = threading.Thread(
            target=run_probe,
            name=f"client-window-probe-{pid}-{generation}",
            daemon=True,
        )
        try:
            worker.start()
        except Exception:  # noqa: BLE001 - retry until the unchanged deadline
            self._probe_in_flight = False
            log.debug(
                "Unable to start client window probe pid=%s",
                self._pid,
                exc_info=True,
            )

    def _consume_probe_result(self) -> bool:
        """Apply only the current probe result; discard cancelled/stale work."""
        while True:
            try:
                generation, visible, error, completed_at = (
                    self._probe_results.get_nowait()
                )
            except Empty:
                return False

            if generation != self._probe_generation or not self._probe_in_flight:
                continue

            self._probe_in_flight = False
            if completed_at > self._deadline:
                self._complete(False, "window-timeout")
                return True
            if error is not None:
                log.debug(
                    "Unable to inspect client window pid=%s while awaiting "
                    "readiness: %s",
                    self._pid,
                    error,
                )
                return False
            if visible:
                self._complete(True, "window-visible")
                return True
            return False

    def _remaining_ms(self) -> int:
        return max(0, int((self._deadline - time.monotonic()) * 1_000))

    def _invalidate_probe(self) -> None:
        self._probe_generation += 1
        self._probe_in_flight = False

    def _complete(self, ready: bool, reason: str) -> None:
        if not self._active:
            return
        self._active = False
        self._timer.stop()
        self._invalidate_probe()
        self.finished.emit(ready, reason)


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

    @property
    def cancel_requested(self) -> bool:
        """Return whether future items were cancelled during the active item."""
        return self._cancel_requested

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
