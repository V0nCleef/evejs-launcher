"""Native QObject teardown must not compete with GUI constructors for the GIL."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QThread, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

from src.core.runtime.data import RuntimeDataSelection
from src.core.runtime.portraits import PortraitImageResult, PortraitRequest, PortraitTarget
from src.workers.db_worker import AccountLoader
from src.workers.lifecycle import move_worker_to_thread
from src.workers.portrait_worker import PortraitLoader


@pytest.mark.parametrize('kind', ['accounts', 'portrait'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_completed_worker_is_destroyed_on_gui_after_its_background_call(
    qapp, tmp_path, kind, outcome,
):
    gui_id = threading.get_ident()
    executed, destroyed, results, failures, cleaned = [], [], [], [], []

    def load_accounts():
        executed.append(threading.get_ident())
        if outcome == 'failure':
            raise ValueError('fixture failure')
        return []

    if kind == 'accounts':
        source = SimpleNamespace(load_accounts=load_accounts)
        worker = AccountLoader(lambda: RuntimeDataSelection(source, None, 'fixture:retirement'))
        worker.completed.connect(results.append)
    else:
        target = PortraitTarget('fixture:retirement', native_root=tmp_path)
        request = PortraitRequest('fixture:retirement', 9001, 128, 1, object())

        class Provider:
            def __init__(self, *_args, **_kwargs):
                pass

            def load(self, actual):
                load_accounts()
                return PortraitImageResult(actual, QImage(128, 128, QImage.Format.Format_RGB32), 'fixture')

        worker = PortraitLoader(target, request, cache_dir=tmp_path, provider_factory=Provider)
        worker.loaded.connect(results.append)

    thread = QThread()
    move_worker_to_thread(worker, thread)
    thread.started.connect(worker.run)
    worker.failed.connect(failures.append)
    worker.cleanup.connect(lambda: cleaned.append(True), Qt.ConnectionType.DirectConnection)
    worker.destroyed.connect(
        lambda: destroyed.append((threading.get_ident(), thread.isRunning())),
        Qt.ConnectionType.DirectConnection,
    )
    if outcome == 'cancelled':
        worker.request_cancel()
    thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (not destroyed or thread.isRunning()):
        qapp.processEvents()
        QTest.qWait(1)
    try:
        assert not thread.isRunning()
        assert cleaned == [True]
        assert destroyed == [(gui_id, False)]
        assert len(executed) == (0 if outcome == 'cancelled' else 1)
        assert all(value != gui_id for value in executed)
        assert len(results) == (1 if outcome == 'success' else 0)
        assert len(failures) == (1 if outcome == 'failure' else 0)
    finally:
        if thread.isRunning():
            thread.quit()
            thread.wait(1000)
        thread.deleteLater()
        qapp.processEvents()
