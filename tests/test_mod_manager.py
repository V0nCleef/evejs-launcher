"""Pure filesystem contracts for loader and source-integrated mods."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.core import mod_manifest as implementation
from src.core.mod_lifecycle_lock import (
    LOCK_RELATIVE_PATH,
    acquire_mod_lifecycle_lock,
)
from src.core.mod_manager import (
    ActivationKind,
    Mod,
    ModActivationError,
    active_loader_mods,
    active_loader_names,
    scan_mods,
    set_mod_active,
    toggle_mod,
)


def _loader(root: Path, mod_id: str, filename: str = "loader.js") -> Path:
    (root / "_local").mkdir(parents=True, exist_ok=True)
    path = root / "mods" / mod_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("module.exports = {};\n", encoding="utf-8")
    return path


def _manifest_payload(mod_id: str) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "id": mod_id,
        "displayName": "Fixture Integrated Mod",
        "version": "1.2.3",
        "description": "A test-only source service.",
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
    }


def _integrated_mod(
    root: Path,
    mod_id: str = "fixture-integrated",
    *,
    enabled: bool = True,
    schema_version: int = 3,
    manifest: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    (root / "_local").mkdir(parents=True, exist_ok=True)
    manifest_path = (
        root
        / "server"
        / "mods"
        / mod_id
        / implementation.MANIFEST_FILENAME
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest or _manifest_payload(mod_id), indent=2) + "\n",
        encoding="utf-8",
    )

    config_path = root / "config" / "mods" / f"{mod_id}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    document = config or {
        "schemaVersion": schema_version,
        "enabled": enabled,
        "pilotCount": 12,
        "operatorKind": "fixture",
        "logistics": {"enabled": True, "capacity": 65000},
    }
    config_path.write_text(
        json.dumps(document, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path, config_path


def _only_mod(root: Path) -> Mod:
    mods = scan_mods(root)
    assert len(mods) == 1
    return mods[0]


def test_legacy_constructor_and_loader_toggle_contract_are_preserved(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Fixture Mod")
    mod = _only_mod(tmp_path)

    assert Mod("legacy", tmp_path / "mods" / "legacy", False).id == "legacy"
    assert mod.activation_kind is ActivationKind.LOADER_RENAME
    assert mod.active
    assert set_mod_active(mod, False) is False
    assert not loader.exists()
    assert (loader.parent / "loader.js.disabled").is_file()
    assert mod.active is False

    assert toggle_mod(mod) is True
    assert loader.is_file()
    assert not (loader.parent / "loader.js.disabled").exists()
    assert mod.active is True


@pytest.mark.parametrize(
    "disabled_name",
    ["loader.js.disabled", "loader.js.off", "loader.js.bak"],
)
def test_each_legacy_disabled_suffix_can_be_enabled(
    tmp_path: Path,
    disabled_name: str,
) -> None:
    _loader(tmp_path, "fixture", disabled_name)
    mod = _only_mod(tmp_path)

    assert not mod.active
    assert set_mod_active(mod, True)
    assert (mod.path / "loader.js").is_file()
    assert not (mod.path / disabled_name).exists()


def test_loader_conflicts_are_visible_invalid_rows(tmp_path: Path) -> None:
    _loader(tmp_path, "fixture", "loader.js")
    _loader(tmp_path, "fixture", "loader.js.off")

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "active and disabled" in (mod.error or "").casefold()
    with pytest.raises(ModActivationError, match="Cannot change"):
        set_mod_active(mod, False)


def test_legacy_folders_without_a_recognized_loader_are_not_mods(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "mods" / "notes-only"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("Not a preload mod.\n", encoding="utf-8")

    assert scan_mods(tmp_path) == []


def test_integrated_manifest_discovers_metadata_and_exact_config_state(
    tmp_path: Path,
) -> None:
    manifest_path, config_path = _integrated_mod(tmp_path, enabled=True)

    mod = _only_mod(tmp_path)

    assert mod.valid
    assert mod.id == "fixture-integrated"
    assert mod.name == "Fixture Integrated Mod"
    assert mod.version == "1.2.3"
    assert mod.description == "A test-only source service."
    assert mod.activation_kind is ActivationKind.JSON_BOOLEAN
    assert mod.active is True
    assert mod.supported_backends == ("native",)
    assert mod.supports_backend("native")
    assert not mod.supports_backend("docker")
    assert mod.manifest_path == manifest_path
    assert mod.config_path == config_path
    assert mod.config_key == "enabled"
    assert mod.allowed_config_schema_versions == (1, 2, 3)
    assert mod.status_protocol == "evejs_mod_status_v1"
    assert mod.status_transport == "server_stdout"
    assert mod.evejs_root == tmp_path.resolve()


def test_mixed_discovery_is_deterministic_and_docker_filter_is_loader_only(
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Zeta Mod")
    _loader(tmp_path, "alpha")
    _integrated_mod(tmp_path, enabled=True)

    mods = scan_mods(tmp_path)

    assert [mod.name for mod in mods] == [
        "alpha",
        "Fixture Integrated Mod",
        "Zeta Mod",
    ]
    assert [mod.name for mod in active_loader_mods(mods)] == ["alpha", "Zeta Mod"]
    assert active_loader_names(mods) == ("alpha", "Zeta Mod")


def test_integrated_toggle_changes_only_top_level_enabled_semantically(
    tmp_path: Path,
) -> None:
    _, config_path = _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)
    before = json.loads(config_path.read_text(encoding="utf-8"))

    assert set_mod_active(mod, False) is False
    disabled = json.loads(config_path.read_text(encoding="utf-8"))
    expected = deepcopy(before)
    expected["enabled"] = False
    assert disabled == expected
    assert disabled["logistics"]["enabled"] is True
    assert mod.active is False

    assert set_mod_active(mod, True) is True
    enabled = json.loads(config_path.read_text(encoding="utf-8"))
    assert enabled == before
    assert mod.active is True


def test_scanned_mod_mutation_uses_stable_lifecycle_lock(tmp_path: Path) -> None:
    _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)

    assert set_mod_active(mod, False) is False

    lock_path = tmp_path / LOCK_RELATIVE_PATH
    assert lock_path.is_file()
    assert lock_path.read_bytes() == b"\0"


def test_busy_lifecycle_lock_rejects_mutation_without_changing_config(
    tmp_path: Path,
) -> None:
    _, config_path = _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)
    original = config_path.read_bytes()

    with acquire_mod_lifecycle_lock(tmp_path):
        with pytest.raises(ModActivationError, match="already using"):
            set_mod_active(mod, False)

    assert config_path.read_bytes() == original
    assert mod.active is True


def test_integrated_idempotent_state_does_not_replace_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)
    original = config_path.read_bytes()

    def unexpected_replace(_source: object, _destination: object) -> None:
        raise AssertionError("idempotent activation must not write")

    monkeypatch.setattr(implementation.os, "replace", unexpected_replace)

    assert set_mod_active(mod, True) is True
    assert config_path.read_bytes() == original


@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_declared_config_schema_versions_are_supported(
    tmp_path: Path,
    schema_version: int,
) -> None:
    _integrated_mod(tmp_path, schema_version=schema_version)

    assert _only_mod(tmp_path).valid


@pytest.mark.parametrize("enabled", [1, 0, "true", None, [], {}])
def test_config_enabled_must_be_an_exact_top_level_boolean(
    tmp_path: Path,
    enabled: object,
) -> None:
    _integrated_mod(
        tmp_path,
        config={"schemaVersion": 3, "enabled": enabled},
    )

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "boolean 'enabled'" in (mod.error or "")


def test_missing_config_is_visible_invalid_and_never_created(tmp_path: Path) -> None:
    _, config_path = _integrated_mod(tmp_path)
    config_path.unlink()

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert not config_path.exists()
    with pytest.raises(ModActivationError):
        set_mod_active(mod, False)
    assert not config_path.exists()


def test_config_removed_after_scan_is_not_recreated(tmp_path: Path) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    config_path.unlink()

    with pytest.raises(ModActivationError, match="no longer safe"):
        set_mod_active(mod, False)

    assert not config_path.exists()


def test_external_uninstall_with_preserved_config_makes_cached_row_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, config_path = _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)
    original_config = config_path.read_bytes()

    # Model a journaled external uninstall: immutable installed payload is
    # removed, while the deliberately editable config survives for reinstall.
    manifest_path.unlink()
    mod.path.rmdir()

    def unexpected_replace(_path: Path, _content: bytes) -> None:
        raise AssertionError("a stale integrated row must never reach config mutation")

    monkeypatch.setattr(implementation, "_atomic_replace_bytes", unexpected_replace)

    with pytest.raises(ModActivationError, match="directory is no longer safe"):
        set_mod_active(mod, False)

    assert config_path.read_bytes() == original_config
    assert mod.active is True


def test_manifest_removed_after_scan_rejects_cached_row_without_config_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, config_path = _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)
    original_config = config_path.read_bytes()
    manifest_path.unlink()

    def unexpected_replace(_path: Path, _content: bytes) -> None:
        raise AssertionError("a manifest-less row must never mutate preserved config")

    monkeypatch.setattr(implementation, "_atomic_replace_bytes", unexpected_replace)

    with pytest.raises(ModActivationError, match="Mod manifest is no longer safe"):
        set_mod_active(mod, False)

    assert config_path.read_bytes() == original_config
    assert mod.active is True


def test_valid_manifest_contract_change_after_scan_requires_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, config_path = _integrated_mod(tmp_path, enabled=True)
    mod = _only_mod(tmp_path)
    original_config = config_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["activation"]["allowedConfigSchemaVersions"] = [3]
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    def unexpected_replace(_path: Path, _content: bytes) -> None:
        raise AssertionError("a changed manifest contract must not authorize mutation")

    monkeypatch.setattr(implementation, "_atomic_replace_bytes", unexpected_replace)

    with pytest.raises(ModActivationError, match="manifest contract changed"):
        set_mod_active(mod, False)

    assert config_path.read_bytes() == original_config
    assert mod.active is True


@pytest.mark.parametrize(
    "config_path",
    [
        "C:/outside/fixture-integrated.json",
        "//server/share/fixture-integrated.json",
        "config/mods/../fixture-integrated.json",
        "config\\mods\\fixture-integrated.json",
        "/config/mods/fixture-integrated.json",
        "config/mods/other.json",
    ],
)
def test_unsafe_or_foreign_config_paths_fail_closed(
    tmp_path: Path,
    config_path: str,
) -> None:
    manifest = _manifest_payload("fixture-integrated")
    manifest["activation"]["configPath"] = config_path  # type: ignore[index]
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert mod.supported_backends == ()


def test_foreign_config_path_error_names_current_manifest_schema(
    tmp_path: Path,
) -> None:
    manifest = _manifest_payload("fixture-integrated")
    manifest["activation"]["configPath"] = "config/mods/other.json"  # type: ignore[index]
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert mod.valid is False
    assert "Schema v2 configPath" in mod.error


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", 1),
        ("kind", "command"),
        ("supportedBackends", ["native", "docker"]),
        ("restart", "none"),
    ],
)
def test_unsupported_manifest_contracts_are_visible_invalid(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    manifest = _manifest_payload("fixture-integrated")
    manifest[field] = value
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert mod.activation_kind is ActivationKind.JSON_BOOLEAN
    assert mod.error


def test_unknown_command_or_hook_fields_are_rejected_not_executed(
    tmp_path: Path,
) -> None:
    manifest = _manifest_payload("fixture-integrated")
    manifest["hooks"] = {"disable": "Remove-Item C:/important"}
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "unknown hooks" in (mod.error or "")


@pytest.mark.parametrize(
    "status",
    [
        {"protocol": "command", "transport": "server_stdout"},
        {"protocol": "evejs_mod_status_v1", "transport": "file"},
        {
            "protocol": "evejs_mod_status_v1",
            "transport": "server_stdout",
            "command": "node verify.js",
        },
    ],
)
def test_unsupported_or_executable_status_contracts_fail_closed(
    tmp_path: Path,
    status: dict[str, object],
) -> None:
    manifest = _manifest_payload("fixture-integrated")
    manifest["status"] = status
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "status" in (mod.error or "").casefold()


def test_missing_runtime_status_contract_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest_payload("fixture-integrated")
    del manifest["status"]
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "missing status" in (mod.error or "").casefold()


def test_duplicate_json_properties_fail_closed(tmp_path: Path) -> None:
    manifest_path, _ = _integrated_mod(tmp_path)
    payload = manifest_path.read_text(encoding="utf-8").rstrip()
    manifest_path.write_text(
        payload[:-1] + ', "id": "fixture-integrated"}\n',
        encoding="utf-8",
    )

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "duplicate property" in (mod.error or "")


def test_utf8_bom_manifest_fails_closed(tmp_path: Path) -> None:
    manifest_path, _ = _integrated_mod(tmp_path)
    manifest_path.write_bytes(b"\xef\xbb\xbf" + manifest_path.read_bytes())

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "without a BOM" in (mod.error or "")


def test_unicode_surrogate_manifest_text_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest_payload("fixture-integrated")
    manifest["description"] = "invalid \ud800 text"
    _integrated_mod(tmp_path, manifest=manifest)

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "surrogate" in (mod.error or "")


def test_oversized_manifest_fails_closed(tmp_path: Path) -> None:
    manifest_path, _ = _integrated_mod(tmp_path)
    manifest_path.write_bytes(b" " * (implementation.MAX_MANIFEST_BYTES + 1))

    mod = _only_mod(tmp_path)

    assert not mod.valid
    assert "size limit" in (mod.error or "")


def test_legacy_and_integrated_id_collision_invalidates_both(tmp_path: Path) -> None:
    _loader(tmp_path, "fixture-integrated")
    _integrated_mod(tmp_path, "fixture-integrated")

    mods = scan_mods(tmp_path)

    assert len(mods) == 2
    assert all(not mod.valid for mod in mods)
    assert all("Duplicate mod id" in (mod.error or "") for mod in mods)
    assert active_loader_names(mods) == ()


def test_atomic_config_replace_uses_same_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    real_replace = implementation.os.replace
    observed: dict[str, Path] = {}

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        observed["source"] = Path(source)
        observed["destination"] = Path(destination)
        assert observed["source"].parent == config_path.parent
        real_replace(source, destination)

    monkeypatch.setattr(implementation.os, "replace", recording_replace)

    set_mod_active(mod, False)

    assert observed["destination"] == config_path
    assert not observed["source"].exists()


def test_replace_failure_preserves_exact_original_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    original = config_path.read_bytes()

    def failing_replace(_source: object, _destination: object) -> None:
        raise PermissionError("fixture denied")

    monkeypatch.setattr(implementation.os, "replace", failing_replace)

    with pytest.raises(ModActivationError, match="Could not change"):
        set_mod_active(mod, False)

    assert config_path.read_bytes() == original
    assert mod.active is True
    assert list(config_path.parent.glob(f".{config_path.name}.*.tmp")) == []


def test_compare_and_swap_rejects_concurrent_config_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    original = config_path.read_bytes()
    real_read = implementation._read_bounded_bytes
    config_reads = 0

    def concurrent_read(path: Path, maximum_bytes: int, label: str) -> bytes:
        nonlocal config_reads
        content = real_read(path, maximum_bytes, label)
        if path == config_path:
            config_reads += 1
            if config_reads == 2:
                changed = json.loads(content.decode("utf-8"))
                changed["pilotCount"] = 99
                return json.dumps(changed).encode("utf-8")
        return content

    monkeypatch.setattr(implementation, "_read_bounded_bytes", concurrent_read)

    with pytest.raises(ModActivationError, match="changed while"):
        set_mod_active(mod, False)

    assert config_path.read_bytes() == original
    assert mod.active is True


def test_post_write_verification_failure_rolls_back_exact_original_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    original = config_path.read_bytes()
    real_parse = implementation._parse_json_object
    parse_count = 0

    def fail_verification(content: bytes, label: str) -> dict[str, object]:
        nonlocal parse_count
        if label == "Mod configuration":
            parse_count += 1
            if parse_count == 2:
                raise implementation.ModManifestError(
                    "fixture verification failure"
                )
        return real_parse(content, label)

    monkeypatch.setattr(implementation, "_parse_json_object", fail_verification)

    with pytest.raises(ModActivationError, match="Could not change"):
        set_mod_active(mod, False)

    assert config_path.read_bytes() == original
    assert mod.active is True


def test_rollback_does_not_overwrite_a_newer_concurrent_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    real_read = implementation._read_bounded_bytes
    config_reads = 0
    concurrent_document: dict[str, object] | None = None

    def edit_after_replace(path: Path, maximum_bytes: int, label: str) -> bytes:
        nonlocal config_reads, concurrent_document
        content = real_read(path, maximum_bytes, label)
        if path == config_path:
            config_reads += 1
            if config_reads == 3:
                concurrent_document = json.loads(content.decode("utf-8"))
                concurrent_document["pilotCount"] = 777
                concurrent_bytes = (
                    json.dumps(concurrent_document, indent=2) + "\n"
                ).encode("utf-8")
                path.write_bytes(concurrent_bytes)
                return concurrent_bytes
        return content

    monkeypatch.setattr(implementation, "_read_bounded_bytes", edit_after_replace)

    with pytest.raises(ModActivationError, match="did not overwrite"):
        set_mod_active(mod, False)

    assert concurrent_document is not None
    assert json.loads(config_path.read_text(encoding="utf-8")) == concurrent_document
    assert mod.active is True


def test_reparse_escape_after_scan_is_rejected_if_symlinks_are_available(
    tmp_path: Path,
) -> None:
    _, config_path = _integrated_mod(tmp_path)
    mod = _only_mod(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-config.json"
    outside.write_text(
        json.dumps({"schemaVersion": 3, "enabled": True}),
        encoding="utf-8",
    )
    config_path.unlink()
    try:
        config_path.symlink_to(outside)
    except OSError:
        outside.unlink(missing_ok=True)
        pytest.skip("This Windows account cannot create symbolic links.")

    try:
        with pytest.raises(ModActivationError, match="escapes"):
            set_mod_active(mod, False)
        assert json.loads(outside.read_text(encoding="utf-8"))["enabled"] is True
    finally:
        config_path.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_explicit_state_rejects_non_boolean_values(tmp_path: Path) -> None:
    _loader(tmp_path, "fixture")
    mod = _only_mod(tmp_path)

    with pytest.raises(TypeError, match="must be a boolean"):
        set_mod_active(mod, 1)  # type: ignore[arg-type]
