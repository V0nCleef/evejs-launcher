"""Versioned character groups for safe one-click multi-launch.

Groups are launcher-owned metadata. They are scoped to the private-safe
runtime target identity already used by the Native/Docker data layer and
refer to exact account/character IDs rather than mutable display names.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping
from uuid import uuid4

from ..config import CONFIG_DIR
from .db import Account, Character


log = logging.getLogger(__name__)

GROUPS_FILE = CONFIG_DIR / "groups.json"
SCHEMA_VERSION = 1
DEFAULT_GROUP_COLOR = "teal"
GROUP_COLORS = ("teal", "gold", "green", "red", "steel")
MAX_GROUP_NAME_LENGTH = 40


class GroupValidationError(ValueError):
    """Raised when an attempted group edit is not safe to persist."""


@dataclass(frozen=True, order=True)
class GroupMember:
    """One exact character membership inside one runtime target."""

    account_id: int
    character_id: int


@dataclass(frozen=True)
class CharacterGroup:
    """A named, ordered character launch preset."""

    group_id: str
    name: str
    color: str = DEFAULT_GROUP_COLOR
    members: tuple[GroupMember, ...] = ()


@dataclass(frozen=True)
class TargetGroupState:
    """All character groups belonging to one runtime target."""

    groups: tuple[CharacterGroup, ...] = ()
    selected_group_id: str | None = None

    @property
    def selected_group(self) -> CharacterGroup | None:
        return next(
            (
                group
                for group in self.groups
                if group.group_id == self.selected_group_id
            ),
            None,
        )


@dataclass(frozen=True)
class GroupResolution:
    """Resolved current rows plus members that cannot be launched safely."""

    rows: tuple[tuple[Account, Character], ...]
    missing: tuple[GroupMember, ...]
    conflicting_account_ids: tuple[int, ...]

    @property
    def valid(self) -> bool:
        return not self.conflicting_account_ids


def normalize_group_name(value: object) -> str:
    """Return a validated display name with surrounding whitespace removed."""
    if not isinstance(value, str):
        raise GroupValidationError("Enter a group name.")
    name = " ".join(value.split())
    if not name:
        raise GroupValidationError("Enter a group name.")
    if len(name) > MAX_GROUP_NAME_LENGTH:
        raise GroupValidationError(
            f"Group names can contain at most {MAX_GROUP_NAME_LENGTH} characters."
        )
    return name


def create_group(
    state: TargetGroupState,
    name: str,
    *,
    color: str = DEFAULT_GROUP_COLOR,
    members: Iterable[GroupMember] = (),
) -> tuple[TargetGroupState, CharacterGroup]:
    """Append a new uniquely named group and select it."""
    normalized_name = normalize_group_name(name)
    _ensure_unique_name(state.groups, normalized_name)
    group = CharacterGroup(
        uuid4().hex,
        normalized_name,
        _normalize_color(color),
        _normalize_members(members),
    )
    return TargetGroupState((*state.groups, group), group.group_id), group


def update_group(
    state: TargetGroupState,
    group: CharacterGroup,
) -> TargetGroupState:
    """Replace one existing group after complete validation."""
    existing = next(
        (candidate for candidate in state.groups if candidate.group_id == group.group_id),
        None,
    )
    if existing is None:
        raise GroupValidationError("The selected group no longer exists.")
    name = normalize_group_name(group.name)
    _ensure_unique_name(state.groups, name, excluding_id=group.group_id)
    normalized = CharacterGroup(
        group.group_id,
        name,
        _normalize_color(group.color),
        _normalize_members(group.members),
    )
    return TargetGroupState(
        tuple(
            normalized if candidate.group_id == group.group_id else candidate
            for candidate in state.groups
        ),
        state.selected_group_id,
    )


def duplicate_group(
    state: TargetGroupState,
    group_id: str,
) -> tuple[TargetGroupState, CharacterGroup]:
    """Copy one group using the first available human-readable name."""
    source = next((group for group in state.groups if group.group_id == group_id), None)
    if source is None:
        raise GroupValidationError("The selected group no longer exists.")
    stem = f"{source.name} Copy"
    name = stem[:MAX_GROUP_NAME_LENGTH]
    suffix = 2
    folded = {group.name.casefold() for group in state.groups}
    while name.casefold() in folded:
        suffix_text = f" {suffix}"
        name = f"{stem[: MAX_GROUP_NAME_LENGTH - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return create_group(
        state,
        name,
        color=source.color,
        members=source.members,
    )


def delete_group(state: TargetGroupState, group_id: str) -> TargetGroupState:
    """Delete launcher metadata for one group without touching game data."""
    remaining = tuple(group for group in state.groups if group.group_id != group_id)
    if len(remaining) == len(state.groups):
        return state
    selected = None if state.selected_group_id == group_id else state.selected_group_id
    return TargetGroupState(remaining, selected)


def select_group(state: TargetGroupState, group_id: str | None) -> TargetGroupState:
    """Select a group, or ``None`` for the built-in All Visible preset."""
    if group_id is not None and not any(
        group.group_id == group_id for group in state.groups
    ):
        group_id = None
    return replace(state, selected_group_id=group_id)


def validate_state(state: TargetGroupState) -> TargetGroupState:
    """Return a completely normalized state or raise a user-facing error."""
    return _validated_state(state)


def resolve_group(
    group: CharacterGroup,
    accounts: Iterable[Account],
) -> GroupResolution:
    """Resolve members against current data without silently choosing conflicts."""
    rows_by_member: dict[GroupMember, tuple[Account, Character]] = {}
    for account in accounts:
        for character in account.characters:
            rows_by_member[GroupMember(account.account_id, character.char_id)] = (
                account,
                character,
            )

    rows: list[tuple[Account, Character]] = []
    missing: list[GroupMember] = []
    account_counts: dict[int, int] = {}
    for member in group.members:
        row = rows_by_member.get(member)
        if row is None:
            missing.append(member)
            continue
        rows.append(row)
        account_counts[member.account_id] = account_counts.get(member.account_id, 0) + 1

    conflicts = tuple(
        sorted(account_id for account_id, count in account_counts.items() if count > 1)
    )
    return GroupResolution(tuple(rows), tuple(missing), conflicts)


def prune_deleted_characters(
    state: TargetGroupState,
    character_ids: Iterable[int],
) -> TargetGroupState:
    """Remove successfully deleted character IDs while retaining empty groups."""
    deleted = {int(character_id) for character_id in character_ids}
    if not deleted:
        return state
    groups = tuple(
        replace(
            group,
            members=tuple(
                member
                for member in group.members
                if member.character_id not in deleted
            ),
        )
        for group in state.groups
    )
    return replace(state, groups=groups)


def load_target_groups(
    target_identity: str,
    *,
    path: Path | None = None,
) -> TargetGroupState:
    """Load and normalize the groups belonging to one runtime target."""
    if not target_identity:
        return TargetGroupState()
    document = _load_document(_groups_path(path))
    targets = document.get("targets")
    if not isinstance(targets, Mapping):
        return TargetGroupState()
    return _parse_target_state(targets.get(target_identity))


def find_relink_candidates(
    target_identity: str,
    accounts: Iterable[Account],
    *,
    path: Path | None = None,
) -> tuple[TargetGroupState, ...]:
    """Find prior target states whose every stored member still matches.

    This supports a moved/upgraded EveJS installation without ever guessing
    from names or exposing the private-safe source hashes in the interface.
    A candidate needs at least one member and every member must resolve to the
    same account/character ID pair in the current data source.
    """
    if not target_identity:
        return ()
    available = {
        GroupMember(account.account_id, character.char_id)
        for account in accounts
        for character in account.characters
    }
    document = _load_document(_groups_path(path))
    targets = document.get("targets")
    if not isinstance(targets, Mapping):
        return ()
    candidates: list[TargetGroupState] = []
    for source_identity, payload in targets.items():
        if source_identity == target_identity:
            continue
        state = _parse_target_state(payload)
        members = {
            member
            for group in state.groups
            for member in group.members
        }
        if members and members.issubset(available):
            candidates.append(state)
    candidates.sort(
        key=lambda state: (
            -sum(len(group.members) for group in state.groups),
            -len(state.groups),
            tuple(group.name.casefold() for group in state.groups),
        )
    )
    return tuple(candidates)


def save_target_groups(
    target_identity: str,
    state: TargetGroupState,
    *,
    path: Path | None = None,
) -> None:
    """Atomically persist one runtime target without overwriting other targets."""
    if not isinstance(target_identity, str) or not target_identity:
        raise GroupValidationError("Character groups need a current EveJS data source.")
    validated = _validated_state(state)
    groups_path = _groups_path(path)
    document = _load_document(groups_path)
    targets = document.get("targets")
    targets = dict(targets) if isinstance(targets, Mapping) else {}
    targets[target_identity] = _state_payload(validated)
    output: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "targets": targets,
    }
    legacy = document.get("legacy_account_groups")
    if isinstance(legacy, Mapping) and legacy:
        output["legacy_account_groups"] = dict(legacy)
    _atomic_write(groups_path, output)


def _groups_path(path: Path | None) -> Path:
    return GROUPS_FILE if path is None else Path(path)


def _ensure_unique_name(
    groups: Iterable[CharacterGroup],
    name: str,
    *,
    excluding_id: str | None = None,
) -> None:
    folded = name.casefold()
    if any(
        group.group_id != excluding_id and group.name.casefold() == folded
        for group in groups
    ):
        raise GroupValidationError(f"A group named '{name}' already exists.")


def _normalize_color(value: object) -> str:
    return value if value in GROUP_COLORS else DEFAULT_GROUP_COLOR


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalize_members(members: Iterable[GroupMember]) -> tuple[GroupMember, ...]:
    normalized: list[GroupMember] = []
    seen: set[GroupMember] = set()
    for value in members:
        if not isinstance(value, GroupMember):
            raise GroupValidationError("A character group contains an invalid member.")
        account_id = _positive_int(value.account_id)
        character_id = _positive_int(value.character_id)
        if account_id is None or character_id is None:
            raise GroupValidationError("Character and account IDs must be positive.")
        member = GroupMember(account_id, character_id)
        if member not in seen:
            normalized.append(member)
            seen.add(member)
    return tuple(normalized)


def _validated_state(state: TargetGroupState) -> TargetGroupState:
    if not isinstance(state, TargetGroupState):
        raise GroupValidationError("Character groups are invalid.")
    groups: list[CharacterGroup] = []
    names: set[str] = set()
    ids: set[str] = set()
    for group in state.groups:
        if not isinstance(group, CharacterGroup):
            raise GroupValidationError("Character groups are invalid.")
        group_id = group.group_id.strip() if isinstance(group.group_id, str) else ""
        if not group_id or len(group_id) > 64 or group_id in ids:
            raise GroupValidationError("Every character group needs a unique ID.")
        name = normalize_group_name(group.name)
        if name.casefold() in names:
            raise GroupValidationError(f"A group named '{name}' already exists.")
        ids.add(group_id)
        names.add(name.casefold())
        groups.append(
            CharacterGroup(
                group_id,
                name,
                _normalize_color(group.color),
                _normalize_members(group.members),
            )
        )
    selected = state.selected_group_id if state.selected_group_id in ids else None
    return TargetGroupState(tuple(groups), selected)


def _parse_target_state(payload: object) -> TargetGroupState:
    if not isinstance(payload, Mapping):
        return TargetGroupState()
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raw_groups = []
    groups: list[CharacterGroup] = []
    ids: set[str] = set()
    names: set[str] = set()
    for raw_group in raw_groups:
        group = _parse_group(raw_group)
        if group is None or group.group_id in ids or group.name.casefold() in names:
            continue
        groups.append(group)
        ids.add(group.group_id)
        names.add(group.name.casefold())
    selected = payload.get("selected_group_id")
    if not isinstance(selected, str) or selected not in ids:
        selected = None
    return TargetGroupState(tuple(groups), selected)


def _parse_group(payload: object) -> CharacterGroup | None:
    if not isinstance(payload, Mapping):
        return None
    group_id = payload.get("id")
    if not isinstance(group_id, str) or not group_id.strip() or len(group_id) > 64:
        return None
    try:
        name = normalize_group_name(payload.get("name"))
    except GroupValidationError:
        return None
    raw_members = payload.get("members")
    members: list[GroupMember] = []
    seen: set[GroupMember] = set()
    if isinstance(raw_members, list):
        for raw_member in raw_members:
            if not isinstance(raw_member, Mapping):
                continue
            account_id = _positive_int(raw_member.get("account_id"))
            character_id = _positive_int(raw_member.get("character_id"))
            if account_id is None or character_id is None:
                continue
            member = GroupMember(account_id, character_id)
            if member not in seen:
                members.append(member)
                seen.add(member)
    return CharacterGroup(
        group_id.strip(),
        name,
        _normalize_color(payload.get("color")),
        tuple(members),
    )


def _state_payload(state: TargetGroupState) -> dict[str, object]:
    return {
        "selected_group_id": state.selected_group_id,
        "groups": [
            {
                "id": group.group_id,
                "name": group.name,
                "color": group.color,
                "members": [
                    {
                        "account_id": member.account_id,
                        "character_id": member.character_id,
                    }
                    for member in group.members
                ],
            }
            for group in state.groups
        ],
    }


def _empty_document() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "targets": {}}


def _load_document(path: Path) -> dict[str, object]:
    if not path.exists():
        return _empty_document()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # A transient read/permission failure is not malformed user data and
        # must never trigger a move or a later overwrite of other targets.
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _backup_broken_file(path, exc)
        return _empty_document()
    if not isinstance(payload, dict):
        _backup_broken_file(path, ValueError("group store root must be an object"))
        return _empty_document()
    if payload.get("schema_version") == SCHEMA_VERSION:
        return payload
    if "schema_version" in payload:
        raise GroupValidationError(
            "Character groups use an unsupported newer storage version."
        )

    # The original unused prototype stored {group_name: [username, ...]}.
    # Preserve it verbatim. Choosing an exact character for a multi-character
    # account must never happen silently.
    legacy = {
        str(name): [str(username) for username in usernames if isinstance(username, str)]
        for name, usernames in payload.items()
        if isinstance(name, str) and isinstance(usernames, list)
    }
    document = _empty_document()
    if legacy:
        document["legacy_account_groups"] = legacy
    return document


def _backup_broken_file(path: Path, error: BaseException) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.{stamp}.broken")
    try:
        os.replace(path, backup)
        log.warning("Invalid character groups moved to %s: %s", backup, error)
    except OSError:
        log.exception("Invalid character groups could not be backed up: %s", path)


def _atomic_write(path: Path, payload: Mapping[str, object]) -> None:
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
            json.dump(payload, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
