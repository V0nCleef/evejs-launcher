"""Updater cannot exit across retained mutations or lose saved preferences."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMainWindow

from src import app as app_module
from src.ui import update_coordinator as updates
from src.app import MainWindow
from src.core.application_operations import MUTATION_FIELDS, active_mutations, update_active
from src.core.mod_management import ManagedModRemovalResult


def window():
    result = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(result)
    result._update_install_worker = None
    result._close_in_progress = False
    result._cfg = {}
    return result


@pytest.mark.parametrize("field", [field for fields in MUTATION_FIELDS.values() for field in fields])
def test_update_refuses_each_retained_mutation(qapp, monkeypatch, field):
    owner = window()
    setattr(owner, field, object())
    messages = []
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *args: messages.append(args))
    monkeypatch.setattr(updates, "UpdateInstallWorker", lambda *a, **k: pytest.fail("Update started during a mutation"))
    owner._begin_update_install()
    assert len(messages) == 1
    owner.deleteLater()


@pytest.mark.parametrize("field,value", [("_update_install_worker", object()), ("_update_handoff_pending", True)])
def test_update_blocks_reverse_order_and_handoff_gap(qapp, field, value):
    owner = window()
    setattr(owner, field, value)
    assert owner._lifecycle_active()
    assert owner._mod_removal_conflict_active()
    assert update_active(owner)
    with pytest.raises(RuntimeError, match="update"):
        owner._begin_lifecycle_worker(None, None)
    owner.deleteLater()


def test_successful_worker_teardown_keeps_update_guard_until_exit(qapp, monkeypatch):
    owner = window()
    deleted = []
    owner._update_install_worker = SimpleNamespace(deleteLater=lambda: deleted.append(True))
    owner._update_install_result = (True, "")
    owner._update_install_thread_finished = True
    owner._update_progress_dialog = None
    timers = []
    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda delay, cb: timers.append((delay, cb)))
    owner._finish_update_install_if_ready()
    assert owner._update_install_worker is None and deleted == [True]
    assert update_active(owner) and active_mutations(owner) == ("update",)
    assert timers == [(750, app_module.hard_exit)]
    owner.deleteLater()


def test_skip_survives_subsequent_application_save(qapp, monkeypatch):
    owner = window()
    owner._cfg = {"update_skip_versions": ["v1.0.51"]}
    owner._latest_version = "v1.0.54"
    owner._latest_changelog = owner._latest_download_url = owner._latest_published = ""
    owner._title_bar = SimpleNamespace(set_update_up_to_date=lambda: None)
    owner._settings_page = SimpleNamespace(set_update_check_done=lambda value: None)
    dialog = SimpleNamespace(exec=lambda: None, result=lambda: 0, skip_requested=True)
    monkeypatch.setattr(updates, "UpdateDialog", lambda **kwargs: dialog)
    saves = []
    monkeypatch.setattr(app_module.config, "save", lambda cfg: saves.append(deepcopy(cfg)))
    owner._on_update_clicked()
    owner._on_update_up_to_date()
    assert saves[-1]["update_skip_versions"] == ["v1.0.51", "v1.0.54"]
    owner.deleteLater()


def test_auto_update_switch_and_interval_apply_immediately(qapp):
    owner = window()
    starts = []
    owner._update_timer = SimpleNamespace(stop=lambda: starts.append("stop"), start=starts.append)
    owner._cfg = {"update_auto_check": False, "update_check_interval_hours": 3}
    owner._start_update_checker = lambda checker: pytest.fail("Disabled automatic check ran")
    owner._apply_update_settings()
    owner._start_automatic_update_check()
    assert starts == ["stop"]
    owner._cfg["update_auto_check"] = True
    owner._apply_update_settings()
    assert starts[-1] == 10800000
    owner._cfg["update_check_interval_hours"] = 0
    owner._apply_update_settings()
    assert starts[-1] == "stop"
    owner.deleteLater()


def test_managed_result_acknowledged_before_nested_dialog_loop(qapp, monkeypatch):
    owner = window()
    deleted = []
    thread = SimpleNamespace(deleteLater=lambda: deleted.append(True))
    owner._lifecycle_thread = thread
    owner._lifecycle_worker = object()
    owner._lifecycle_thread_finished = False
    owner._lifecycle_result_received = False
    owner._mods_page = SimpleNamespace(refresh_mods=lambda: None, set_lifecycle_busy=lambda busy: None)
    owner._publish_mod_runtime_snapshot = lambda snapshot: None
    calls = []

    def show(*args):
        calls.append(args)
        assert owner._lifecycle_result_received
        owner._lifecycle_thread_finished = True
        owner._finish_lifecycle_if_complete()
        assert owner._lifecycle_thread is thread
        # A duplicate completion delivered by the nested event loop is inert.
        owner._on_managed_mod_removal_completed(result)

    monkeypatch.setattr(app_module.QMessageBox, "information", show)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *args: pytest.fail("False removal failure"))
    result = ManagedModRemovalResult(request=None, success=True, message="Removed")
    owner._on_managed_mod_removal_completed(result)
    assert len(calls) == 1 and deleted == [True]
    assert owner._lifecycle_thread is None
    owner.deleteLater()


def test_close_waits_for_observed_exit_and_retains_refused_clients(qapp, monkeypatch):
    owner = window()
    owner._tracker = SimpleNamespace(prune_dead=lambda: None, running_count=1)
    owner._close_clients_deadline = 10.0
    ignored, timers, warnings = [], [], []
    event = SimpleNamespace(ignore=lambda: ignored.append(True))
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 5.0)
    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda delay, cb: timers.append(delay))
    monkeypatch.setattr(app_module.QMessageBox, "warning", lambda *args: warnings.append(args))
    assert owner._wait_for_client_exit(event)
    assert ignored == [True] and timers == [250] and warnings == []
    monkeypatch.setattr(app_module.time, "monotonic", lambda: 11.0)
    assert owner._wait_for_client_exit(event)
    assert owner._tracker.running_count == 1 and len(warnings) == 1
    assert not owner._close_in_progress and owner._close_clients_deadline is None
    owner._close_clients_deadline = 20.0
    owner._tracker.running_count = 0
    assert not owner._wait_for_client_exit(event)
    owner.deleteLater()
