"""Offscreen DLSS5 uninstall UI/worker contracts; no installed files or GUI launch."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest
from PyQt6.QtCore import QEventLoop, QThread, QTimer
from PyQt6.QtWidgets import QMainWindow

from src import app as app_module
from src.app import MainWindow
from src.core.dlss5_uninstall import DLSS5UninstallRequest, DLSS5UninstallResult
from src.core.mod_manifest import ActivationKind, Mod
from src.core.service_status import RuntimeBackend
from src.pages import mods_page as page_module
from src.workers.dlss5_uninstall_worker import DLSS5UninstallWorker


def _mod(root: Path) -> Mod:
    package = root / "mods/DLSS5"
    return Mod(
        name="EveJS DLSS5", path=package, active=True, id="evejs-dlss5",
        activation_kind=ActivationKind.CLIENT_PACKAGE, evejs_root=root,
        manager_path=package / "EveJS-Integration/Manage-EveJSDLSS5.ps1",
        manager_sha256="A" * 64, supported_backends=(), restart_scope="client_launch",
    )


class _Page:
    refresh_count = 0
    lifecycle_busy = False

    def refresh_mods(self):
        self.refresh_count += 1

    def set_lifecycle_busy(self, busy):
        self.lifecycle_busy = busy


@pytest.fixture
def ui(qapp, tmp_path, monkeypatch):
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {"evejs_root": str(tmp_path), "client_path": str(tmp_path / "client/tq")}
    window._lifecycle_thread = None
    window._mods_page = _Page()
    window._docker_mode = lambda: False
    window._server_proc = object()  # deliberately live: client uninstall must not touch services
    window._market_proc = object()
    window._run_stop_sequence = lambda **kw: pytest.fail("DLSS5 must not stop services")
    window._publish_mod_runtime_snapshot = lambda value: pytest.fail("DLSS5 must not clear server attestation")
    notices = []
    for method in ("information", "warning", "critical"):
        monkeypatch.setattr(app_module.QMessageBox, method, lambda *args: notices.append(args))
    monkeypatch.setattr(app_module.QMessageBox, "question",
                        lambda *args: app_module.QMessageBox.StandardButton.Yes)
    yield window, notices
    window.deleteLater()


@pytest.mark.parametrize("valid,backend,enabled", [
    (True, RuntimeBackend.NATIVE, True),
    (False, RuntimeBackend.NATIVE, False),
    (True, RuntimeBackend.DOCKER_COMPOSE, False),
])
def test_dlss5_row_exposes_uninstall_without_generic_registry(qapp, tmp_path, monkeypatch, valid, backend, enabled):
    mod = replace(_mod(tmp_path), valid=valid, error=None if valid else "untrusted manager")
    monkeypatch.setattr(page_module, "discover_dlss5_client_mod", lambda root: mod)
    monkeypatch.setattr(page_module, "scan_mods", lambda root: [])
    monkeypatch.setattr(page_module.config, "CONFIG_DIR", tmp_path / "settings")
    monkeypatch.setattr(page_module, "read_managed_mod_registration",
                        lambda mod: pytest.fail("client package must not use generic registry"))
    page = page_module.ModsPage()
    page._runtime_backend = backend
    page.set_evejs_root(str(tmp_path))
    emitted = []
    page.remove_mod_requested.connect(emitted.append)
    row = page._rows[0]
    assert row.remove_btn.text() == "UNINSTALL"
    assert row.remove_btn.isEnabled() is enabled
    assert not row.toggle.isEnabled()
    row.remove_btn.click()
    assert emitted == ([mod] if enabled else [])
    page.set_lifecycle_busy(True)
    assert not row.remove_btn.isEnabled()
    page.set_lifecycle_busy(False)
    assert row.remove_btn.isEnabled() is enabled
    page.deleteLater()


def test_confirmed_uninstall_uses_snapshot_and_background_worker_not_server_removal(ui, tmp_path):
    window, notices = ui
    started = []
    window._dlss5_uninstall_worker_factory = lambda request: request
    window._begin_lifecycle_worker = lambda worker, handler: started.append((worker, handler))
    mod = _mod(tmp_path)
    window._on_mod_remove_requested(mod)
    assert started == [(DLSS5UninstallRequest(tmp_path, tmp_path / "client/tq", mod.path, mod.manager_sha256),
                        window._on_dlss5_uninstall_completed)]
    assert not notices


def test_confirmation_warns_shared_client_and_defaults_cancel(ui, tmp_path, monkeypatch):
    window, notices = ui
    questions = []
    def cancel(*args):
        questions.append(args)
        return app_module.QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(app_module.QMessageBox, "question", cancel)
    window._begin_lifecycle_worker = lambda *args: pytest.fail("cancel must do nothing")
    window._on_mod_remove_requested(_mod(tmp_path))
    assert "sharing this client" in questions[0][2]
    assert "Backups, characters, profiles" in questions[0][2]
    assert questions[0][-1] == app_module.QMessageBox.StandardButton.Cancel
    assert not notices


@pytest.mark.parametrize("block", ["docker", "launch", "lifecycle", "stale-root", "invalid", "other-id", "no-manager"])
def test_invalid_or_busy_uninstall_cannot_start(ui, tmp_path, block):
    window, notices = ui
    mod = _mod(tmp_path)
    if block == "docker":
        window._docker_mode = lambda: True
    elif block == "launch":
        window._client_launch_request = object()
    elif block == "lifecycle":
        window._lifecycle_thread = object()
    elif block == "stale-root":
        mod.evejs_root = tmp_path / "different"
    elif block == "invalid":
        mod.valid = False
    elif block == "other-id":
        mod.id = "untrusted-client-mod"
    else:
        mod.manager_path = None
    window._begin_lifecycle_worker = lambda *args: pytest.fail("blocked request reached worker")
    window._on_mod_remove_requested(mod)
    assert notices
    window._lifecycle_thread = None


def test_dialog_event_race_rechecks_launch_reservation(ui, tmp_path, monkeypatch):
    window, notices = ui
    def confirm(*args):
        window._client_launch_request = object()
        return app_module.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(app_module.QMessageBox, "question", confirm)
    window._begin_lifecycle_worker = lambda *args: pytest.fail("raced request reached worker")
    window._on_mod_remove_requested(_mod(tmp_path))
    assert notices[-1][1] == "DLSS5 Uninstall Busy"


def test_dialog_event_race_rechecks_selected_client(ui, tmp_path, monkeypatch):
    window, notices = ui
    def confirm(*args):
        window._cfg["client_path"] = str(tmp_path / "another-client")
        return app_module.QMessageBox.StandardButton.Yes
    monkeypatch.setattr(app_module.QMessageBox, "question", confirm)
    window._begin_lifecycle_worker = lambda *args: pytest.fail("selection changed during prompt")
    window._on_mod_remove_requested(_mod(tmp_path))
    assert "changed" in notices[-1][2]


@pytest.mark.parametrize("outcome", ["success", "failure", "invalid"])
def test_terminal_result_refreshes_without_clearing_server_state(ui, tmp_path, outcome):
    window, notices = ui
    request = DLSS5UninstallRequest(tmp_path, tmp_path / "client", tmp_path / "mods/DLSS5", "A" * 64)
    result = DLSS5UninstallResult(request, outcome == "success", "fixture result", tmp_path / "retained")
    completed = []
    window._finish_lifecycle_if_complete = lambda: completed.append(True)
    window._on_dlss5_uninstall_completed(object() if outcome == "invalid" else result)
    assert window._mods_page.refresh_count == 1
    assert completed == [True]
    assert window._lifecycle_result_received
    assert notices[-1][1] == ("DLSS5 Uninstalled" if outcome == "success" else "DLSS5 Uninstall Failed")
    if outcome != "invalid":
        assert str(tmp_path / "retained") in notices[-1][2]


@pytest.mark.parametrize("failure", ["raise", "bad-result"])
def test_worker_exception_or_invalid_result_always_emits_terminal_and_cleanup(qapp, tmp_path, failure):
    request = DLSS5UninstallRequest(tmp_path, tmp_path, tmp_path, "A" * 64)
    def execute(request):
        if failure == "raise":
            raise RuntimeError("fixture failure")
        return None
    worker = DLSS5UninstallWorker(request, executor=execute)
    events = []
    worker.completed.connect(lambda result: events.append(result))
    worker.cleanup.connect(lambda: events.append("cleanup"))
    worker.run()
    assert isinstance(events[0], DLSS5UninstallResult)
    assert not events[0].success
    assert events[1] == "cleanup"
    worker.deleteLater()


def test_real_qthread_runs_off_gui_and_releases_lifecycle(ui, qapp, tmp_path):
    window, notices = ui
    request = DLSS5UninstallRequest(tmp_path, tmp_path, tmp_path, "A" * 64)
    thread_ids = []
    def execute(request):
        thread_ids.append(threading.get_ident())
        return DLSS5UninstallResult(request, True, "fixture rollback verified")
    worker = DLSS5UninstallWorker(request, executor=execute)
    window._begin_lifecycle_worker(worker, window._on_dlss5_uninstall_completed)
    assert window._mods_page.lifecycle_busy
    deadline = time.monotonic() + 4
    while window._lifecycle_thread is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert window._lifecycle_thread is None
    assert not window._mods_page.lifecycle_busy
    assert thread_ids and thread_ids[0] != threading.get_ident()
    assert notices[-1][1] == "DLSS5 Uninstalled"


def test_worker_start_failure_releases_reservation(ui, tmp_path, monkeypatch):
    window, notices = ui
    class FailingThread(QThread):
        def start(self, *args, **kwargs):
            raise RuntimeError("fixture start failure")
    monkeypatch.setattr(app_module, "QThread", FailingThread)
    window._on_mod_remove_requested(_mod(tmp_path))
    assert window._lifecycle_thread is None
    assert not window._mods_page.lifecycle_busy
    assert "could not start" in notices[-1][2]


def test_missing_worker_result_is_recovered_without_leaving_ui_busy(ui, tmp_path):
    window, notices = ui
    request = DLSS5UninstallRequest(tmp_path, tmp_path, tmp_path, "A" * 64)
    worker = DLSS5UninstallWorker(request)
    class FinishedThread:
        deleted = False
        def deleteLater(self):
            self.deleted = True
    thread = FinishedThread()
    window._lifecycle_worker = worker
    window._lifecycle_thread = thread
    window._lifecycle_thread_finished = True
    window._lifecycle_result_received = False
    window._recover_missing_mod_removal_result(worker)
    assert window._lifecycle_thread is None
    assert thread.deleted
    assert "without a terminal result" in notices[-1][2]
    worker.deleteLater()


@pytest.mark.parametrize("outcome", ["success", "failure", "invalid"])
def test_terminal_popup_nested_event_loop_does_not_fabricate_missing_result(ui, tmp_path, monkeypatch, outcome):
    """A QMessageBox pumps QThread.finished while completed is still presenting."""
    window, notices = ui
    request = DLSS5UninstallRequest(tmp_path, tmp_path, tmp_path, "A" * 64)
    worker = DLSS5UninstallWorker(request)
    class FinishedThread:
        deleted = False
        def deleteLater(self):
            self.deleted = True
    thread = FinishedThread()
    window._lifecycle_worker = worker
    window._lifecycle_thread = thread
    window._lifecycle_thread_finished = False
    window._lifecycle_result_received = False
    window._mods_page.lifecycle_busy = True
    during_popup = []

    def present(*args):
        notices.append(args)
        if len(notices) != 1:
            return
        loop = QEventLoop()
        QTimer.singleShot(0, window._on_lifecycle_thread_finished)
        # Also exercise a fallback queued before completed was delivered.
        QTimer.singleShot(0, lambda: window._recover_missing_mod_removal_result(worker))
        QTimer.singleShot(10, loop.quit)
        loop.exec()
        during_popup.append((window._lifecycle_result_received,
                             window._lifecycle_thread is thread,
                             window._mods_page.lifecycle_busy))

    monkeypatch.setattr(app_module.QMessageBox, "information", present)
    monkeypatch.setattr(app_module.QMessageBox, "critical", present)
    result = DLSS5UninstallResult(request, outcome == "success", "actual terminal result")
    window._on_dlss5_uninstall_completed(object() if outcome == "invalid" else result)
    assert len(notices) == 1, "finished/recovery fabricated a second popup during the real result"
    assert during_popup == [(True, True, True)], "reserve lifecycle until result presentation ends"
    assert window._lifecycle_thread is None and window._lifecycle_worker is None
    assert thread.deleted
    assert not window._mods_page.lifecycle_busy
    assert not window._lifecycle_result_received
    worker.deleteLater()
