from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QMainWindow

from src.core.application_operations import active_mutations
from src.core.local_mod_packages import LocalModPackages
from src.core.mod_inventory import ModInventory
from src.core.mod_manifest import scan_mods
from src.pages import mods_page as mods_page_module
from src.pages.mods_page import ModsPage
from src.ui.mod_coordinator import ModCoordinator


def _foreign_install(tmp_path: Path) -> tuple[Path, bytes]:
    saved = tmp_path / "saved-install"
    current = tmp_path / "copied-install"
    for root in (saved, current):
        loader = root / "mods" / "Existing" / "loader.js"
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"module.exports = {};\n")
    LocalModPackages(saved).adopt(scan_mods(str(saved))[0])
    saved_registry = LocalModPackages(saved).registry_path
    current_registry = LocalModPackages(current).registry_path
    current_registry.parent.mkdir(parents=True)
    original = saved_registry.read_bytes()
    current_registry.write_bytes(original)
    return current, original


def _package_zip(tmp_path: Path) -> tuple[Path, bytes]:
    source = tmp_path / "Added.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("loader.js", "module.exports = { added: true };\n")
        archive.writestr("settings.json", '{"keep":true}\n')
    return source, source.read_bytes()


def _wait(qapp, predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            qapp.processEvents()
            if predicate():
                return
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for the guided mod import lifecycle."


def _coordinator(qapp, root: Path, tmp_path: Path):
    owner = QMainWindow()
    page = ModsPage(owner, defer_inventory=True)
    page.set_evejs_root(str(root))
    owner._cfg = {"evejs_root": str(root), "client_path": str(tmp_path / "missing-client")}
    owner._close_in_progress = False
    owner._docker_mode = lambda: False
    owner._set_operation_controls_busy = page.set_lifecycle_busy
    coordinator = ModCoordinator(owner)
    coordinator.attach(page)
    _wait(qapp, lambda: coordinator._token is None and page._inventory.relocation_preview is not None)
    return owner, page, coordinator


def test_guided_import_registers_then_imports_same_zip_after_worker_teardown(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_before = _foreign_install(tmp_path)
    source, source_before = _package_zip(tmp_path)
    owner, page, coordinator = _coordinator(qapp, root, tmp_path)

    # Exercise fresh preflight even when the page's last inventory is stale.
    page._inventory = ModInventory(str(root))
    picker_calls = []
    confirmations = []
    information = []
    warnings = []
    monkeypatch.setattr(
        "src.ui.mod_coordinator.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: (picker_calls.append(source) or (str(source), "ZIP archives (*.zip)")),
    )
    from src.ui import mod_coordinator as coordinator_module
    def confirm(*args, **_kwargs):
        confirmations.append(args[2])
        coordinator.import_package("zip")  # A second click cannot open a second picker.
        return coordinator_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(coordinator_module.QMessageBox, "question", confirm)
    monkeypatch.setattr(coordinator_module.QMessageBox, "information", lambda *_args: information.append(_args[-1]))
    monkeypatch.setattr(coordinator_module.QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))

    original_run = coordinator.run
    run_names = []

    def monitored_run(operation, callback):
        # Every stage must wait for the prior worker and its reservation to end.
        assert coordinator._token is None
        assert not active_mutations(owner)
        run_names.append(getattr(operation, "__name__", "anonymous"))
        return original_run(operation, callback)

    monkeypatch.setattr(coordinator, "run", monitored_run)
    busy = []
    owner._set_operation_controls_busy = lambda value: (busy.append(value), page.set_lifecycle_busy(value))
    coordinator._refresh_pending = True

    coordinator.import_package("zip")
    _wait(
        qapp,
        lambda: coordinator._token is None
        and coordinator._guided_import_pending is None
        and any(mod.name == "Added" for mod in page._inventory.mods),
    )

    registry = LocalModPackages(root)
    backup_files = list(registry.registry_path.parent.glob("registry.before-relocation-*.json"))
    assert len(picker_calls) == 1
    assert len(confirmations) == 1
    assert "Selected package: Added.zip" in confirmations[0]
    assert "Records to check" not in confirmations[0]
    assert not warnings
    assert len(information) == 1 and "Finish or cancel" in information[0]
    assert not any("registered" in message.lower() for message in information)
    assert source.read_bytes() == source_before
    assert len(backup_files) == 1 and backup_files[0].read_bytes() == registry_before
    assert (root / "mods" / "Existing" / "loader.js").read_bytes() == b"module.exports = {};\n"
    assert (root / "mods" / "Added" / "loader.js.disabled").is_file()
    assert json.loads((root / "mods" / "Added" / "settings.json").read_text(encoding="utf-8")) == {"keep": True}
    assert run_names[:3] == ["prepare_import", "register_then_continue", "import_selected_package"]
    assert busy[:6] == [True, False, True, False, True, False]
    assert page._inventory.relocation_preview is None

    owner.close()
    owner.deleteLater()


def test_cancelled_guided_import_keeps_registry_and_does_not_import(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_before = _foreign_install(tmp_path)
    source, source_before = _package_zip(tmp_path)
    owner, page, coordinator = _coordinator(qapp, root, tmp_path)
    picker_calls = []
    confirmations = []
    monkeypatch.setattr(
        "src.ui.mod_coordinator.QFileDialog.getOpenFileName",
        lambda *_args, **_kwargs: (picker_calls.append(source) or (str(source), "ZIP archives (*.zip)")),
    )
    from src.ui import mod_coordinator as coordinator_module
    monkeypatch.setattr(
        coordinator_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: (confirmations.append(True) or coordinator_module.QMessageBox.StandardButton.Cancel),
    )

    coordinator.import_package("zip")
    _wait(qapp, lambda: coordinator._token is None and coordinator._guided_import_pending is None)

    assert len(picker_calls) == 1 and len(confirmations) == 1
    assert LocalModPackages(root).registry_path.read_bytes() == registry_before
    assert not list(LocalModPackages(root).registry_path.parent.glob("registry.before-relocation-*.json"))
    assert not (root / "mods" / "Added").exists()
    assert source.read_bytes() == source_before
    assert page._inventory.relocation_preview is not None
    owner.close()
    owner.deleteLater()


def test_closing_during_registration_confirmation_clears_pending_without_modal(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_before = _foreign_install(tmp_path)
    source, _source_before = _package_zip(tmp_path)
    owner, _page, coordinator = _coordinator(qapp, root, tmp_path)
    messages = []
    picker_calls = []

    def pick_source(*_args, **_kwargs):
        picker_calls.append(True)
        return str(source), "ZIP archives (*.zip)"

    from src.ui import mod_coordinator as coordinator_module

    def close_during_confirmation(*_args, **_kwargs):
        owner._close_in_progress = True
        return coordinator_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(coordinator_module.QFileDialog, "getOpenFileName", pick_source)
    monkeypatch.setattr(coordinator_module.QMessageBox, "question", close_during_confirmation)
    monkeypatch.setattr(coordinator_module.QMessageBox, "information", lambda *_args: messages.append(_args[-1]))
    monkeypatch.setattr(coordinator_module.QMessageBox, "warning", lambda *_args: messages.append(_args[-1]))

    coordinator.import_package("zip")
    _wait(qapp, lambda: coordinator._token is None and coordinator._guided_import_pending is None)

    assert picker_calls == [True]
    assert messages == []
    assert LocalModPackages(root).registry_path.read_bytes() == registry_before
    assert not list(LocalModPackages(root).registry_path.parent.glob("registry.before-relocation-*.json"))
    assert not (root / "mods" / "Added").exists()
    owner._close_in_progress = False
    owner.close()
    owner.deleteLater()


@pytest.mark.parametrize("stale_at", ["picker-root", "picker-backend", "confirm-root", "confirm-backend"])
def test_guided_import_rechecks_folder_and_backend_before_registering(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stale_at: str
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_before = _foreign_install(tmp_path)
    source, _source_before = _package_zip(tmp_path)
    owner, _page, coordinator = _coordinator(qapp, root, tmp_path)
    other_root = tmp_path / "other-install"
    other_root.mkdir()
    picker_calls = []
    confirmations = []
    messages = []

    def pick_source(*_args, **_kwargs):
        picker_calls.append(True)
        if stale_at == "picker-root":
            owner._cfg["evejs_root"] = str(other_root)
        elif stale_at == "picker-backend":
            owner._docker_mode = lambda: True
        return str(source), "ZIP archives (*.zip)"

    def confirm(*_args, **_kwargs):
        confirmations.append(True)
        if stale_at == "confirm-root":
            owner._cfg["evejs_root"] = str(other_root)
        elif stale_at == "confirm-backend":
            owner._docker_mode = lambda: True
        return coordinator_module.QMessageBox.StandardButton.Yes

    from src.ui import mod_coordinator as coordinator_module
    monkeypatch.setattr(coordinator_module.QFileDialog, "getOpenFileName", pick_source)
    monkeypatch.setattr(coordinator_module.QMessageBox, "question", confirm)
    monkeypatch.setattr(coordinator_module.QMessageBox, "information", lambda *_args: messages.append(_args[-1]))
    monkeypatch.setattr(coordinator_module.QMessageBox, "warning", lambda *_args: messages.append(_args[-1]))

    coordinator.import_package("zip")
    _wait(qapp, lambda: coordinator._token is None and coordinator._guided_import_pending is None)

    assert len(picker_calls) == 1
    assert len(confirmations) == (0 if stale_at.startswith("picker") else 1)
    assert messages and all("foreign" not in message.lower() for message in messages)
    assert LocalModPackages(root).registry_path.read_bytes() == registry_before
    assert not list(LocalModPackages(root).registry_path.parent.glob("registry.before-relocation-*.json"))
    assert not (root / "mods" / "Added").exists()
    owner.close()
    owner.deleteLater()
