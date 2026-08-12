"""Safety and rollback tests for offline Native character creation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from src.core import character_creation as creation
from src.core.character_creation import (
    CharacterCreationError,
    CharacterCreationRequest,
    create_character,
    normalize_creation_request,
)


def _store(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "evejs"
    game_store = root / "_local" / "gameStore"
    data = game_store / "data"
    data.mkdir(parents=True)
    database = game_store / "gamestore.sqlite"
    con = sqlite3.connect(database)
    try:
        for table in creation._MUTATED_TABLES:
            con.execute(
                f'CREATE TABLE "{table}" (key TEXT PRIMARY KEY, json TEXT NOT NULL)'
            )
            con.execute(
                f'INSERT INTO "{table}"(key, json) VALUES (?, ?)',
                ("fixture", json.dumps({"table": table, "value": "original"})),
            )
            table_dir = data / table
            table_dir.mkdir()
            (table_dir / "data.json").write_text(
                json.dumps({"table": table, "value": "original"}),
                encoding="utf-8",
            )
        con.commit()
    finally:
        con.close()
    (game_store / "manifest.json").write_text("original manifest", encoding="utf-8")
    return root, game_store


def test_creation_request_validation_is_local_and_strict(tmp_path: Path) -> None:
    root, _store_path = _store(tmp_path)
    normalized = normalize_creation_request(
        CharacterCreationRequest(
            str(root),
            "fixture-account",
            "  Fixture   Pilot  ",
            False,
        )
    )
    assert normalized.character_name == "Fixture Pilot"

    with pytest.raises(CharacterCreationError, match="Account names"):
        normalize_creation_request(
            CharacterCreationRequest(str(root), "bad/account", "Fixture Pilot", False)
        )


def test_creation_helper_runs_as_offline_maintenance_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setenv("EVEJS_GAMESTORE_OWNER_ROLE", "reader")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                'EVEJS_LAUNCHER_RESULT={"ok":true,"accountId":7,'
                '"characterId":140000007,"rookieShipVerified":true}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(creation.subprocess, "run", fake_run)

    result = creation._run_helper(
        CharacterCreationRequest(
            str(root),
            "fixture-account",
            "Fixture Pilot",
            False,
        ),
        root,
        game_store,
    )

    assert result["ok"] is True
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["EVEJS_GAMESTORE_OWNER_ROLE"] == "maintenance"
    assert os.environ["EVEJS_GAMESTORE_OWNER_ROLE"] == "reader"


def test_creation_helper_reports_work_and_shutdown_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                'EVEJS_LAUNCHER_RESULT={"ok":false,"error":"WORK_FAILURE",'
                '"shutdownError":"CLEANUP_FAILURE"}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(creation.subprocess, "run", fake_run)

    with pytest.raises(
        CharacterCreationError,
        match="WORK_FAILURE; GameStore shutdown also failed: CLEANUP_FAILURE",
    ):
        creation._run_helper(
            CharacterCreationRequest(
                str(root),
                "fixture-account",
                "Fixture Pilot",
                False,
            ),
            root,
            game_store,
        )


def test_create_character_verifies_persistence_and_retains_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)

    def fake_helper(request, _root, _game_store):
        con = sqlite3.connect(game_store / "gamestore.sqlite")
        try:
            con.execute(
                'INSERT INTO "accounts"(key, json) VALUES (?, ?)',
                (
                    request.username,
                    json.dumps({"id": 7, "isGM": False, "banned": False}),
                ),
            )
            con.execute(
                'INSERT INTO "characters"(key, json) VALUES (?, ?)',
                (
                    "140000007",
                    json.dumps(
                        {
                            "accountId": 7,
                            "characterName": request.character_name,
                            "shipID": 1990000007,
                        }
                    ),
                ),
            )
            con.execute(
                'INSERT INTO "items"(key, json) VALUES (?, ?)',
                ("1990000007", json.dumps({"ownerID": 140000007})),
            )
            con.commit()
        finally:
            con.close()
        return {
            "ok": True,
            "accountId": 7,
            "characterId": 140000007,
            "rookieShipVerified": True,
        }

    monkeypatch.setattr(creation, "_run_helper", fake_helper)
    result = create_character(
        CharacterCreationRequest(
            str(root),
            "fixture-account",
            "Fixture Pilot",
            False,
            backup_root=tmp_path / "backups",
        )
    )

    assert result.account_id == 7
    assert result.character_id == 140000007
    assert (result.backup_path / "tables.json").is_file()
    assert (result.backup_path / "metadata.json").is_file()


def test_failed_creation_restores_tables_data_files_and_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)
    database = game_store / "gamestore.sqlite"

    def failing_helper(_request, _root, _game_store):
        con = sqlite3.connect(database)
        try:
            con.execute('DELETE FROM "accounts"')
            con.execute(
                'INSERT INTO "accounts"(key, json) VALUES (?, ?)',
                ("partial", "{}"),
            )
            con.commit()
        finally:
            con.close()
        (game_store / "data" / "accounts" / "data.json").write_text(
            "partial", encoding="utf-8"
        )
        (game_store / "manifest.json").write_text("partial", encoding="utf-8")
        raise CharacterCreationError("simulated helper failure")

    monkeypatch.setattr(creation, "_run_helper", failing_helper)
    with pytest.raises(CharacterCreationError, match="simulated helper failure"):
        create_character(
            CharacterCreationRequest(
                str(root),
                "fixture-account",
                "Fixture Pilot",
                False,
                backup_root=tmp_path / "backups",
            )
        )

    con = sqlite3.connect(database)
    try:
        rows = con.execute('SELECT key, json FROM "accounts"').fetchall()
    finally:
        con.close()
    assert rows == [
        (
            "fixture",
            json.dumps({"table": "accounts", "value": "original"}),
        )
    ]
    assert json.loads(
        (game_store / "data" / "accounts" / "data.json").read_text(
            encoding="utf-8"
        )
    )["value"] == "original"
    assert (game_store / "manifest.json").read_text(encoding="utf-8") == "original manifest"
