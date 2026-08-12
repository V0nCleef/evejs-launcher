"""Safety, verification, and rollback tests for Native EveJS deletion."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from src.core import character_deletion as deletion
from src.core.character_deletion import (
    CharacterDeletionError,
    CharacterDeletionRequest,
    CharacterDeletionScope,
    delete_character_or_account,
    normalize_deletion_request,
)


def _record(account_id: int, name: str, *, character_id: int) -> dict:
    return {
        "accountId": account_id,
        "characterName": name,
        "shipID": character_id + 1_000_000,
        "corporationID": 1000169,
    }


def _store(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "evejs"
    game_store = root / "_local" / "gameStore"
    data = game_store / "data"
    data.mkdir(parents=True)
    database = game_store / "gamestore.sqlite"
    connection = sqlite3.connect(database)
    try:
        for table in deletion._MUTATED_TABLES:
            connection.execute(
                f'CREATE TABLE "{table}" (key TEXT PRIMARY KEY, json TEXT NOT NULL)'
            )
            table_dir = data / table
            table_dir.mkdir()
            (table_dir / "data.json").write_text(
                json.dumps({"table": table, "value": "original"}),
                encoding="utf-8",
            )
        connection.execute(
            'INSERT INTO "accounts"(key, json) VALUES (?, ?)',
            (
                "fixture-account",
                json.dumps({"id": 7, "isGM": False, "banned": False}),
            ),
        )
        connection.execute(
            'INSERT INTO "characters"(key, json) VALUES (?, ?)',
            ("140000007", json.dumps(_record(7, "Fixture One", character_id=140000007))),
        )
        connection.execute(
            'INSERT INTO "characters"(key, json) VALUES (?, ?)',
            ("140000008", json.dumps(_record(7, "Fixture Two", character_id=140000008))),
        )
        connection.commit()
    finally:
        connection.close()
    (game_store / "manifest.json").write_text("original manifest", encoding="utf-8")
    runtime_portraits = game_store / "images" / "Character"
    runtime_portraits.mkdir(parents=True)
    (runtime_portraits / "140000007_128.jpg").write_bytes(b"runtime portrait")
    legacy_portraits = (
        root
        / "server"
        / "src"
        / "_secondary"
        / "image"
        / "generated"
        / "Character"
    )
    legacy_portraits.mkdir(parents=True)
    (legacy_portraits / "140000007_64.png").write_bytes(b"legacy portrait")
    return root, game_store


def _request(
    root: Path,
    *,
    scope: CharacterDeletionScope = CharacterDeletionScope.CHARACTER,
) -> CharacterDeletionRequest:
    return CharacterDeletionRequest(
        str(root),
        "fixture-account",
        7,
        140000007,
        "Fixture One",
        scope,
    )


def _mark_deleted(
    game_store: Path,
    character_ids: tuple[int, ...],
    *,
    delete_account: bool,
) -> None:
    database = game_store / "gamestore.sqlite"
    connection = sqlite3.connect(database)
    try:
        for character_id in character_ids:
            row = connection.execute(
                'SELECT json FROM "characters" WHERE key = ?',
                (str(character_id),),
            ).fetchone()
            record = json.loads(row[0])
            record.update(
                {
                    "accountId": None,
                    "isDeleted": True,
                    "deletedByAccountId": 7,
                }
            )
            connection.execute(
                'UPDATE "characters" SET json = ? WHERE key = ?',
                (json.dumps(record), str(character_id)),
            )
        if delete_account:
            connection.execute(
                'DELETE FROM "accounts" WHERE key = ?',
                ("fixture-account",),
            )
        connection.commit()
    finally:
        connection.close()


def test_deletion_request_validation_is_exact_and_typed(tmp_path: Path) -> None:
    root, _game_store = _store(tmp_path)
    normalized = normalize_deletion_request(_request(root))

    assert normalized.scope is CharacterDeletionScope.CHARACTER
    assert normalized.character_id == 140000007

    with pytest.raises(CharacterDeletionError, match="account target"):
        normalize_deletion_request(
            CharacterDeletionRequest(
                str(root),
                " fixture-account",
                7,
                140000007,
                "Fixture One",
                CharacterDeletionScope.CHARACTER,
            )
        )


def test_deletion_helper_runs_as_offline_maintenance_owner(
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
                'EVEJS_LAUNCHER_RESULT={"ok":true,"accountDeleted":true,'
                '"deletedCharacters":[{"characterID":140000007,'
                '"characterName":"Fixture One"}]}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(deletion.subprocess, "run", fake_run)

    result = deletion._run_helper(
        _request(root, scope=CharacterDeletionScope.ACCOUNT),
        root,
        game_store,
    )

    assert result["ok"] is True
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["EVEJS_GAMESTORE_OWNER_ROLE"] == "maintenance"
    assert os.environ["EVEJS_GAMESTORE_OWNER_ROLE"] == "reader"


def test_deletion_helper_reports_work_and_shutdown_failures(
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

    monkeypatch.setattr(deletion.subprocess, "run", fake_run)

    with pytest.raises(
        CharacterDeletionError,
        match="WORK_FAILURE; GameStore shutdown also failed: CLEANUP_FAILURE",
    ):
        deletion._run_helper(
            _request(root, scope=CharacterDeletionScope.ACCOUNT),
            root,
            game_store,
        )


def test_delete_character_retains_account_verifies_and_keeps_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)

    def fake_helper(request, _root, _game_store):
        _mark_deleted(game_store, (request.character_id,), delete_account=False)
        return {
            "ok": True,
            "accountDeleted": False,
            "deletedCharacters": [
                {
                    "characterID": request.character_id,
                    "characterName": request.character_name,
                }
            ],
        }

    monkeypatch.setattr(deletion, "_run_helper", fake_helper)
    request = _request(root)
    request = CharacterDeletionRequest(
        **{**request.__dict__, "backup_root": tmp_path / "backups"}
    )
    result = delete_character_or_account(request)

    assert result.account_deleted is False
    assert result.deleted_character_ids == (140000007,)
    assert (result.backup_path / "tables.json").is_file()
    connection = sqlite3.connect(game_store / "gamestore.sqlite")
    try:
        assert connection.execute(
            'SELECT 1 FROM "accounts" WHERE key = ?', ("fixture-account",)
        ).fetchone()
        retained = json.loads(
            connection.execute(
                'SELECT json FROM "characters" WHERE key = ?', ("140000008",)
            ).fetchone()[0]
        )
    finally:
        connection.close()
    assert retained["accountId"] == 7


def test_delete_account_deletes_every_active_character(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)

    def fake_helper(_request, _root, _game_store):
        _mark_deleted(game_store, (140000007, 140000008), delete_account=True)
        return {
            "ok": True,
            "accountDeleted": True,
            "deletedCharacters": [
                {"characterID": 140000007, "characterName": "Fixture One"},
                {"characterID": 140000008, "characterName": "Fixture Two"},
            ],
        }

    monkeypatch.setattr(deletion, "_run_helper", fake_helper)
    request = _request(root, scope=CharacterDeletionScope.ACCOUNT)
    request = CharacterDeletionRequest(
        **{**request.__dict__, "backup_root": tmp_path / "backups"}
    )
    result = delete_character_or_account(request)

    assert result.account_deleted is True
    assert result.deleted_character_ids == (140000007, 140000008)


def test_failed_deletion_restores_tables_files_manifest_and_portraits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)
    runtime_portrait = game_store / "images" / "Character" / "140000007_128.jpg"
    legacy_portrait = (
        root
        / "server"
        / "src"
        / "_secondary"
        / "image"
        / "generated"
        / "Character"
        / "140000007_64.png"
    )

    def failing_helper(_request, _root, _game_store):
        connection = sqlite3.connect(game_store / "gamestore.sqlite")
        try:
            connection.execute('DELETE FROM "accounts"')
            connection.execute('DELETE FROM "characters"')
            connection.commit()
        finally:
            connection.close()
        (game_store / "data" / "accounts" / "data.json").write_text(
            "partial", encoding="utf-8"
        )
        (game_store / "manifest.json").write_text("partial", encoding="utf-8")
        runtime_portrait.unlink()
        legacy_portrait.unlink()
        raise CharacterDeletionError("simulated helper failure")

    monkeypatch.setattr(deletion, "_run_helper", failing_helper)
    request = _request(root)
    request = CharacterDeletionRequest(
        **{**request.__dict__, "backup_root": tmp_path / "backups"}
    )
    with pytest.raises(CharacterDeletionError, match="simulated helper failure"):
        delete_character_or_account(request)

    connection = sqlite3.connect(game_store / "gamestore.sqlite")
    try:
        assert connection.execute('SELECT count(*) FROM "accounts"').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM "characters"').fetchone()[0] == 2
    finally:
        connection.close()
    assert json.loads(
        (game_store / "data" / "accounts" / "data.json").read_text(
            encoding="utf-8"
        )
    )["value"] == "original"
    assert (game_store / "manifest.json").read_text(encoding="utf-8") == "original manifest"
    assert runtime_portrait.read_bytes() == b"runtime portrait"
    assert legacy_portrait.read_bytes() == b"legacy portrait"
