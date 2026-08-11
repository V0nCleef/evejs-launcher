"""Overview snapshot and pending-import state-machine tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.overview_state import (
    OverviewSnapshotRequired,
    add_pending_overview_import,
    pending_overview_source,
    prepare_overview_launch,
    process_overview_ack_files,
    remove_characters_from_overview_state,
    snapshot_for_character,
)


def test_capture_then_apply_ack_completes_pending_import(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    ack_dir = tmp_path / "acks"
    source_id = 140000101
    target_id = 140000102
    hashvalue = "a" * 40

    add_pending_overview_import(target_id, source_id, state_path)
    assert pending_overview_source(target_id, state_path) == source_id
    with pytest.raises(OverviewSnapshotRequired) as missing:
        prepare_overview_launch(
            target_id,
            state_path=state_path,
            ack_dir=ack_dir,
        )
    assert missing.value.source_character_id == source_id

    capture = prepare_overview_launch(
        source_id,
        state_path=state_path,
        ack_dir=ack_dir,
    )
    assert capture.command == f"capture|{source_id}"
    capture.ack_path.write_text(
        f"capture|{source_id}|{hashvalue}|9",
        encoding="utf-8",
    )
    events = process_overview_ack_files(state_path=state_path, ack_dir=ack_dir)
    assert [event.kind for event in events] == ["capture"]
    assert snapshot_for_character(source_id, state_path)["sqID"] == 9

    apply = prepare_overview_launch(
        target_id,
        state_path=state_path,
        ack_dir=ack_dir,
    )
    assert apply.command == f"apply|{target_id}|{hashvalue}|9"
    apply.ack_path.write_text(
        f"apply|{target_id}|{hashvalue}|9",
        encoding="utf-8",
    )
    events = process_overview_ack_files(state_path=state_path, ack_dir=ack_dir)
    assert [event.kind for event in events] == ["apply"]
    assert pending_overview_source(target_id, state_path) is None


def test_error_ack_is_consumed_without_clearing_pending_import(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    ack_dir = tmp_path / "acks"
    add_pending_overview_import(12, 11, state_path)
    ack_dir.mkdir()
    (ack_dir / "failure.ack").write_text(
        "error|12|remote preset unavailable",
        encoding="utf-8",
    )

    events = process_overview_ack_files(state_path=state_path, ack_dir=ack_dir)

    assert events[0].kind == "error"
    assert pending_overview_source(12, state_path) == 11
    assert not (ack_dir / "failure.ack").exists()


def test_deleted_character_prunes_snapshot_target_and_source_dependencies(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.json"
    ack_dir = tmp_path / "acks"
    source_id = 101
    target_id = 102
    other_target_id = 103
    capture = prepare_overview_launch(
        source_id,
        state_path=state_path,
        ack_dir=ack_dir,
    )
    capture.ack_path.write_text(
        f"capture|{source_id}|{'b' * 40}|17",
        encoding="utf-8",
    )
    process_overview_ack_files(state_path=state_path, ack_dir=ack_dir)
    add_pending_overview_import(target_id, source_id, state_path)
    add_pending_overview_import(other_target_id, source_id, state_path)

    remove_characters_from_overview_state({source_id, target_id}, state_path)

    assert snapshot_for_character(source_id, state_path) is None
    assert pending_overview_source(target_id, state_path) is None
    assert pending_overview_source(other_target_id, state_path) is None
