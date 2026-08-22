"""Mods-page backend capability and recreation messaging contracts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from src.core.mod_manager import ActivationKind
from src.core.mod_activation_state import mod_activation_state_path
from src.core.mod_management import (
    INNO_USER_PROVIDER,
    MANAGED_MOD_SCHEMA_VERSION,
    ManagedModRegistration,
    ModManagementError,
    ModNotManagedError,
    RemovalInventoryEntry,
)
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.pages import mods_page as mods_page_module
from src.pages.mods_page import ModsPage


@pytest.fixture(autouse=True)
def _activation_state_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mods_page_module.config,
        "CONFIG_DIR",
        tmp_path / "launcher-state",
    )

    def unmanaged(_mod) -> ManagedModRegistration:
        raise ModNotManagedError("fixture mod is externally installed")

    # Never inspect a developer machine's real HKCU enrollment from UI tests.
    monkeypatch.setattr(
        mods_page_module,
        "read_managed_mod_registration",
        unmanaged,
    )


def _loader(root: Path, mod_name: str, *, active: bool = True) -> Path:
    filename = "loader.js" if active else "loader.js.disabled"
    loader = root / "mods" / mod_name / filename
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")
    return loader


def _integrated_mod(
    root: Path,
    *,
    mod_id: str = "evejs-temp-npc",
    enabled: bool = True,
) -> tuple[Path, Path]:
    config_path = root / "config" / "mods" / f"{mod_id}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "enabled": enabled,
                "pilotCount": 12,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        root / "server" / "mods" / mod_id / "evejs-launcher.mod.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "id": mod_id,
                "displayName": "EveJS Temp NPC",
                "version": "0.4.2-prototype",
                "description": "Fixture source-integrated mod.",
                "kind": "source-integrated",
                "supportedBackends": ["native"],
                "activation": {
                    "strategy": "json_boolean",
                    "configPath": f"config/mods/{mod_id}.json",
                    "property": "enabled",
                    "allowedConfigSchemaVersions": [1, 2, 3],
                },
                "status": {
                    "protocol": "evejs_mod_status_v1",
                    "transport": "server_stdout",
                },
                "restart": "game_server",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path, config_path


def _managed_registration(root: Path) -> ManagedModRegistration:
    return ManagedModRegistration(
        schema_version=MANAGED_MOD_SCHEMA_VERSION,
        provider=INNO_USER_PROVIDER,
        app_id="{3CB3F7D0-7068-4C88-98A9-41A38C52B672}",
        mod_id="evejs-temp-npc",
        display_name="EveJS Temp NPC",
        package_version="0.4.2-prototype",
        evejs_root=root,
        activation_contract_sha256="a" * 64,
        bundle_sha256="b" * 64,
        expand_helper_sha256="c" * 64,
        current_pointer_sha256="d" * 64,
        removal_inventory_path=root / "kit" / "removal-inventory.json",
        removal_inventory_sha256="e" * 64,
        removal_inventory=(RemovalInventoryEntry("server/mods/evejs-temp-npc/evejs-launcher.mod.json", "absent"),),
        uninstaller_path=root / "kit" / "unins000.exe",
        uninstaller_sha256="f" * 64,
        uninstaller_data_path=root / "kit" / "unins000.dat",
        uninstaller_data_sha256="1" * 64,
        supports_purge_state=True,
    )


def test_native_mod_controls_keep_existing_restart_contract(qapp, tmp_path: Path) -> None:
    _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    assert page.apply_btn.text() == "Apply && Restart Server"
    assert page.apply_btn.isEnabled()
    assert page._rows[0].toggle.isEnabled()
    assert "Native" in page.lbl_backend.text()
    assert page.manifest_title.text() == "MOD MANIFEST"


def test_managed_docker_exposes_supported_preloads_with_recreation_message(
    qapp,
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Zeta Mod")
    _loader(tmp_path, "alpha")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.MANAGED,
    )

    assert page.apply_btn.text() == "Apply && Recreate Server"
    assert page.apply_btn.isEnabled()
    assert all(row.toggle.isEnabled() for row in page._rows)
    assert "recreat" in page.lbl_backend.text().casefold()
    assert page.selected_loader_names() == ("alpha", "Zeta Mod")
    assert page.selected_mod_names() == page.selected_loader_names()


def test_native_integrated_mod_uses_declared_config_and_marks_restart_required(
    qapp,
    tmp_path: Path,
) -> None:
    _manifest, config_path = _integrated_mod(tmp_path, enabled=True)
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    assert len(page._rows) == 1
    row = page._rows[0]
    assert row.mod.activation_kind is ActivationKind.JSON_BOOLEAN
    assert row.toggle.isEnabled()
    assert row.toggle.isChecked()
    assert row.kind_badge.text() == "CFG"
    assert row.kind_badge.toolTip() == "SOURCE-INTEGRATED MOD"
    assert row.path_label.text() == (
        "config / mods / evejs-temp-npc.json → enabled"
    )
    assert page.selected_loader_names() == ()

    row.toggle.setChecked(False)
    qapp.processEvents()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["enabled"] is False
    assert config["pilotCount"] == 12
    assert row.mod.active is False
    assert row.state_label.text() == "RESTART REQUIRED"
    assert page.count_label.text() == "0 CONFIGURED ON / 1 INSTALLED"


def test_unmanaged_mod_is_explicitly_external_and_cannot_request_removal(
    qapp,
    tmp_path: Path,
) -> None:
    _integrated_mod(tmp_path)
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    emitted: list[object] = []
    page.remove_mod_requested.connect(emitted.append)

    row = page._rows[0]
    row.remove_btn.click()

    assert row.remove_btn.text() == "EXTERNAL"
    assert row.remove_btn.property("class") == "modManagementAction"
    assert row.remove_btn.property("managementRole") == "external"
    assert row.remove_btn.size().width() == 94
    assert row.remove_btn.size().height() == 32
    assert not row.remove_btn.isEnabled()
    assert row.remove_btn.cursor().shape() is Qt.CursorShape.ArrowCursor
    assert "matching Setup" in row.remove_btn.toolTip()
    assert emitted == []


def test_managed_native_mod_exposes_remove_and_emits_exact_discovered_mod(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _integrated_mod(tmp_path)
    registration = _managed_registration(tmp_path.resolve())
    monkeypatch.setattr(
        mods_page_module,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    emitted: list[object] = []
    page.remove_mod_requested.connect(emitted.append)

    row = page._rows[0]
    row.remove_btn.click()

    assert row.remove_btn.text() == "REMOVE"
    assert row.remove_btn.property("class") == "modManagementAction"
    assert row.remove_btn.property("managementRole") == "remove"
    assert row.remove_btn.isEnabled()
    assert row.remove_btn.cursor().shape() is Qt.CursorShape.PointingHandCursor
    assert row.remove_btn.accessibleName() == "Remove EveJS Temp NPC"
    assert emitted == [row.mod]


def test_invalid_management_enrollment_is_repair_only_and_fail_closed(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _integrated_mod(tmp_path)

    def invalid(_mod) -> ManagedModRegistration:
        raise ModManagementError("registered bundle hash does not match")

    monkeypatch.setattr(
        mods_page_module,
        "read_managed_mod_registration",
        invalid,
    )
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        mods_page_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )

    row = page._rows[0]
    assert row.remove_btn.text() == "REPAIR"
    assert row.remove_btn.property("managementRole") == "repair"
    assert row.remove_btn.isEnabled()
    assert "bundle hash does not match" in row.remove_btn.toolTip()
    row.remove_btn.click()
    assert len(warnings) == 1
    assert warnings[0][1] == "Mod Removal Needs Repair"
    assert "Nothing was removed" in warnings[0][2]


def test_lifecycle_busy_locks_remove_and_refresh_then_restores_capability(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _integrated_mod(tmp_path)
    registration = _managed_registration(tmp_path.resolve())
    monkeypatch.setattr(
        mods_page_module,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    row = page._rows[0]

    page.set_lifecycle_busy(True)

    assert not row.remove_btn.isEnabled()
    assert row.remove_btn.cursor().shape() is Qt.CursorShape.ArrowCursor
    assert not page.refresh_btn.isEnabled()

    page.set_lifecycle_busy(False)

    assert row.remove_btn.isEnabled()
    assert row.remove_btn.cursor().shape() is Qt.CursorShape.PointingHandCursor
    assert page.refresh_btn.isEnabled()


def test_lifecycle_busy_refresh_does_not_permanently_disable_remove(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row rebuilt during shutdown must recover its removal capability."""
    _integrated_mod(tmp_path, enabled=False)
    registration = _managed_registration(tmp_path.resolve())
    monkeypatch.setattr(
        mods_page_module,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    emitted: list[object] = []
    page.remove_mod_requested.connect(emitted.append)
    original_row = page._rows[0]

    page.set_lifecycle_busy(True)
    page.set_mod_runtime_snapshot(None)
    replacement_row = page._rows[0]

    assert replacement_row is not original_row
    assert not replacement_row.remove_btn.isEnabled()

    page.set_lifecycle_busy(False)

    assert not replacement_row.toggle.isChecked()
    assert replacement_row.state_label.text() == "RUNTIME UNVERIFIED"
    assert page.count_label.text() == "0 CONFIGURED ON / 1 INSTALLED"
    assert replacement_row.remove_btn.isEnabled()
    assert (
        replacement_row.remove_btn.cursor().shape()
        is Qt.CursorShape.PointingHandCursor
    )
    replacement_row.remove_btn.click()
    assert emitted == [replacement_row.mod]


def test_pending_activation_survives_refresh_and_page_reconstruction(
    qapp,
    tmp_path: Path,
) -> None:
    _integrated_mod(tmp_path, enabled=True)
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    page._rows[0].toggle.setChecked(False)
    qapp.processEvents()

    page.refresh_mods()
    assert page._rows[0].state_label.text() == "RESTART REQUIRED"

    replacement = ModsPage()
    replacement.set_evejs_root(str(tmp_path))
    assert replacement._rows[0].state_label.text() == "RESTART REQUIRED"


def test_managed_docker_integrated_mod_is_native_only_and_not_a_preload(
    qapp,
    tmp_path: Path,
) -> None:
    _integrated_mod(tmp_path)
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.MANAGED,
    )

    row = page._rows[0]
    assert not row.toggle.isEnabled()
    assert row.state_label.text() == "NATIVE ONLY"
    assert "Native servers only" in row.toggle.toolTip()
    assert page.selected_loader_names() == ()
    assert not page.apply_btn.isEnabled()


