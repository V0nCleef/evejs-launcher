"""An idle operation slot does not mean the running runtime can be abandoned."""
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMainWindow

from src.app import MainWindow
from src.core.service_status import RuntimeSnapshot, ServiceState
from src.core.application_operations import running_runtime_components
from src.ui import update_coordinator as updates


@pytest.mark.parametrize("component", ["game", "market", "clients", "external_game", "external_market", "starting", "stopping"])
def test_update_refuses_running_runtime_before_creating_worker(qapp, monkeypatch, component):
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
        assert "stop" in messages[0][2].lower()
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


def test_observed_compose_services_block_update_without_native_handles():
    owner = SimpleNamespace(
        _runtime_snapshot=RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.ONLINE, 0),
    )
    assert running_runtime_components(owner) == ("Market",)
