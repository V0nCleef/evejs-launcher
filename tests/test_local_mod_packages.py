from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from src.core import local_mod_packages as packages
from src.core.local_mod_packages import (
    CleanupDecision, LocalModCleanupPending, LocalModPackageError,
    LocalModPackages, PackageLimits,
)
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock, ModLifecycleBusyError
from src.core.mod_manifest import ActivationKind, Mod, scan_mods


@pytest.fixture
def root(tmp_path):
    value = tmp_path / "EveJS"
    (value / "mods").mkdir(parents=True)
    return value


@pytest.fixture(autouse=True)
def isolated_registration(monkeypatch, tmp_path):
    # Never query machine installer registrations or use the launcher's profile.
    from src.core import mod_management
    from src import config
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher-config")
    def absent(_mod):
        raise mod_management.ModNotManagedError("No fixture installer enrollment")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", absent)


def folder(parent, name="Example", *, active=True):
    path = parent / name
    path.mkdir(parents=True)
    (path / ("loader.js" if active else "loader.js.off")).write_text("require('./lib');\n")
    (path / "lib.js").write_text("module.exports = 17;\n")
    return path


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def archive(path, entries):
    with zipfile.ZipFile(path, "w") as stream:
        for name, value in entries:
            stream.writestr(name, value)
    return path


def one(root, name="Example"):
    return next(mod for mod in scan_mods(root) if mod.id == name)


def test_folder_import_defaults_disabled_preserves_source_and_root_data(root, tmp_path):
    source = folder(tmp_path)
    expected = snapshot(source)
    (root / "config").mkdir()
    (root / "config" / "Example.json").write_text('{"enabled":true}')
    service = LocalModPackages(root)
    assert service.inspect(source).active
    record = service.import_package(source)
    assert record.relative_path == "mods/Example"
    assert record.status == "installed"
    assert not one(root).active
    assert snapshot(source) == expected
    assert (root / "config" / "Example.json").read_text() == '{"enabled":true}'
    assert service.registry_path == root / "_local/launcher-mods/registry.json"


def test_enclosing_zip_folder_import_and_enabled_opt_in(root, tmp_path):
    source = archive(tmp_path / "release-v1.zip", [
        ("Example/", b""), ("Example/loader.js", b"// local code"),
        ("Example/lib/rules.js", b"module.exports = 9"), ("Example/state/", b""),
    ])
    service = LocalModPackages(root)
    assert service.inspect(source).folder_name == "Example"
    service.import_package(source, enabled=True)
    assert one(root).active
    assert (root / "mods/Example/lib/rules.js").read_bytes() == b"module.exports = 9"
    assert (root / "mods/Example/state").is_dir()


@pytest.mark.parametrize("member", [
    "../escaped.js", "/escaped.js", "C:/escaped.js", "Example/../escaped.js",
    "Example/CON.js", "Example/file.js:payload", "Example/trailing. ",
])
def test_unsafe_zip_paths_rejected_before_install(root, tmp_path, member):
    source = archive(tmp_path / "bad.zip", [("Example/loader.js", b"code"), (member, b"bad")])
    with pytest.raises(LocalModPackageError):
        LocalModPackages(root).import_package(source)
    assert not list((root / "mods").iterdir())
    assert not (tmp_path / "escaped.js").exists()


@pytest.mark.parametrize("entries", [
    [("loader.js", b"a"), ("LOADER.JS", b"b")],
    [("loader.js", b"a"), ("lib", b"b"), ("lib/code.js", b"c")],
    [("A/loader.js", b"a"), ("B/loader.js", b"b")],
])
def test_ambiguous_zip_layouts_rejected(root, tmp_path, entries):
    source = archive(tmp_path / "bad.zip", entries)
    with pytest.raises(LocalModPackageError):
        LocalModPackages(root).import_package(source)
    assert not list((root / "mods").iterdir())


def test_zip_symlink_and_limits_rejected(root, tmp_path):
    source = tmp_path / "linked.zip"
    with zipfile.ZipFile(source, "w") as stream:
        info = zipfile.ZipInfo("loader.js")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(info, "../outside")
    with pytest.raises(LocalModPackageError, match="linked"):
        LocalModPackages(root).inspect(source)
    source = archive(tmp_path / "large.zip", [("loader.js", b"0123456789")])
    with pytest.raises(LocalModPackageError, match="size limit"):
        LocalModPackages(root, limits=PackageLimits(max_file_bytes=5)).inspect(source)


