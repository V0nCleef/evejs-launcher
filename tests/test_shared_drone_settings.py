import json
from pathlib import Path
import struct

import pytest

from src.core import shared_drone_settings as codec
from src.core import shared_settings_profile as profile, platform
from src.core.shared_client_settings import SharedSettingsError


def string(text):
    return codec.Value(0x2e, text.encode())


def integer(value):
    return codec.Value(0x03, struct.pack('<q', value))


def dictionary(values):
    return codec.Value(0x16, children=[node for key, value in values.items() for node in (value, string(key))])


def account(members=(9000000000001,), group_id='own-id', layout='master-layout', stamp=123):
    groups = dictionary({'Miners': dictionary({'id': codec.Value(0x2c, children=[string('Miners'), string(group_id)]),
        'droneGroupID': integer(101), 'droneIDs': codec.Value(0x15, children=[integer(i) for i in members])})})
    setting = lambda value: codec.Value(0x2c, children=[integer(stamp), value])
    return codec.dumps(dictionary({'ui': dictionary({'droneBlah2': setting(groups),
        'drones_favoriteGroupID_17478': setting(codec.Value(0x2c, children=[string('Miners'), string(group_id)])),
        'layout': setting(string(layout))})}))


def value(data, name):
    return codec.get(codec.get(codec.loads(data), 'ui'), name).children[1]


def members(data):
    drones = codec.get(codec.get(value(data, 'droneBlah2'), 'Miners'), 'droneIDs')
    return [struct.unpack('<q', node.raw)[0] for node in drones.children]


def test_merge_changes_only_native_drone_leaves_and_preserves_group_ids_and_favourites():
    source = account((1001,), 'source-id', 'new-shared-layout')
    personal = account((9000000000001, 9000000000002), 'personal-id', 'old-private-layout')
    result = codec.merge(source, personal)
    assert members(result) == [9000000000001, 9000000000002]
    assert value(result, 'layout').text() == 'new-shared-layout'
    assert codec.signature(result) == codec.signature(personal)
    assert value(result, 'drones_favoriteGroupID_17478').children[1].text() == 'personal-id'
    assert members(source) == [1001]


def test_access_times_and_drone_set_order_do_not_disguise_a_template_copy():
    a = account((1001, 1002), stamp=1)
    b = account((1002, 1001), stamp=999)
    assert codec.signature(a) == codec.signature(b)
    assert codec.merge(a, b) == a


def test_empty_drone_groups_stay_empty_instead_of_being_recreated():
    personal = codec.loads(account())
    codec.get(codec.get(personal, 'ui'), 'droneBlah2').children[1] = dictionary({})
    result = codec.merge(account((1001,)), codec.dumps(personal))
    assert codec.entries(value(result, 'droneBlah2')) == []


def test_no_personal_groups_does_not_require_decoding_an_opaque_template():
    assert codec.merge(b'opaque-fixture', None) == b'opaque-fixture'
    assert codec.merge(b'opaque-fixture', b'no-drone-setting') == b'opaque-fixture'


@pytest.mark.parametrize('data', [b'', b'\x7e\0\0\0\0\x16\xff\xff\xff\xff\x7f',
    b'\x7e\0\0\0\0\x16\x01\x1b\x01\x0e', b'\x7e\0\0\0\0\x21dangerous.pickle'])
def test_unsupported_truncated_and_invalid_reference_data_is_rejected(data):
    with pytest.raises(codec.SettingsFormatError):
        codec.loads(data)


def test_shared_reference_expansion_and_cycle_rejection():
    # A one-pair dictionary stores the same saved byte string as value and key.
    data = b'\x7e\x01\0\0\0\x16\x01\x50\x02ui\x1b\x01\x01\0\0\0'
    root = codec.loads(data)
    assert codec.dumps(codec.loads(codec.dumps(root))) == codec.dumps(root)
    cycle = dictionary({})
    cycle.children = [cycle, string('cycle')]
    with pytest.raises(codec.SettingsFormatError, match='Cyclic'):
        codec.dumps(cycle)


@pytest.fixture
def world(tmp_path, monkeypatch):
    current = tmp_path / 'launcher/Profiles/anna/tq'
    current.mkdir(parents=True)
    local = tmp_path / 'local'
    local.mkdir()
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr('src.core.overview_patch.is_eve_client_running', lambda: False)
    source = profile.SharedSettingsSource('vonessa', 11, 101, str(tmp_path / 'server'))
    source_tq = current.parent.parent / 'vonessa/tq'
    source_tq.mkdir(parents=True)
    separate = platform.get_eve_settings_path(str(source_tq))
    separate.mkdir(parents=True)
    (separate / 'core_char_101.dat').write_bytes(profile._EMPTY_SETTINGS)
    (separate / 'core_user_11.dat').write_bytes(account((1001,), 'main-group'))
    launch = lambda account_id=12, character_id=102: profile.prepare_shared_profile_launch(
        current, source, account_id=account_id, character_id=character_id, evejs_root=source.evejs_root)
    return current, source, source_tq, launch


