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


def test_copy_existing_overviews_pins_snapshot_and_consumes_matching_ack(tmp_path):
    from src.core import overview_state as overview

    path, acks = tmp_path / "overview.json", tmp_path / "acks"
    state = {"version": 1, "snapshots": {"10": {"hashvalue": "a" * 40, "sqID": 4}}, "pendingImports": {}}
    overview.save_overview_state(state, path)
    overview.queue_overview_copies(10, (20, 30), path)
    state = overview.load_overview_state(path)
    state["snapshots"]["10"] = {"hashvalue": "b" * 40, "sqID": 5}
    overview.save_overview_state(state, path)
    first = overview.prepare_overview_launch(20, state_path=path, ack_dir=acks)
    other = overview.prepare_overview_launch(30, state_path=path, ack_dir=acks)
    assert first.command == "apply|20|" + "a" * 40 + "|4"
    first.ack_path.write_text("apply|20|" + "b" * 40 + "|5", encoding="utf-8")
    assert overview.process_overview_ack_files(state_path=path, ack_dir=acks)[0].kind == "invalid"
    assert overview.prepare_overview_launch(20, state_path=path, ack_dir=acks).command == first.command
    first.ack_path.write_text(first.command, encoding="utf-8")
    assert overview.process_overview_ack_files(state_path=path, ack_dir=acks)[0].kind == "apply"
    assert overview.prepare_overview_launch(20, state_path=path, ack_dir=acks).command == "capture|20"
    assert overview.prepare_overview_launch(30, state_path=path, ack_dir=acks).command == other.command


def test_overview_copy_ignores_stale_ack_and_consumes_success_before_relaunch(tmp_path):
    from src.core import overview_state as overview

    path, acks = tmp_path / "overview.json", tmp_path / "acks"
    overview.save_overview_state(
        {"version": 1, "snapshots": {"10": {"hashvalue": "a" * 40, "sqID": 4}}, "pendingImports": {}},
        path,
    )
    overview.queue_overview_copies(10, (20,), path)
    launch = overview.prepare_overview_launch(20, state_path=path, ack_dir=acks)
    stale = acks / "other-launch.ack"
    stale.write_text(launch.command, encoding="utf-8")
    assert overview.process_overview_ack_files(state_path=path, ack_dir=acks)[0].kind == "invalid"
    with pytest.raises(overview.OverviewSeparateSettingsRequired):
        overview.prepare_overview_launch(20, state_path=path, ack_dir=acks, separate_settings=False)
    assert overview.prepare_overview_launch(20, state_path=path, ack_dir=acks) == launch
    launch.ack_path.write_text(launch.command, encoding="utf-8")
    next_launch = overview.prepare_overview_launch(20, state_path=path, ack_dir=acks)
    assert next_launch.command == "capture|20"
    assert next_launch.ack_path != launch.ack_path


def test_overview_dialog_selects_multiple_targets_without_source(qapp):
    from PyQt6.QtCore import Qt
    from src.ui.overview_copy import OverviewCopyDialog

    dialog = OverviewCopyDialog(((10, "Source"), (20, "Target A"), (30, "Target B")), {10})
    assert dialog.source.count() == 1 and dialog.recipients.count() == 2
    assert not dialog._confirm.isEnabled()
    for index in range(dialog.recipients.count()):
        dialog.recipients.item(index).setCheckState(Qt.CheckState.Checked)
    assert dialog.selection() == (10, (20, 30))
    assert dialog._confirm.isEnabled()
    dialog.deleteLater()
