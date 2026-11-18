"""Saved presentation order is separate from account names and launch groups."""
import json

import pytest

from src.core.character_order import (
    CharacterOrderError, load_character_order, move_character, ordered_keys,
    save_character_order,
)

A, B, C, D = (1, 101), (2, 102), (3, 103), (4, 104)


def test_saved_order_round_trips_and_keeps_installations_separate(tmp_path):
    path = tmp_path/'character-order.json'
    save_character_order('installation-a', (C, A, B), path=path)
    save_character_order('installation-b', (B, A), path=path)
    assert load_character_order('installation-a', path=path) == (C, A, B)
    assert load_character_order('installation-b', path=path) == (B, A)
    assert load_character_order('new-installation', path=path) == ()


def test_refresh_and_new_character_preserve_custom_order():
    assert ordered_keys((C, A, B), (A, B, C, D)) == (C, A, B, D)
    assert ordered_keys((), (A, B, C)) == (A, B, C)


def test_filtered_reorder_preserves_hidden_slots_and_missing_members():
    # B is hidden; D is temporarily absent from the current roster.
    assert move_character((A, B, C, D), (A, B, C), (A, C), C, A, after=False) == (C, B, A, D)
    assert move_character((A, B, C), (A, B, C), (A, B, C), A, C, after=True) == (B, C, A)


@pytest.mark.parametrize('value', [None, 'bad', [(True, 1)], [(1, 0)], [(1, 2), (1, 2)], [(1, 2, 3)]])
def test_invalid_order_never_overwrites_existing_data(tmp_path, value):
    path = tmp_path/'character-order.json'
    save_character_order('installation', (A, B), path=path)
    before = path.read_bytes()
    with pytest.raises(CharacterOrderError):
        save_character_order('installation', value, path=path)
    assert path.read_bytes() == before


def test_corrupt_or_newer_store_is_preserved_for_recovery(tmp_path):
    path = tmp_path/'character-order.json'
    for payload in ('broken-json', json.dumps({'schema_version':99,'targets':{}})):
        path.write_text(payload)
        with pytest.raises(CharacterOrderError):
            save_character_order('installation', (A,), path=path)
        assert path.read_text() == payload


def test_failed_atomic_replace_preserves_all_scopes_and_removes_temp(tmp_path, monkeypatch):
    from src.core import character_order
    path = tmp_path/'character-order.json'
    save_character_order('installation', (A,), path=path)
    before = path.read_bytes()
    def fail(*_args):
        raise OSError('disposable write failure')
    monkeypatch.setattr(character_order.os, 'replace', fail)
    with pytest.raises(OSError):
        save_character_order('installation', (B, A), path=path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['character-order.json']


def test_forged_or_invisible_drop_does_not_change_the_order():
    with pytest.raises(CharacterOrderError):
        move_character((A, B), (A, B), (A,), B, A, after=False)