def test_folder_reparse_point_rejected_without_following_it(root, tmp_path, monkeypatch):
    source = folder(tmp_path)
    original = Path.lstat
    def fake_stat(path):
        result = original(path)
        if path == source / "lib.js":
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result
    monkeypatch.setattr(Path, "lstat", fake_stat)
    with pytest.raises(LocalModPackageError, match="reparse"):
        LocalModPackages(root).inspect(source)


def test_collision_is_case_insensitive_and_never_overwrites(root, tmp_path):
    existing = folder(root / "mods", "example")
    expected = snapshot(existing)
    source = folder(tmp_path, "Example")
    with pytest.raises(LocalModPackageError, match="already exists"):
        LocalModPackages(root).import_package(source)
    assert snapshot(existing) == expected


def test_integrated_identity_collision_blocks_import(root, tmp_path, monkeypatch):
    source = folder(tmp_path)
    integrated = Mod("External", root / "server/mods/elsewhere", True, id="example", evejs_root=root,
                     activation_kind=ActivationKind.JSON_BOOLEAN)
    monkeypatch.setattr(packages, "scan_mods", lambda _root: [integrated])
    with pytest.raises(LocalModPackageError, match="identity"):
        LocalModPackages(root).import_package(source)


def test_adoption_keeps_legacy_files_states_and_current_order(root):
    folder(root / "mods", "Zulu")
    folder(root / "mods", "Alpha", active=False)
    service = LocalModPackages(root)
    current = [one(root, "Zulu"), one(root, "Alpha")]
    expected = {mod.id: snapshot(mod.path) for mod in current}
    service.adopt(current[1], effective_order=current)
    assert [mod.id for mod in service.sort_mods(reversed(current))] == ["Zulu", "Alpha"]
    assert [mod.id for mod in service.sort_mods_for_runtime(reversed(current))] == ["Zulu", "Alpha"]
    assert {mod.id: snapshot(mod.path) for mod in current} == expected
    renamed = [replace(current[1], name="AAA"), replace(current[0], name="ZZZ")]
    assert [mod.id for mod in service.sort_mods(renamed)] == ["Zulu", "Alpha"]


def test_order_read_is_pure_newcomers_append_and_explicit_order_persists(root, tmp_path):
    folder(root / "mods", "Zulu")
    folder(root / "mods", "Beta", active=False)
    service = LocalModPackages(root)
    current = [one(root, "Zulu"), one(root, "Beta")]
    assert service.sort_mods(current) == current
    assert not service.registry_path.exists()
    service.remember_order(current)
    service.import_package(folder(tmp_path, "Alpha"))
    assert [mod.id for mod in service.sort_mods(scan_mods(root))] == ["Zulu", "Beta", "Alpha"]
    requested = [one(root, "Alpha"), one(root, "Zulu"), one(root, "Beta")]
    service.set_order(requested)
    assert [mod.id for mod in LocalModPackages(root).sort_mods(scan_mods(root))] == ["Alpha", "Zulu", "Beta"]


def test_edited_mod_remove_restore_preserves_config_and_other_mod(root):
    folder(root / "mods", "One")
    folder(root / "mods", "Two")
    service = LocalModPackages(root)
    service.remember_order([one(root, "Two"), one(root, "One")])
    mod = one(root, "One")
    service.adopt(mod)
    (mod.path / "lib.js").write_text("// user's local edit")
    (mod.path / "settings.json").write_text('{"volume":17}')
    (root / "config").mkdir()
    external = root / "config/One.json"
    external.write_text('{"save":"preserve"}')
    expected = snapshot(mod.path)
    other = snapshot(root / "mods/Two")
    removed = service.remove(mod)
    assert removed.status == "quarantined"
    assert not mod.path.exists()
    assert snapshot(root / removed.archive_path) == expected
    assert external.read_text() == '{"save":"preserve"}'
    assert snapshot(root / "mods/Two") == other
    restored = service.restore(removed.record_id)
    assert restored.status == "installed"
    assert snapshot(mod.path) == expected
    assert one(root, "One").active
    assert [mod.id for mod in service.sort_mods(scan_mods(root))] == ["Two", "One"]


