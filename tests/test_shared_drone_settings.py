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
