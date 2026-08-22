"""Durable activation-intent contracts for launcher-managed mods."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from src import config
from src.core.mod_activation_state import (
    ACTIVATION_STATE_DIRECTORY,
    ACTIVATION_STATE_SCHEMA_VERSION,
    ActivationPhase,
    MAX_ACTIVATION_STATE_BYTES,
    ModActivationStateError,
    ModActivationStateReadError,
    ModActivationStateWriteError,
    ModActivationStatus,
    ModActivationTransitionError,
    clear_confirmed_mod_activation,
    clear_confirmed_mod_activations,
    fail_mod_activation,
    list_mod_activation_intents,
    mark_mod_activation_pending,
    mod_activation_state_path,
    prepare_mod_activation,
    project_mod_activation,
    read_mod_activation_state,
    reconcile_mod_activation,
    retire_removed_mod_activation,
)
from src.core.mod_manifest import ActivationKind, Mod
from src.core.mod_runtime_state import (
    DOCKER_BACKEND,
    build_docker_mod_runtime_snapshot,
    build_mod_runtime_plan,
)
from src.core.runtime.docker_mods import (
    build_docker_mod_override,
    docker_mod_override_path,
)


UTC_NOW = datetime(2026, 8, 22, 10, 11, 12, 345678, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_launcher_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher-config")


def _loader(
    root: Path,
    mod_id: str = "legacy-loader",
    *,
    active: bool = True,
    version: str = "1.0.0",
) -> Mod:
    root.mkdir(parents=True, exist_ok=True)
    folder = root / "mods" / mod_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "loader.js").write_text("module.exports = {};\n", encoding="utf-8")
    return Mod(
        name=mod_id,
        path=folder,
        active=active,
        id=mod_id,
        version=version,
        description="Activation journal fixture.",
        activation_kind=ActivationKind.LOADER_RENAME,
        supported_backends=("native", "docker"),
        restart_scope="game_server",
        evejs_root=root,
    )


def _snapshot(root: Path, mods: list[Mod], *selected: str):
    # Build evidence as it would have existed at runtime observation time,
    # then restore the current configured state used by projection tests.
    configured = [mod.active for mod in mods]
    try:
        chosen = set(selected)
        for mod in mods:
            mod.active = mod.id in chosen
        override = build_docker_mod_override(root, selected)
        override_path = docker_mod_override_path(root)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_bytes(override.content.encode("utf-8"))
        plan = build_mod_runtime_plan(
            root,
            mods,
            backend=DOCKER_BACKEND,
            mode="modded",
            runtime_identity="activation-state-fixture",
            selected_loader_ids=selected,
        )
        return build_docker_mod_runtime_snapshot(
            plan,
            mods,
            effective_node_options_sha256=hashlib.sha256(
                override.node_options.encode("utf-8")
            ).hexdigest(),
            runtime_identity="docker-runtime-fixture",
            pid=4321,
            observed_at=UTC_NOW,
        )
    finally:
        for mod, active in zip(mods, configured, strict=True):
            mod.active = active


def _payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_missing_state_is_empty_and_path_is_hashed_per_canonical_root(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "eve-one"
    second_root = tmp_path / "eve-two"
    first_root.mkdir()
    second_root.mkdir()

    state = read_mod_activation_state(first_root / ".")
    first_path = mod_activation_state_path(first_root)
    second_path = mod_activation_state_path(second_root)

    expected_hash = hashlib.sha256(
        os.path.normcase(str(first_root.resolve())).encode("utf-8")
    ).hexdigest()
    assert state.schema_version == ACTIVATION_STATE_SCHEMA_VERSION
    assert state.root == first_root.resolve()
    assert state.intents == ()
    assert first_path == (
        config.CONFIG_DIR / ACTIVATION_STATE_DIRECTORY / f"{expected_hash}.json"
    )
    assert first_path != second_path
    assert not first_path.exists()


def test_prepare_is_durable_utf8_without_bom_and_does_not_change_mod_payload(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, active=False)
    loader_bytes = (mod.path / "loader.js").read_bytes()

    intent = prepare_mod_activation(mod, True, updated_at=UTC_NOW)
    path = mod_activation_state_path(root)
    content = path.read_bytes()
    persisted = _payload(path)

    assert not content.startswith(b"\xef\xbb\xbf")
    assert content.endswith(b"\n")
    assert intent.phase is ActivationPhase.PREPARED
    assert intent.desired is True
    assert intent.updated_at == UTC_NOW
    assert set(persisted) == {"schemaVersion", "root", "records"}
    assert set(persisted["records"]) == {mod.id}
    assert persisted["records"][mod.id]["errorCode"] is None
    assert (mod.path / "loader.js").read_bytes() == loader_bytes
    assert mod.active is False


def test_prepare_replaces_an_older_valid_operation_for_toggle_back(
    tmp_path: Path,
) -> None:
    mod = _loader(tmp_path / "eve", active=True)
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    first = mark_mod_activation_pending(mod, False, updated_at=UTC_NOW)

    replacement = prepare_mod_activation(
        mod,
        True,
        updated_at=UTC_NOW + timedelta(seconds=1),
    )
    records = list_mod_activation_intents(mod.evejs_root)

    assert first.phase is ActivationPhase.PENDING_RESTART
    assert records == (replacement,)
    assert replacement.desired is True
    assert replacement.phase is ActivationPhase.PREPARED


def test_mark_pending_requires_the_exact_prepared_operation(
    tmp_path: Path,
) -> None:
    mod = _loader(tmp_path / "eve")

    with pytest.raises(ModActivationTransitionError, match="No prepared"):
        mark_mod_activation_pending(mod, False)

    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    with pytest.raises(ModActivationTransitionError, match="does not match"):
        mark_mod_activation_pending(mod, True)

    pending = mark_mod_activation_pending(mod, False, updated_at=UTC_NOW)
    assert pending.phase is ActivationPhase.PENDING_RESTART
    with pytest.raises(ModActivationTransitionError, match="only from the prepared"):
        mark_mod_activation_pending(mod, False)


def test_mark_pending_rejects_contract_drift_after_prepare(tmp_path: Path) -> None:
    mod = _loader(tmp_path / "eve")
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    mod.version = "2.0.0"

    with pytest.raises(ModActivationTransitionError, match="current request and contract"):
        mark_mod_activation_pending(mod, False)


def test_fail_requires_matching_operation_and_only_accepts_safe_bounded_codes(
    tmp_path: Path,
) -> None:
    mod = _loader(tmp_path / "eve")
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)

    with pytest.raises(ModActivationStateError, match="machine token"):
        fail_mod_activation(mod, False, "C:\\private\\server.log blew up")
    with pytest.raises(ModActivationTransitionError, match="does not match"):
        fail_mod_activation(mod, True, "config-write-failed")

    failed = fail_mod_activation(
        mod,
        False,
        "config-write-failed",
        updated_at=UTC_NOW + timedelta(seconds=2),
    )

    assert failed.phase is ActivationPhase.FAILED
    assert failed.error_code == "config-write-failed"
    assert read_mod_activation_state(mod.evejs_root).for_mod(mod.id) == failed


def test_prepared_recovery_projects_pending_only_when_config_write_landed(
    tmp_path: Path,
) -> None:
    mod = _loader(tmp_path / "eve", active=True)
    prepared = prepare_mod_activation(mod, False, updated_at=UTC_NOW)

    incomplete = project_mod_activation(mod, None, prepared)
    mod.active = False
    recovered = project_mod_activation(mod, None, prepared)

    assert incomplete.status is ModActivationStatus.VERIFICATION_FAILED
    assert incomplete.reason_code == "prepared-operation-incomplete"
    assert recovered.status is ModActivationStatus.RESTART_REQUIRED
    assert recovered.reason_code == "prepared-operation-recovered"
    assert recovered.pending is True


def test_pending_record_survives_reload_and_projects_restart_required(
    tmp_path: Path,
) -> None:
    mod = _loader(tmp_path / "eve", active=False)
    prepare_mod_activation(mod, True, updated_at=UTC_NOW)
    mod.active = True
    mark_mod_activation_pending(mod, True, updated_at=UTC_NOW)

    state = read_mod_activation_state(mod.evejs_root)
    projection = reconcile_mod_activation(mod, None, state)

    assert state.for_mod(mod.id).phase is ActivationPhase.PENDING_RESTART
    assert projection.status is ModActivationStatus.RESTART_REQUIRED
    assert projection.intent_phase is ActivationPhase.PENDING_RESTART
    assert projection.effective is None


def test_projection_without_intent_distinguishes_unverified_verified_and_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, active=True)
    running = _snapshot(root, [mod], mod.path.name)
    disabled = _snapshot(root, [mod])

    assert project_mod_activation(mod, None).status is ModActivationStatus.RUNTIME_UNVERIFIED
    verified = project_mod_activation(mod, running)
    mismatch = project_mod_activation(mod, disabled)
    assert verified.status is ModActivationStatus.VERIFIED
    assert verified.effective is True
    assert mismatch.status is ModActivationStatus.RESTART_REQUIRED
    assert mismatch.effective is False


def test_failed_operation_stays_failed_until_exact_runtime_confirmation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, active=False)
    prepare_mod_activation(mod, True, updated_at=UTC_NOW)
    mod.active = True
    failed = fail_mod_activation(mod, True, "runtime-verification-failed")

    failure = project_mod_activation(mod, _snapshot(root, [mod]), failed)
    confirmed = project_mod_activation(
        mod,
        _snapshot(root, [mod], mod.path.name),
        failed,
    )

    assert failure.status is ModActivationStatus.VERIFICATION_FAILED
    assert failure.error_code == "runtime-verification-failed"
    assert confirmed.status is ModActivationStatus.VERIFIED
    assert confirmed.clearable is True


def test_projection_fails_closed_for_intent_runtime_and_root_contract_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, active=True)
    intent = prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    old_snapshot = _snapshot(root, [mod], mod.path.name)
    other_root = tmp_path / "other"
    other = _loader(other_root, mod.id, active=True)
    other_snapshot = _snapshot(other_root, [other], other.path.name)

    mod.version = "changed-contract"

    assert (
        project_mod_activation(mod, old_snapshot, intent).status
        is ModActivationStatus.STALE_CONTRACT
    )
    assert project_mod_activation(mod, old_snapshot).reason_code == "runtime-contract-mismatch"
    assert project_mod_activation(mod, other_snapshot).reason_code == "runtime-root-mismatch"


def test_exact_runtime_confirmation_clears_record_and_toggle_back_is_clearable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, active=True)
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    mod.active = False
    mark_mod_activation_pending(mod, False, updated_at=UTC_NOW)

    # The requested disable has not happened in this runtime yet.
    still_running = _snapshot(root, [mod], mod.path.name)
    assert clear_confirmed_mod_activation(mod, still_running) is False
    assert list_mod_activation_intents(root)

    # The user toggles back to the already-effective state before restarting.
    prepare_mod_activation(mod, True, updated_at=UTC_NOW)
    mod.active = True
    mark_mod_activation_pending(mod, True, updated_at=UTC_NOW)
    assert clear_confirmed_mod_activation(mod, still_running) is True
    assert list_mod_activation_intents(root) == ()


def test_clear_requires_exact_root_contract_config_and_unambiguous_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, active=False)
    prepare_mod_activation(mod, True, updated_at=UTC_NOW)
    mod.active = True
    mark_mod_activation_pending(mod, True, updated_at=UTC_NOW)
    matching = _snapshot(root, [mod], mod.path.name)

    other_root = tmp_path / "other"
    other = _loader(other_root, mod.id)
    with pytest.raises(ModActivationStateError, match="different EveJS root"):
        clear_confirmed_mod_activations(root, _snapshot(other_root, [other]), [mod])

    ambiguous = replace(matching, mods=matching.mods + matching.mods)
    assert clear_confirmed_mod_activation(mod, ambiguous) is False
    mod.active = False
    assert clear_confirmed_mod_activation(mod, matching) is False
    mod.active = True
    mod.version = "drifted"
    assert clear_confirmed_mod_activation(mod, matching) is False
    assert list_mod_activation_intents(root)


def test_bulk_clear_removes_only_individually_confirmed_records(tmp_path: Path) -> None:
    root = tmp_path / "eve"
    first = _loader(root, "first", active=False)
    second = _loader(root, "second", active=False)
    for mod in (first, second):
        prepare_mod_activation(mod, True, updated_at=UTC_NOW)
        mod.active = True
        mark_mod_activation_pending(mod, True, updated_at=UTC_NOW)

    snapshot = _snapshot(root, [first, second], first.path.name)
    cleared = clear_confirmed_mod_activations(root, snapshot, [first, second])

    assert cleared == ("first",)
    assert tuple(item.id for item in list_mod_activation_intents(root)) == ("second",)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda raw: b"\xef\xbb\xbf" + raw, "without a BOM"),
        (
            lambda raw: raw.replace(
                b'"schemaVersion": 1,',
                b'"schemaVersion": 1, "schemaVersion": 1,',
                1,
            ),
            "duplicate JSON key",
        ),
        (
            lambda raw: raw.replace(
                b'"desired": true,',
                b'"desired": 1,',
                1,
            ),
            "must be a boolean",
        ),
        (
            lambda raw: raw.replace(
                b'"phase": "prepared",',
                b'"phase": "magic",',
                1,
            ),
            "unsupported phase",
        ),
        (
            lambda raw: raw.replace(
                b'"errorCode": null,',
                b'"errorCode": "unexpected",',
                1,
            ),
            "may not store an error",
        ),
        (
            lambda raw: raw.replace(
                b'"updatedAt": "2026-08-22T10:11:12.345678Z"',
                b'"updatedAt": "2026-08-22 10:11:12Z"',
                1,
            ),
            "canonical UTC",
        ),
        (
            lambda raw: raw.replace(b'"records": {', b'"unknown": 1, "records": {', 1),
            "fields are not exact",
        ),
    ],
)
def test_reader_rejects_noncanonical_or_nonexact_documents(
    tmp_path: Path,
    mutate,
    match: str,
) -> None:
    mod = _loader(tmp_path / "eve", active=False)
    prepare_mod_activation(mod, True, updated_at=UTC_NOW)
    path = mod_activation_state_path(mod.evejs_root)
    path.write_bytes(mutate(path.read_bytes()))

    with pytest.raises(ModActivationStateReadError, match=match):
        read_mod_activation_state(mod.evejs_root)


def test_corrupt_state_is_never_silently_overwritten_by_prepare(tmp_path: Path) -> None:
    mod = _loader(tmp_path / "eve")
    path = mod_activation_state_path(mod.evejs_root)
    path.parent.mkdir(parents=True)
    corrupted = b'{"schemaVersion":1,"broken":'
    path.write_bytes(corrupted)

    with pytest.raises(ModActivationStateReadError, match="strict UTF-8 JSON"):
        prepare_mod_activation(mod, False)

    assert path.read_bytes() == corrupted


def test_reader_rejects_oversized_state_before_json_parse(tmp_path: Path) -> None:
    root = tmp_path / "eve"
    root.mkdir()
    path = mod_activation_state_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{" + b" " * MAX_ACTIVATION_STATE_BYTES)

    with pytest.raises(ModActivationStateReadError, match="size limit"):
        read_mod_activation_state(root)


def test_reader_normalizes_over_limit_json_integer_to_state_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    root.mkdir()
    path = mod_activation_state_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(
        b'{"schemaVersion":'
        + (b"9" * 5000)
        + b',"root":"fixture","updatedAt":"fixture","records":{}}'
    )

    with pytest.raises(ModActivationStateReadError, match="strict UTF-8 JSON"):
        read_mod_activation_state(root)


def test_reader_normalizes_excessive_json_nesting_to_state_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    root.mkdir()
    path = mod_activation_state_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"nested":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}")

    with pytest.raises(ModActivationStateReadError, match="strict UTF-8 JSON"):
        read_mod_activation_state(root)


def test_reader_rejects_document_bound_to_another_root(tmp_path: Path) -> None:
    root = tmp_path / "eve"
    other_root = tmp_path / "other"
    mod = _loader(root)
    other_root.mkdir()
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    path = mod_activation_state_path(root)
    payload = _payload(path)
    payload["root"] = str(other_root.resolve())
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModActivationStateReadError, match="different EveJS root"):
        read_mod_activation_state(root)


def test_atomic_replace_failure_preserves_the_previous_valid_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _loader(tmp_path / "eve")
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    path = mod_activation_state_path(mod.evejs_root)
    original = path.read_bytes()

    def _fail_replace(_source, _destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("src.core.mod_activation_state.os.replace", _fail_replace)
    with pytest.raises(ModActivationStateWriteError, match="atomically"):
        prepare_mod_activation(mod, True, updated_at=UTC_NOW + timedelta(seconds=1))

    assert path.read_bytes() == original
    assert not tuple(path.parent.glob(f".{path.name}.*.tmp"))


def test_naive_timestamps_and_boolean_subclasses_fail_closed(tmp_path: Path) -> None:
    mod = _loader(tmp_path / "eve")

    with pytest.raises(ModActivationStateError, match="timezone-aware"):
        prepare_mod_activation(mod, False, updated_at=datetime(2026, 8, 22))
    with pytest.raises(ModActivationStateError, match="must be a boolean"):
        prepare_mod_activation(mod, 1)  # type: ignore[arg-type]


def test_retire_removed_activation_deletes_only_the_exact_removed_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    removed = _loader(root, "removed-mod")
    surviving = _loader(root, "surviving-mod")
    prepare_mod_activation(removed, False, updated_at=UTC_NOW)
    prepare_mod_activation(surviving, False, updated_at=UTC_NOW)
    records = {item.id: item for item in list_mod_activation_intents(root)}
    (removed.path / "loader.js").unlink()

    retired = retire_removed_mod_activation(
        root,
        removed.id,
        records[removed.id].contract_sha256,
    )

    assert retired is True
    remaining = list_mod_activation_intents(root)
    assert tuple(item.id for item in remaining) == (surviving.id,)
    assert remaining[0] == records[surviving.id]


def test_retire_removed_activation_refuses_newer_contract_and_preserves_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, "removed-mod")
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    original = list_mod_activation_intents(root)
    (mod.path / "loader.js").unlink()

    with pytest.raises(ModActivationStateWriteError, match="different mod contract"):
        retire_removed_mod_activation(root, mod.id, "f" * 64)

    assert list_mod_activation_intents(root) == original


def test_retire_removed_activation_refuses_while_mod_is_still_discoverable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "eve"
    mod = _loader(root, "still-installed")
    prepare_mod_activation(mod, False, updated_at=UTC_NOW)
    intent = list_mod_activation_intents(root)[0]

    with pytest.raises(ModActivationStateWriteError, match="still installed"):
        retire_removed_mod_activation(root, mod.id, intent.contract_sha256)

    assert list_mod_activation_intents(root) == (intent,)
