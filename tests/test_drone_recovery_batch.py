"""Exercise the actual saved-profile catalog, ownership checks and launch handoff."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from src.core import native_drone_profiles as native
from src.core import shared_settings_profile as profiles
from src.core import drone_recovery_catalog as catalog
from src.core.drone_recovery_batch import (
    RecoveryContext, RecoveryChangedError, apply_recovery, scan_recovery,
)
from src.core.shared_client_settings import SharedSettingsError
from test_drone_group_recovery import preferences, memberships

SCOPE='installation-groups:'+'c'*64


def put(con, table, key, row):
    con.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?)',(str(key),json.dumps(row)))


def saved(root, world, members, *, stable=None, account=1, character=101,
          session=None, group='same-group', target=None, active=True):
    source={'username':f'pilot{account}','account_id':account,'character_id':character}
    identity={'source':source,'storageIdentity':stable} if stable else {
        'source':{**source,'evejs_root':str(world)},'serverRoot':str(world)}
    if target is not None:
        identity['targetIdentity']=target
    base=root/hashlib.sha256(profiles._json_bytes(identity)).hexdigest()
    main=base/'main'
    folder=main if session is None else base/'sessions'/session
    folder.mkdir(parents=True,exist_ok=True)
    data=preferences({'採掘':(group,members)})
    user=folder/f'core_user_{account}.dat'
    user.write_bytes(data)
    (folder/f'core_char_{character}.dat').write_bytes(profiles._EMPTY_SETTINGS)
    identity={**identity,'settingsFolder':str(main).lower()}
    manifest=root/hashlib.sha256(profiles._json_bytes(identity)).hexdigest()/'active.json'
    manifest.parent.mkdir(parents=True,exist_ok=True)
    files={'account.dat':data,'character.dat':profiles._EMPTY_SETTINGS}
    hashes={name:hashlib.sha256(value).hexdigest() for name,value in files.items()}
    revision=hashlib.sha256(profiles._json_bytes(hashes)).hexdigest()
    snapshot=manifest.parent/revision
    snapshot.mkdir(exist_ok=True)
    for name,value in files.items():
        (snapshot/name).write_bytes(value)
    manifest.write_bytes(profiles._json_bytes({'schemaVersion':1,'identity':identity,'hashes':hashes,'revision':revision}))
    if session is not None and active:
        pointer=base/'pilots'/f'{account}-{character}.json'
        pointer.parent.mkdir(exist_ok=True)
        pointer.write_text(json.dumps({'schemaVersion':1,'accountID':account,'characterID':character,'session':session}))
    return user


@pytest.fixture
def recovery_world(tmp_path, monkeypatch):
    monkeypatch.setattr('src.core.overview_patch.is_eve_client_running',lambda:False)
    root=tmp_path/'SharedProfiles'
    current=tmp_path/'current-server'
    old=tmp_path/'old-server'
    database=tmp_path/'gamestore.sqlite'
    con=sqlite3.connect(database)
    for table in ('accounts','characters','items'):
        con.execute(f'CREATE TABLE {table}(key TEXT PRIMARY KEY,json TEXT NOT NULL)')
    for account,character,ship in ((1,101,1001),(2,202,2002)):
        put(con,'accounts',f'pilot{account}',{'id':account})
        put(con,'characters',character,{'accountId':account,'characterName':f'Character {character}','shipID':ship})
        put(con,'items',ship,{'itemID':ship,'ownerID':character,'categoryID':6})
    for item,owner,ship,count in ((11,101,1001,-1),(12,101,1001,5),(21,202,2002,2)):
        put(con,'items',item,{'itemID':item,'ownerID':owner,'categoryID':18,'flagID':87,
                            'locationID':ship,'singleton':int(count==-1),'quantity':count,'stacksize':1 if count==-1 else count})
    con.commit()
    con.close()
    saved(root,current,[999],stable=SCOPE)
    saved(root,current,[998],stable=SCOPE,account=2,character=202)
    first=saved(root,old,[11,12])
    saved(root,old,[21],account=2,character=202)
    context=RecoveryContext(root,SCOPE,database,str(current),(root,))
    return context,old,first


def test_catalog_reads_only_exact_owned_drone_items_including_native_singletons(recovery_world):
    context,_,_=recovery_world
    before=context.database.read_bytes()
    con=sqlite3.connect(context.database)
    # Owned deployed/hangar items are legitimate preference facts, not launch permission.
    put(con,'items',13,{'itemID':13,'ownerID':101,'categoryID':18,'flagID':0,'locationID':900,'quantity':3})
    put(con,'items',14,{'itemID':14,'ownerID':True,'categoryID':18,'quantity':5})
    put(con,'items',15,{'itemID':15,'ownerID':101,'categoryID':20,'quantity':5})
    put(con,'items',16,{'itemID':16,'ownerID':101,'categoryID':18,'quantity':True})
    put(con,'items',17,{'itemID':17,'ownerID':101,'categoryID':18,'quantity':-1,'singleton':0})
    put(con,'characters',202,{'accountId':2,'characterName':'No active ship'})
    con.commit(); con.close()
    before=context.database.read_bytes()
    pilots=catalog.inventory_facts(context.database)
    assert dict(pilots[0].quantities)=={11:1,12:5,13:3}
    assert dict(pilots[0].bay_quantities)=={11:1,12:5}
    assert pilots[1].ship_id==0 and pilots[1].owned_ids=={21}
    assert context.database.read_bytes()==before


def test_batch_recovers_multiple_characters_without_crossing_ownership(recovery_world):
    context,old,first=recovery_world
    first.write_bytes(preferences({'採掘':('same-group',[11,12,21,404])}))
    scan=scan_recovery(context)
    world=next(w for w in scan.worlds if w.source_root==str(old).lower())
    assert len(world.characters)==2
    assert [row.drone_count for row in world.characters]==[6,2]
    assert memberships(world.characters[0].plan.candidate)=={'採掘':{999,11,12}}
    assert memberships(world.characters[1].plan.candidate)=={'採掘':{998,21}}
    originals={p:p.read_bytes() for p in context.storage_root.rglob('*.dat')}
    database=context.database.read_bytes()
    receipt=apply_recovery(scan,world.source_root)
    assert receipt=={'characters':2,'groups':2,'drones':8,'serverChanged':False,'historicalProfilesChanged':False}
    for row in world.characters:
        assert native.read_personal_settings(context.storage_root,SCOPE,row.pilot.account_id,
                                            row.pilot.character_id)==row.plan.candidate
    assert all(path.read_bytes()==data for path,data in originals.items())
    assert context.database.read_bytes()==database
    assert not any(w.characters for w in scan_recovery(context).worlds)


@pytest.mark.parametrize('change',['preferences','history','owner','quantity','database'])
def test_stale_review_is_rejected_before_creating_recovery_slots(recovery_world,change):
    context,old,first=recovery_world
    scan=scan_recovery(context)
    if change=='preferences':
        path=next(row.before_path for row in scan.worlds[0].characters)
        path.write_bytes(preferences({'採掘':('same-group',[777])}))
    elif change=='history':
        first.write_bytes(preferences({'採掘':('same-group',[11])}))
    elif change in ('owner','quantity'):
        con=sqlite3.connect(context.database)
        row=json.loads(con.execute("SELECT json FROM items WHERE key='12'").fetchone()[0])
        row['ownerID' if change=='owner' else 'quantity']=202 if change=='owner' else 4
        put(con,'items',12,row)
        con.commit(); con.close()
    else:
        replacement=context.database.with_suffix('.new')
        replacement.write_bytes(context.database.read_bytes())
        replacement.replace(context.database)
    with pytest.raises(RecoveryChangedError,match='Scan again'):
        apply_recovery(scan,str(old).lower())
    assert not (context.storage_root/'native-drones').exists()


def test_running_native_client_blocks_apply(recovery_world,monkeypatch):
    context,old,_=recovery_world
    scan=scan_recovery(context)
    monkeypatch.setattr('src.core.overview_patch.is_eve_client_running',lambda:True)
    with pytest.raises(SharedSettingsError,match='Close EVE clients'):
        apply_recovery(scan,str(old).lower())
    assert not (context.storage_root/'native-drones').exists()


def test_explicit_cleared_groups_remain_authoritative(recovery_world):
    context,_,_=recovery_world
    path=next(row.before_path for row in scan_recovery(context).worlds[0].characters if row.pilot.account_id==1)
    path.write_bytes(preferences({}))
    assert all(row.pilot.account_id!=1 for world in scan_recovery(context).worlds for row in world.characters)


def test_missing_current_personal_definitions_can_be_restored_in_one_batch(recovery_world):
    context,old,_=recovery_world
    # A new world has no attributed native file yet; only the explicitly reviewed old world does.
    from dataclasses import replace
    context=replace(context,world_root=str(Path(context.world_root).parent/'brand-new-world'),scope='installation-groups:'+'e'*64)
    scan=scan_recovery(context)
    rows=next(world.characters for world in scan.worlds if world.source_root==str(old).lower())
    assert len(rows)==2 and all(row.initializes_profile for row in rows)
    apply_recovery(scan,str(old).lower())
    assert memberships(native.read_personal_settings(context.storage_root,context.scope,1,101))=={'採掘':{11,12}}


def test_foreign_scope_and_docker_target_are_not_recovery_sources(recovery_world):
    context,old,_=recovery_world
    foreign=saved(context.storage_root,old,[11],stable='installation-groups:'+'d'*64)
    docker=saved(context.storage_root,old,[11],target='another-compose-world')
    pilots=catalog.inventory_facts(context.database)
    rows=catalog.saved_profiles(context.history_roots,SCOPE,context.world_root,pilots)
    assert len(rows)==4
    assert foreign not in {row.path for row in rows} and docker not in {row.path for row in rows}


def test_recovery_slot_is_consumed_by_the_normal_shared_launch(recovery_world,tmp_path,monkeypatch):
    context,old,_=recovery_world
    apply_recovery(scan_recovery(context),str(old).lower())
    source=profiles.SharedSettingsSource('pilot2',2,202,context.world_root)
    tq=tmp_path/'launcher/Profiles/pilot1/tq'
    tq.mkdir(parents=True)
    local=tmp_path/'local'
    local.mkdir()
    monkeypatch.setenv('LOCALAPPDATA',str(local))
    source_tq=tq.parent.parent/'pilot2/tq'
    source_tq.mkdir(parents=True)
    from src.core import platform
    settings=platform.get_eve_settings_path(str(source_tq))
    settings.mkdir(parents=True)
    (settings/'core_char_202.dat').write_bytes(profiles._EMPTY_SETTINGS)
    (settings/'core_user_2.dat').write_bytes(preferences({'Other source':('different',[21])},layout='new shared layout'))
    native.personal_settings_file(context.storage_root,SCOPE,2,202).write_bytes(
        preferences({'Other source':('different',[21])},layout='new shared layout'))
    main=profiles.shared_profile_main_folder(tq,source,evejs_root=context.world_root,
        storage_root=context.storage_root,storage_identity=SCOPE)
    (main/'core_user_2.dat').write_bytes(
        preferences({'Other source':('different',[21])},layout='new shared layout'))
    result=profiles.prepare_shared_profile_launch(tq,source,account_id=1,character_id=101,
        evejs_root=context.world_root,storage_root=context.storage_root,storage_identity=SCOPE)
    data=(result.folder/'core_user_1.dat').read_bytes()
    assert memberships(data)=={'採掘':{999,11,12}}
    assert native.personal_settings_file(context.storage_root,SCOPE,1,101)==result.folder/'core_user_1.dat'
    from src.core import shared_drone_settings as codec
    assert codec.get(codec.get(codec.loads(data),'ui'),'layout').children[1].text()=='new shared layout'


def test_archive_without_same_account_character_provenance_is_excluded(recovery_world):
    context,old,_=recovery_world
    wrong=saved(context.storage_root,old,[11],account=1,character=202,session='b'*32)
    paths={item.path for item in catalog.saved_profiles(context.history_roots,SCOPE,context.world_root,
                                                     catalog.inventory_facts(context.database))}
    assert wrong not in paths


def test_all_characters_recover_together_and_second_scan_is_idempotent(recovery_world):
    context,old,first=recovery_world
    before=first.read_bytes()
    facts=context.database.read_bytes()
    result=apply_recovery(scan_recovery(context),str(old).lower())
    assert result==dict(characters=2,groups=2,drones=8,serverChanged=False,historicalProfilesChanged=False)
    assert memberships(native.read_personal_settings(context.storage_root,SCOPE,1,101))=={'採掘':{999,11,12}}
    assert memberships(native.read_personal_settings(context.storage_root,SCOPE,2,202))=={'採掘':{998,21}}
    assert all(not world.characters for world in scan_recovery(context).worlds)
    assert first.read_bytes()==before and context.database.read_bytes()==facts


def test_batch_failure_rolls_back_all_personal_pointers(recovery_world,monkeypatch):
    context,old,_=recovery_world
    original=profiles._atomic_bytes
    failed=False
    def failure(path,data):
        nonlocal failed
        if path.name=='core_user_2.dat' and not failed:
            failed=True
            raise OSError('injected failure')
        return original(path,data)
    monkeypatch.setattr(profiles,'_atomic_bytes',failure)
    with pytest.raises(OSError,match='injected'):
        apply_recovery(scan_recovery(context),str(old).lower())
    assert native.read_personal_settings(context.storage_root,SCOPE,1,101)==preferences({'採掘':('same-group',[999])})
    assert native.read_personal_settings(context.storage_root,SCOPE,2,202)==preferences({'採掘':('same-group',[998])})
    store=context.storage_root/'native-drones'/hashlib.sha256(SCOPE.encode()).hexdigest()
    assert not (store/'1-101.json').exists() and not (store/'2-202.json').exists()


def test_current_malformed_profile_is_not_replaced_by_old_history(recovery_world):
    context,_,_=recovery_world
    path=native.personal_settings_file(context.storage_root,SCOPE,1,101)
    path.write_bytes(b'not native preference data')
    scan=scan_recovery(context)
    assert 101 in scan.blocked_characters
    assert all(row.pilot.character_id!=101 for world in scan.worlds for row in world.characters)
    assert path.read_bytes()==b'not native preference data'


def test_unscoped_current_corruption_cannot_be_mistaken_for_missing_settings(recovery_world):
    context,_,_=recovery_world
    from dataclasses import replace
    context=replace(context,scope='installation-groups:'+'e'*64)
    current=saved(context.storage_root,Path(context.world_root),[999])
    current.write_bytes(b'corrupt native droneBlah2 data')
    scan=scan_recovery(context)
    assert 101 in scan.blocked_characters
    assert all(row.pilot.character_id!=101 for world in scan.worlds for row in world.characters)


def test_recovery_keeps_independent_characters_on_the_same_account(recovery_world):
    context,old,_=recovery_world
    con=sqlite3.connect(context.database)
    put(con,'characters',102,{'accountId':1,'characterName':'Same account, other pilot'})
    put(con,'items',31,{'itemID':31,'ownerID':102,'categoryID':18,'quantity':1})
    con.commit(); con.close()
    saved(context.storage_root,old,[31],account=1,character=102,session='b'*32,group='other-pilot-group')
    apply_recovery(scan_recovery(context),str(old).lower())
    first=memberships(native.read_personal_settings(context.storage_root,SCOPE,1,101))
    other=memberships(native.read_personal_settings(context.storage_root,SCOPE,1,102))
    assert first=={'採掘':{999,11,12}}
    assert other=={'採掘':{31}}


def test_source_catalog_refuses_redirected_history_root(recovery_world,tmp_path):
    context,_,_=recovery_world
    from src.core.profiles import _ensure_profile_junction
    redirected=tmp_path/'redirected-history'
    _ensure_profile_junction(redirected,context.storage_root)
    try:
        with pytest.raises(SharedSettingsError,match='redirected'):
            catalog.saved_profiles((redirected,),SCOPE,context.world_root,catalog.inventory_facts(context.database))
    finally:
        # Remove only the junction entry, never its destination.
        import os
        os.rmdir(redirected)