def test_pilot_groups_survive_relaunch_while_updated_shared_layout_still_applies(world):
    current, source, source_tq, launch = world
    master = profile.prepare_shared_profile_launch(source_tq, source, account_id=11,
        character_id=101, evejs_root=source.evejs_root)
    first = launch()
    (first.folder / 'core_user_12.dat').write_bytes(account((2001, 2002), 'anna-group', 'anna-layout'))
    (master.folder / 'core_user_11.dat').write_bytes(account((1001,), 'main-group', 'changed-master-layout'))
    before = (master.folder / 'core_user_11.dat').read_bytes()
    again = launch()
    actual = (again.folder / 'core_user_12.dat').read_bytes()
    assert again.folder != first.folder
    assert members(actual) == [2001, 2002]
    assert value(actual, 'layout').text() == 'changed-master-layout'
    assert value(actual, 'drones_favoriteGroupID_17478').children[1].text() == 'anna-group'
    assert (master.folder / 'core_user_11.dat').read_bytes() == before


def test_pilots_including_characters_on_the_same_account_keep_distinct_memberships(world, monkeypatch):
    _, _, _, launch = world
    first = launch()
    (first.folder / 'core_user_12.dat').write_bytes(account((2001,), 'anna-group'))
    other = launch(character_id=103)
    (other.folder / 'core_user_12.dat').write_bytes(account((3001,), 'other-group'))
    monkeypatch.setattr('src.core.overview_patch.is_eve_client_running', lambda: True)
    assert members((launch().folder / 'core_user_12.dat').read_bytes()) == [2001]
    assert members((launch(character_id=103).folder / 'core_user_12.dat').read_bytes()) == [3001]


def test_first_fixed_launch_recovers_older_personal_groups_from_before_a_later_reset(world):
    current, source, _, _ = world
    storage = profile.shared_profile_main_folder(current, source, evejs_root=source.evejs_root).parent
    old = storage / 'sessions' / ('a' * 32)
    newer = storage / 'sessions' / ('b' * 32)
    for folder in (old, newer):
        folder.mkdir(parents=True)
        (folder / 'core_char_102.dat').write_bytes(profile._EMPTY_SETTINGS)
    (old / 'core_user_12.dat').write_bytes(account((2001,), 'personal-group'))
    (newer / 'core_user_12.dat').write_bytes(account((1001,), 'main-group', stamp=999))
    main = storage / 'main'
    main.mkdir()
    (main / 'core_user_11.dat').write_bytes(account((1001,), 'main-group'))
    result = profile._previous_drone_settings(storage, current, main, source, 12, 102)
    assert members(result) == [2001]


def test_invalid_personal_pointer_is_rejected_without_replacing_a_prior_profile(world):
    _, _, _, launch = world
    first = launch()
    previous = (first.folder / 'core_user_12.dat').read_bytes()
    pointer = first.folder.parent.parent / 'pilots/12-102.json'
    record = json.loads(pointer.read_text())
    record['session'] = '../elsewhere'
    pointer.write_text(json.dumps(record))
    with pytest.raises(SharedSettingsError, match='could not be verified'):
        launch()
    assert (first.folder / 'core_user_12.dat').read_bytes() == previous


def test_drone_groups_survive_server_upgrade_and_account_path_isolation(world, tmp_path):
    from dataclasses import replace

    current, source, _, _ = world
    scope = 'installation-groups:' + 'a' * 64
    storage = current.parent.parent.parent / 'SharedProfiles'
    options = dict(storage_identity=scope, storage_root=storage)
    first = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root, **options)
    personal = account((2001, 2002), 'personal-group')
    (first.folder / 'core_user_12.dat').write_bytes(personal)

    # A server upgrade and a case-collision profile migration may happen on the
    # same launch. Neither changes the pilot's logical installation or IDs.
    upgraded = replace(source, evejs_root=str(tmp_path / 'new-server'))
    isolated = current.parent.parent.parent / 'Profiles-exact-accounts' / ('b' * 32) / 'anna/tq'
    isolated.mkdir(parents=True)
    again = profile.prepare_shared_profile_launch(isolated, upgraded, account_id=12,
        character_id=102, evejs_root=upgraded.evejs_root, **options)
    assert codec.signature((again.folder / 'core_user_12.dat').read_bytes()) == codec.signature(personal)
    assert members((again.folder / 'core_user_12.dat').read_bytes()) == [2001, 2002]
    assert (first.folder / 'core_user_12.dat').read_bytes() == personal


