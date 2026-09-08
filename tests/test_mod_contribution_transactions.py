"""Ownership composition and recovery against disposable configuration files."""
from pathlib import Path
import json
import os

import pytest

from src.core.mod_contributions import (
    ContributionConflict, ContributionError, ContributionOwner, ContributionStore,
    FileTarget, KeyEdit, UnknownOwnershipError, read_target,
)
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock


@pytest.fixture
def setup_store(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    path = root / "settings.json"
    path.write_bytes(b'{ "a": 0, "b": 0, "manual": 10 }\n')
    store = ContributionStore(root)
    target = FileTarget.capture(path, root, "json")
    owner_a = ContributionOwner(root, "mods/a")
    owner_b = ContributionOwner(root, "mods/b")
    return store, target, owner_a, owner_b


def apply(store, owner, target, key, value, **kwargs):
    return store.commit(store.plan_edits(owner, [KeyEdit(target, key, value, **kwargs)]))


def read(target):
    return json.loads(target.path.read_bytes())


def test_distinct_keys_compose_and_removal_preserves_other_mod_and_manual_edits(setup_store):
    store, target, a, b = setup_store
    apply(store, a, target, ("a",), 1)
    apply(store, b, target, ("b",), 2)
    target.path.write_bytes(target.path.read_bytes().replace(b'"manual": 10', b'"manual": 99'))
    store.commit(store.plan_remove(a))
    assert read(target) == {"a": 0, "b": 2, "manual": 99}
    store.commit(store.plan_remove(b))
    assert read(target) == {"a": 0, "b": 0, "manual": 99}
    before = target.path.read_bytes()
    assert store.commit(store.plan_remove(a)).is_noop
    assert target.path.read_bytes() == before


@pytest.mark.parametrize("remove_first", ["a", "b"])
def test_same_key_requires_precedence_and_removes_only_the_selected_layer(setup_store, remove_first):
    store, target, a, b = setup_store
    apply(store, a, target, ("a",), 1)
    with pytest.raises(ContributionConflict, match="precedence"):
        store.plan_edits(b, [KeyEdit(target, ("a",), 2)])
    assert read(target)["a"] == 1
    apply(store, b, target, ("a",), 2, allow_override=True)
    first, second = (a, b) if remove_first == "a" else (b, a)
    store.commit(store.plan_remove(first))
    assert read(target)["a"] == (2 if first == a else 1)
    store.commit(store.plan_remove(second))
    assert read(target)["a"] == 0


def test_identical_contributions_share_a_value_without_rewriting_it(setup_store):
    store, target, a, b = setup_store
    apply(store, a, target, ("a",), 1)
    before = target.path.read_bytes()
    assert apply(store, b, target, ("a",), 1).changed_paths == ()
    store.commit(store.plan_remove(a))
    assert target.path.read_bytes() == before
    store.commit(store.plan_remove(b))
    assert read(target)["a"] == 0


def test_noop_save_changes_neither_document_registry_nor_journal(setup_store):
    store, target, a, _b = setup_store
    apply(store, a, target, ("a",), 1)
    before = target.path.read_bytes(), store.index_path.read_bytes(), target.path.stat().st_mtime_ns
    transactions = list(store.transactions.iterdir())
    plan = store.plan_edits(a, [KeyEdit(target, ("a",), 1)])
    assert plan.is_noop
    assert store.commit(plan).is_noop
    assert (target.path.read_bytes(), store.index_path.read_bytes(), target.path.stat().st_mtime_ns) == before
    assert list(store.transactions.iterdir()) == transactions


def test_manual_same_key_edits_are_preserved_and_explicit_override_restores_them(setup_store):
    store, target, a, _b = setup_store
    apply(store, a, target, ("a",), 1)
    target.path.write_bytes(target.path.read_bytes().replace(b'"a": 1', b'"a": 5'))
    with pytest.raises(ContributionConflict, match="unrecorded"):
        store.plan_remove(a)
    with pytest.raises(ContributionConflict):
        store.plan_edits(a, [KeyEdit(target, ("a",), 2)])
    assert read(target)["a"] == 5
    apply(store, a, target, ("a",), 2, allow_override=True)
    store.commit(store.plan_remove(a))
    assert read(target)["a"] == 5


def test_removing_an_ineffective_layer_does_not_overwrite_a_manual_edit(setup_store):
    store, target, a, b = setup_store
    apply(store, a, target, ("a",), 1)
    apply(store, b, target, ("a",), 2, allow_override=True)
    target.path.write_bytes(target.path.read_bytes().replace(b'"a": 2', b'"a": 5'))
    store.commit(store.plan_remove(a))
    assert read(target)["a"] == 5


def test_whole_expected_before_check_rejects_a_stale_form_without_any_write(setup_store):
    store, target, a, _b = setup_store
    plan = store.plan_edits(a, [KeyEdit(target, ("a",), 1)])
    target.path.write_bytes(target.path.read_bytes().replace(b'"manual": 10', b'"manual": 11'))
    current = target.path.read_bytes()
    with pytest.raises(ContributionConflict, match="after planning"):
        store.commit(plan)
    assert target.path.read_bytes() == current
    assert not store.index_path.exists()


def test_ownership_changes_also_invalidate_a_prepared_plan(setup_store):
    store, target, a, b = setup_store
    plan = store.plan_edits(a, [KeyEdit(target, ("a",), 1)])
    apply(store, b, target, ("b",), 2)
    with pytest.raises(ContributionConflict, match="ownership changed"):
        store.commit(plan)
    assert read(target)["a"] == 0


def two_file_plan(setup_store):
    store, first, a, b = setup_store
    path = store.root / "second.json"
    path.write_bytes(b'{"enabled": false}\n')
    second = FileTarget.capture(path, store.root, "json")
    plan = store.plan_edits(a, [KeyEdit(first, ("a",), 1), KeyEdit(second, ("enabled",), True)])
    return store, first, second, a, b, plan


def test_mid_commit_failure_rolls_back_exact_preoperation_bytes(setup_store, monkeypatch):
    store, first, second, _a, _b, plan = two_file_plan(setup_store)
    before = first.path.read_bytes(), second.path.read_bytes()
    real_replace = store._replace_file

    def fail_second(path, content):
        if path == second.path and b"true" in content:
            raise PermissionError("injected second-file failure")
        real_replace(path, content)

    monkeypatch.setattr(store, "_replace_file", fail_second)
    with pytest.raises(PermissionError):
        store.commit(plan)
    assert (first.path.read_bytes(), second.path.read_bytes()) == before
    assert not store.index_path.exists()
    assert store.recover().unresolved == ()


@pytest.mark.parametrize("crash_after_ledger", [False, True])
def test_interrupted_commit_recovers_after_a_fresh_store_is_created(setup_store, monkeypatch, crash_after_ledger):
    store, first, second, _a, _b, plan = two_file_plan(setup_store)
    before = first.path.read_bytes(), second.path.read_bytes()
    real_replace = store._replace_file

    def crash(path, content):
        real_replace(path, content)
        if path == (store.index_path if crash_after_ledger else first.path):
            raise SystemExit("simulated process loss")

    monkeypatch.setattr(store, "_replace_file", crash)
    with pytest.raises(SystemExit):
        store.commit(plan)
    recovered = ContributionStore(store.root).recover()
    assert len(recovered.recovered) == 1
    assert recovered.unresolved == ()
    assert (first.path.read_bytes(), second.path.read_bytes()) == before
    assert not store.index_path.exists()


def test_recovery_preserves_intervening_edits_and_does_not_block_unrelated_files(setup_store, monkeypatch):
    store, first, second, _a, b, plan = two_file_plan(setup_store)
    real_replace = store._replace_file

    def crash(path, content):
        real_replace(path, content)
        if path == first.path:
            raise SystemExit

    monkeypatch.setattr(store, "_replace_file", crash)
    with pytest.raises(SystemExit):
        store.commit(plan)
    first.path.write_bytes(b'{"a": 50, "manual": "keep"}\n')
    fresh = ContributionStore(store.root)
    assert fresh.recover().unresolved == (first.path,)
    apply(fresh, b, second, ("enabled",), True)
    # Resolve only the interrupted first file; a later unrelated commit survives.
    first.path.write_bytes(plan.files[0].before)
    assert fresh.recover().unresolved == ()
    assert read(second)["enabled"] is True
    fresh.commit(fresh.plan_remove(b))
    assert read(second)["enabled"] is False


def test_unknown_historical_ownership_does_not_authorize_removal(setup_store):
    store, target, a, _b = setup_store
    before = target.path.read_bytes()
    with pytest.raises(UnknownOwnershipError):
        store.plan_remove(a)
    assert target.path.read_bytes() == before


def test_shared_store_separates_mod_root_and_profile_ownership(tmp_path):
    server_a, server_b, client = [tmp_path / part for part in ("server-a", "server-b", "client")]
    profile_a, profile_b = [tmp_path / part for part in ("profile-a", "profile-b")]
    for folder in (server_a, server_b, client, profile_a, profile_b):
        folder.mkdir()
    for profile in (profile_a, profile_b):
        (profile / "prefs.ini").write_bytes(b"quality=low\n")
    store = ContributionStore(client, allowed_roots=(profile_a, profile_b))
    first = FileTarget.capture(profile_a / "prefs.ini", profile_a, "ini")
    second = FileTarget.capture(profile_b / "prefs.ini", profile_b, "ini")
    a = ContributionOwner(server_a, "mods/graphics", "profile-a")
    b = ContributionOwner(server_a, "mods/graphics", "profile-b")
    other_root = ContributionOwner(server_b, "mods/graphics", "profile-a")
    assert len({a.id, b.id, other_root.id}) == 3
    apply(store, a, first, ("quality",), "high")
    apply(store, b, second, ("quality",), "medium")
    store = ContributionStore(client, allowed_roots=(profile_a, profile_b))
    store.commit(store.plan_remove(a))
    assert first.path.read_bytes() == b"quality=low\n"
    assert second.path.read_bytes() == b"quality=medium\n"


def test_host_held_lease_uses_locked_seams_without_nested_acquisition(setup_store):
    store, target, a, _b = setup_store
    with acquire_mod_lifecycle_lock(store.root):
        store.commit_locked(store.plan_edits_locked(a, [KeyEdit(target, ("a",), 1)]))
        store.commit_locked(store.plan_remove_locked(a))
    assert read(target)["a"] == 0


def test_target_boundaries_and_database_disguises_are_rejected(setup_store, tmp_path):
    store, _target, a, _b = setup_store
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    with pytest.raises(ContributionError):
        FileTarget.capture(outside, store.root, "json")
    outside_target = FileTarget.capture(outside, tmp_path, "json")
    with pytest.raises(ContributionError):
        store.plan_edits(a, [KeyEdit(outside_target, ("x",), 1)])
    database = store.root / "state.json"
    database.write_bytes(b"SQLite format 3\0" + b"\0" * 40)
    with pytest.raises(ContributionError, match="database"):
        FileTarget.capture(database, store.root, "json")


def test_ancestor_key_overlap_requires_a_different_patch_shape(setup_store):
    store, target, a, b = setup_store
    apply(store, a, target, ("new",), {"nested": 1})
    with pytest.raises(ContributionConflict, match="Ancestor"):
        store.plan_edits(b, [KeyEdit(target, ("new", "nested"), 2, allow_override=True)])


def test_reading_a_new_private_profile_is_read_only_and_first_save_creates_its_parents(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    path = root / "profile-a" / "mods" / "example" / "settings.json"
    target = FileTarget.capture(path, root, "json")
    assert read_target(target) is None
    assert list(root.iterdir()) == []
    store = ContributionStore(root)
    owner = ContributionOwner(root, "mods/example", "profile-a")
    plan = store.plan_edits(owner, [KeyEdit(target, ("enabled",), True)])
    assert not path.parent.exists()
    store.commit(plan)
    assert read_target(target) == b'{"enabled": true}'
    store.commit(store.plan_remove(owner))
    assert json.loads(read_target(target)) == {}


def test_failed_first_save_removes_its_new_files_and_preserves_existing_files(setup_store, monkeypatch):
    store, existing, owner, _b = setup_store
    new = FileTarget.capture(store.root / "new-profile" / "private" / "settings.json", store.root, "json")
    before = read_target(existing)
    plan = store.plan_edits(owner, [KeyEdit(new, ("enabled",), True), KeyEdit(existing, ("a",), 2)])
    real_replace = store._replace_file

    def fail_existing(path, content):
        if path == existing.path and content != before:
            raise PermissionError("injected first-save failure")
        real_replace(path, content)

    monkeypatch.setattr(store, "_replace_file", fail_existing)
    with pytest.raises(PermissionError):
        store.commit(plan)
    assert not new.path.exists()
    assert read_target(existing) == before
    assert not store.index_path.exists()
    assert store.recover().unresolved == ()


def make_directory_link(source, destination):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(source), str(destination))
    else:
        destination.symlink_to(source, target_is_directory=True)


def test_target_rejects_an_intermediate_junction_even_inside_the_allowed_root(setup_store):
    store, _target, _a, _b = setup_store
    physical = store.root / "physical"
    physical.mkdir()
    (physical / "settings.json").write_bytes(b"{}")
    linked = store.root / "linked"
    make_directory_link(physical, linked)
    with pytest.raises(ContributionError, match="ancestor is linked"):
        FileTarget.capture(linked / "settings.json", store.root, "json")


def test_junction_added_after_planning_is_rejected_by_read_and_commit(setup_store):
    store, _target, owner, _b = setup_store
    path = store.root / "private" / "settings.json"
    target = FileTarget.capture(path, store.root, "json")
    plan = store.plan_edits(owner, [KeyEdit(target, ("enabled",), True)])
    physical = store.root / "other-profile"
    physical.mkdir()
    make_directory_link(physical, path.parent)
    with pytest.raises(ContributionError, match="ancestor is linked"):
        read_target(target)
    with pytest.raises(ContributionError, match="ancestor is linked"):
        store.commit(plan)
    assert list(physical.iterdir()) == []
    assert not store.index_path.exists()


def test_lexical_traversal_is_rejected_before_normalizing_an_inside_root_destination(setup_store):
    store, _target, _a, _b = setup_store
    with pytest.raises(ContributionError, match="unambiguous"):
        FileTarget.capture(store.root / "missing" / ".." / "settings.json", store.root, "json")


def test_repeated_ineffective_owner_request_is_a_noop_without_taking_precedence(setup_store):
    store, target, a, b = setup_store
    apply(store, a, target, ("a",), 1)
    apply(store, b, target, ("a",), 2, allow_override=True)
    before = read_target(target), store.index_path.read_bytes()
    assert apply(store, a, target, ("a",), 1).is_noop
    assert (read_target(target), store.index_path.read_bytes()) == before


def test_removing_an_already_absent_key_does_not_create_an_empty_json_file(setup_store):
    store, _target, a, _b = setup_store
    target = FileTarget.capture(store.root / "missing" / "settings.json", store.root, "json")
    apply(store, a, target, ("absent",), None, delete=True)
    store.commit(store.plan_remove(a))
    assert read_target(target) is None
    assert not target.path.parent.exists()


def test_malformed_pending_record_is_rejected_before_any_rollback_write(setup_store, monkeypatch):
    store, first, second, _a, _b, plan = two_file_plan(setup_store)
    real_replace = store._replace_file

    def crash(path, content):
        real_replace(path, content)
        if path == store.index_path:
            raise SystemExit

    monkeypatch.setattr(store, "_replace_file", crash)
    with pytest.raises(SystemExit):
        store.commit(plan)
    journal_path = next(store.transactions.iterdir()) / "journal.json"
    journal = json.loads(journal_path.read_bytes())
    journal["records"][0] = {"bad": "record"}
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    before = read_target(first), read_target(second), store.index_path.read_bytes()
    with pytest.raises(ContributionError, match="recovery record"):
        ContributionStore(store.root).recover()
    assert (read_target(first), read_target(second), store.index_path.read_bytes()) == before


def test_multi_owner_batch_commits_once_and_keeps_distinct_removal_ownership(setup_store):
    store, target, a, b = setup_store
    plan = store.plan_batch(((a, [KeyEdit(target, ("a",), 1)]), (b, [KeyEdit(target, ("b",), 2)])))
    store.commit(plan)
    assert len(list(store.transactions.iterdir())) == 1
    assert read(target) == {"a": 1, "b": 2, "manual": 10}
    store.commit(store.plan_remove(a))
    assert read(target) == {"a": 0, "b": 2, "manual": 10}


def test_multi_owner_conflict_does_not_write_any_owners_values(setup_store):
    store, target, a, b = setup_store
    before = read_target(target)
    with pytest.raises(ContributionConflict, match="precedence"):
        store.plan_batch(((a, [KeyEdit(target, ("a",), 1)]), (b, [KeyEdit(target, ("a",), 2)])))
    assert read_target(target) == before
    assert not store.index_path.exists()


def test_owner_enumeration_is_read_only_and_bound_to_mod_root_and_folder(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    store = ContributionStore(root)
    a = ContributionOwner(root, "mods/a")
    assert store.owners_for_mod(a) == ()
    assert list(root.iterdir()) == []
    path = root / "settings.json"
    path.write_bytes(b"{}")
    target = FileTarget.capture(path, root, "json")
    profile = ContributionOwner(root, "mods/a", "profile-a")
    other = ContributionOwner(root, "mods/b", "profile-a")
    store.commit(store.plan_batch(((a, [KeyEdit(target, ("global",), 1)]), (profile, [KeyEdit(target, ("profile",), 2)]), (other, [KeyEdit(target, ("other",), 3)]))))
    assert {owner.id for owner in store.owners_for_mod(a)} == {a.id, profile.id}
    content = store.index_path.read_bytes()
    with acquire_mod_lifecycle_lock(root):
        assert {owner.id for owner in store.owners_for_mod(a)} == {a.id, profile.id}
    assert store.index_path.read_bytes() == content


def test_batch_removal_preserves_private_preferences_and_other_mods_in_one_transaction(setup_store):
    store, shared, a, b = setup_store
    profile_a = ContributionOwner(store.root, a.mod_relative_path, "profile-a")
    profile_b = ContributionOwner(store.root, a.mod_relative_path, "profile-b")
    private_root = store.root / "private-preferences"
    private = FileTarget.capture(private_root / "prefs.ini", store.root, "ini")
    store.commit(store.plan_batch(((profile_a, [KeyEdit(shared, ("a",), 1), KeyEdit(private, ("quality",), "high")]),
        (profile_b, [KeyEdit(shared, ("b",), 2)]), (b, [KeyEdit(shared, ("other-mod",), 3)]))))
    shared.path.write_bytes(shared.path.read_bytes().replace(b'"manual": 10', b'"manual": 99'))
    owners = store.owners_for_mod(a)
    before_count = len(list(store.transactions.iterdir()))
    store.commit(store.plan_remove_batch(owners, preserve_roots=(private_root,)))
    assert len(list(store.transactions.iterdir())) == before_count + 1
    assert read(shared) == {"a": 0, "b": 0, "manual": 99, "other-mod": 3}
    assert private.path.read_bytes() == b"quality=high\n"
    assert store.commit(store.plan_remove_batch(owners, preserve_roots=(private_root,))).is_noop
    # Full explicit removal still removes the retained private owner layers.
    store.commit(store.plan_remove_batch(owners))
    assert private.path.read_bytes() == b""


def test_preserve_scope_cannot_follow_a_junction_or_escape_the_captured_roots(setup_store, tmp_path):
    store, target, a, _b = setup_store
    apply(store, a, target, ("a",), 1)
    with pytest.raises(ContributionError, match="outside"):
        store.plan_remove_batch((a,), preserve_roots=(tmp_path,))
    physical = store.root / "physical"
    physical.mkdir()
    linked = store.root / "linked"
    make_directory_link(physical, linked)
    with pytest.raises(ContributionError, match="ancestor is linked"):
        store.plan_remove_batch((a,), preserve_roots=(linked,))
    assert read(target)["a"] == 1
