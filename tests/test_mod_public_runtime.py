"""Root/path identity and partial observations without starting a runtime."""
from dataclasses import replace
import hashlib
import json

import pytest

from src import config
from src.core.local_mod_packages import CleanupDecision, LocalModPackages
from src.core.mod_activation_service import request_mod_activation
from src.core.mod_activation_state import (
    ModActivationStatus, clear_confirmed_mod_activations, mod_activation_state_path,
    read_mod_activation_state, reconcile_mod_activation,
)
from src.core.mod_manifest import ActivationKind, scan_mods
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock, ModLifecycleBusyError
from src.core.mod_runtime_state import (
    ModRuntimeIdentityError, ModRuntimeSnapshotError, ModRuntimeStateError,
    build_docker_mod_runtime_snapshot, build_mod_runtime_plan,
    build_native_mod_runtime_snapshot, collect_native_status_markers,
    mod_state_key, native_mod_preload_paths, read_mod_runtime_snapshot,
    write_mod_runtime_snapshot,
)
from src.core.runtime.docker_mods import build_docker_mod_override, docker_mod_override_path


@pytest.fixture
def root(tmp_path, monkeypatch):
    from src.core import mod_management
    def absent(_):
        raise mod_management.ModNotManagedError("No fixture installer enrollment")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", absent)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "profile")
    monkeypatch.setenv("APPDATA", str(tmp_path / "profile"))
    value = tmp_path / "EveJS"
    (value / "mods").mkdir(parents=True)
    return value


def public(root, folder, *, mod_id="author.example", kind="loader", active=True):
    path = root / "mods" / folder
    path.mkdir()
    strategy = {"loader": "loader_rename", "source-integrated": "json_boolean",
                "settings": "package", "client-package": "client_package"}[kind]
    payload = {"schemaVersion": 3, "id": mod_id, "displayName": folder,
        "version": "1.0.0", "kind": kind, "restart": "game_server",
        "activation": {"strategy": strategy}}
    if kind == "loader":
        (path / ("loader.js" if active else "loader.js.disabled")).write_text("// local loader\n")
    elif kind == "source-integrated":
        payload["activation"].update(configPath=f"{folder}.json", property=["feature", "enabled"])
        (root / f"{folder}.json").write_text(json.dumps({"feature": {"enabled": active}, "keep": 17}))
    (path / "evejs-launcher.mod.json").write_text(json.dumps(payload))
    return path


def plan(root, mods, *, backend="native", selected=None):
    selected = selected if selected is not None else [mod.path.name for mod in mods
        if mod.activation_kind is ActivationKind.LOADER_RENAME and mod.active]
    if backend == "docker_compose":
        material = build_docker_mod_override(root, selected)
        path = docker_mod_override_path(root)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(material.content.encode("utf-8"))
    return build_mod_runtime_plan(root, mods, backend=backend, mode="modded",
        runtime_identity="fixture-runtime", selected_loader_ids=selected)


def marker(mod_id, state="running", *, pid=321):
    return ("EVEJS_MOD_STATUS " + json.dumps({"id": mod_id, "pid": pid, "state": state}) + "\n").encode()


def native(root, mods, stdout=b"", *, strict_status=False):
    return build_native_mod_runtime_snapshot(plan(root, mods), mods, stdout,
        pid=321, strict_status=strict_status)


@pytest.mark.parametrize("backend", ["native", "docker_compose"])
def test_same_author_id_loaders_keep_folder_order_and_round_trip(root, backend):
    first = public(root, "FirstFolder")
    second = public(root, "SecondFolder")
    mods = scan_mods(root)
    frozen = plan(root, mods, backend=backend, selected=[second.name, first.name])
    assert frozen.schema_version == 2
    assert {entry.id for entry in frozen.mods} == {"path:mods/firstfolder", "path:mods/secondfolder"}
    assert frozen.selected_loader_ids == (second.name, first.name)
    if backend == "native":
        assert native_mod_preload_paths(frozen) == (second / "loader.js", first / "loader.js")
        snapshot = build_native_mod_runtime_snapshot(frozen, mods, b"", pid=321, strict_status=False)
    else:
        snapshot = build_docker_mod_runtime_snapshot(frozen, mods,
            effective_node_options_sha256=hashlib.sha256(frozen.docker_node_options.encode()).hexdigest(),
            runtime_identity="fixture-runtime", pid=321)
    write_mod_runtime_snapshot(snapshot)
    restored = read_mod_runtime_snapshot(root, backend=backend, runtime_identity="fixture-runtime", pid=321)
    assert restored == snapshot
    assert restored.selected_loader_ids == (second.name, first.name)
    assert all(restored.effective_for(mod) is True for mod in mods)


