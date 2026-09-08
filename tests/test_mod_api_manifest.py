from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.core.local_mod_packages import CleanupDecision, LocalModPackages
from src.core.mod_api_manifest import ModApiManifestError, read_api_manifest
from src.core.mod_manifest import ActivationKind, ModActivationError, scan_mods, set_mod_active


@pytest.fixture
def root(tmp_path, monkeypatch):
    value = tmp_path / "EveJS"
    value.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "profile"))
    from src.core import mod_management
    def absent(_):
        raise mod_management.ModNotManagedError("No fixture enrollment")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", absent)
    return value


def manifest(*, kind="loader", strategy=None, mod_id="author.example"):
    return {"schemaVersion": 3, "id": mod_id, "displayName": "Author Example", "version": "1.2.3",
            "kind": kind, "restart": "game_server" if kind in {"loader", "source-integrated"} else "client",
            "activation": {"strategy": strategy or {"loader": "loader_rename", "source-integrated": "json_boolean",
                            "client-package": "client_package", "settings": "package"}[kind]}}


def install(root, payload, *, folder="DifferentFolder", where="mods", loader=None):
    path = root / where / folder
    path.mkdir(parents=True)
    (path / "evejs-launcher.mod.json").write_text(json.dumps(payload))
    if loader is True or (loader is None and payload.get("kind") == "loader"):
        (path / "loader.js").write_text("// real local loader\n")
    return path


def single(root):
    mods = scan_mods(root)
    assert len(mods) == 1
    return mods[0]


def test_loader_descriptor_is_optional_one_row_and_id_need_not_match_folder(root):
    payload = manifest()
    payload["settings"] = {"futureRawSchema": ["not interpreted by discovery"]}
    path = install(root, payload)
    mod = single(root)
    assert mod.valid and mod.active
    assert mod.id == "author.example" and mod.path.name == "DifferentFolder"
    assert mod.name == "Author Example"
    assert mod.api_descriptor.folder == path
    assert mod.api_descriptor.supported_backends == ("native", "docker")
    assert mod.settings_schema == payload["settings"]
    assert mod.identity == mod.api_descriptor.identity


@pytest.mark.parametrize("bad", ['{"schemaVersion":', '{"schemaVersion":99}', '{"schemaVersion":3,"id":"x"}'])
def test_malformed_optional_descriptor_keeps_working_legacy_loader(root, bad):
    path = install(root, manifest())
    (path / "evejs-launcher.mod.json").write_text(bad)
    mod = single(root)
    assert mod.valid and mod.active
    assert mod.api_descriptor is None and mod.settings_schema is None
    assert mod.descriptor_error
    assert set_mod_active(mod, False) is False
    assert (path / "loader.js.disabled").exists()


def test_malformed_descriptor_without_loader_remains_visible_for_recovery(root):
    install(root, {"schemaVersion": 3}, loader=False)
    mod = single(root)
    assert not mod.valid
    assert mod.error


def test_schema3_duplicate_display_ids_have_distinct_physical_identities(root):
    install(root, manifest(), folder="First")
    install(root, manifest(), folder="Second")
    mods = scan_mods(root)
    assert len(mods) == 2 and all(mod.valid for mod in mods)
    assert len({mod.id for mod in mods}) == 1
    assert len({mod.identity for mod in mods}) == 2


@pytest.mark.parametrize("where", ["mods", "server/mods"])
def test_nested_json_boolean_activation_preserves_unrelated_bytes(root, where):
    payload = manifest(kind="source-integrated")
    payload["activation"].update(configPath="config/local-example.json", property=["features", "mining", "enabled"])
    install(root, payload, where=where)
    config = root / "config/local-example.json"
    config.parent.mkdir()
    before = b'{\r\n  "features": { "mining": { "enabled" : true, "volume": 7 } },\r\n  "other": [1, 2, 3]\r\n}\r\n'
    config.write_bytes(before)
    mod = single(root)
    assert mod.valid and mod.active
    assert mod.config_key == ("features", "mining", "enabled")
    assert mod.allowed_config_schema_versions == ()
    assert set_mod_active(mod, False) is False
    assert config.read_bytes() == before.replace(b'"enabled" : true', b'"enabled" : false')
    assert single(root).active is False


def test_string_json_property_is_one_literal_key_not_a_dotted_path(root):
    payload = manifest(kind="settings", strategy="json_boolean")
    payload["activation"].update(configPath="options.json", property="feature.enabled")
    install(root, payload)
    (root / "options.json").write_text('{"feature.enabled":true,"feature":{"enabled":true}}')
    mod = single(root)
    assert mod.valid
    set_mod_active(mod, False)
    assert json.loads((root / "options.json").read_text()) == {"feature.enabled": False, "feature": {"enabled": True}}


def test_optional_config_version_constraint_is_applied_when_declared(root):
    payload = manifest(kind="source-integrated")
    payload["activation"].update(configPath="options.json", property="enabled", allowedConfigSchemaVersions=[7])
    install(root, payload)
    (root / "options.json").write_text('{"schemaVersion":8,"enabled":true}')
    assert not single(root).valid
    (root / "options.json").write_text('{"schemaVersion":7,"enabled":true}')
    assert single(root).valid