def test_managed_docker_mixed_manifest_only_exposes_loader_controls(
    qapp,
    tmp_path: Path,
) -> None:
    _integrated_mod(tmp_path)
    _loader(tmp_path, "Fixture Loader")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.MANAGED,
    )

    loader_row = next(
        row
        for row in page._rows
        if row.mod.activation_kind is ActivationKind.LOADER_RENAME
    )
    integrated_row = next(
        row
        for row in page._rows
        if row.mod.activation_kind is ActivationKind.JSON_BOOLEAN
    )
    assert loader_row.toggle.isEnabled()
    assert not integrated_row.toggle.isEnabled()
    assert page.selected_loader_names() == ("Fixture Loader",)
    assert page.apply_btn.isEnabled()


def test_connect_only_lists_mods_but_cannot_mutate_files_or_emit_apply(
    qapp,
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    emitted: list[str] = []
    page.apply_restart_clicked.connect(lambda: emitted.append("apply"))

    page.set_runtime_context(
        RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.CONNECT_ONLY,
    )
    row = page._rows[0]
    row._on_toggled(True)
    page._on_apply_clicked()

    assert loader.exists()
    assert not (loader.parent / "loader.js.disabled").exists()
    assert not row.toggle.isEnabled()
    assert "Connect-only" in row.toggle.toolTip()
    assert not page.apply_btn.isEnabled()
    assert "Connect-only" in page.lbl_backend.text()
    assert emitted == []


def test_invalid_integrated_manifest_is_visible_but_fails_closed(
    qapp,
    tmp_path: Path,
) -> None:
    manifest = (
        tmp_path
        / "server"
        / "mods"
        / "broken-mod"
        / "evejs-launcher.mod.json"
    )
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"schemaVersion": 1}\n', encoding="utf-8")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    assert len(page._rows) == 1
    row = page._rows[0]
    assert row.mod.valid is False
    assert not row.toggle.isEnabled()
    assert row.state_label.text() == "INVALID"
    assert row.toggle.toolTip()
    assert not page.apply_btn.isEnabled()