def test_same_id_activation_intents_and_mutations_stay_independent(root):
    first = public(root, "First")
    second = public(root, "Second")
    mods = {mod.path.name: mod for mod in scan_mods(root)}
    request_mod_activation(mods["First"], False)
    assert (first / "loader.js.disabled").exists()
    assert (second / "loader.js").read_text() == "// local loader\n"
    state = read_mod_activation_state(root)
    assert state.schema_version == 2
    assert [intent.id for intent in state.intents] == ["path:mods/first"]
    assert state.for_mod("path:mods/second") is None
    fresh = scan_mods(root)
    snapshot = native(root, fresh)
    assert clear_confirmed_mod_activations(root, snapshot, fresh) == ("path:mods/first",)
    assert not read_mod_activation_state(root).intents


@pytest.mark.parametrize("problem", ["missing", "duplicate", "malformed"])
def test_optional_diagnostic_preserves_successful_neighbor(root, problem):
    public(root, "Affected", kind="source-integrated", mod_id="affected")
    public(root, "Healthy", kind="source-integrated", mod_id="healthy")
    mods = {mod.id: mod for mod in scan_mods(root)}
    bad = b"" if problem == "missing" else (
        marker("affected") * 2 if problem == "duplicate" else b"EVEJS_MOD_STATUS {bad}\n")
    snapshot = native(root, list(mods.values()), bad + marker("healthy"))
    assert snapshot.effective_for(mods["healthy"]) is True
    assert snapshot.effective_for(mods["affected"]) is None
    expected = "status-marker-duplicate" if problem == "duplicate" else "status-marker-missing"
    assert snapshot.diagnostic_for(mods["affected"]) == expected
    projection = reconcile_mod_activation(mods["affected"], snapshot)
    assert projection.status is ModActivationStatus.RUNTIME_UNVERIFIED
    assert projection.configured and projection.effective is None
    assert projection.reason_code == expected
    write_mod_runtime_snapshot(snapshot)
    assert read_mod_runtime_snapshot(root, backend="native", runtime_identity="fixture-runtime", pid=321) == snapshot


def test_v1_marker_cannot_claim_which_duplicate_public_id_emitted_it(root):
    public(root, "One", kind="source-integrated")
    public(root, "Two", kind="source-integrated")
    mods = scan_mods(root)
    observed = collect_native_status_markers(marker("author.example"), mods, pid=321)
    assert not observed.markers
    assert set(observed.diagnostics.values()) == {"status-id-ambiguous"}
    snapshot = native(root, mods, marker("author.example"))
    assert all(snapshot.effective_for(mod) is None for mod in mods)


def test_optional_marker_with_wrong_process_identity_still_fails(root):
    public(root, "One", kind="source-integrated")
    with pytest.raises(ModRuntimeIdentityError, match="current PID"):
        native(root, scan_mods(root), marker("author.example", pid=999))


def test_observed_config_mismatch_is_retained_without_claiming_success(root):
    public(root, "One", kind="source-integrated", active=False)
    mod = scan_mods(root)[0]
    snapshot = native(root, [mod], marker(mod.id))
    assert snapshot.effective_for(mod) is True
    projection = reconcile_mod_activation(mod, snapshot)
    assert projection.status is ModActivationStatus.RESTART_REQUIRED
    assert not projection.configured and projection.effective
    assert snapshot.diagnostic_for(mod) == "configured-runtime-mismatch"


@pytest.mark.parametrize("kind", ["settings", "client-package"])
def test_registry_configured_state_never_becomes_game_runtime_proof(root, kind):
    public(root, "Package", kind=kind)
    service = LocalModPackages(root)
    record = service.adopt(scan_mods(root)[0])
    assert record.enabled is False
    request_mod_activation(scan_mods(root)[0], True)
    mod = scan_mods(root)[0]
    assert mod.active and service.records()[0].enabled is True
    snapshot = native(root, [mod])
    projection = reconcile_mod_activation(mod, snapshot)
    assert projection.status is ModActivationStatus.RUNTIME_UNVERIFIED
    assert projection.reason_code == "provider-evidence-missing"
    assert snapshot.effective_for(mod) is None
    assert not clear_confirmed_mod_activations(root, snapshot, [mod])
    forged = replace(snapshot.mods[0], effective=True)
    with pytest.raises(ModRuntimeSnapshotError):
        write_mod_runtime_snapshot(replace(snapshot, mods=(forged,)))