def test_scoped_profile_imports_current_legacy_groups_and_layout_without_editing_them(world):
    current, source, _, launch = world
    legacy = launch()
    personal = account((2001, 2002), 'personal-group')
    (legacy.folder / 'core_user_12.dat').write_bytes(personal)
    old_main = profile.shared_profile_main_folder(current, source, evejs_root=source.evejs_root)
    updated_master = account((1001,), 'main-group', 'latest-master-layout')
    (old_main / 'core_user_11.dat').write_bytes(updated_master)
    before = {p: p.read_bytes() for p in legacy.folder.parent.parent.rglob('*') if p.is_file()}

    result = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root,
        storage_identity='installation-groups:' + 'a' * 64,
        storage_root=current.parent.parent.parent / 'SharedProfiles')
    actual = (result.folder / 'core_user_12.dat').read_bytes()
    assert codec.signature(actual) == codec.signature(personal)
    assert value(actual, 'layout').text() == 'latest-master-layout'
    assert all(p.read_bytes() == data for p, data in before.items())


def test_different_installations_never_inherit_each_others_scoped_drone_groups(world):
    current, source, _, _ = world
    storage = current.parent.parent.parent / 'SharedProfiles'
    first = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root,
        storage_identity='installation-groups:' + 'a' * 64, storage_root=storage)
    (first.folder / 'core_user_12.dat').write_bytes(account((2001,), 'first-world-group'))
    other = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root,
        storage_identity='installation-groups:' + 'b' * 64, storage_root=storage)
    assert codec.entries(value((other.folder / 'core_user_12.dat').read_bytes(),'droneBlah2')) == []


def test_existing_empty_scoped_groups_remain_authoritative_over_legacy_groups(world):
    current, source, _, launch = world
    legacy = launch()
    (legacy.folder / 'core_user_12.dat').write_bytes(account((2001,), 'old-personal-group'))
    options = dict(storage_identity='installation-groups:' + 'a' * 64,
                   storage_root=current.parent.parent.parent / 'SharedProfiles')
    first = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root, **options)
    empty = codec.loads(account())
    codec.get(codec.get(empty, 'ui'), 'droneBlah2').children[1] = dictionary({})
    (first.folder / 'core_user_12.dat').write_bytes(codec.dumps(empty))
    again = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root, **options)
    assert codec.entries(value((again.folder / 'core_user_12.dat').read_bytes(), 'droneBlah2')) == []


def test_scoped_migration_imports_an_isolated_legacy_pilot_into_the_canonical_store(world):
    current, source, source_tq, _ = world
    canonical = current.parent.parent.parent / 'SharedProfiles'
    isolated = current.parent.parent.parent / 'Profiles-exact-accounts' / ('b' * 32) / 'anna/tq'
    isolated.mkdir(parents=True)
    # Seed the old isolated namespace from the explicit selected source folder.
    legacy = profile.prepare_shared_profile_launch(isolated, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root,
        legacy_folder=str(platform.get_eve_settings_path(str(source_tq))))
    personal = account((2001,), 'isolated-personal')
    (legacy.folder / 'core_user_12.dat').write_bytes(personal)
    result = profile.prepare_shared_profile_launch(isolated, source, account_id=12,
        character_id=102, evejs_root=source.evejs_root,
        storage_identity='installation-groups:' + 'a' * 64, storage_root=canonical)
    assert result.folder.is_relative_to(canonical)
    assert members((result.folder / 'core_user_12.dat').read_bytes()) == [2001]
    assert (legacy.folder / 'core_user_12.dat').read_bytes() == personal


def test_first_scoped_launch_waits_for_running_clients_before_copying_preferences(world, monkeypatch):
    current, source, _, launch = world
    legacy = launch()
    before = {p: p.read_bytes() for p in legacy.folder.parent.parent.rglob('*') if p.is_file()}
    monkeypatch.setattr('src.core.overview_patch.is_eve_client_running', lambda: True)
    with pytest.raises(SharedSettingsError, match='Close EVE clients'):
        profile.prepare_shared_profile_launch(current, source, account_id=12,
            character_id=102, evejs_root=source.evejs_root,
            storage_identity='installation-groups:' + 'a' * 64,
            storage_root=current.parent.parent.parent / 'SharedProfiles')
    assert all(p.read_bytes() == data for p, data in before.items())