def test_removed_disabled_mod_stays_disabled_after_restore(root):
    folder(root / "mods", active=False)
    service = LocalModPackages(root)
    removed = service.remove(one(root))
    service.restore(removed.record_id)
    assert not one(root).active
    assert (root / "mods/Example/loader.js.off").is_file()


def test_cleanup_pending_failed_and_then_ready_preserve_provider_until_finished(root):
    folder(root / "mods")
    service = LocalModPackages(root)
    mod = one(root)
    for state in ("cleanup_pending", "cleanup_failed"):
        with pytest.raises(LocalModCleanupPending) as caught:
            service.remove(mod, cleanup_gate=lambda request: CleanupDecision(False, state, "Waiting for cleanup", True))
        assert caught.value.decision.restart_required
        assert (mod.path / "loader.js").exists()
        assert service.records()[0].cleanup["state"] == state
    received = []
    def complete(request):
        received.append(request)
        return CleanupDecision(True, "inactive")
    removed = service.remove(mod, cleanup_gate=complete)
    assert received[0].root == root.resolve()
    assert received[0].action == "remove"
    assert received[0].record is not None
    assert removed.status == "quarantined"


def test_disable_cleanup_gate_does_not_rename_until_ready(root):
    folder(root / "mods")
    folder(root / "mods", "Other")
    service = LocalModPackages(root)
    mod = one(root)
    with pytest.raises(LocalModCleanupPending):
        service.disable(mod, cleanup_gate=lambda _: CleanupDecision(False, "cleanup_pending"))
    assert one(root).active
    assert service.disable(mod, cleanup_gate=lambda _: CleanupDecision(True)) is False
    assert not one(root).active
    assert one(root, "Other").active


def test_same_mod_in_two_roots_has_independent_records(root, tmp_path):
    other = tmp_path / "OtherEveJS"
    (other / "mods").mkdir(parents=True)
    folder(root / "mods")
    folder(other / "mods")
    first, second = LocalModPackages(root), LocalModPackages(other)
    removed = first.remove(one(root))
    assert one(other).active
    with pytest.raises(LocalModPackageError, match="does not exist"):
        second.restore(removed.record_id)
    second.adopt(one(other))
    assert first.records()[0].record_id != second.records()[0].record_id
    with pytest.raises(LocalModPackageError, match="different EveJS root"):
        first.remove(one(other))


@pytest.mark.parametrize("kind", ["descriptor", "uninstaller", "registration", "integrated"])
def test_installer_and_integrated_mods_are_not_plain_folders(root, monkeypatch, kind):
    path = folder(root / "mods")
    mod = one(root)
    if kind == "descriptor":
        (path / "evejs-launcher.mod.json").write_text('{"kind":"source-integrated"}')
    elif kind == "uninstaller":
        (path / "unins000.exe").write_bytes(b"fixture")
    elif kind == "registration":
        from src.core import mod_management
        monkeypatch.setattr(mod_management, "read_managed_mod_registration", lambda _: object())
    else:
        mod = replace(mod, activation_kind=ActivationKind.JSON_BOOLEAN)
    with pytest.raises(LocalModPackageError, match="provider|managed"):
        LocalModPackages(root).remove(mod)
    assert (path / "loader.js").exists()


def test_restore_does_not_overwrite_new_mod_or_changed_archive(root):
    folder(root / "mods")
    service = LocalModPackages(root)
    removed = service.remove(one(root))
    new = folder(root / "mods")
    with pytest.raises(LocalModPackageError, match="already exists"):
        service.restore(removed.record_id)
    assert new.is_dir()
    # Move only this disposable conflicting fixture aside.
    new.rename(root / "new-mod-held")
    (root / removed.archive_path / "lib.js").write_text("changed archive")
    with pytest.raises(LocalModPackageError, match="archived package changed"):
        service.restore(removed.record_id)
    assert not (root / "mods/Example").exists()


