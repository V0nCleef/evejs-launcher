import json

import pytest

from src.core import native_drone_profiles as native
from src.core import shared_drone_settings as codec
from src.core import shared_settings_profile as profile, platform
from src.core.shared_client_settings import SharedSettingsError
from test_shared_drone_settings import world, account, members, value


def options(current):
    return dict(storage_identity='installation-groups:'+'a'*64,
                storage_root=current.parent.parent.parent/'SharedProfiles')


def test_new_followers_do_not_inherit_source_memberships_or_favourites(world):
    _,_,_,launch=world
    own=launch()
    data=(own.folder/'core_user_12.dat').read_bytes()
    assert codec.entries(value(data,'droneBlah2'))==[]
    assert codec.get(codec.get(codec.loads(data),'ui'),'drones_favoriteGroupID_17478') is None
    assert value(data,'layout').text()=='master-layout'


def test_personal_groups_survive_a_shared_layout_source_change(world):
    current,source,_,_=world
    first=profile.prepare_shared_profile_launch(current,source,account_id=12,
        character_id=102,evejs_root=source.evejs_root,**options(current))
    personal=account((2001,2002),'personal-key','private-layout')
    (first.folder/'core_user_12.dat').write_bytes(personal)
    replacement=profile.SharedSettingsSource('other-source',13,103,source.evejs_root)
    replacement_tq=current.parent.parent/'other-source/tq'
    replacement_tq.mkdir(parents=True)
    settings=platform.get_eve_settings_path(str(replacement_tq))
    settings.mkdir(parents=True)
    (settings/'core_char_103.dat').write_bytes(profile._EMPTY_SETTINGS)
    (settings/'core_user_13.dat').write_bytes(account((3001,),'other-key','new-master-layout'))
    again=profile.prepare_shared_profile_launch(current,replacement,account_id=12,
        character_id=102,evejs_root=source.evejs_root,**options(current))
    data=(again.folder/'core_user_12.dat').read_bytes()
    assert members(data)==[2001,2002]
    assert codec.signature(data)==codec.signature(personal)
    assert value(data,'layout').text()=='new-master-layout'
    assert (first.folder/'core_user_12.dat').read_bytes()==personal


def test_scoped_predecessor_bootstraps_without_a_native_drone_pointer(world):
    current,source,_,_=world
    opts=options(current)
    root,scope=opts['storage_root'],opts['storage_identity']
    first=profile.prepare_shared_profile_launch(current,source,account_id=12,
        character_id=102,evejs_root=source.evejs_root,**opts)
    personal=account((2001,),'own-key')
    (first.folder/'core_user_12.dat').write_bytes(personal)
    native._record_path(root,scope,12,102).unlink()
    (first.folder/'native-drone-profile.json').unlink()
    assert native.read_personal_settings(root,scope,12,102)==personal
    assert native.read_personal_settings(root,'installation-groups:'+'b'*64,12,102) is None


def test_native_drone_pointer_rejects_another_world_marker(world):
    current,source,_,_=world
    opts=options(current)
    first=profile.prepare_shared_profile_launch(current,source,account_id=12,
        character_id=102,evejs_root=source.evejs_root,**opts)
    marker=first.folder/'native-drone-profile.json'
    record=json.loads(marker.read_text())
    record['storageIdentity']='installation-groups:'+'b'*64
    marker.write_text(json.dumps(record))
    with pytest.raises(SharedSettingsError,match='could not be verified'):
        native.read_personal_settings(opts['storage_root'],opts['storage_identity'],12,102)
