"""Retained transfer ownership protects runtime actions and both image formats."""
from types import SimpleNamespace
import json
import subprocess

import pytest
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMainWindow

from src import app as app_module
from src.app import MainWindow
from src.core.application_operations import active_mutations, update_blocked
from src.core.runtime.docker_cli import DockerCommandRunner
from src.core import save_migration_storage as storage
from src.ui.installation_coordinator import InstallationBar


def owner():
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._save_migration_thread = object()
    window._cfg = {}
    return window


def test_retained_migration_blocks_update_lifecycle_settings_switch_and_close(qapp):
    window = owner()
    try:
        assert "save_migration" in active_mutations(window)
        assert update_blocked(window)
        assert window._lifecycle_active()
        assert window._mod_removal_conflict_active()
        assert window._settings_save_rejection({})
        bar = SimpleNamespace(_launcher=window)
        assert InstallationBar.rejection(bar)
        event = QCloseEvent()
        window._close_launcher(event)
        assert not event.isAccepted()
    finally:
        # This is deliberately not a running worker or a complete app window.
        window._save_migration_thread = None
        window.deleteLater()


def test_footer_refresh_does_not_rebuild_an_unchanged_migration_picker(tmp_path):
    calls = []
    page = SimpleNamespace(busy=False, set_installations=lambda *args: calls.append(args))
    window = SimpleNamespace(_cfg={'evejs_root':str(tmp_path),'runtime_backend':'native'},
                             _save_migration_page=page)
    MainWindow._refresh_save_migration_installations(window)
    MainWindow._refresh_save_migration_installations(window)
    assert len(calls) == 1
    page.busy = True
    window._cfg['client_path'] = str(tmp_path/'changed-client')
    MainWindow._refresh_save_migration_installations(window)
    assert len(calls) == 1
    page.busy = False
    MainWindow._refresh_save_migration_installations(window)
    assert len(calls) == 2


@pytest.mark.parametrize("retained", ["_save_migration_thread", "_client_launch_request", "_update_install_worker"])
def test_migration_refuses_an_existing_retained_operation(qapp, monkeypatch, retained):
    window = owner()
    window._save_migration_thread = None
    setattr(window, retained, object())
    messages = []
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *args: messages.append(args))
    try:
        assert not window._can_start_save_migration()
        assert len(messages) == 1
    finally:
        setattr(window, retained, None)
        window.deleteLater()


def test_migration_does_not_close_running_clients(qapp, monkeypatch):
    window = owner()
    window._save_migration_thread = None
    window._tracker = SimpleNamespace(running_count=1)
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *args: None)
    try:
        assert not window._can_start_save_migration()
    finally:
        window.deleteLater()


def test_all_docker_calls_keep_the_captured_local_endpoint(tmp_path):
    calls = []
    def run(argv, cwd, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{}', '')
    def run_input(argv, cwd, timeout, payload):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{}', '')
    runner = storage._pin_runner(DockerCommandRunner('docker', execute=run, execute_input=run_input), 'unix:///qa.sock')
    runner.run(('ps', '-q'), cwd=tmp_path)
    runner.run_parsed(('inspect', 'qa'), cwd=tmp_path, parser=json.loads)
    runner.run_with_input_parsed(('run', 'qa'), cwd=tmp_path, input_bytes=b'{}', parser=lambda raw, code: None)
    assert len(calls) == 3
    assert all(argv[:3] == ('docker', '--host', 'unix:///qa.sock') for argv in calls)


def test_image_attestation_rejects_mismatch_and_missing_definitions_before_storage_access(tmp_path):
    definition = tmp_path / 'format.js'
    definition.write_text('current save format', encoding='utf-8')
    calls = []
    def execute(argv, cwd, timeout, payload):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"format.js":"mismatched"}', '')
    runner = DockerCommandRunner('docker', execute_input=execute)
    with pytest.raises(storage.SaveMigrationError, match='differs'):
        storage._verify_image_definitions(runner, tmp_path, 'sha256:fixture', ('format.js',))
    assert len(calls) == 1
    assert '--network' in calls[0] and '--read-only' in calls[0]
    assert '--mount' not in calls[0]
    with pytest.raises(storage.SaveMigrationError, match='missing'):
        storage._verify_image_definitions(runner, tmp_path, 'sha256:fixture', ('absent.js',))
    assert len(calls) == 1


def test_transfer_progress_cannot_be_dismissed_mid_transaction(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QDialog
    from src.pages.save_migration_page import _TransferProgressDialog

    dialog = _TransferProgressDialog()
    dialog.show()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible()
    assert not dialog.close()
    assert dialog.isVisible()
    dialog.done(QDialog.DialogCode.Accepted)
    dialog.deleteLater()


@pytest.mark.parametrize("empty_error", [False, True])
def test_worker_start_failure_releases_ownership_and_keeps_page_usable(qapp, monkeypatch, empty_error):
    from src.pages import save_migration_page as page_module
    page = page_module.SaveMigrationPage(verifier=lambda *a: None, can_start=lambda: True,
                                         installation_label=lambda entry: entry['name'])
    ownership = []
    outcomes = []
    page.busy_changed.connect(ownership.append)
    def fail(worker):
        raise RuntimeError() if empty_error else RuntimeError('Disposable thread start failure')
    monkeypatch.setattr(page_module._TransferThread, 'start', fail)
    monkeypatch.setattr(page, '_show_outcome', lambda *args: outcomes.append(args))
    page._start(SimpleNamespace())
    assert not page.busy
    assert ownership[-1] is None and len(ownership) == 2
    assert ('RuntimeError' if empty_error else 'start failure') in page.status.text()
    assert outcomes[0][1] == 'Save Migration stopped'
    page.deleteLater()