def test_failed_registry_commit_rolls_folder_back_exactly(root, monkeypatch):
    folder(root / "mods")
    service = LocalModPackages(root)
    mod = one(root)
    service.adopt(mod)
    expected = snapshot(mod.path)
    old_registry = service.registry_path.read_bytes()
    original = service._save
    calls = 0
    def fail_commit(registry):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected atomic registry failure")
        original(registry)
    monkeypatch.setattr(service, "_save", fail_commit)
    with pytest.raises(LocalModPackageError, match="rolled back"):
        service.remove(mod)
    assert snapshot(mod.path) == expected
    assert service.registry_path.read_bytes() == old_registry


def test_interrupted_move_recovers_from_persisted_journal(root, monkeypatch):
    folder(root / "mods")
    service = LocalModPackages(root)
    original = Path.rename
    def crash_after_move(source, destination):
        result = original(source, destination)
        if source == root / "mods/Example":
            raise KeyboardInterrupt("simulate abrupt process termination")
        return result
    monkeypatch.setattr(Path, "rename", crash_after_move)
    with pytest.raises(KeyboardInterrupt):
        service.remove(one(root))
    monkeypatch.setattr(Path, "rename", original)
    assert service.records()[0].transaction is not None
    with pytest.raises(LocalModPackageError, match="Recover"):
        service.restore(service.records()[0].record_id)
    recovered = LocalModPackages(root).recover_pending()
    assert len(recovered) == 1
    assert recovered[0].status == "quarantined"
    service.restore(recovered[0].record_id)
    assert one(root).active


def test_registry_tamper_cannot_retarget_quarantine(root):
    folder(root / "mods")
    service = LocalModPackages(root)
    removed = service.remove(one(root))
    value = json.loads(service.registry_path.read_text())
    value["records"][removed.record_id]["archive_path"] = "config"
    service.registry_path.write_text(json.dumps(value))
    with pytest.raises(LocalModPackageError, match="record is invalid"):
        service.restore(removed.record_id)
    assert (root / removed.archive_path).is_dir()


def test_existing_lifecycle_lock_blocks_all_package_mutations(root, tmp_path):
    source = folder(tmp_path)
    service = LocalModPackages(root)
    with acquire_mod_lifecycle_lock(root):
        with pytest.raises(ModLifecycleBusyError):
            service.import_package(source)
    assert not list((root / "mods").iterdir())


@pytest.mark.parametrize("kind", ["settings", "client-package"])
@pytest.mark.parametrize("active_at_removal", [False, True])
def test_public_descriptor_only_import_is_inactive_host_data_without_rewriting(root, tmp_path, kind, active_at_removal):
    descriptor = json.dumps({"schemaVersion": 3, "id": "public.example", "kind": kind,
                             "enabled": True, "otherMetadata": {"future": "accepted by shape inspection"}})
    source = archive(tmp_path / "public.zip", [
        ("AuthorFolder/evejs-launcher.mod.json", descriptor), ("AuthorFolder/helper.js", b"// author helper")])
    service = LocalModPackages(root)
    preview = service.inspect(source)
    assert preview.mod_id == "public.example"
    assert preview.package_kind == kind
    assert not preview.has_loader
    record = service.import_package(source)
    assert record.enabled is False
    assert record.mod_id == "public.example"
    assert (root / record.relative_path / "evejs-launcher.mod.json").read_text() == descriptor
    enabled = service.set_enabled(record.record_id, True)
    assert enabled.enabled is True
    with pytest.raises(LocalModPackageError, match="cleanup provider"):
        service.set_enabled(record.record_id, False)
    disabled = service.set_enabled(record.record_id, False, cleanup_gate=lambda _: CleanupDecision(True))
    assert disabled.enabled is False
    if active_at_removal:
        service.set_enabled(record.record_id, True)
    mod = Mod("AuthorFolder", root / record.relative_path, active_at_removal, id=record.mod_id,
              evejs_root=root, activation_kind=ActivationKind.CLIENT_PACKAGE)
    with pytest.raises(LocalModPackageError, match="cleanup provider"):
        service.remove(mod)
    removed = service.remove(mod, cleanup_gate=lambda _: CleanupDecision(True))
    assert removed.enabled is False
    # Old candidates retained the enabled bit after successful external cleanup.
    # Undo must repair that metadata without invoking an installer implicitly.
    registry = json.loads(service.registry_path.read_text())
    registry["records"][removed.record_id]["enabled"] = True
    service.registry_path.write_text(json.dumps(registry))
    restored = service.restore(removed.record_id)
    assert restored.enabled is False
    assert (mod.path / "helper.js").read_bytes() == b"// author helper"


