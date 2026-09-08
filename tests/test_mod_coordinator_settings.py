import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

from PyQt6.QtWidgets import QMainWindow

from src.core.application_operations import active_mutations
from src.ui.mod_coordinator import ModCoordinator


def wait_idle(qapp, coordinator):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        qapp.processEvents()
        if coordinator._token is None:
            qapp.processEvents()  # deliver a reviewed operation's deferred continuation
            if coordinator._token is None:
                break
        if coordinator._thread is not None:
            coordinator._thread.wait(5)
    qapp.processEvents()
    assert coordinator._token is None


def test_connect_only_settings_cannot_start_an_edit(qapp, monkeypatch):
    from src.ui import mod_coordinator
    owner = QMainWindow()
    coordinator = ModCoordinator(owner)
    coordinator._page = SimpleNamespace(_can_mutate=lambda: False)
    errors = []
    monkeypatch.setattr(mod_coordinator.QMessageBox, "warning", lambda _parent, _title, message: errors.append(message))
    coordinator.configure(SimpleNamespace(settings_schema={"schemaVersion": 1}))
    assert errors == ["Connect-only Docker mode cannot change mod or Compose state."]
    assert coordinator._token is None and coordinator._dialog is None
    owner.deleteLater()


@pytest.mark.parametrize("outside_edit", [False, True])
def test_configure_opens_and_saves_through_real_qt_worker(qapp, tmp_path, monkeypatch, outside_edit):
    folder = tmp_path / "mods" / "Example"
    folder.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / "example.json"
    target.write_text('{"value": false, "unrelated": 42}')
    schema = {"schemaVersion": 1,
        "files": [{"id": "c", "base": "evejs", "path": "config/example.json", "format": "json"}],
        "fields": [{"id": "on", "label": "Enabled", "type": "boolean", "default": False, "file": "c", "key": ["value"], "restart": "game_server"}]}
    manifest = folder / "evejs-launcher.mod.json"
    manifest.write_text(json.dumps({"settings": schema}))
    mod = SimpleNamespace(name="Example", path=folder, evejs_root=tmp_path, settings_schema=schema, manifest_path=manifest)
    owner = QMainWindow()
    owner._cfg = {"evejs_root": str(tmp_path), "client_path": str(tmp_path / "missing-client")}
    owner._accounts = []
    owner._close_in_progress = False
    busy = []
    owner._set_operation_controls_busy = busy.append
    coordinator = ModCoordinator(owner)
    coordinator.configure(mod)
    assert "mod_operation" in active_mutations(owner)
    wait_idle(qapp, coordinator)
    dialog = coordinator._dialog
    assert dialog is not None and dialog.isVisible()
    assert json.loads(target.read_text())["value"] is False
    dialog._controls["on"].click()
    assert dialog.is_dirty()
    if outside_edit:
        from src.ui import mod_coordinator
        from src.widgets.mod_conflict_dialog import ModConflictDialog
        class ApplyDraft(ModConflictDialog):
            def exec(self):
                self.policy.setCurrentIndex(1)
                self.accept()
                return 1
        monkeypatch.setattr(mod_coordinator, "ModConflictDialog", ApplyDraft)
        target.write_text('{"value": true, "unrelated": 42}')
    dialog.save_button.click()
    wait_idle(qapp, coordinator)
    assert not dialog.is_dirty()
    assert json.loads(target.read_text()) == {"value": True, "unrelated": 42}
    assert busy == [True, False] * (3 if outside_edit else 2)
    dialog.close()
    owner.deleteLater()


def test_package_import_does_not_require_a_configured_client_to_exist(qapp, tmp_path, monkeypatch):
    from src.ui import mod_coordinator
    root = tmp_path / "evejs"
    root.mkdir()
    source = tmp_path / "Example"
    source.mkdir()
    (source / "loader.js").write_text("module.exports = {};\n")
    owner = QMainWindow()
    owner._cfg = {"evejs_root": str(root), "client_path": str(tmp_path / "missing-client")}
    owner._docker_mode = lambda: False
    coordinator = ModCoordinator(owner)
    errors, results = [], []
    monkeypatch.setattr(mod_coordinator.QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    monkeypatch.setattr(mod_coordinator.QFileDialog, "getExistingDirectory", lambda *args: str(source))
    monkeypatch.setattr(coordinator, "run", lambda operation, callback: results.append(operation()))
    coordinator.import_package("folder")
    assert not errors and len(results) == 1
    assert (root / results[0].relative_path / "loader.js.disabled").is_file()
    # Helpers and shared cleanup still capture the actual client. A stale path
    # must not silently discard its contributions or binary restoration work.
    with pytest.raises(FileNotFoundError):
        coordinator._context()
    owner.deleteLater()


def test_terminal_ui_nested_loop_does_not_release_operation_early(qapp):
    owner = QMainWindow()
    owner._close_in_progress = False
    owner._set_operation_controls_busy = lambda busy: None
    coordinator = ModCoordinator(owner)
    observed = []

    def present(result):
        assert result.success
        coordinator._finish()
        qapp.processEvents()
        observed.append(active_mutations(owner))

    assert coordinator.run(lambda: "done", present)
    wait_idle(qapp, coordinator)
    assert observed == [("mod_operation",)]
    assert active_mutations(owner) == ()
    owner.deleteLater()