def test_schema1_evidence_and_journal_survive_public_entry_addition(root):
    legacy = root / "mods" / "Legacy"
    legacy.mkdir()
    (legacy / "loader.js").write_text("// legacy")
    old_mod = scan_mods(root)[0]
    snapshot = native(root, [old_mod])
    assert snapshot.schema_version == 1
    write_mod_runtime_snapshot(snapshot)
    request_mod_activation(old_mod, False)
    before = json.loads(mod_activation_state_path(root).read_text())
    assert before["schemaVersion"] == 1 and set(before["records"]) == {"Legacy"}
    public(root, "New", kind="source-integrated", mod_id="Legacy")
    current = {mod.path.name: mod for mod in scan_mods(root)}
    request_mod_activation(current["New"], False)
    after = json.loads(mod_activation_state_path(root).read_text())
    assert after["schemaVersion"] == 2
    assert after["records"]["Legacy"] == before["records"]["Legacy"]
    assert set(after["records"]) == {"Legacy", "path:mods/new"}
    restored = read_mod_runtime_snapshot(root, backend="native", runtime_identity="fixture-runtime", pid=321)
    assert restored == snapshot
    assert restored.effective_for(current["New"]) is None


def test_public_contract_change_and_wrong_loader_binding_fail_safely(root):
    path = public(root, "Package")
    mods = scan_mods(root)
    frozen = plan(root, mods)
    snapshot = native(root, mods)
    descriptor = path / "evejs-launcher.mod.json"
    payload = json.loads(descriptor.read_text())
    payload["version"] = "2.0.0"
    descriptor.write_text(json.dumps(payload))
    with pytest.raises(ModRuntimeStateError, match="drifted"):
        build_native_mod_runtime_snapshot(frozen, scan_mods(root), b"", pid=321, strict_status=False)
    assert snapshot.effective_for(scan_mods(root)[0]) is None
    bad = replace(snapshot.mods[0], loader_name="AnotherFolder")
    with pytest.raises(ModRuntimeSnapshotError, match="selected folder"):
        write_mod_runtime_snapshot(replace(snapshot, mods=(bad,)))


def test_new_path_key_cannot_be_smuggled_into_old_snapshot_schema(root):
    public(root, "Package")
    snapshot = native(root, scan_mods(root))
    with pytest.raises(ModRuntimeSnapshotError):
        write_mod_runtime_snapshot(replace(snapshot, schema_version=1))


def test_public_provider_mutation_uses_one_lock_and_durable_journal(root):
    public(root, "Provider", kind="client-package")
    service = LocalModPackages(root)
    record = service.adopt(scan_mods(root)[0])
    service.set_enabled(record.record_id, True)
    mod = scan_mods(root)[0]
    calls = []
    def mutate(current, desired):
        assert current is mod and desired is False
        assert read_mod_activation_state(root).for_mod(mod_state_key(mod)).phase.value == "prepared"
        with pytest.raises(ModLifecycleBusyError):
            with acquire_mod_lifecycle_lock(root):
                pass
        result = service.set_enabled_locked(record.record_id, desired,
            cleanup_gate=lambda request: calls.append(request.action) or CleanupDecision(True))
        return result.enabled
    assert request_mod_activation(mod, False, mutation=mutate) is False
    assert calls == ["disable"]
    assert not scan_mods(root)[0].active
    assert read_mod_activation_state(root).for_mod(mod_state_key(mod)).phase.value == "pending_restart"


def test_failed_public_provider_mutation_does_not_claim_configured_success(root):
    public(root, "Provider", kind="settings")
    service = LocalModPackages(root)
    service.adopt(scan_mods(root)[0])
    mod = scan_mods(root)[0]
    def fail(_mod, _desired):
        raise RuntimeError("Provider preparation incomplete")
    with pytest.raises(RuntimeError, match="incomplete"):
        request_mod_activation(mod, True, mutation=fail)
    assert not scan_mods(root)[0].active
    projection = reconcile_mod_activation(mod, None)
    assert projection.status is ModActivationStatus.VERIFICATION_FAILED
    assert read_mod_activation_state(root).for_mod(mod_state_key(mod)).error_code == "activation-mutation-failed"