def test_pending_cleanup_cannot_be_bypassed_by_omitting_gate(root):
    folder(root / "mods")
    service = LocalModPackages(root)
    mod = one(root)
    with pytest.raises(LocalModCleanupPending):
        service.remove(mod, cleanup_gate=lambda _: CleanupDecision(False))
    with pytest.raises(LocalModCleanupPending):
        service.remove(mod)
    with pytest.raises(LocalModCleanupPending):
        service.disable(mod)
    assert (mod.path / "loader.js").exists()


def test_import_detects_identity_change_after_preview(root, tmp_path, monkeypatch):
    source = folder(tmp_path)
    descriptor = source / "evejs-launcher.mod.json"
    descriptor.write_text('{"schemaVersion":3,"kind":"loader","id":"first"}')
    service = LocalModPackages(root)
    copy = service._copy_package
    def changed(preview, destination):
        descriptor.write_text('{"schemaVersion":3,"kind":"loader","id":"changed"}')
        copy(preview, destination)
    monkeypatch.setattr(service, "_copy_package", changed)
    with pytest.raises(LocalModPackageError, match="identity changed"):
        service.import_package(source)
    assert not list((root / "mods").iterdir())


def test_interrupted_import_retains_staged_bytes_for_explicit_recovery(root, tmp_path, monkeypatch):
    source = folder(tmp_path)
    service = LocalModPackages(root)
    original = Path.rename
    def crash_before_move(path, destination):
        if destination == root / "mods/Example":
            raise KeyboardInterrupt("simulate termination before committed move")
        return original(path, destination)
    monkeypatch.setattr(Path, "rename", crash_before_move)
    with pytest.raises(KeyboardInterrupt):
        service.import_package(source)
    monkeypatch.setattr(Path, "rename", original)
    record = service.records()[0]
    assert record.transaction["action"] == "import"
    assert (root / record.transaction["source"] / "lib.js").exists()
    recovered = LocalModPackages(root).recover_pending()
    assert len(recovered) == 1
    assert (root / "mods/Example/lib.js").read_bytes() == (source / "lib.js").read_bytes()
    assert not one(root).active


def test_registry_cannot_be_reused_for_different_root(root, tmp_path):
    folder(root / "mods")
    service = LocalModPackages(root)
    service.adopt(one(root))
    other = tmp_path / "OtherEveJS"
    registry = other / "_local/launcher-mods/registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(service.registry_path.read_bytes())
    with pytest.raises(LocalModPackageError, match="another EveJS root"):
        LocalModPackages(other).records()


def test_disable_without_cleanup_uses_existing_durable_activation(root):
    folder(root / "mods")
    service = LocalModPackages(root)
    assert service.disable(one(root)) is False
    from src.core.mod_activation_state import read_mod_activation_state
    assert read_mod_activation_state(root).for_mod("Example").phase == "pending_restart"
    assert not one(root).active


def test_public_display_id_can_repeat_for_distinct_private_folders(root, tmp_path):
    source = archive(tmp_path / "public.zip", [("evejs-launcher.mod.json",
        '{"schemaVersion":3,"id":"same.id","kind":"settings"}')])
    service = LocalModPackages(root)
    service.import_package(source, folder_name="One")
    service.import_package(source, folder_name="Two")
    assert (root / "mods/Two").exists()
    assert len(service.records()) == 2
    assert len({record.mod_id for record in service.records()}) == 1
    assert len({record.relative_path for record in service.records()}) == 2


def test_can_manage_is_read_only_and_damaged_installer_ownership_stays_protected(root, monkeypatch):
    from src.core import mod_management
    path = folder(root / "mods")
    mod = one(root)
    service = LocalModPackages(root)
    before = snapshot(root)
    assert service.can_manage(mod)
    assert snapshot(root) == before and not service.registry_path.exists()
    def broken(_):
        raise mod_management.ModManagementError("Malformed existing enrollment")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", broken)
    assert not service.can_manage(mod)
    assert snapshot(root) == before and path.exists()


