"""Updates preserve steady runtimes and exclude every retained mutation."""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMainWindow

from src.app import MainWindow
from src.core.service_status import RuntimeSnapshot, ServiceState
from src.core.application_operations import running_runtime_components
from src.ui import update_coordinator as updates


@pytest.mark.parametrize("component", ["starting", "stopping"])
def test_update_refuses_transitional_runtime_before_creating_worker(qapp, monkeypatch, component):
    owner = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(owner)
    owner._update_install_worker = None
    owner._close_in_progress = False
    owner._latest_download_url = "https://example.invalid/launcher.zip"
    owner._latest_version = "v0.0.0"
    owner._server_proc = SimpleNamespace(poll=lambda: None) if component == "game" else None
    owner._market_proc = SimpleNamespace(poll=lambda: None) if component == "market" else None
    owner._tracker = SimpleNamespace(running_count=int(component == "clients"))
    owner._service_reachability = (component == "external_game", component == "external_market")
    state = {"starting": ServiceState.STARTING, "stopping": ServiceState.STOPPING}.get(component, ServiceState.OFFLINE)
    owner._runtime_snapshot = RuntimeSnapshot(state, ServiceState.OFFLINE, 0)
    messages = []
    monkeypatch.setattr(updates.QMessageBox, "information", lambda *args: messages.append(args))
    monkeypatch.setattr(updates, "UpdateProgressDialog", lambda *a, **k: pytest.fail("Update started while runtime was active"))
    try:
        owner._begin_update_install()
        assert len(messages) == 1
        assert "finish" in messages[0][2].lower()
        assert owner._update_install_worker is None
    finally:
        owner.deleteLater()


@pytest.mark.parametrize("return_code", [0, 1])
def test_exited_services_and_empty_tracker_allow_update(return_code):
    owner = SimpleNamespace(
        _server_proc=SimpleNamespace(poll=lambda: return_code),
        _market_proc=SimpleNamespace(poll=lambda: return_code),
        _tracker=SimpleNamespace(running_count=0),
        _runtime_snapshot=RuntimeSnapshot(ServiceState.FAILED, ServiceState.OFFLINE, 0),
    )
    assert running_runtime_components(owner) == ()


def test_observed_compose_services_are_visible_without_native_handles():
    owner = SimpleNamespace(
        _runtime_snapshot=RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.ONLINE, 0),
    )
    assert running_runtime_components(owner) == ("Market",)


@pytest.mark.parametrize("backend", ["native", "docker_compose"])
@pytest.mark.parametrize("component", ["game", "market", "clients", "external"])
def test_steady_runtime_reaches_checkpoint_and_update_worker(qapp, monkeypatch, backend, component):
    owner = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(owner)
    events = []
    class Signal:
        def connect(self, *_args):
            pass
    class Worker:
        stage_changed = download_progress = completed = finished = Signal()
        def start(self):
            events.append("worker")
    class Dialog:
        def set_stage(self, *_args): pass
        def set_download_progress(self, *_args): pass
        def show(self): events.append("dialog")
    owner._cfg = {"runtime_backend": backend}
    owner._latest_download_url = "https://example.invalid/launcher.zip"
    owner._latest_version = "v1.0.84"
    owner._runtime_snapshot = RuntimeSnapshot(ServiceState.ONLINE, ServiceState.ONLINE, 1)
    owner._server_proc = SimpleNamespace(poll=lambda: None) if component == "game" else None
    owner._market_proc = SimpleNamespace(poll=lambda: None) if component == "market" else None
    owner._tracker = SimpleNamespace(running_count=int(component == "clients"))
    monkeypatch.setattr(updates, "prepare_runtime_handoff", lambda *_: events.append("checkpoint"))
    monkeypatch.setattr(updates, "UpdateProgressDialog", lambda *_a, **_k: Dialog())
    monkeypatch.setattr(updates, "UpdateInstallWorker", lambda *_a, **_k: Worker())
    monkeypatch.setattr(MainWindow, "_set_operation_controls_busy", lambda *_: None)
    monkeypatch.setattr(updates.QMessageBox, "information", lambda *_: pytest.fail("Steady runtime blocked update"))
    try:
        owner._begin_update_install()
        assert events == ["checkpoint", "dialog", "worker"]
    finally:
        owner._update_install_worker = None
        owner.deleteLater()


@pytest.mark.parametrize("field", [
    "_lifecycle_thread", "_mod_lifecycle_lease", "_character_creation_request",
    "_character_deletion_thread", "_docker_character_request", "_overview_patch_thread",
    "_client_launch_request", "_launch_queue", "_docker_preflight_thread", "_docker_tool_request",
    "_market_preflight_request", "_mod_operation_thread", "_mod_operation_request",
    "_mod_settings_request", "_close_in_progress",
])
def test_active_mutation_cannot_even_prepare_a_checkpoint(qapp, monkeypatch, field):
    owner = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(owner)
    setattr(owner, field, object())
    monkeypatch.setattr(updates, "prepare_runtime_handoff", lambda *_: pytest.fail("Mutation raced update"))
    messages = []
    monkeypatch.setattr(updates.QMessageBox, "information", lambda *_a: messages.append(1))
    try:
        owner._begin_update_install()
        assert messages == [1]
    finally:
        setattr(owner, field, None)
        owner.deleteLater()


def test_update_preparation_failure_revokes_checkpoint_before_reenabling_controls(qapp, monkeypatch):
    owner = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(owner)
    events = []
    ticket = object()
    owner._update_runtime_ticket = ticket
    owner._update_install_result = (False, "Download failed")
    owner._update_install_thread_finished = True
    owner._update_install_worker = SimpleNamespace(deleteLater=lambda: events.append("retired"))
    owner._update_progress_dialog = SimpleNamespace(show_error=lambda _error: events.append("error"))
    monkeypatch.setattr(updates, "cancel_runtime_handoff", lambda value: events.append("revoked") if value is ticket else pytest.fail("Wrong ticket"))
    monkeypatch.setattr(MainWindow, "_set_operation_controls_busy", lambda *_: events.append("enabled"))
    try:
        owner._finish_update_install_if_ready()
        assert events == ["retired", "revoked", "enabled", "error"]
        assert owner._update_runtime_ticket is None
    finally:
        owner.deleteLater()
