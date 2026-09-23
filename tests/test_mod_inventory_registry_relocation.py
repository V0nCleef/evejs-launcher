from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from PyQt6.QtWidgets import QMainWindow

from src.core.local_mod_packages import LocalModPackages
from src.core.mod_inventory import load_mod_inventory
from src.core.mod_manifest import scan_mods
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.pages import mods_page as mods_page_module
from src.pages.mods_page import ModsPage
from src.ui.mod_coordinator import ModCoordinator


def _foreign_registry(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    saved_root = tmp_path / "saved-install"
    selected_root = tmp_path / "copied-install"
    for root in (saved_root, selected_root):
        loader = root / "mods" / "Example" / "loader.js"
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"module.exports = {};\n")

    saved_mod = scan_mods(str(saved_root))[0]
    LocalModPackages(saved_root).adopt(saved_mod)
    source_registry = LocalModPackages(saved_root).registry_path
    selected_registry = LocalModPackages(selected_root).registry_path
    selected_registry.parent.mkdir(parents=True)
    original_registry = source_registry.read_bytes()
    selected_registry.write_bytes(original_registry)
    loader_path = selected_root / "mods" / "Example" / "loader.js"
    return selected_root, selected_registry, original_registry, loader_path.read_bytes()


def _show_inventory(page: ModsPage, root: Path) -> None:
    page.set_evejs_root(str(root))
    page.show_inventory(load_mod_inventory(str(root)))


def test_foreign_registry_preview_is_metadata_only_and_explained_in_page(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_path, registry_before, loader_before = _foreign_registry(tmp_path)
    registry_digest = hashlib.sha256(registry_before).hexdigest()
    inventory = load_mod_inventory(str(root))

    assert inventory.relocation_preview is not None
    assert inventory.relocation_preview.saved_root == str(tmp_path / "saved-install")
    assert inventory.relocation_preview.current_root == str(root)
    assert inventory.relocation_preview.registry_sha256 == registry_digest
    assert inventory.registry_error == ""
    assert not inventory.local_removable
    assert registry_path.read_bytes() == registry_before
    assert (root / "mods" / "Example" / "loader.js").read_bytes() == loader_before

    page = ModsPage(defer_inventory=True)
    _show_inventory(page, root)
    emitted = []
    page.registry_relocation_requested.connect(emitted.append)
    page.register_mods_btn.click()

    assert not page.register_mods_btn.isHidden()
    assert page.register_mods_btn.isEnabled()
    assert not page.registry_banner.isHidden()
    assert "Finish setting up mods for this folder" in page.registry_banner_title.text()
    banner_copy = page.registry_banner_description.text().lower()
    assert "copied or moved" in banner_copy
    assert "launcher can recognize" in banner_copy
    assert "registry" not in banner_copy
    assert "schema" not in banner_copy
    assert page.operation_notice.isHidden()
    assert "Register mods here" in page._rows[0].remove_btn.toolTip()
    assert str(tmp_path / "saved-install") in page._rows[0].remove_btn.toolTip()
    assert "no verified removal provider" in page._rows[0].remove_btn.toolTip().lower()
    assert emitted == [inventory.relocation_preview]
    assert registry_path.read_bytes() == registry_before
    page.deleteLater()


def test_registry_registration_control_obeys_busy_and_connect_only_read_only(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, _registry_path, _registry_before, _loader_before = _foreign_registry(tmp_path)
    page = ModsPage(defer_inventory=True)
    _show_inventory(page, root)

    assert page.register_mods_btn.isEnabled()
    page.set_lifecycle_busy(True)
    assert not page.register_mods_btn.isEnabled()
    page.set_lifecycle_busy(False)
    page.set_runtime_context(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.CONNECT_ONLY)
    assert not page.registry_banner.isHidden()
    assert not page.register_mods_btn.isEnabled()
    assert "Connect-only" in page.register_mods_btn.toolTip()
    page.deleteLater()


def _wait_until(qapp, predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            qapp.processEvents()
            if predicate():
                return
        time.sleep(0.005)
    assert predicate(), "Timed out waiting for the mod operation and deferred refresh."


def test_register_action_uses_worker_and_enables_only_verified_local_removal(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_path, registry_before, loader_before = _foreign_registry(tmp_path)
    owner = QMainWindow()
    page = ModsPage(owner, defer_inventory=True)
    page.set_evejs_root(str(root))
    owner._cfg = {"evejs_root": str(root), "client_path": str(tmp_path / "missing-client")}
    owner._close_in_progress = False
    owner._docker_mode = lambda: False
    owner._set_operation_controls_busy = page.set_lifecycle_busy
    coordinator = ModCoordinator(owner)
    coordinator.attach(page)
    _wait_until(qapp, lambda: coordinator._token is None and page._inventory.relocation_preview is not None)

    from src.ui import mod_coordinator as coordinator_module
    monkeypatch.setattr(
        coordinator_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: coordinator_module.QMessageBox.StandardButton.Yes,
    )
    notices = []
    warnings = []
    monkeypatch.setattr(coordinator_module.QMessageBox, "information", lambda *_args: notices.append(_args[-1]))
    monkeypatch.setattr(coordinator_module.QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))

    page.register_mods_btn.click()
    _wait_until(qapp, lambda: coordinator._token is None and page._inventory.relocation_preview is None)

    registry = LocalModPackages(root)
    backups = list(registry.registry_path.parent.glob("registry.before-relocation-*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == registry_before
    assert registry.registry_path.read_bytes() != registry_before
    assert str(root) == json.loads(registry.registry_path.read_text(encoding="utf-8"))["root"]
    assert (root / "mods" / "Example" / "loader.js").read_bytes() == loader_before
    assert not warnings
    assert notices and str(backups[0]) in notices[0]
    assert page._inventory.registry_error == ""
    assert len(page._inventory.local_removable) == 1
    assert page._rows[0].remove_btn.text() == "REMOVE"
    assert page._rows[0].remove_btn.isEnabled()
    assert page.registry_banner.isHidden()

    owner.close()
    owner.deleteLater()


def test_confirmation_rechecks_selected_root_before_any_registry_write(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(mods_page_module.config, "CONFIG_DIR", tmp_path / "launcher-state")
    root, registry_path, registry_before, _loader_before = _foreign_registry(tmp_path)
    other_root = tmp_path / "other-install"
    other_root.mkdir()
    owner = QMainWindow()
    page = ModsPage(owner, defer_inventory=True)
    page.set_evejs_root(str(root))
    page.show_inventory(load_mod_inventory(str(root)))
    owner._cfg = {"evejs_root": str(root), "client_path": str(tmp_path / "missing-client")}
    owner._close_in_progress = False
    owner._set_operation_controls_busy = page.set_lifecycle_busy
    coordinator = ModCoordinator(owner)
    coordinator._page = page
    page.registry_relocation_requested.connect(coordinator.register_copied_registry)

    from src.ui import mod_coordinator as coordinator_module
    warnings = []

    def change_root_during_dialog(*_args, **_kwargs):
        owner._cfg["evejs_root"] = str(other_root)
        return coordinator_module.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(coordinator_module.QMessageBox, "question", change_root_during_dialog)
    monkeypatch.setattr(coordinator_module.QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    page.register_mods_btn.click()

    assert warnings and "changed while the confirmation was open" in warnings[-1]
    assert coordinator._token is None
    assert registry_path.read_bytes() == registry_before
    assert not list(registry_path.parent.glob("registry.before-relocation-*.json"))
    owner.close()
    owner.deleteLater()