def test_cached_public_descriptor_cannot_retarget_configuration(root):
    payload = manifest(kind="source-integrated")
    payload["activation"].update(configPath="one.json", property=["enabled"])
    path = install(root, payload)
    for name in ("one.json", "two.json"):
        (root / name).write_text('{"enabled":true}')
    mod = single(root)
    payload["activation"]["configPath"] = "two.json"
    (path / "evejs-launcher.mod.json").write_text(json.dumps(payload))
    with pytest.raises(ModActivationError, match="refresh"):
        set_mod_active(mod, False)
    assert (root / "one.json").read_text() == '{"enabled":true}'
    assert (root / "two.json").read_text() == '{"enabled":true}'


@pytest.mark.parametrize("path", ["../foreign.json", "C:/foreign.json", "config/NUL.json",
    "_local/launcher-mods/registry.json", "_LOCAL/Launcher-Mods/registry.json",
    "_local/GameStore/data/accounts/data.json", "_LOCAL/GAMESTORE/manifest.json"])
def test_public_config_path_rejects_escape_host_registry_and_shared_data(root, path):
    payload = manifest(kind="source-integrated")
    payload["activation"].update(configPath=path, property="enabled")
    folder = install(root, payload)
    with pytest.raises(ModApiManifestError):
        read_api_manifest(root, folder)


def test_helper_metadata_is_parsed_without_execution_or_author_hash(root):
    payload = manifest(kind="client-package")
    payload["launcherApi"] = {"version": 1, "minLauncherVersion": "1.0.53",
        "helper": {"runtime": "node", "path": "helper.js"}, "capabilities": ["prepare_profile", "prepare_remove"]}
    folder = install(root, payload)
    (folder / "helper.js").write_text("throw new Error('this must never execute during discovery')")
    descriptor = read_api_manifest(root, folder)
    assert descriptor.launcher_api.helper.path == folder / "helper.js"
    assert descriptor.launcher_api.capabilities == ("prepare_profile", "prepare_remove")
    assert single(root).valid
    assert single(root).active is False


@pytest.mark.parametrize("change", [
    {"supportedBackends": [{}]}, {"launcherApi": {"version": 2}},
    {"launcherApi": {"version": 1, "capabilities": [{}]}},
    {"launcherApi": {"version": 1, "capabilities": ["run_arbitrary_command"]}},
    {"launcherApi": {"version": 1, "helper": {"runtime": "node", "path": "../helper.js"}}},
])
def test_bad_optional_api_fields_do_not_crash_or_disable_legacy_loader(root, change):
    payload = manifest()
    payload.update(change)
    install(root, payload)
    mod = single(root)
    assert mod.valid and mod.active and mod.descriptor_error


def test_public_package_import_adoption_and_configured_state_are_root_local(root, tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = install(source_root, manifest(kind="settings"))
    service = LocalModPackages(root)
    record = service.import_package(source)
    mod = single(root)
    assert mod.valid and mod.activation_kind is ActivationKind.PACKAGE and not mod.active
    assert mod.id == record.mod_id
    assert set_mod_active(mod, True) is True
    assert single(root).active is True
    with pytest.raises(ModActivationError, match="cleanup provider"):
        set_mod_active(single(root), False)
    assert single(root).active
    assert service.disable(single(root), cleanup_gate=lambda _: CleanupDecision(True)) is False
    assert not single(root).active
    # Explicitly selecting a manually copied public folder also permits adoption.
    other = install(root, manifest(kind="client-package", mod_id="another.author"), folder="Manual")
    manual = next(mod for mod in scan_mods(root) if mod.path == other)
    adopted = service.adopt(manual)
    assert adopted.enabled is False and adopted.package_kind == "client-package"


def test_package_activation_is_atomic_under_existing_lifecycle_lock(root, tmp_path):
    from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = install(source_root, manifest(kind="client-package"))
    service = LocalModPackages(root)
    record = service.import_package(source)
    with acquire_mod_lifecycle_lock(root):
        assert service.set_enabled_locked(record.record_id, True).enabled is True
    assert single(root).active


@pytest.mark.parametrize("where", ["mods", "server/mods"])
def test_schema3_large_localized_settings_fit_public_manifest_limit(root, where):
    payload = manifest(kind="settings")
    payload["settings"] = {"localized": "localized display text " * 5000}
    folder = install(root, payload, where=where)
    assert (folder / "evejs-launcher.mod.json").stat().st_size > 64 * 1024
    mod = single(root)
    assert mod.valid and mod.settings_schema == payload["settings"]


def test_public_manifest_above_one_mebibyte_is_rejected_but_loader_survives(root):
    payload = manifest()
    payload["settings"] = {"large": "x" * (1024 * 1024)}
    install(root, payload)
    mod = single(root)
    assert mod.valid and mod.active and mod.descriptor_error
    assert mod.api_descriptor is None
