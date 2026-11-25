"""Persistent launcher state for overview capture and one-shot imports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from ..config import CONFIG_DIR


STATE_FILE = CONFIG_DIR / "overview_bridge.json"
ACK_DIR = CONFIG_DIR / "overview_bridge" / "acks"


class OverviewSnapshotRequired(RuntimeError):
    """Raised when a target import references a source not captured yet."""

    def __init__(self, source_character_id: int) -> None:
        super().__init__("The selected source overview has not been captured yet.")
        self.source_character_id = source_character_id


class OverviewSeparateSettingsRequired(RuntimeError):
    """Raised when an existing-character copy would apply in Shared mode."""


@dataclass(frozen=True)
class OverviewBridgeLaunch:
    command: str
    ack_path: Path


@dataclass(frozen=True)
class OverviewBridgeEvent:
    kind: str
    character_id: int
    message: str = ""


def _empty_state() -> dict:
    return {"version": 1, "snapshots": {}, "pendingImports": {}}


def load_overview_state(path: Path = STATE_FILE) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(value, dict):
        return _empty_state()
    snapshots = value.get("snapshots")
    pending = value.get("pendingImports")
    return {
        "version": 1,
        "snapshots": snapshots if isinstance(snapshots, dict) else {},
        "pendingImports": pending if isinstance(pending, dict) else {},
    }


def save_overview_state(state: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(state, temporary, indent=2, sort_keys=True)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def snapshot_for_character(character_id: int, path: Path = STATE_FILE) -> dict | None:
    return _validated_snapshot(load_overview_state(path)["snapshots"].get(str(character_id)))


def _validated_snapshot(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    hashvalue = value.get("hashvalue")
    sq_id = value.get("sqID")
    if (
        not isinstance(hashvalue, str)
        or len(hashvalue) != 40
        or isinstance(sq_id, bool)
        or not isinstance(sq_id, int)
        or sq_id <= 0
    ):
        return None
    return value


def add_pending_overview_import(
    target_character_id: int,
    source_character_id: int,
    path: Path = STATE_FILE,
) -> None:
    if target_character_id <= 0 or source_character_id <= 0:
        raise ValueError("Overview import character IDs must be positive.")
    state = load_overview_state(path)
    state["pendingImports"][str(target_character_id)] = {
        "sourceCharacterID": source_character_id,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    save_overview_state(state, path)


def queue_overview_copies(source_character_id, target_character_ids, path: Path = STATE_FILE) -> None:
    """Pin the current source snapshot for a one-time import on each target."""
    targets = tuple(dict.fromkeys(target_character_ids))
    if (type(source_character_id) is not int or source_character_id <= 0 or not targets
            or any(type(target) is not int or target <= 0 or target == source_character_id for target in targets)):
        raise ValueError("Choose a source and at least one different character.")
    state = load_overview_state(path)
    snapshot = _validated_snapshot(state["snapshots"].get(str(source_character_id)))
    if snapshot is None:
        raise OverviewSnapshotRequired(source_character_id)
    pending = state["pendingImports"]
    if any(str(target) in pending for target in targets):
        raise ValueError("One of the selected characters already has an overview copy queued.")
    now = datetime.now(timezone.utc).isoformat()
    for target in targets:
        pending[str(target)] = {
            "sourceCharacterID": source_character_id,
            "createdAt": now,
            "ackID": uuid.uuid4().hex,
            "snapshot": {"hashvalue": snapshot["hashvalue"], "sqID": snapshot["sqID"]},
        }
    save_overview_state(state, path)


def pending_overview_source(
    target_character_id: int,
    path: Path = STATE_FILE,
) -> int | None:
    pending = load_overview_state(path)["pendingImports"].get(
        str(target_character_id)
    )
    if not isinstance(pending, dict):
        return None
    source_id = pending.get("sourceCharacterID")
    if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
        return None
    return source_id


def remove_characters_from_overview_state(
    character_ids: list[int] | tuple[int, ...] | set[int],
    path: Path = STATE_FILE,
) -> None:
    """Remove snapshots and pending imports owned by deleted characters.

    Pending imports that use a deleted character as their source are removed as
    well, so a later launch cannot retain an impossible overview dependency.
    """
    normalized = {
        character_id
        for character_id in character_ids
        if isinstance(character_id, int)
        and not isinstance(character_id, bool)
        and character_id > 0
    }
    if not normalized:
        return
    state = load_overview_state(path)
    for character_id in normalized:
        state["snapshots"].pop(str(character_id), None)
        state["pendingImports"].pop(str(character_id), None)
    for target_id, pending in list(state["pendingImports"].items()):
        if (
            isinstance(pending, dict)
            and pending.get("sourceCharacterID") in normalized
        ):
            state["pendingImports"].pop(target_id, None)
    save_overview_state(state, path)


def prepare_overview_launch(
    character_id: int,
    *,
    state_path: Path = STATE_FILE,
    ack_dir: Path = ACK_DIR,
    separate_settings: bool = True,
) -> OverviewBridgeLaunch:
    """Build a capture command, or an apply command for a pending target."""
    if isinstance(character_id, bool) or not isinstance(character_id, int) or character_id <= 0:
        raise ValueError("A positive character ID is required for the overview bridge.")
    state = load_overview_state(state_path)
    pending = state["pendingImports"].get(str(character_id))
    ack_id = _pinned_ack_id(pending)
    if ack_id is not None and (ack_dir / f"{ack_id}.ack").is_file():
        # A launch can follow a successful import before the periodic poll runs.
        process_overview_ack_files(state_path=state_path, ack_dir=ack_dir)
        state = load_overview_state(state_path)
        pending = state["pendingImports"].get(str(character_id))
        ack_id = _pinned_ack_id(pending)
    if isinstance(pending, dict):
        if "snapshot" in pending and not separate_settings:
            raise OverviewSeparateSettingsRequired
        source_id = pending.get("sourceCharacterID")
        if (
            isinstance(source_id, bool)
            or not isinstance(source_id, int)
            or source_id <= 0
        ):
            raise OverviewSnapshotRequired(0)
        snapshot = _validated_snapshot(pending.get("snapshot", state["snapshots"].get(str(source_id))))
        if snapshot is None or ("snapshot" in pending and ack_id is None):
            raise OverviewSnapshotRequired(source_id)
        command = f"apply|{character_id}|{snapshot['hashvalue']}|{snapshot['sqID']}"
    else:
        command = f"capture|{character_id}"

    ack_dir.mkdir(parents=True, exist_ok=True)
    ack_path = ack_dir / f"{ack_id or uuid.uuid4().hex}.ack"
    return OverviewBridgeLaunch(command=command, ack_path=ack_path)


def _pinned_ack_id(pending: object) -> str | None:
    value = pending.get("ackID") if isinstance(pending, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else None


def process_overview_ack_files(
    *,
    state_path: Path = STATE_FILE,
    ack_dir: Path = ACK_DIR,
) -> list[OverviewBridgeEvent]:
    """Consume completed bridge acknowledgements and update durable state."""
    if not ack_dir.is_dir():
        return []
    state = load_overview_state(state_path)
    changed = False
    events: list[OverviewBridgeEvent] = []
    processed: list[Path] = []
    for ack_path in sorted(ack_dir.glob("*.ack")):
        try:
            text = ack_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        parts = text.split("|")
        try:
            kind = parts[0]
            character_id = int(parts[1])
            if character_id <= 0:
                raise ValueError
            if kind == "capture" and len(parts) == 4:
                hashvalue = parts[2]
                sq_id = int(parts[3])
                if len(hashvalue) != 40 or sq_id <= 0:
                    raise ValueError
                state["snapshots"][str(character_id)] = {
                    "hashvalue": hashvalue,
                    "sqID": sq_id,
                    "capturedAt": datetime.now(timezone.utc).isoformat(),
                }
                changed = True
                events.append(OverviewBridgeEvent("capture", character_id))
            elif kind == "apply" and len(parts) == 4:
                queued = state["pendingImports"].get(str(character_id), {})
                pinned = queued.get("snapshot") if isinstance(queued, dict) else None
                if isinstance(queued, dict) and "snapshot" in queued:
                    if (_pinned_ack_id(queued) != ack_path.stem
                            or not isinstance(pinned, dict)
                            or parts[2] != pinned.get("hashvalue")
                            or int(parts[3]) != pinned.get("sqID")):
                        raise ValueError("The acknowledgement does not match the queued overview")
                state["pendingImports"].pop(str(character_id), None)
                changed = True
                events.append(OverviewBridgeEvent("apply", character_id))
            elif kind == "error" and len(parts) >= 3:
                events.append(
                    OverviewBridgeEvent("error", character_id, " ".join(parts[2:]))
                )
            else:
                raise ValueError
        except (ValueError, IndexError):
            events.append(OverviewBridgeEvent("invalid", 0, ack_path.name))
        processed.append(ack_path)
    if changed:
        save_overview_state(state, state_path)
    for ack_path in processed:
        try:
            ack_path.unlink()
        except OSError:
            pass
    return events
