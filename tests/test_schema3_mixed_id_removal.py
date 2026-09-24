import json
from dataclasses import replace

import pytest

from src.core import mod_management
from src.core.local_mod_packages import LocalModPackageError, LocalModPackages
from src.core.mod_manifest import scan_mods, set_mod_active


def _loader_package(tmp_path, mod_id):
    source = tmp_path / "source" / "ImportedLoader"
    source.mkdir(parents=True)
    manifest = {
        "schemaVersion": 3,
        "id": mod_id,
        "displayName": "Imported Loader",
        "version": "1.0.0",
        "kind": "loader",
        "restart": "game_server",
        "activation": {"strategy": "loader_rename"},
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    loader_bytes = b"// imported loader stays intact\n"
    (source / "evejs-launcher.mod.json").write_bytes(manifest_bytes)
    (source / "loader.js").write_bytes(loader_bytes)
    return source, manifest_bytes, loader_bytes


def _snapshot(folder):
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in folder.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("mod_id", ["MiXeD.Author", "mixed.author"])
def test_schema3_import_remove_restore_and_toggle_preserves_public_id(
    tmp_path, monkeypatch, mod_id
):
    root = tmp_path / "EveJS"
    root.mkdir()
    source, manifest_bytes, loader_bytes = _loader_package(tmp_path, mod_id)
    registry_reads = []

    def absent_registry(path, **_kwargs):
        registry_reads.append(path)
        return None

    monkeypatch.setattr(mod_management, "_read_registry_values", absent_registry)
    service = LocalModPackages(root)
    imported = service.import_package(source)
    installed = root / imported.relative_path
    mod = next(row for row in scan_mods(root) if row.path == installed)
    assert mod.valid and mod.id == mod_id
    assert mod.api_descriptor is not None and mod.api_descriptor.id == mod_id

    removed = service.remove(mod)
    assert removed.status == "quarantined"
    assert set(registry_reads) == {mod_management.managed_mod_registry_path(mod_id.lower())}
    restored = service.restore(imported.record_id)
    assert restored.status == "installed"

    mod = next(row for row in scan_mods(root) if row.path == installed)
    assert mod.id == mod_id and mod.api_descriptor.id == mod_id
    assert [set_mod_active(mod, state) for state in (True, False, True)] == [True, False, True]
    assert (installed / "evejs-launcher.mod.json").read_bytes() == manifest_bytes
    assert (installed / "loader.js").read_bytes() == loader_bytes


def test_damaged_mixed_case_registration_blocks_local_removal_without_changes(
    tmp_path, monkeypatch
):
    root = tmp_path / "EveJS"
    root.mkdir()
    source, _manifest_bytes, _loader_bytes = _loader_package(tmp_path, "MiXeD.Author")
    service = LocalModPackages(root)
    imported = service.import_package(source)
    installed = root / imported.relative_path
    mod = next(row for row in scan_mods(root) if row.path == installed)
    before_files = _snapshot(installed)
    before_registry = service.registry_path.read_bytes()
    registry_reads = []
    expected_path = mod_management.managed_mod_registry_path("mixed.author")

    def damaged_registry(path, **_kwargs):
        registry_reads.append(path)
        return {} if path == expected_path else None

    monkeypatch.setattr(mod_management, "_read_registry_values", damaged_registry)
    with pytest.raises(LocalModPackageError, match="Installer ownership"):
        service.remove(mod)

    assert registry_reads and set(registry_reads) == {expected_path}
    assert installed.is_dir()
    assert _snapshot(installed) == before_files
    assert service.registry_path.read_bytes() == before_registry


def test_schema2_mod_id_does_not_get_schema3_case_normalization(tmp_path, monkeypatch):
    root = tmp_path / "EveJS"
    folder = root / "server" / "mods" / "schema2.loader"
    folder.mkdir(parents=True)
    config = root / "config" / "mods" / "schema2.loader.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"schemaVersion":1,"enabled":true}')
    manifest_path = folder / "evejs-launcher.mod.json"
    manifest_path.write_text(json.dumps({
        "schemaVersion": 2,
        "id": "schema2.loader",
        "displayName": "Schema 2 Loader",
        "version": "1.0.0",
        "description": "",
        "kind": "source-integrated",
        "supportedBackends": ["native"],
        "restart": "game_server",
        "status": {"protocol": "evejs_mod_status_v1", "transport": "server_stdout"},
        "activation": {
            "strategy": "json_boolean",
            "configPath": "config/mods/schema2.loader.json",
            "property": "enabled",
            "allowedConfigSchemaVersions": [1],
        },
    }))
    mod = next(row for row in scan_mods(root) if row.path == folder)
    assert mod.valid and mod.api_descriptor is None
    mixed_case = replace(mod, id="Schema2.Loader")
    registry_reads = []
    monkeypatch.setattr(
        mod_management,
        "_read_registry_values",
        lambda path, **_kwargs: registry_reads.append(path),
    )

    with pytest.raises(mod_management.ModManagementError, match="managed mod id"):
        mod_management.read_managed_mod_registration(mixed_case)
    assert registry_reads == []