def test_can_manage_rejects_corrupt_local_registry_without_replacing_it(root):
    folder(root / "mods")
    mod = one(root)
    service = LocalModPackages(root)
    service.registry_path.parent.mkdir(parents=True)
    service.registry_path.write_bytes(b'{"schemaVersion":')
    assert not service.can_manage(mod)
    assert service.registry_path.read_bytes() == b'{"schemaVersion":'


def _copy_valid_registry_with_disabled_mods(root: Path, tmp_path: Path) -> LocalModPackages:
    original = tmp_path / "OriginalEveJS"
    (original / "mods").mkdir(parents=True)
    original_mods = {}
    for name in ("Alpha", "Beta", "Quarantined"):
        folder(original / "mods", name, active=False)
        original_mods[name] = one(original, name)

    source = LocalModPackages(original)
    source.adopt(
        original_mods["Alpha"],
        effective_order=[original_mods["Alpha"], original_mods["Beta"], original_mods["Quarantined"]],
    )
    source.adopt(original_mods["Beta"])
    source.remove(original_mods["Quarantined"])

    for name in ("Alpha", "Beta"):
        folder(root / "mods", name, active=False)
    target = LocalModPackages(root)
    target.registry_path.parent.mkdir(parents=True, exist_ok=True)
    target.registry_path.write_bytes(source.registry_path.read_bytes())
    return target


