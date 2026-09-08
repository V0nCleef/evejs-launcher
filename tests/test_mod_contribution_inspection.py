import json

import pytest

from src.core.mod_contributions import ContributionError, ContributionOwner, ContributionStore, FileTarget, KeyEdit


def test_file_owner_inspection_is_read_only_and_keeps_all_independent_contributors(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "settings.json"
    path.write_bytes(b'{"a":0,"b":0}')
    store = ContributionStore(root)
    assert store.owners_for_path(path) == ()
    assert not store.index_path.exists()
    target = FileTarget.capture(path, root, "json")
    owners = [ContributionOwner(root, "mods/a"), ContributionOwner(root, "mods/b", "profile-one")]
    for owner, key in zip(owners, ("a", "b")):
        store.commit(store.plan_edits(owner, [KeyEdit(target, (key,), 1)]))
    before = store.index_path.read_bytes(), path.read_bytes()
    assert set(store.owners_for_path(path)) == set(owners)
    assert (store.index_path.read_bytes(), path.read_bytes()) == before
    assert store.owners_for_path(root / "other.json") == ()
    payload = json.loads(store.index_path.read_bytes())
    payload["owners"][owners[0].id]["mod_relative_path"] = "mods/forged"
    store.index_path.write_text(json.dumps(payload))
    with pytest.raises(ContributionError, match="identity does not match"):
        store.owners_for_path(path)
