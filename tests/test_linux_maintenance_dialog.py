"""A real Qt worker keeps the modal owner alive until both terminal signals."""
import threading
import time

import pytest
from PyQt6.QtWidgets import QApplication

from src.widgets.linux_maintenance_dialog import LinuxMaintenanceDialog


def spin(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            pytest.fail('Qt worker did not finish')
        time.sleep(0.01)


def test_running_operation_rejects_close_until_result_and_thread_finish():
    app = QApplication.instance() or QApplication([])
    release = threading.Event()
    dialog = LinuxMaintenanceDialog('')
    dialog.show()
    gui_thread = threading.get_ident()
    observed = []

    def operation():
        observed.append(threading.get_ident())
        assert release.wait(3)
        return 'fixture operation completed'

    try:
        dialog._start(operation, mutating=False)
        spin(app, lambda: bool(observed))
        dialog.reject()
        assert dialog.isVisible()
        assert not dialog.close_button.isEnabled()
        assert observed != [gui_thread]
        release.set()
        spin(app, lambda: dialog._thread is None)
        assert dialog.output.toPlainText() == 'Completed.\n\nfixture operation completed'
        dialog.reject()
        assert not dialog.isVisible()
    finally:
        release.set()
        spin(app, lambda: dialog._thread is None)
        dialog.close()


def test_failed_worker_releases_controls_and_reports_changes():
    app = QApplication.instance() or QApplication([])
    dialog = LinuxMaintenanceDialog('')
    changed = []
    dialog.changed.connect(lambda: changed.append(True))

    def operation():
        raise RuntimeError('fixture failure')

    dialog._start(operation, mutating=True)
    spin(app, lambda: dialog._thread is None)
    assert dialog.output.toPlainText() == 'Failed.\n\nfixture failure'
    assert changed == [True]
    assert dialog.close_button.isEnabled()
    dialog.close()