def test_runtime_order_preserves_valid_foreign_order_for_disabled_copy(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    original_registry = service.registry_path.read_bytes()
    mods = [one(root, "Beta"), one(root, "Alpha")]

    assert [mod.id for mod in service.sort_mods_for_runtime(mods)] == ["Alpha", "Beta"]
    assert service.registry_path.read_bytes() == original_registry
    assert {record["status"] for record in json.loads(original_registry)["records"].values()} == {
        "installed", "quarantined",
    }
    with pytest.raises(LocalModPackageError, match="another EveJS root"):
        service.records()
    with pytest.raises(LocalModPackageError, match="another EveJS root"):
        service.remember_order(mods)
    assert service.registry_path.read_bytes() == original_registry


def test_runtime_order_preserves_valid_foreign_order_for_active_mods(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    original_registry = service.registry_path.read_bytes()
    for name in ("Alpha", "Beta"):
        disabled_loader = root / f"mods/{name}/loader.js.off"
        disabled_loader.rename(disabled_loader.with_name("loader.js"))
    mods = [one(root, "Beta"), one(root, "Alpha")]

    assert all(mod.active for mod in mods)
    assert [mod.id for mod in service.sort_mods_for_runtime(mods)] == ["Alpha", "Beta"]
    assert service.registry_path.read_bytes() == original_registry


def test_runtime_order_rejects_mod_from_another_physical_root(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    other_root = tmp_path / "OtherEveJS"
    (other_root / "mods").mkdir(parents=True)
    folder(other_root / "mods", "External", active=True)
    original_registry = service.registry_path.read_bytes()

    with pytest.raises(LocalModPackageError, match="different EveJS root"):
        service.sort_mods_for_runtime([one(other_root, "External")])
    assert service.registry_path.read_bytes() == original_registry


@pytest.mark.parametrize("damage", ["schema", "record", "transaction", "malformed-transaction"])
def test_runtime_order_rejects_corrupt_or_pending_foreign_registry(root, tmp_path, damage):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    value = json.loads(service.registry_path.read_text(encoding="utf-8"))
    record_id = next(
        key for key, row in value["records"].items() if row["status"] == "installed"
    )
    if damage == "schema":
        value["schemaVersion"] = 2
    elif damage == "record":
        value["records"][record_id]["package_kind"] = "unknown-package-kind"
    elif damage == "transaction":
        record = value["records"][record_id]
        record["transaction"] = {
            "action": "import",
            "source": f"_local/launcher-mods/staging/{record_id}",
            "destination": record["relative_path"],
            "status": "installed",
            "fingerprint": record["fingerprint"],
        }
    else:
        value["records"][record_id]["transaction"] = {"action": "import"}
    service.registry_path.write_text(json.dumps(value), encoding="utf-8")
    original_registry = service.registry_path.read_bytes()

    with pytest.raises(LocalModPackageError):
        service.sort_mods_for_runtime(scan_mods(root))
    assert service.registry_path.read_bytes() == original_registry


def test_registry_relocation_preview_is_read_only_and_describes_exact_registry(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    original = service.registry_path.read_bytes()
    original_root = json.loads(original)["root"]
    original_mods = snapshot(root / "mods")

    preview = service.relocation_preview()

    assert preview.saved_root == original_root
    assert preview.current_root == str(root.resolve())
    assert preview.registry_sha256 == hashlib.sha256(original).hexdigest()
    assert preview.record_count == 3
    assert service.registry_path.read_bytes() == original
    assert snapshot(root / "mods") == original_mods


def test_registry_relocation_backs_up_exact_bytes_and_changes_only_root(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    original = service.registry_path.read_bytes()
    original_document = json.loads(original)
    original_mods = snapshot(root / "mods")
    preview = service.relocation_preview()

    backup = service.relocate_registry(preview.registry_sha256)

    assert backup.parent == service.registry_path.parent
    assert backup.read_bytes() == original
    updated = json.loads(service.registry_path.read_text(encoding="utf-8"))
    assert updated == {**original_document, "root": str(root.resolve())}
    assert snapshot(root / "mods") == original_mods
    assert {record.status for record in service.records()} == {"installed", "quarantined"}


def test_registry_relocation_rejects_a_stale_preview_without_writing(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    preview = service.relocation_preview()
    changed = json.loads(service.registry_path.read_text(encoding="utf-8"))
    changed["order"].reverse()
    service.registry_path.write_text(json.dumps(changed), encoding="utf-8")
    current = service.registry_path.read_bytes()

    with pytest.raises(LocalModPackageError, match="changed after preview"):
        service.relocate_registry(preview.registry_sha256)

    assert service.registry_path.read_bytes() == current
    assert not list(service.registry_path.parent.glob("registry.before-relocation-*.json"))


@pytest.mark.parametrize("damage", ["pending-package", "pending-update", "corrupt-record", "outside-path"])
def test_registry_relocation_rejects_pending_corrupt_or_outside_metadata(root, tmp_path, damage):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    value = json.loads(service.registry_path.read_text(encoding="utf-8"))
    record_id = next(
        key for key, row in value["records"].items() if row["status"] == "installed"
    )
    if damage == "pending-package":
        record = value["records"][record_id]
        record["transaction"] = {
            "action": "import",
            "source": f"_local/launcher-mods/staging/{record_id}",
            "destination": record["relative_path"],
            "status": "installed",
            "fingerprint": record["fingerprint"],
        }
    elif damage == "corrupt-record":
        value["records"][record_id]["package_kind"] = "unknown-package-kind"
    elif damage == "outside-path":
        value["records"][record_id]["relative_path"] = "../outside"
    service.registry_path.write_text(json.dumps(value), encoding="utf-8")
    original = service.registry_path.read_bytes()
    if damage == "pending-update":
        update = root / "_local/launcher-mod-updates" / ("a" * 32)
        update.mkdir(parents=True)
        (update / "update.json").write_text(
            json.dumps({"schemaVersion": 1, "phase": "prepared"}), encoding="utf-8"
        )

    with pytest.raises(LocalModPackageError):
        service.relocation_preview()

    assert service.registry_path.read_bytes() == original
    assert not list(service.registry_path.parent.glob("registry.before-relocation-*.json"))


def test_registry_relocation_rejects_reparse_record_paths(root, tmp_path):
    service = _copy_valid_registry_with_disabled_mods(root, tmp_path)
    package = root / "mods/Alpha"
    held = root / "mods/Alpha-held"
    package.rename(held)
    try:
        package.symlink_to(held, target_is_directory=True)
    except OSError:
        held.rename(package)
        pytest.skip("This Windows account cannot create directory symbolic links.")

    try:
        with pytest.raises(LocalModPackageError, match="Linked/reparse"):
            service.relocation_preview()
    finally:
        package.unlink()
        held.rename(package)
