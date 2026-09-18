import json
import sqlite3

import pytest

from src.core import db
from src.core.runtime.data import NativeDataSource, SqliteGameStoreDataSource, _map_display_values, DataSourceError


def make_store(tmp_path, authority=True):
    store = tmp_path / '_local/gameStore'
    store.mkdir(parents=True)
    with sqlite3.connect(store / 'gamestore.sqlite') as con:
        con.execute('CREATE TABLE accounts (key TEXT PRIMARY KEY, json TEXT)')
        con.execute('CREATE TABLE characters (key TEXT PRIMARY KEY, json TEXT)')
        con.execute('INSERT INTO accounts VALUES (?,?)', ('pilot', json.dumps({'id': 1})))
        con.execute('INSERT INTO characters VALUES (?,?)', ('42', json.dumps({'accountId':1, 'characterName':'Pilot', 'balance':1000000000, 'securityRating':0.0324, 'solarSystemID':30000142})))
        if authority:
            con.execute('CREATE TABLE walletAuthorityState (key TEXT PRIMARY KEY, json TEXT)')
            con.execute('INSERT INTO walletAuthorityState VALUES (?,?)', ('character:42', json.dumps({'balance':1234567.89})))
            con.execute('INSERT INTO walletAuthorityState VALUES (?,?)', ('corporation:42', json.dumps({'balance':999})))
    con.close()
    systems = store / 'data/solarSystems/data.json'
    systems.parent.mkdir(parents=True)
    systems.write_text(json.dumps({'solarSystems':[{'solarSystemID':30000142,'solarSystemName':'Jita','security':0.945}]}))
    return store


@pytest.mark.parametrize('docker_bind', [False, True])
def test_wallet_refresh_and_location_security_use_read_only_current_data(tmp_path, docker_bind):
    store = make_store(tmp_path)
    source = SqliteGameStoreDataSource(store) if docker_bind else NativeDataSource(str(tmp_path))
    character = source.load_accounts()[0].characters[0]
    assert character.isk == 1234567.89
    assert character.security_status == 0.0324
    assert character.location == 'Jita · 0.9'
    detail = source.get_character_detail(42)
    assert detail['balance'] == 1234567.89
    assert detail['securityStatus'] == 0.0324
    assert detail['solarSystemName'] == character.location
    # The authoritative balance changes while legacy character data stays stale.
    with sqlite3.connect(store / 'gamestore.sqlite') as con:
        con.execute("UPDATE walletAuthorityState SET json=? WHERE key='character:42'", (json.dumps({'balance':0}),))
    assert source.load_accounts()[0].characters[0].isk == 0
    assert source.get_character_detail(42)['balance'] == 0
    with sqlite3.connect(store / 'gamestore.sqlite') as con:
        assert json.loads(con.execute('SELECT json FROM characters').fetchone()[0])['balance'] == 1000000000


def test_legacy_database_and_invalid_authority_value_fall_back(tmp_path):
    store = make_store(tmp_path, authority=False)
    source = SqliteGameStoreDataSource(store)
    assert source.load_accounts()[0].characters[0].isk == 1000000000
    with sqlite3.connect(store / 'gamestore.sqlite') as con:
        con.execute('CREATE TABLE walletAuthorityState (key TEXT PRIMARY KEY, json TEXT)')
        con.execute('INSERT INTO walletAuthorityState VALUES (?,?)', ('character:42', '{"balance":null}'))
    assert source.get_character_detail(42)['balance'] == 1000000000
    assert db._security_status({'securityStatus':0,'securityRating':5}) == 0
    assert db._location_label('Wormhole', -1.0) == 'Wormhole · -1.0'


def test_docker_projection_keeps_only_display_fields_and_preserves_zero():
    result = _map_display_values({'players':[{'characterId':'42','balance':0,'securityStatus':0.03,'solarSystemName':'Jita','solarSystemSecurity':0.945,'secret':'discard'}]})
    assert result == {42:{'balance':0,'securityStatus':0.03,'solarSystemName':'Jita · 0.9'}}
    with pytest.raises(DataSourceError):
        _map_display_values({'players':[{'characterId':42,'balance':True}]})
