"""Return completed Python QObjects before retiring their worker event loops."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSlot


class _ReturnToOwner(QObject):
    def __init__(self, worker: QObject, owner: QThread, background: QThread) -> None:
        super().__init__(worker)
        self._owner = owner
        self._background = background

    @pyqtSlot()
    def prepare_retirement(self) -> None:
        worker = self.parent()
        if worker is not None:
            # Run directly in the current affinity thread, before deleteLater.
            # A Python QObject destructor there can hold Qt's connection mutex
            # while waiting for the GIL held by a GUI widget constructor.
            worker.moveToThread(self._owner)
        self._background.quit()


def move_worker_to_thread(worker: QObject, thread: QThread, *, finish_signal: str = "cleanup") -> None:
    """Move a one-shot worker; its cleanup returns deletion to its creator.

    Results and I/O still run in the dedicated thread. Cleanup moves the QObject
    home, then quits that event loop. Queued deletion happens only after run()
    has returned and the background thread has finished. Callers must retain
    their worker until thread.finished. Other lifecycles retain their behavior.
    """
    cleanup = getattr(worker, finish_signal, None)
    if cleanup is not None:
        if worker.parent() is not None:
            raise ValueError("A worker moved to a dedicated thread must have no parent")
        guard = _ReturnToOwner(worker, worker.thread(), thread)
        # The guard moves with its QObject parent and is retained by it.
        cleanup.connect(guard.prepare_retirement, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater, Qt.ConnectionType.QueuedConnection)
    worker.moveToThread(thread)
