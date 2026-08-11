"""Character-group persistence, validation, and resolution tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.db import Account, Character
from src.core.groups import (
    CharacterGroup,
    GroupMember,
    GroupValidationError,
    TargetGroupState,
    create_group,
    delete_group,
    duplicate_group,
    find_relink_candidates,
    load_target_groups,
    prune_deleted_characters,
    resolve_group,
    save_target_groups,
    select_group,
    update_group,
)


def _account(
    username: str,
    account_id: int,
    *characters: tuple[int, str],
) -> Account:
    return Account(
        username=username,
        account_id=account_id,
        role="0",
        banned=False,
        characters=[Character(char_id, name) for char_id, name in characters],
    )


def test_versioned_round_trip_preserves_target_isolation(tmp_path: Path) -> None:
    path = tmp_path / "groups.json"
    first = TargetGroupState(
        (
            CharacterGroup(
                "miners-id",
                "Miners",
                "gold",
                (GroupMember(7, 140000007), GroupMember(8, 140000008)),
            ),
        ),
        "miners-id",
    )
    second = TargetGroupState(
        (CharacterGroup("scouts-id", "Scouts"),),
        None,
    )

    save_target_groups("native:first", first, path=path)
    save_target_groups("docker:second", second, path=path)

    assert load_target_groups("native:first", path=path) == first
    assert load_target_groups("docker:second", path=path) == second
    assert load_target_groups("native:missing", path=path) == TargetGroupState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert set(payload["targets"]) == {"native:first", "docker:second"}
    assert not list(tmp_path.glob("*.tmp"))


def test_create_update_duplicate_delete_and_casefold_validation() -> None:
    state, miners = create_group(TargetGroupState(), "  Mining   Fleet  ")
    assert miners.name == "Mining Fleet"
    assert state.selected_group_id == miners.group_id

    with pytest.raises(GroupValidationError, match="already exists"):
        create_group(state, "mining fleet")

    updated = update_group(
        state,
        CharacterGroup(
            miners.group_id,
            "Miners",
            "green",
            (GroupMember(1, 101), GroupMember(1, 101)),
        ),
    )
    assert updated.groups[0].members == (GroupMember(1, 101),)

    duplicated, copy = duplicate_group(updated, miners.group_id)
    assert copy.name == "Miners Copy"
    assert copy.members == updated.groups[0].members
    assert duplicated.selected_group_id == copy.group_id

    selected = select_group(duplicated, miners.group_id)
    deleted = delete_group(selected, miners.group_id)
    assert deleted.selected_group_id is None
    assert deleted.groups == (copy,)


def test_resolution_uses_exact_ids_and_reports_account_conflicts() -> None:
    accounts = [
        _account("account-a", 1, (101, "First"), (102, "Second")),
        _account("account-b", 2, (201, "Third")),
    ]
    group = CharacterGroup(
        "group-id",
        "Fleet",
        members=(
            GroupMember(1, 102),
            GroupMember(2, 201),
            GroupMember(1, 101),
            GroupMember(9, 999),
        ),
    )

    resolution = resolve_group(group, accounts)

    assert [(account.username, character.name) for account, character in resolution.rows] == [
        ("account-a", "Second"),
        ("account-b", "Third"),
        ("account-a", "First"),
    ]
    assert resolution.missing == (GroupMember(9, 999),)
    assert resolution.conflicting_account_ids == (1,)
    assert resolution.valid is False


def test_pruning_deleted_characters_retains_empty_groups() -> None:
    state = TargetGroupState(
        (
            CharacterGroup(
                "miners-id",
                "Miners",
                members=(GroupMember(1, 101),),
            ),
            CharacterGroup(
                "fleet-id",
                "Fleet",
                members=(GroupMember(1, 101), GroupMember(2, 201)),
            ),
        ),
        "miners-id",
    )

    pruned = prune_deleted_characters(state, [101])

    assert pruned.groups[0].members == ()
    assert pruned.groups[1].members == (GroupMember(2, 201),)
    assert pruned.selected_group_id == "miners-id"


def test_malformed_store_is_backed_up_before_defaults_are_returned(
    tmp_path: Path,
) -> None:
    path = tmp_path / "groups.json"
    path.write_text('{"schema_version": ', encoding="utf-8")

    assert load_target_groups("native:test", path=path) == TargetGroupState()

    backups = list(tmp_path.glob("groups.json.*.broken"))
    assert not path.exists()
    assert len(backups) == 1


def test_legacy_account_groups_are_preserved_during_first_versioned_save(
    tmp_path: Path,
) -> None:
    path = tmp_path / "groups.json"
    path.write_text(
        json.dumps({"Old Fleet": ["fixture-account"]}),
        encoding="utf-8",
    )

    save_target_groups(
        "native:test",
        TargetGroupState((CharacterGroup("new-id", "New Fleet"),)),
        path=path,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["legacy_account_groups"] == {
        "Old Fleet": ["fixture-account"]
    }
    assert load_target_groups("native:test", path=path).groups[0].name == "New Fleet"


def test_relink_candidates_require_every_exact_member_to_match(
    tmp_path: Path,
) -> None:
    path = tmp_path / "groups.json"
    portable = TargetGroupState(
        (
            CharacterGroup(
                "miners",
                "Miners",
                members=(GroupMember(1, 101), GroupMember(2, 201)),
            ),
        ),
        "miners",
    )
    stale = TargetGroupState(
        (
            CharacterGroup(
                "stale",
                "Stale",
                members=(GroupMember(1, 101), GroupMember(9, 999)),
            ),
        )
    )
    save_target_groups("native:old-portable", portable, path=path)
    save_target_groups("native:old-stale", stale, path=path)
    accounts = [
        _account("account-a", 1, (101, "First")),
        _account("account-b", 2, (201, "Second")),
    ]

    assert find_relink_candidates("native:new", accounts, path=path) == (portable,)
