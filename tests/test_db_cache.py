"""Tests for root-dependent database lookup cache invalidation."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.core import db


def test_shared_save_reads_authoritative_balance_without_wallet_view(tmp_path):
    path = tmp_path / "gamestore.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript("""
            PRAGMA application_id = 1163282244;
            PRAGMA user_version = 35;
            CREATE TABLE account_records(key TEXT, json TEXT);
            CREATE VIEW accounts AS SELECT * FROM account_records;
            CREATE TABLE character_records(key TEXT, json TEXT);
            CREATE VIEW characters AS SELECT * FROM character_records;
            CREATE TABLE character_wallets(character_id INTEGER PRIMARY KEY, balance_cents INTEGER);
            CREATE VIEW walletAuthorityState AS SELECT bogus() AS json;
            INSERT INTO account_records VALUES ('fixture', '{"id":1}');
            INSERT INTO character_records VALUES ('2', '{"accountId":1,"characterName":"Pilot","balance":9}');
            INSERT INTO character_wallets VALUES (2, 54321);
        """)

    assert db.load_accounts_from_game_store(tmp_path)[0].characters[0].isk == 543.21
    assert db.get_character_detail_from_game_store(tmp_path, 2)["balance"] == 543.21


def _write_solar_system_data(root: Path, name: str) -> None:
    path = root / "_local" / "gameStore" / "data" / "solarSystems" / "data.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "solarSystems": [
                    {"solarSystemID": 30000001, "solarSystemName": name}
                ]
            }
        ),
        encoding="utf-8",
    )


def test_clear_solar_system_name_cache_allows_a_new_root_to_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_solar_system_data(first_root, "First System")
    _write_solar_system_data(second_root, "Second System")
    monkeypatch.setattr(db, "_SOLAR_SYSTEM_NAMES", None)

    assert db._load_solar_system_names(str(first_root))[30000001] == "First System"

    db.clear_solar_system_name_cache()

    assert db._load_solar_system_names(str(second_root))[30000001] == "Second System"


def test_solar_system_cache_isolated_by_root_without_global_clear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_solar_system_data(first_root, "First System")
    _write_solar_system_data(second_root, "Second System")
    monkeypatch.setattr(db, "_SOLAR_SYSTEM_NAMES", None)

    assert db._load_solar_system_names(str(first_root))[30000001] == "First System"
    assert db._load_solar_system_names(str(second_root))[30000001] == "Second System"
    assert db._load_solar_system_names(str(first_root))[30000001] == "First System"
