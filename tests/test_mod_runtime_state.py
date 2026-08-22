"""Pure contracts for verified launcher mod runtime evidence."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import mod_runtime_state as implementation
from src.core.mod_manifest import ActivationKind, Mod
from src.core.runtime.docker_mods import (
    build_docker_mod_override,
    docker_mod_override_path,
)
from src.core.mod_runtime_state import (
    DOCKER_BACKEND,
    DOCKER_OVERRIDE_EVIDENCE,
    MAX_LOADER_PAYLOAD_BYTES,
    MAX_RUNTIME_IDENTITY_BYTES,
    MAX_SERVER_CONSOLE_BYTES,
    MAX_STATUS_LINE_BYTES,
    ModRuntimeSnapshotError,
    ModRuntimePlan,
    ModRuntimePlanEntry,
    ModRuntimeStateError,
    ModStatusProtocolError,
    NATIVE_BACKEND,
    NATIVE_LOADER_EVIDENCE,
    NATIVE_STATUS_EVIDENCE,
    RUNTIME_SNAPSHOT_FILENAME,
    STATUS_MARKER_PREFIX,
    STATUS_PROTOCOL,
    STATUS_TRANSPORT,
    build_docker_mod_runtime_snapshot,
    build_mod_runtime_plan,
    build_native_mod_runtime_snapshot,
    mod_contract_sha256,
    mod_runtime_snapshot_path,
    native_mod_preload_paths,
    parse_native_status_markers,
    read_mod_runtime_snapshot,
    read_server_console_bytes,
    write_mod_runtime_snapshot,
)


PID = 4321
RUNTIME_ID = "native-launch-20260822-4321"
DOCKER_RUNTIME_ID = "docker-container-deadbeef"


def _loader(
    root: Path,
    mod_id: str = "legacy-loader",
    *,
    active: bool = True,
    valid: bool = True,
) -> Mod:
    folder = root / "mods" / mod_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("loader.js" if active else "loader.js.disabled")).write_text(
        "module.exports = {};\n",
        encoding="utf-8",
    )
    return Mod(
        name=mod_id,
        path=folder,
        active=active,
        id=mod_id,
        activation_kind=ActivationKind.LOADER_RENAME,
        supported_backends=("native", "docker"),
        valid=valid,
        error=None if valid else "fixture invalid loader",
        evejs_root=root,
    )


def _integrated(
    root: Path,
    mod_id: str = "evejs-temp-npc",
    *,
    active: bool = True,
    valid: bool = True,
    protocol: str | None = STATUS_PROTOCOL,
    transport: str | None = STATUS_TRANSPORT,
) -> Mod:
    folder = root / "server" / "mods" / mod_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest_path = folder / "evejs-launcher.mod.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    config_path = root / "config" / "mods" / f"{mod_id}.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"schemaVersion": 3, "enabled": active}) + "\n",
        encoding="utf-8",
    )
    mod = Mod(
        name="Temporary NPC Prototype",
        path=folder,
        active=active,
        id=mod_id,
        version="0.4.2-prototype",
        description="Runtime-state fixture.",
        activation_kind=ActivationKind.JSON_BOOLEAN,
        supported_backends=("native",),
        restart_scope="game_server",
        manifest_path=manifest_path,
        config_path=config_path,
        config_key="enabled",
        allowed_config_schema_versions=(1, 2, 3),
        status_protocol=protocol or "",
        status_transport=transport or "",
        valid=valid,
        error=None if valid else "fixture invalid manifest",
        evejs_root=root,
    )
    return mod


def _marker(mod_id: str, state: str, *, pid: int = PID) -> bytes:
    payload = json.dumps(
        {"id": mod_id, "pid": pid, "state": state},
        separators=(",", ":"),
    )
    return f"{STATUS_MARKER_PREFIX}{payload}\n".encode("utf-8")


def _entry(snapshot, mod_id: str):
    return next(item for item in snapshot.mods if item.id == mod_id)


def _node_options_sha(plan: ModRuntimePlan) -> str:
    return hashlib.sha256((plan.docker_node_options or "").encode("utf-8")).hexdigest()


def _plan(
    root: Path,
    mods: list[Mod] | tuple[Mod, ...],
    *,
    backend: str = NATIVE_BACKEND,
    mode: str = "modded",
    runtime_identity: str | None = None,
    selected_loader_ids: list[str] | tuple[str, ...] | None = None,
) -> ModRuntimePlan:
    normalized = tuple(mods)
    if selected_loader_ids is None:
        selected_loader_ids = [
            mod.id
            for mod in normalized
            if mode == "modded"
            and mod.activation_kind is ActivationKind.LOADER_RENAME
            and mod.active
        ]
    if backend == DOCKER_BACKEND:
        override_path = docker_mod_override_path(root)
        material = build_docker_mod_override(root, selected_loader_ids)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_bytes(material.content.encode("utf-8"))
    return build_mod_runtime_plan(
        root,
        normalized,
        backend=backend,
        mode=mode,
        runtime_identity=(
            runtime_identity
            if runtime_identity is not None
            else (DOCKER_RUNTIME_ID if backend == DOCKER_BACKEND else RUNTIME_ID)
        ),
        selected_loader_ids=selected_loader_ids,
    )


def test_parser_accepts_noise_crlf_and_one_current_marker_per_declared_mod(
    tmp_path: Path,
) -> None:
    first = _integrated(tmp_path, "alpha", active=True)
    second = _integrated(tmp_path, "beta", active=False)
    stdout = (
        b"ordinary boot log\r\n"
        + _marker("beta", "disabled").replace(b"\n", b"\r\n")
        + _marker("alpha", "running")
        + b"ready\n"
    )

    markers = parse_native_status_markers(stdout, [first, second], pid=PID)

    assert set(markers) == {"alpha", "beta"}
    assert markers["alpha"].pid == PID
    assert markers["alpha"].effective is True
    assert markers["beta"].effective is False


@pytest.mark.parametrize(
    ("stdout", "match"),
    [
        (
            STATUS_MARKER_PREFIX.encode("ascii")
            + b"\xef\xbb\xbf{}\n",
            "without a BOM",
        ),
        (STATUS_MARKER_PREFIX.encode("ascii") + b"\xff\n", "strict UTF-8"),
        (
            b'EVEJS_MOD_STATUS {"id":"alpha","id":"alpha","pid":4321,"state":"running"}\n',
            "duplicate JSON key",
        ),
        (
            b'EVEJS_MOD_STATUS {"id":"alpha","pid":4321}\n',
            "fields are not exact",
        ),
        (
            b'EVEJS_MOD_STATUS {"id":"alpha","pid":4321,"state":"running","extra":1}\n',
            "fields are not exact",
        ),
        (_marker("alpha", "running", pid=99), "current PID"),
        (
            b'EVEJS_MOD_STATUS {"id":"alpha","pid":true,"state":"running"}\n',
            "positive integer",
        ),
        (_marker("alpha", "other"), "exactly 'running' or 'disabled'"),
        (b"EVEJS_MOD_STATUS{}\n", "Malformed"),
        (
            b'EVEJS_MOD_STATUS  {"id":"alpha","pid":4321,"state":"running"}\n',
            "consume the complete",
        ),
        (
            b'EVEJS_MOD_STATUS {"id":"alpha","pid":4321,"state":"running"} \n',
            "consume the complete",
        ),
    ],
)
def test_parser_rejects_malformed_marker_protocol(
    tmp_path: Path,
    stdout: bytes,
    match: str,
) -> None:
    mod = _integrated(tmp_path, "alpha")

    with pytest.raises(ModStatusProtocolError, match=match):
        parse_native_status_markers(stdout, [mod], pid=PID)


def test_parser_rejects_missing_duplicate_and_unexpected_ids(tmp_path: Path) -> None:
    mod = _integrated(tmp_path, "alpha")

    with pytest.raises(ModStatusProtocolError, match="Missing"):
        parse_native_status_markers(b"booted\n", [mod], pid=PID)
    with pytest.raises(ModStatusProtocolError, match="more than one"):
        parse_native_status_markers(
            _marker("alpha", "running") + _marker("alpha", "running"),
            [mod],
            pid=PID,
        )
    with pytest.raises(ModStatusProtocolError, match="Unexpected"):
        parse_native_status_markers(
            _marker("alpha", "running") + _marker("surprise", "running"),
            [mod],
            pid=PID,
        )


def test_parser_ignores_arbitrary_non_marker_stdout_bytes(tmp_path: Path) -> None:
    mod = _integrated(tmp_path, "alpha")
    stdout = b"\xff\xfe binary-ish dependency noise\n" + _marker("alpha", "running")

    markers = parse_native_status_markers(stdout, [mod], pid=PID)

    assert markers["alpha"].effective is True


def test_parser_is_bounded_by_total_output_and_status_line(tmp_path: Path) -> None:
    mod = _integrated(tmp_path, "alpha")

    with pytest.raises(ModStatusProtocolError, match="stdout exceeds"):
        parse_native_status_markers(
            b"x" * (MAX_SERVER_CONSOLE_BYTES + 1),
            [mod],
            pid=PID,
        )

    markers = parse_native_status_markers(
        b"x" * (MAX_STATUS_LINE_BYTES + 1) + b"\n" + _marker("alpha", "running"),
        [mod],
        pid=PID,
    )
    assert markers["alpha"].effective is True

    with pytest.raises(ModStatusProtocolError, match="EVEJS_MOD_STATUS line exceeds"):
        parse_native_status_markers(
            b"EVEJS_MOD_STATUS " + b"x" * MAX_STATUS_LINE_BYTES,
            [mod],
            pid=PID,
        )


def test_parser_requires_schema_v2_status_declaration(tmp_path: Path) -> None:
    missing = _integrated(tmp_path, "alpha", protocol=None)

    with pytest.raises(ModStatusProtocolError, match="does not declare"):
        parse_native_status_markers(_marker("alpha", "running"), [missing], pid=PID)


def test_native_builder_derives_integrated_and_mode_sensitive_loader_effectiveness(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, active=True)
    integrated = _integrated(tmp_path, active=False)
    observed = datetime(2026, 8, 22, 12, 30, tzinfo=timezone(timedelta(hours=2)))

    modded_plan = _plan(tmp_path, [loader, integrated])
    modded = build_native_mod_runtime_snapshot(
        modded_plan,
        [loader, integrated],
        _marker(integrated.id, "disabled"),
        pid=PID,
        observed_at=observed,
    )
    vanilla_plan = _plan(tmp_path, [loader, integrated], mode="vanilla")
    vanilla = build_native_mod_runtime_snapshot(
        vanilla_plan,
        [loader, integrated],
        _marker(integrated.id, "disabled"),
        pid=PID,
        observed_at=observed,
    )

    assert modded.backend == NATIVE_BACKEND
    assert modded.pid == PID
    assert modded.observed_at == datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)
    assert _entry(modded, loader.id).effective is True
    assert _entry(vanilla, loader.id).effective is False
    assert _entry(modded, loader.id).evidence == NATIVE_LOADER_EVIDENCE
    assert _entry(modded, integrated.id).effective is False
    assert _entry(modded, integrated.id).evidence == NATIVE_STATUS_EVIDENCE


def test_native_builder_fails_closed_on_invalid_discovery_or_state_mismatch(
    tmp_path: Path,
) -> None:
    invalid = _loader(tmp_path, "broken-loader", valid=False)
    integrated = _integrated(tmp_path, active=True)

    with pytest.raises(ModRuntimeStateError, match="invalid"):
        _plan(
            tmp_path,
            [invalid, integrated],
        )
    plan = _plan(tmp_path, [integrated])
    with pytest.raises(ModRuntimeStateError, match="does not match planned"):
        build_native_mod_runtime_snapshot(
            plan,
            [integrated],
            _marker(integrated.id, "disabled"),
            pid=PID,
        )


def test_native_builder_requires_exact_configured_preload_plan(tmp_path: Path) -> None:
    active = _loader(tmp_path, "active", active=True)
    disabled = _loader(tmp_path, "disabled", active=False)

    with pytest.raises(ModRuntimeStateError, match="launch plan does not match"):
        build_mod_runtime_plan(
            tmp_path,
            [active, disabled],
            backend=NATIVE_BACKEND,
            mode="modded",
            runtime_identity=RUNTIME_ID,
            selected_loader_ids=[],
        )
    with pytest.raises(ModRuntimeStateError, match="launch plan does not match"):
        build_mod_runtime_plan(
            tmp_path,
            [active, disabled],
            backend=NATIVE_BACKEND,
            mode="modded",
            runtime_identity=RUNTIME_ID,
            selected_loader_ids=[active.id, disabled.id],
        )
    with pytest.raises(ModRuntimeStateError, match="vanilla launch plan"):
        build_mod_runtime_plan(
            tmp_path,
            [active, disabled],
            backend=NATIVE_BACKEND,
            mode="vanilla",
            runtime_identity=RUNTIME_ID,
            selected_loader_ids=[active.id],
        )


def test_prelaunch_plan_freezes_every_contract_state_selection_and_identity(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Capital Loader", active=True)
    integrated = _integrated(tmp_path, "integrated", active=False)

    plan = _plan(
        tmp_path,
        [loader, integrated],
        selected_loader_ids=[loader.id],
    )

    assert plan.root == tmp_path.resolve()
    assert plan.backend == NATIVE_BACKEND
    assert plan.mode == "modded"
    assert plan.runtime_identity == RUNTIME_ID
    assert plan.selected_loader_ids == (loader.id,)
    assert [entry.id for entry in plan.mods] == [loader.id, integrated.id]
    assert {
        entry.id: entry.configured_active for entry in plan.mods
    } == {loader.id: True, integrated.id: False}
    assert {
        entry.id: entry.contract_sha256 for entry in plan.mods
    } == {
        loader.id: mod_contract_sha256(loader),
        integrated.id: mod_contract_sha256(integrated),
    }
    assert len(plan.plan_sha256) == 64
    with pytest.raises(FrozenInstanceError):
        plan.mode = "vanilla"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.mods[0].configured_active = False  # type: ignore[misc]


def test_plan_sha_is_canonical_but_preserves_exact_loader_preload_order(
    tmp_path: Path,
) -> None:
    alpha = _loader(tmp_path, "Alpha Loader")
    beta = _loader(tmp_path, "Beta Loader")

    first = _plan(
        tmp_path,
        [alpha, beta],
        selected_loader_ids=[alpha.id, beta.id],
    )
    same = _plan(
        tmp_path,
        [beta, alpha],
        selected_loader_ids=[alpha.id, beta.id],
    )
    reversed_selection = _plan(
        tmp_path,
        [alpha, beta],
        selected_loader_ids=[beta.id, alpha.id],
    )

    assert first.plan_sha256 == same.plan_sha256
    assert first.plan_sha256 != reversed_selection.plan_sha256
    assert reversed_selection.selected_loader_ids == (beta.id, alpha.id)


def test_native_plan_exposes_exact_ordered_preload_binding(tmp_path: Path) -> None:
    alpha = _loader(tmp_path, "Alpha Loader")
    beta = _loader(tmp_path, "Beta Loader")
    plan = _plan(
        tmp_path,
        [alpha, beta],
        selected_loader_ids=[beta.id, alpha.id],
    )

    assert native_mod_preload_paths(plan) == (
        beta.path / "loader.js",
        alpha.path / "loader.js",
    )

    (beta.path / "loader.js").rename(beta.path / "loader.js.disabled")
    with pytest.raises(ModRuntimeStateError, match="unavailable or unsafe"):
        native_mod_preload_paths(plan)


def test_native_preload_binding_rejects_tampered_plan_sha(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    plan = _plan(tmp_path, [loader])
    tampered = replace(plan, runtime_identity="different-runtime")

    with pytest.raises(ModRuntimeStateError, match="SHA-256"):
        native_mod_preload_paths(tampered)


@pytest.mark.parametrize(
    "runtime_identity",
    ["", " leading", "trailing ", "x" * (MAX_RUNTIME_IDENTITY_BYTES + 1)],
)
def test_plan_rejects_unsafe_or_unbounded_runtime_identity(
    tmp_path: Path,
    runtime_identity: str,
) -> None:
    loader = _loader(tmp_path)

    with pytest.raises(ModRuntimeStateError, match="Runtime identity"):
        _plan(tmp_path, [loader], runtime_identity=runtime_identity)


def test_snapshot_builder_rejects_every_post_start_rescan_drift(tmp_path: Path) -> None:
    loader = _loader(tmp_path, "loader")
    integrated = _integrated(tmp_path, "integrated")
    plan = _plan(tmp_path, [loader, integrated])

    with pytest.raises(ModRuntimeStateError, match="does not match"):
        build_native_mod_runtime_snapshot(
            plan,
            [loader],
            _marker(integrated.id, "running"),
            pid=PID,
        )

    integrated.active = False
    with pytest.raises(ModRuntimeStateError, match="Configured state"):
        build_native_mod_runtime_snapshot(
            plan,
            [loader, integrated],
            _marker(integrated.id, "disabled"),
            pid=PID,
        )
    integrated.active = True

    loader_path = loader.path / "loader.js"
    original = loader_path.read_bytes()
    loader_path.write_text("module.exports = { drifted: true };\n", encoding="utf-8")
    with pytest.raises(ModRuntimeStateError, match="Immutable contract"):
        build_native_mod_runtime_snapshot(
            plan,
            [loader, integrated],
            _marker(integrated.id, "running"),
            pid=PID,
        )
    loader_path.write_bytes(original)

    changed_kind = replace(
        loader,
        activation_kind=ActivationKind.JSON_BOOLEAN,
        supported_backends=("native",),
        manifest_path=integrated.manifest_path,
        config_path=integrated.config_path,
        config_key="enabled",
        allowed_config_schema_versions=(1, 2, 3),
        status_protocol=STATUS_PROTOCOL,
        status_transport=STATUS_TRANSPORT,
    )
    with pytest.raises(ModRuntimeStateError, match="Activation kind"):
        build_native_mod_runtime_snapshot(
            plan,
            [changed_kind, integrated],
            _marker(loader.id, "running") + _marker(integrated.id, "running"),
            pid=PID,
        )


def test_snapshot_uses_frozen_plan_hash_after_one_rescan_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader(tmp_path)
    plan = _plan(tmp_path, [loader])
    planned_hash = plan.mods[0].contract_sha256
    calls = 0

    def validation_hash(_mod: Mod) -> str:
        nonlocal calls
        calls += 1
        return planned_hash if calls == 1 else "f" * 64

    monkeypatch.setattr(implementation, "mod_contract_sha256", validation_hash)
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [loader],
        b"",
        pid=PID,
    )

    assert calls == 1
    assert snapshot.mods[0].contract_sha256 == planned_hash


def test_effective_for_requires_kind_root_and_immutable_contract_fingerprint(
    tmp_path: Path,
) -> None:
    mod = _integrated(tmp_path, active=True)
    plan = _plan(tmp_path, [mod])
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [mod],
        _marker(mod.id, "running"),
        pid=PID,
    )
    original_hash = mod_contract_sha256(mod)

    assert snapshot.effective_for(mod) is True
    mod.active = False
    assert mod_contract_sha256(mod) == original_hash
    assert snapshot.effective_for(mod) is True

    mod.status_transport = "somewhere_else"
    assert snapshot.effective_for(mod) is None
    mod.status_transport = STATUS_TRANSPORT
    mod.version = "changed-contract"
    assert snapshot.effective_for(mod) is None

    other_root = tmp_path / "other-root"
    other_root.mkdir()
    same_id_elsewhere = _integrated(other_root, mod.id)
    assert snapshot.effective_for(same_id_elsewhere) is None


def test_contract_fingerprint_includes_status_declaration_and_activation_kind(
    tmp_path: Path,
) -> None:
    integrated = _integrated(tmp_path)
    original = mod_contract_sha256(integrated)

    integrated.status_protocol = "changed"
    with pytest.raises(ModStatusProtocolError):
        mod_contract_sha256(integrated)
    integrated.status_protocol = STATUS_PROTOCOL
    integrated.allowed_config_schema_versions = (3,)
    assert mod_contract_sha256(integrated) != original


def test_loader_content_change_invalidates_effective_runtime_evidence(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path)
    plan = _plan(tmp_path, [loader])
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [loader],
        b"",
        pid=PID,
    )

    assert snapshot.effective_for(loader) is True
    (loader.path / "loader.js").write_text(
        "module.exports = { changed: true };\n",
        encoding="utf-8",
    )
    assert snapshot.effective_for(loader) is None


@pytest.mark.parametrize(
    "disabled_name",
    ["loader.js.disabled", "loader.js.off", "loader.js.bak"],
)
def test_loader_enable_disable_rename_preserves_payload_contract_fingerprint(
    tmp_path: Path,
    disabled_name: str,
) -> None:
    loader = _loader(tmp_path)
    enabled_hash = mod_contract_sha256(loader)

    (loader.path / "loader.js").rename(loader.path / disabled_name)
    loader.active = False
    disabled_hash = mod_contract_sha256(loader)

    assert disabled_hash == enabled_hash


def test_loader_contract_rejects_missing_ambiguous_and_nonregular_payloads(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path)
    active_path = loader.path / "loader.js"
    original = active_path.read_bytes()

    active_path.unlink()
    with pytest.raises(ModRuntimeStateError, match="exactly one"):
        mod_contract_sha256(loader)

    active_path.write_bytes(original)
    (loader.path / "loader.js.disabled").write_bytes(original)
    with pytest.raises(ModRuntimeStateError, match="exactly one"):
        mod_contract_sha256(loader)

    (loader.path / "loader.js.disabled").unlink()
    active_path.unlink()
    active_path.mkdir()
    with pytest.raises(ModRuntimeStateError, match="unsafe or not a regular file"):
        mod_contract_sha256(loader)


def test_loader_contract_rejects_symlink_payload_if_supported(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    active_path = loader.path / "loader.js"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-loader.js"
    outside.write_text("module.exports = { outside: true };\n", encoding="utf-8")
    active_path.unlink()
    try:
        active_path.symlink_to(outside)
    except OSError:
        outside.unlink(missing_ok=True)
        pytest.skip("This Windows account cannot create symbolic links.")
    try:
        with pytest.raises(ModRuntimeStateError, match="unsafe"):
            mod_contract_sha256(loader)
    finally:
        active_path.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)


def test_loader_contract_reader_is_bounded(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    (loader.path / "loader.js").write_bytes(
        b"x" * (MAX_LOADER_PAYLOAD_BYTES + 1)
    )

    with pytest.raises(ModRuntimeStateError, match="exceeds"):
        mod_contract_sha256(loader)


def test_atomic_snapshot_round_trip_and_pid_currentness(tmp_path: Path) -> None:
    loader = _loader(tmp_path)
    integrated = _integrated(tmp_path)
    plan = _plan(tmp_path, [loader, integrated])
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [loader, integrated],
        _marker(integrated.id, "running"),
        pid=PID,
        observed_at=datetime(2026, 8, 22, 10, 30, 15, 123456, timezone.utc),
    )

    path = write_mod_runtime_snapshot(snapshot)
    loaded = read_mod_runtime_snapshot(
        tmp_path,
        backend=NATIVE_BACKEND,
        runtime_identity=RUNTIME_ID,
        pid=PID,
    )

    assert path == tmp_path.resolve() / "_local" / RUNTIME_SNAPSHOT_FILENAME
    assert path == mod_runtime_snapshot_path(tmp_path)
    assert loaded == snapshot
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) == snapshot
    assert read_mod_runtime_snapshot(
        tmp_path,
        backend=NATIVE_BACKEND,
        runtime_identity=RUNTIME_ID,
    ) is None
    assert read_mod_runtime_snapshot(
        tmp_path,
        backend=NATIVE_BACKEND,
        pid=PID,
    ) is None
    assert read_mod_runtime_snapshot(
        tmp_path,
        backend=NATIVE_BACKEND,
        runtime_identity="wrong-runtime",
        pid=PID,
    ) is None
    assert (
        read_mod_runtime_snapshot(
            tmp_path,
            backend=NATIVE_BACKEND,
            runtime_identity=RUNTIME_ID,
            pid=PID + 1,
        )
        is None
    )
    assert not list(path.parent.glob(f".{RUNTIME_SNAPSHOT_FILENAME}.*.tmp"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schemaVersion",
        "root",
        "backend",
        "mode",
        "runtimeIdentity",
        "planSha256",
        "dockerOverride",
        "dockerNodeOptionsSha256",
        "selectedLoaderIds",
        "pid",
        "observedAt",
        "mods",
    }
    assert payload["observedAt"] == "2026-08-22T10:30:15.123456Z"
    assert payload["runtimeIdentity"] == RUNTIME_ID
    assert payload["planSha256"] == plan.plan_sha256


def test_safe_reader_rejects_stale_root_backend_and_corrupt_json(tmp_path: Path) -> None:
    mod = _loader(tmp_path)
    plan = _plan(tmp_path, [mod])
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [mod],
        b"booted\n",
        pid=PID,
    )
    path = write_mod_runtime_snapshot(snapshot)
    other_root = tmp_path / "other"
    other_root.mkdir()

    assert read_mod_runtime_snapshot(tmp_path, backend=DOCKER_BACKEND) is None
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["root"] = str(other_root.resolve())
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None

    path.write_bytes(b"\xef\xbb\xbf{}")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None
    path.write_text('{"schemaVersion":1,"schemaVersion":1}', encoding="utf-8")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None
    path.write_bytes(b"\xff")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None


def test_runtime_snapshot_parser_normalizes_over_limit_json_integer(
    tmp_path: Path,
) -> None:
    content = b'{"schemaVersion":' + (b"9" * 5000) + b"}"
    path = mod_runtime_snapshot_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(content)

    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None
    with pytest.raises(ModRuntimeSnapshotError, match="strict UTF-8 JSON"):
        implementation._parse_json_object(
            content,
            "Runtime snapshot",
            error_type=ModRuntimeSnapshotError,
        )


def test_safe_reader_rejects_unknown_snapshot_fields_and_evidence(tmp_path: Path) -> None:
    mod = _loader(tmp_path)
    plan = _plan(tmp_path, [mod], mode="vanilla")
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [mod],
        b"",
        pid=PID,
    )
    path = write_mod_runtime_snapshot(snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None

    write_mod_runtime_snapshot(snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mods"][0]["evidence"] = "trust_me_bro"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None

    write_mod_runtime_snapshot(snapshot)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mods"][0]["effective"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None


def test_docker_builder_uses_override_selection_and_allows_unknown_pid(
    tmp_path: Path,
) -> None:
    selected = _loader(tmp_path, "selected", active=True)
    excluded = _loader(tmp_path, "excluded", active=False)
    plan = _plan(
        tmp_path,
        [excluded, selected],
        backend=DOCKER_BACKEND,
    )

    snapshot = build_docker_mod_runtime_snapshot(
        plan,
        [excluded, selected],
        effective_node_options_sha256=_node_options_sha(plan),
        runtime_identity="docker-runtime-fixture",
        pid=None,
    )

    assert snapshot.backend == DOCKER_BACKEND
    assert snapshot.pid is None
    assert plan.docker_override_path == docker_mod_override_path(tmp_path)
    assert plan.docker_override_sha256 == build_docker_mod_override(
        tmp_path,
        [selected.id],
    ).content_hash
    assert snapshot.docker_override_path == plan.docker_override_path
    assert snapshot.docker_override_sha256 == plan.docker_override_sha256
    assert _entry(snapshot, "selected").effective is True
    assert _entry(snapshot, "excluded").effective is False
    assert {item.evidence for item in snapshot.mods} == {DOCKER_OVERRIDE_EVIDENCE}
    write_mod_runtime_snapshot(snapshot)
    assert read_mod_runtime_snapshot(tmp_path, backend=DOCKER_BACKEND) == snapshot
    assert (
        read_mod_runtime_snapshot(
            tmp_path,
            backend=DOCKER_BACKEND,
            runtime_identity="docker-runtime-fixture",
        )
        == snapshot
    )
    assert read_mod_runtime_snapshot(tmp_path, backend=DOCKER_BACKEND, pid=PID) is None


def test_docker_snapshot_rejects_override_drift_after_plan(tmp_path: Path) -> None:
    loader = _loader(tmp_path, "Loader With Spaces")
    plan = _plan(tmp_path, [loader], backend=DOCKER_BACKEND)
    assert plan.docker_override_path is not None
    plan.docker_override_path.write_bytes(b"services: {}\n")

    with pytest.raises(ModRuntimeStateError, match="drifted"):
        build_docker_mod_runtime_snapshot(
            plan,
            [loader],
            effective_node_options_sha256=_node_options_sha(plan),
            runtime_identity="docker-runtime-fixture",
        )


def test_docker_snapshot_round_trip_preserves_beta_alpha_preload_order(
    tmp_path: Path,
) -> None:
    alpha = _loader(tmp_path, "alpha")
    beta = _loader(tmp_path, "beta")
    plan = _plan(
        tmp_path,
        [alpha, beta],
        backend=DOCKER_BACKEND,
        selected_loader_ids=[beta.id, alpha.id],
    )
    snapshot = build_docker_mod_runtime_snapshot(
        plan,
        [beta, alpha],
        effective_node_options_sha256=_node_options_sha(plan),
        runtime_identity="docker-runtime-fixture",
    )

    assert snapshot.selected_loader_ids == (beta.id, alpha.id)
    write_mod_runtime_snapshot(snapshot)
    loaded = read_mod_runtime_snapshot(tmp_path, backend=DOCKER_BACKEND)
    assert loaded == snapshot
    assert loaded is not None
    assert loaded.selected_loader_ids == (beta.id, alpha.id)

    path = mod_runtime_snapshot_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dockerOverride"]["sha256"] = "f" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_mod_runtime_snapshot(tmp_path, backend=DOCKER_BACKEND) is None


def test_docker_plan_rejects_override_that_does_not_match_selection(
    tmp_path: Path,
) -> None:
    alpha = _loader(tmp_path, "alpha")
    beta = _loader(tmp_path, "beta", active=False)
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(build_docker_mod_override(tmp_path, [beta.id]).content.encode())

    with pytest.raises(ModRuntimeStateError, match="does not match"):
        build_mod_runtime_plan(
            tmp_path,
            [alpha, beta],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[alpha.id],
        )


def test_docker_candidate_plan_binds_exact_material_before_filesystem_write(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "alpha")
    material = build_docker_mod_override(tmp_path, [loader.id])
    path = docker_mod_override_path(tmp_path)

    plan = build_mod_runtime_plan(
        tmp_path,
        [loader],
        backend=DOCKER_BACKEND,
        mode="modded",
        runtime_identity=DOCKER_RUNTIME_ID,
        selected_loader_ids=[loader.id],
        docker_override_material=material,
    )

    assert not path.exists()
    assert plan.docker_override_path == path
    assert plan.docker_override_sha256 == material.content_hash
    assert plan.docker_node_options == material.node_options


def test_candidate_plan_rejects_mismatched_or_native_docker_material(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "alpha")
    material = build_docker_mod_override(tmp_path, [loader.id])

    with pytest.raises(ModRuntimeStateError, match="does not match"):
        build_mod_runtime_plan(
            tmp_path,
            [loader],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[loader.id],
            docker_override_material=replace(material, node_options="drift"),
        )

    with pytest.raises(ModRuntimeStateError, match="Native.*Docker"):
        build_mod_runtime_plan(
            tmp_path,
            [loader],
            backend=NATIVE_BACKEND,
            mode="modded",
            runtime_identity=RUNTIME_ID,
            selected_loader_ids=[loader.id],
            docker_override_material=material,
        )


def test_docker_empty_override_is_bound_and_reverified(tmp_path: Path) -> None:
    loader = _loader(tmp_path, active=True)
    plan = _plan(
        tmp_path,
        [loader],
        backend=DOCKER_BACKEND,
        mode="vanilla",
        selected_loader_ids=[],
    )

    assert plan.docker_override_sha256 == build_docker_mod_override(
        tmp_path,
        [],
    ).content_hash
    assert plan.docker_node_options == ""
    leaked_base_sha = hashlib.sha256(
        b'--require "/app/mods/Still Loaded/loader.js"'
    ).hexdigest()
    with pytest.raises(ModRuntimeStateError, match="does not match"):
        build_docker_mod_runtime_snapshot(
            plan,
            [loader],
            effective_node_options_sha256=leaked_base_sha,
            runtime_identity="docker-runtime-fixture",
        )
    snapshot = build_docker_mod_runtime_snapshot(
        plan,
        [loader],
        effective_node_options_sha256=_node_options_sha(plan),
        runtime_identity="docker-runtime-fixture",
    )
    assert _entry(snapshot, loader.id).effective is False

    assert plan.docker_override_path is not None
    plan.docker_override_path.write_bytes(b"services: {}\n")
    with pytest.raises(ModRuntimeStateError, match="drifted"):
        build_docker_mod_runtime_snapshot(
            plan,
            [loader],
            effective_node_options_sha256=_node_options_sha(plan),
            runtime_identity="docker-runtime-fixture",
        )


def test_docker_builder_rejects_invalid_non_loader_and_unexpected_selection(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "loader")
    invalid = _loader(tmp_path, "invalid", valid=False)
    integrated = _integrated(tmp_path)

    with pytest.raises(ModRuntimeStateError, match="unexpected loader"):
        build_mod_runtime_plan(
            tmp_path,
            [loader],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=["not-installed"],
        )
    with pytest.raises(ModRuntimeStateError, match="invalid"):
        build_mod_runtime_plan(
            tmp_path,
            [invalid],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[],
        )
    with pytest.raises(ModRuntimeStateError, match="does not support"):
        build_mod_runtime_plan(
            tmp_path,
            [integrated],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[],
        )


def test_docker_builder_rejects_config_mismatch_and_vanilla_preloads(
    tmp_path: Path,
) -> None:
    active = _loader(tmp_path, "active", active=True)

    with pytest.raises(ModRuntimeStateError, match="launch plan does not match"):
        build_mod_runtime_plan(
            tmp_path,
            [active],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[],
        )
    with pytest.raises(ModRuntimeStateError, match="vanilla launch plan"):
        build_mod_runtime_plan(
            tmp_path,
            [active],
            backend=DOCKER_BACKEND,
            mode="vanilla",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[active.id],
        )


def test_legacy_ids_allow_uppercase_and_spaces_for_native_and_docker(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Capital Loader Mod")
    native_plan = _plan(tmp_path, [loader])
    assert native_mod_preload_paths(native_plan) == (loader.path / "loader.js",)

    docker_plan = _plan(tmp_path, [loader], backend=DOCKER_BACKEND)
    snapshot = build_docker_mod_runtime_snapshot(
        docker_plan,
        [loader],
        effective_node_options_sha256=_node_options_sha(docker_plan),
        runtime_identity="docker-runtime-fixture",
    )
    assert snapshot.effective_for(loader) is True


def test_integrated_ids_remain_strict_lowercase(tmp_path: Path) -> None:
    integrated = _integrated(tmp_path, "Uppercase-Integrated")

    with pytest.raises(ModRuntimeStateError, match="Integrated mod id"):
        _plan(tmp_path, [integrated])


@pytest.mark.parametrize(
    "unsafe_id",
    ["../escape", "bad/name", "bad\\name", "bad*name", "trailing."],
)
def test_legacy_ids_reject_unsafe_folder_components(
    tmp_path: Path,
    unsafe_id: str,
) -> None:
    loader = _loader(tmp_path)

    with pytest.raises(ModRuntimeStateError, match="Loader mod id"):
        _plan(tmp_path, [replace(loader, id=unsafe_id)])


def test_mod_ids_are_casefold_unique_and_docker_ids_must_be_trimmed(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path, "Mixed Case")
    duplicate = replace(loader, id="mixed case")
    with pytest.raises(ModRuntimeStateError, match="unique"):
        _plan(tmp_path, [loader, duplicate])

    leading_space = _loader(tmp_path, " Leading Space")
    with pytest.raises(ModRuntimeStateError, match="trimmed exactly"):
        build_mod_runtime_plan(
            tmp_path,
            [leading_space],
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity=DOCKER_RUNTIME_ID,
            selected_loader_ids=[leading_space.id],
        )


def test_json_recursion_is_translated_for_marker_and_snapshot_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integrated = _integrated(tmp_path, "integrated")

    def recurse(*_args, **_kwargs):
        raise RecursionError("fixture recursion")

    monkeypatch.setattr(implementation.json, "loads", recurse)
    with pytest.raises(ModStatusProtocolError, match="nesting limit"):
        parse_native_status_markers(
            _marker(integrated.id, "running"),
            [integrated],
            pid=PID,
        )


def test_snapshot_json_recursion_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader(tmp_path)
    plan = _plan(tmp_path, [loader])
    snapshot = build_native_mod_runtime_snapshot(plan, [loader], b"", pid=PID)
    write_mod_runtime_snapshot(snapshot)

    def recurse(*_args, **_kwargs):
        raise RecursionError("fixture recursion")

    monkeypatch.setattr(implementation.json, "loads", recurse)
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None


def test_console_file_reader_never_reads_unbounded_input(tmp_path: Path) -> None:
    console = tmp_path / "server.stdout.log"
    console.write_bytes(b"hello\n")
    assert read_server_console_bytes(console, maximum_bytes=6) == b"hello\n"

    console.write_bytes(b"1234567")
    with pytest.raises(ModStatusProtocolError, match="exceeds"):
        read_server_console_bytes(console, maximum_bytes=6)


def test_bounded_console_reader_rejects_file_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = tmp_path / "server.stdout.log"
    console.write_bytes(b"hello\n")
    real_fstat = implementation.os.fstat

    def changed_identity(descriptor: int):
        current = real_fstat(descriptor)
        return SimpleNamespace(
            st_dev=current.st_dev,
            st_ino=current.st_ino + 1,
            st_mode=current.st_mode,
            st_size=current.st_size,
            st_mtime_ns=current.st_mtime_ns,
        )

    monkeypatch.setattr(implementation.os, "fstat", changed_identity)
    with pytest.raises(ModStatusProtocolError, match="changed while"):
        read_server_console_bytes(console)


def test_snapshot_reader_rejects_file_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = _loader(tmp_path)
    plan = _plan(tmp_path, [loader])
    snapshot = build_native_mod_runtime_snapshot(plan, [loader], b"", pid=PID)
    write_mod_runtime_snapshot(snapshot)
    real_fstat = implementation.os.fstat

    def changed_identity(descriptor: int):
        current = real_fstat(descriptor)
        return SimpleNamespace(
            st_dev=current.st_dev,
            st_ino=current.st_ino + 1,
            st_mode=current.st_mode,
            st_size=current.st_size,
            st_mtime_ns=current.st_mtime_ns,
        )

    monkeypatch.setattr(implementation.os, "fstat", changed_identity)
    assert read_mod_runtime_snapshot(tmp_path, backend=NATIVE_BACKEND) is None


def test_snapshot_writer_explicitly_documents_external_lock_ownership() -> None:
    assert write_mod_runtime_snapshot.__doc__ is not None
    assert "caller must own the lifecycle lock" in write_mod_runtime_snapshot.__doc__


def test_writer_rejects_noncanonical_evidence_without_replacing_existing_file(
    tmp_path: Path,
) -> None:
    mod = _loader(tmp_path)
    plan = _plan(tmp_path, [mod])
    snapshot = build_native_mod_runtime_snapshot(
        plan,
        [mod],
        b"",
        pid=PID,
    )
    path = write_mod_runtime_snapshot(snapshot)
    original = path.read_bytes()
    bad_entry = snapshot.mods[0].__class__(
        id=snapshot.mods[0].id,
        activation_kind=snapshot.mods[0].activation_kind,
        contract_sha256=snapshot.mods[0].contract_sha256,
        effective=snapshot.mods[0].effective,
        evidence="made_up",
    )
    bad_snapshot = snapshot.__class__(
        schema_version=snapshot.schema_version,
        root=snapshot.root,
        backend=snapshot.backend,
        mode=snapshot.mode,
        runtime_identity=snapshot.runtime_identity,
        plan_sha256=snapshot.plan_sha256,
        docker_override_path=snapshot.docker_override_path,
        docker_override_sha256=snapshot.docker_override_sha256,
        docker_node_options_sha256=snapshot.docker_node_options_sha256,
        selected_loader_ids=snapshot.selected_loader_ids,
        pid=snapshot.pid,
        observed_at=snapshot.observed_at,
        mods=(bad_entry,),
    )

    with pytest.raises(ModRuntimeSnapshotError, match="Runtime evidence"):
        write_mod_runtime_snapshot(bad_snapshot)
    assert path.read_bytes() == original


def test_writer_rejects_boolean_schema_version_and_effective_vanilla_loader(
    tmp_path: Path,
) -> None:
    loader = _loader(tmp_path)
    plan = _plan(tmp_path, [loader], mode="vanilla")
    snapshot = build_native_mod_runtime_snapshot(plan, [loader], b"", pid=PID)
    path = write_mod_runtime_snapshot(snapshot)
    original = path.read_bytes()

    with pytest.raises(ModRuntimeSnapshotError, match="schemaVersion"):
        write_mod_runtime_snapshot(replace(snapshot, schema_version=True))

    effective_entry = replace(snapshot.mods[0], effective=True)
    with pytest.raises(ModRuntimeSnapshotError, match="vanilla"):
        write_mod_runtime_snapshot(replace(snapshot, mods=(effective_entry,)))
    assert path.read_bytes() == original
