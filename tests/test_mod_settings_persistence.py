from copy import deepcopy
import json

import pytest

from src.core.mod_contributions import ContributionConflict, ContributionStore
from src.core.mod_settings import ModSettingsContext, ModSettingsSession


def fixture(tmp_path, *, profile=False, mod="Example", key="enabled"):
    root = tmp_path / "EveJS"
    folder = root / "mods" / mod
    folder.mkdir(parents=True, exist_ok=True)
    client = tmp_path / "client"
    client.mkdir(exist_ok=True)
    profiles = tmp_path / "Profiles"
    profiles.mkdir(exist_ok=True)
    context = ModSettingsContext(root, folder, client, "pilot-a" if profile else "", profiles / "pilot-a" if profile else None)
    declaration = {
        "schemaVersion": 1,
        "files": [{"id": "config", "base": "profile" if profile else "evejs", "path": "private/options.ini" if profile else "config/shared.json", "format": "ini" if profile else "json"}],
        "fields": [{"id": "option", "label": "Option", "type": "boolean", "default": False,
                    "file": "config", "key": ["Rendering", key] if profile else [key], "storage": "numeric_boolean" if profile else "native"}],
    }
    return context, declaration


def files_below(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_open_and_noop_save_write_nothing_even_for_a_new_profile(tmp_path):
    context, schema = fixture(tmp_path, profile=True)
    before = files_below(tmp_path)
    session = ModSettingsSession.open(context, schema, scope="profile")
    assert session.values == {"option": False}
    assert session.save(session.values) is session
    assert files_below(tmp_path) == before
    assert not context.mod_data_root.exists()


@pytest.mark.parametrize("keep_current", [True, False])
def test_reviewed_precedence_preserves_other_mod_and_supports_keep_current(tmp_path, keep_current):
    a, schema = fixture(tmp_path, mod="A")
    ModSettingsSession.open(a, schema).save({"option": True})
    b, schema = fixture(tmp_path, mod="B")
    session = ModSettingsSession.open(b, schema)
    with pytest.raises(ContributionConflict):
        session.save({"option": False})
    before = files_below(tmp_path)
    review = session.review_save({"option": False})
    assert files_below(tmp_path) == before
    saved = session.apply_review(review, review.preserve if keep_current else review.restore)
    assert saved.values["option"] is keep_current
    store = ContributionStore(a.evejs_root)
    if keep_current:
        assert files_below(tmp_path) == before
    else:
        store.commit(store.plan_remove(b.owner))
        assert ModSettingsSession.open(a, schema).values["option"] is True


def test_settings_review_rejects_changed_manifest_before_write(tmp_path):
    context, schema = fixture(tmp_path)
    manifest = context.mod_folder / "evejs-launcher.mod.json"
    manifest.write_text(json.dumps({"settings": schema}))
    session = ModSettingsSession.open(context, schema, manifest_path=manifest)
    review = session.review_save({"option": True})
    manifest.write_text('{}')
    before = files_below(tmp_path)
    with pytest.raises(RuntimeError, match="declaration changed"):
        session.apply_review(review, review.restore)
    assert files_below(tmp_path) == before


def test_first_profile_save_creates_private_text_config_and_reads_numeric_boolean(tmp_path):
    context, schema = fixture(tmp_path, profile=True)
    session = ModSettingsSession.open(context, schema, scope="profile")
    saved = session.save({"option": True})
    assert saved.values == {"option": True}
    assert "enabled=1" in (context.mod_data_root / "private/options.ini").read_text().replace(" ", "").lower()
    assert ModSettingsSession.open(context, schema, scope="profile").values == saved.values
    reopened_context = ModSettingsContext(context.evejs_root, context.mod_folder, context.client_root, context.profile_id, context.profile_root)
    reopened = ModSettingsSession.open(reopened_context, schema, scope="profile")
    assert reopened.save({"option": False}).values == {"option": False}
    assert reopened.targets == saved.targets


def test_save_preserves_comments_and_unrelated_external_edits(tmp_path):
    context, schema = fixture(tmp_path, profile=True)
    target = context.mod_data_root / "private/options.ini"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"; preference\r\n[Rendering]\r\nenabled = 0 ; retain\r\nother=old\r\n")
    session = ModSettingsSession.open(context, schema, scope="profile")
    target.write_bytes(target.read_bytes().replace(b"other=old", b"other=new"))
    session.save({"option": True})
    assert target.read_bytes() == b"; preference\r\n[Rendering]\r\nenabled = 1 ; retain\r\nother=new\r\n"


def test_conflicting_external_edit_and_changed_author_contract_are_preserved(tmp_path):
    context, schema = fixture(tmp_path)
    target = context.evejs_root / "config/shared.json"
    target.parent.mkdir()
    target.write_text('{"enabled": false, "other": 7}')
    manifest = context.mod_folder / "evejs-launcher.mod.json"
    manifest.write_text(json.dumps({"settings": schema}))
    session = ModSettingsSession.open(context, schema, manifest_path=manifest)
    target.write_text('{"enabled": true, "other": 8}')
    with pytest.raises(ContributionConflict):
        session.save({"option": True})
    assert json.loads(target.read_text())["other"] == 8
    target.write_text('{"enabled": false, "other": 8}')
    manifest.write_text('{}')
    with pytest.raises(RuntimeError, match="declaration changed"):
        session.save({"option": True})
    assert json.loads(target.read_text())["enabled"] is False


def test_two_mod_forms_share_config_and_remove_only_their_own_keys(tmp_path):
    a, schema_a = fixture(tmp_path, mod="A", key="a")
    b, schema_b = fixture(tmp_path, mod="B", key="b")
    target = a.evejs_root / "config/shared.json"
    target.parent.mkdir()
    target.write_bytes(b'{"a": false, "b": false, "manual": 4}\n')
    first = ModSettingsSession.open(a, schema_a)
    second = ModSettingsSession.open(b, schema_b)
    first.save({"option": True})
    second.save({"option": True})
    store = ContributionStore(a.evejs_root)
    store.commit(store.plan_remove(a.owner))
    assert json.loads(target.read_bytes()) == {"a": False, "b": True, "manual": 4}


def test_equal_mod_names_in_separate_roots_keep_profile_data_separate(tmp_path):
    a, schema = fixture(tmp_path / "a", profile=True)
    b, _ = fixture(tmp_path / "b", profile=True)
    # The users' two roots deliberately share one physical client/profile tree.
    b = ModSettingsContext(b.evejs_root, b.mod_folder, a.client_root, a.profile_id, a.profile_root)
    assert a.mod_data_root != b.mod_data_root
    ModSettingsSession.open(a, schema, scope="profile").save({"option": True})
    assert ModSettingsSession.open(b, schema, scope="profile").values == {"option": False}
