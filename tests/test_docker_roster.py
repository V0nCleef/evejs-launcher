import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from src.core.runtime.data import DataSourceError, _map_roster_accounts
from src.core.runtime.character_roster import ROSTER_SCRIPT


def test_roster_requires_real_accounts_and_ignores_order_of_unmatched_rows():
    valid = dict(characterId="10", characterName="Pilot", accountId=1, accountName="real", banned=False, isGM=False)
    unmatched = dict(valid, characterId="11", accountId=99, accountName="Unknown account")
    for players in ([unmatched, valid], [valid, unmatched], [dict(unmatched, accountId=None), valid]):
        accounts = _map_roster_accounts({"accounts": [{"accountId": 1, "accountName": "real"}], "players": players})
        assert [(a.username, [c.char_id for c in a.characters]) for a in accounts] == [("real", [10])]
    assert _map_roster_accounts({"accounts": [], "players": [unmatched]}) == []
    with pytest.raises(DataSourceError):
        _map_roster_accounts({"players": [valid]})


def test_readonly_roster_script_uses_snapshot_join_and_never_requests_detail(tmp_path):
    database = tmp_path / "gamestore.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE accounts (key TEXT PRIMARY KEY, json TEXT)")
        db.execute("CREATE TABLE characters (key TEXT PRIMARY KEY, json TEXT)")
        db.execute("CREATE TABLE walletAuthorityState (key TEXT PRIMARY KEY, json TEXT)")
        db.execute("CREATE TABLE solarSystems (key TEXT PRIMARY KEY, json TEXT)")
        db.execute("INSERT INTO walletAuthorityState VALUES (?,?)",("character:10",'{"balance":123456}'))
        db.execute("INSERT INTO solarSystems VALUES (?,?)",("30000142",'{"solarSystemName":"Jita"}'))
        db.execute("INSERT INTO accounts VALUES (?, ?)", ("real", json.dumps({"id": 1, "role": "0", "password": "not-exported"})))
        for identity, name, account in ((10, "Zulu Pilot", 1), (11, "AAA Bot", 99), (12, "Retired", None)):
            db.execute("INSERT INTO characters VALUES (?, ?)", (str(identity), json.dumps({"characterName": name, "accountId": account,"solarSystemID":30000142,"balance":1})))
    before = database.read_bytes()
    # Exercise the real SQL/JSON projection against Node's built-in SQLite.
    # Production uses better-sqlite3 inside the selected server container.
    harness = r'''
const vm=require('node:vm'),fs=require('node:fs');
const {DatabaseSync}=require('node:sqlite');
const source=JSON.parse(fs.readFileSync(0,'utf8'));
class Database extends DatabaseSync {
 constructor(){super(process.argv[1],{readOnly:true});}
 pragma(sql){this.exec('PRAGMA '+sql);}
}
vm.runInNewContext(source,{require:id=>id.endsWith('better-sqlite3')?Database:require(id),process:{stdout:process.stdout,argv:['node']},console});
'''
    result = subprocess.run(["node", "-e", harness, str(database)], input=json.dumps(ROSTER_SCRIPT), text=True, capture_output=True, check=True, timeout=10)
    payload = json.loads(result.stdout)
    assert "not-exported" not in result.stdout
    assert [p["characterName"] for p in payload["players"]] == ["Zulu Pilot"]
    assert payload["skippedRecords"] == 2
    assert payload["players"][0]["balance"] == 123456 and payload["players"][0]["solarSystemName"] == "Jita"
    assert _map_roster_accounts(payload)[0].username == "real"
    assert database.read_bytes() == before
