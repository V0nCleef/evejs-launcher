"""Reporter-shaped controls: 11 real account keys and 12 characters."""
import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from src.core.db import Account, load_accounts_from_game_store
from src.core.runtime.character_roster import ROSTER_SCRIPT
from src.core.runtime.data import DataSourceError, _map_roster_accounts
from test_roster_recovery_diagnostics import source


def reported_shape(pair=("Pilot", "pilot")):
    names = [*pair, *(f"fixture-{i}" for i in range(3, 12))]
    accounts = [dict(accountId=i+1, accountName=name, banned=False, isGM=False)
                for i, name in enumerate(names)]
    players = [dict(characterId=str(140000001+i), characterName=f"Fixture {i+1}", **accounts[i % 11])
               for i in range(12)]
    return dict(accounts=accounts, players=players)


@pytest.mark.parametrize("pair", [("Pilot", "pilot"), ("Straße", "STRASSE"), ("pilot", " pilot ")])
def test_exact_account_keys_preserve_all_reported_accounts_and_characters(tmp_path, pair):
    accounts = source(tmp_path, roster=reported_shape(pair)).load_accounts()
    assert len(accounts) == 11 and sum(len(a.characters) for a in accounts) == 12
    assert {a.username for a in accounts} == {a["accountName"] for a in reported_shape(pair)["accounts"]}
    assert next(a for a in accounts if a.account_id == 1).username == pair[0]
    assert next(a for a in accounts if a.account_id == 2).username == pair[1]
    assert [c.char_id for c in next(a for a in accounts if a.account_id == 1).characters] == [140000001, 140000012]
    assert [c.char_id for c in next(a for a in accounts if a.account_id == 2).characters] == [140000002]


@pytest.mark.parametrize("field", ["accountName", "accountId"])
def test_actual_duplicate_identity_still_fails_closed(field):
    exported = reported_shape()
    exported["accounts"][1][field] = exported["accounts"][0][field]
    with pytest.raises(DataSourceError) as failure:
        _map_roster_accounts(exported)
    assert failure.value.diagnostic.reason == "duplicate_identity"
    assert failure.value.diagnostic.field == field


def test_real_readonly_sql_projection_matches_native_exact_key_reader(tmp_path):
    exported = reported_shape()
    database = tmp_path/"gamestore.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE accounts (key TEXT PRIMARY KEY, json TEXT NOT NULL)")
        db.execute("CREATE TABLE characters (key TEXT PRIMARY KEY, json TEXT NOT NULL)")
        for account in exported["accounts"]:
            db.execute("INSERT INTO accounts VALUES (?,?)",
                       (account["accountName"], json.dumps(dict(id=account["accountId"], role="0", password="private-fixture"))))
        for player in exported["players"]:
            db.execute("INSERT INTO characters VALUES (?,?)",
                       (player["characterId"], json.dumps(player)))
    before = database.read_bytes()
    harness = r'''
const vm=require('node:vm'),fs=require('node:fs');
const {DatabaseSync}=require('node:sqlite');
class Database extends DatabaseSync {
 constructor(){super(process.argv[1],{readOnly:true});}
 pragma(sql){this.exec('PRAGMA '+sql);}
}
vm.runInNewContext(JSON.parse(fs.readFileSync(0,'utf8')), {
 require:id=>id.endsWith('better-sqlite3')?Database:require(id),
 process:{stdout:process.stdout,argv:['node']},console
});
'''
    result = subprocess.run(["node","-e",harness,str(database)], input=json.dumps(ROSTER_SCRIPT),
                            capture_output=True, text=True, check=True, timeout=10)
    assert "private-fixture" not in result.stdout
    docker = _map_roster_accounts(json.loads(result.stdout))
    native = load_accounts_from_game_store(tmp_path)
    project = lambda accounts: {a.username:(a.account_id, sorted(c.char_id for c in a.characters)) for a in accounts}
    assert project(docker) == project(native)
    assert len(docker) == 11 and sum(len(a.characters) for a in docker) == 12
    assert database.read_bytes() == before


def test_windows_equivalent_names_get_distinct_stable_profile_roots(tmp_path):
    from src.core.profiles import account_profiles_root, get_settings_key
    base = tmp_path/"Profiles"
    names = ["Pilot", "pilot", "other"]
    first = account_profiles_root(base, names[0], names)
    second = account_profiles_root(base, names[1], names)
    assert first != second and first.is_relative_to(tmp_path) and second.is_relative_to(tmp_path)
    assert get_settings_key(str(first/"Pilot/tq")) != get_settings_key(str(second/"pilot/tq"))
    assert account_profiles_root(base, "other", names) == base
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    import os
    assert not os.path.samefile(first, second)
    # Removing the other account must not send this one back to its old alias.
    assert account_profiles_root(base, "Pilot", ["Pilot"]) == first


def test_launch_request_preserves_exact_username_account_and_profile_scope(tmp_path, monkeypatch):
    from src import app as application
    from src.app import MainWindow
    from src.core.launcher import ClientLaunchContext
    monkeypatch.setattr(application, "PROFILES_ROOT", tmp_path/"Profiles")
    client = tmp_path/"client"; client.mkdir()
    window = SimpleNamespace(
        _cfg=dict(evejs_root=str(tmp_path/"world"),client_path=str(client)),
        _resolve_client_launch_context=lambda:(ClientLaunchContext.native(),""),
        _resolve_configured_client_path=lambda *_:client,
        _tracker=SimpleNamespace(is_account_running=lambda _:False),
        _pending_client_launches=set(), _docker_mode=lambda:False,
        _accounts=[Account("Pilot",1,"0",False),Account("pilot",2,"0",False)])
    requests = [MainWindow._make_client_launch_request(window,name,"") for name in ("Pilot","pilot")]
    assert [r.username for r in requests] == ["Pilot","pilot"]
    assert [r.account_id for r in requests] == [1,2]
    assert requests[0].profiles_root != requests[1].profiles_root


@pytest.mark.parametrize("name", ["\nPilot", "Pilot\t", "Pilot" + " " * 256, "Pilot\ud800"])
def test_preserving_exact_keys_does_not_accept_controls_or_oversized_names(name):
    exported = reported_shape()
    exported["accounts"][0]["accountName"] = name
    with pytest.raises(DataSourceError):
        _map_roster_accounts(exported)