def test_corrupt_activation_journal_disables_mutation_fail_closed(
    qapp,
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    journal = mod_activation_state_path(tmp_path)
    journal.parent.mkdir(parents=True)
    journal.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")

    page = ModsPage()
    page.set_evejs_root(str(tmp_path))

    row = page._rows[0]
    assert row.state_label.text() == "STATE ERROR"
    assert not row.toggle.isEnabled()
    assert not page.apply_btn.isEnabled()
    assert "journal" in row.toggle.toolTip().casefold()


def test_failed_mutation_restores_control_and_reports_error(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    row = page._rows[0]

    def fail(_mod, _desired: bool) -> bool:
        raise PermissionError("fixture denied")

    monkeypatch.setattr(mods_page_module, "request_mod_activation", fail)
    row.toggle.setChecked(False)
    qapp.processEvents()

    assert loader.is_file()
    assert row.mod.active is True
    assert row.toggle.isChecked()
    assert row.state_label.text() == "VERIFICATION FAILED"
    assert "fixture denied" in row.toggle.toolTip()


def test_lifecycle_busy_temporarily_locks_controls_and_preserves_pending_state(
    qapp,
    tmp_path: Path,
) -> None:
    _manifest, _config = _integrated_mod(tmp_path, enabled=True)
    _loader(tmp_path, "Fixture Loader")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    integrated_row = next(
        row
        for row in page._rows
        if row.mod.activation_kind is ActivationKind.JSON_BOOLEAN
    )
    loader_row = next(
        row
        for row in page._rows
        if row.mod.activation_kind is ActivationKind.LOADER_RENAME
    )

    integrated_row.toggle.setChecked(False)
    qapp.processEvents()
    assert integrated_row.state_label.text() == "RESTART REQUIRED"

    page.set_lifecycle_busy(True)

    assert all(not row.toggle.isEnabled() for row in page._rows)
    assert not page.apply_btn.isEnabled()
    assert integrated_row.state_label.text() == "SERVER BUSY"
    loader_row.toggle.setChecked(False)
    assert loader_row.toggle.isChecked()
    assert (tmp_path / "mods" / "Fixture Loader" / "loader.js").is_file()

    page.set_lifecycle_busy(False)

    assert all(row.toggle.isEnabled() for row in page._rows)
    assert page.apply_btn.isEnabled()
    assert integrated_row.state_label.text() == "RESTART REQUIRED"


def test_root_change_rebuilds_cards_from_new_root(qapp, tmp_path: Path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    _loader(old_root, "Old Mod")
    _loader(new_root, "New Mod")
    page = ModsPage()
    page.set_evejs_root(str(old_root))

    page.set_evejs_root(str(new_root))

    assert [row.mod.name for row in page._rows] == ["New Mod"]


def test_deep_signal_manifest_is_accessible_and_privacy_safe_at_minimum_width(
    qapp,
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Fixture Mod")
    page = ModsPage()
    page.set_evejs_root(str(tmp_path))
    page.resize(756, 560)
    page.show()
    qapp.processEvents()

    try:
        row = page._rows[0]

        assert page.page_header.title_label.text() == "MODS"
        assert page.page_header.eyebrow_label.text() == "EXTENSION CONTROL"
        assert page.runtime_panel.property("class") == "modsRuntimePanel"
        assert page.manifest_panel.property("class") == "modsManifestPanel"
        assert page.action_rail.property("class") == "modsActionRail"
        assert page.runtime_state_label.text() == "NATIVE HOST"
        assert page._scroll.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page._scroll.horizontalScrollBar().maximum() == 0

        assert page.refresh_btn.accessibleName() == "Refresh mods"
        assert page.refresh_btn.accessibleDescription()
        assert page.apply_btn.accessibleName()
        assert page.apply_btn.accessibleDescription()
        assert row.accessibleName() == "Mod Fixture Mod"
        assert row.toggle.accessibleName() == "Enable Fixture Mod"
        assert row.toggle.accessibleDescription()
        assert row.state_label.text() == "RUNTIME UNVERIFIED"

        assert str(tmp_path) not in row.path_label.text()
        assert row.path_label.toolTip() == str(loader.parent)
    finally:
        page.close()
        page.deleteLater()
