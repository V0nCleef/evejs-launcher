"""Safety and rollback tests for offline Native character creation."""
from __future__ import annotations

from contextlib import contextmanager
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
    normalize_character_name,
    normalize_creation_request,
)


@pytest.fixture(autouse=True)
def _hold_test_maintenance_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def hold(_root: Path, _store: Path):
        yield

    monkeypatch.setattr(creation, "hold_persistence_maintenance", hold)


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


def test_character_name_normalization_matches_javascript_protocol() -> None:
    assert normalize_character_name("\ufeff  Étoile\u00a0🚀  \ufeff") == "Étoile 🚀"
    assert normalize_character_name("Pilot\u200bName") == "Pilot\u200bName"
    assert normalize_character_name("Pilot\x7fName") is None
    assert normalize_character_name("🚀" * 18) == "🚀" * 18
    assert normalize_character_name("🚀" * 19) is None


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
        captured["payload"] = json.loads(kwargs["input"])
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
        {"maintenance": 4, "world": 9},
    )

    assert result["ok"] is True
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["EVEJS_GAMESTORE_OWNER_ROLE"] == "maintenance"
    assert os.environ["EVEJS_GAMESTORE_OWNER_ROLE"] == "reader"
    assert captured["payload"]["ownerCheckpoint"] == {
        "maintenance": 4,
        "world": 9,
    }


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


def test_creation_helper_marks_owner_conflict_as_pre_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=(
                'EVEJS_LAUNCHER_RESULT={"ok":false,"error":"OWNER_CONFLICT",'
                '"code":"PERSISTENCE_OWNER_CONFLICT","operationStarted":false}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(creation.subprocess, "run", fake_run)

    with pytest.raises(CharacterCreationError, match="OWNER_CONFLICT") as captured:
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

    assert captured.value.rollback_required is False


def test_pre_operation_helper_failure_does_not_restore_a_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _game_store = _store(tmp_path)
    backup = tmp_path / "backups" / "fixture"
    restored: list[Path] = []
    events: list[str] = []

    @contextmanager
    def hold(_root: Path, _store: Path):
        events.append("lease-enter")
        try:
            yield
        finally:
            events.append("lease-exit")

    monkeypatch.setattr(creation, "hold_persistence_maintenance", hold)
    monkeypatch.setattr(
        creation,
        "wait_for_persistence_owners",
        lambda _path: events.append("owners-clear"),
    )
    monkeypatch.setattr(
        creation,
        "_create_backup",
        lambda *_args: events.append("backup") or backup,
    )

    def fail_before_operation(*_args):
        events.append("helper")
        raise CharacterCreationError("OWNER_CONFLICT", rollback_required=False)

    monkeypatch.setattr(creation, "_run_helper", fail_before_operation)
    monkeypatch.setattr(
        creation,
        "_restore_backup",
        lambda _store, path: restored.append(path),
    )

    with pytest.raises(CharacterCreationError, match="OWNER_CONFLICT"):
        create_character(
            CharacterCreationRequest(
                str(root),
                "fixture-account",
                "Fixture Pilot",
                False,
                backup_root=tmp_path / "backups",
            )
        )

    assert events == [
        "owners-clear",
        "lease-enter",
        "backup",
        "lease-exit",
        "helper",
    ]
    assert restored == []

def test_create_character_verifies_persistence_and_retains_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, game_store = _store(tmp_path)

    def fake_helper(request, _root, _game_store, _owner_checkpoint):
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
    ownership_barriers: list[Path] = []
    lease_events: list[str] = []

    @contextmanager
    def hold(_root: Path, _store: Path):
        lease_events.append("enter")
        try:
            yield
        finally:
            lease_events.append("exit")

    def failing_helper(_request, _root, _game_store, _owner_checkpoint):
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

    monkeypatch.setattr(
        creation,
        "wait_for_persistence_owners",
        lambda path: ownership_barriers.append(path),
    )
    monkeypatch.setattr(creation, "hold_persistence_maintenance", hold)
    monkeypatch.setattr(
        creation,
        "persistence_owner_checkpoint",
        lambda _path: {"maintenance": 4},
    )
    monkeypatch.setattr(
        creation,
        "assert_persistence_owner_checkpoint",
        lambda *_args, **_kwargs: None,
    )
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
    assert ownership_barriers == [database, database]
    assert lease_events == ["enter", "exit", "enter", "exit"]


def test_stale_rollback_checkpoint_retains_backup_without_restoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _game_store = _store(tmp_path)
    backup = tmp_path / "backups" / "fixture"
    restored: list[Path] = []
    monkeypatch.setattr(creation, "persistence_owner_checkpoint", lambda _path: {"maintenance": 4, "world": 9})
    monkeypatch.setattr(creation, "_create_backup", lambda *_args: backup)
    monkeypatch.setattr(
        creation,
        "_run_helper",
        lambda *_args: (_ for _ in ()).throw(CharacterCreationError("WORK_FAILURE")),
    )
    monkeypatch.setattr(
        creation,
        "assert_persistence_owner_checkpoint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            creation.PersistenceMaintenanceError("ownership changed after the maintenance backup")
        ),
    )
    monkeypatch.setattr(creation, "_restore_backup", lambda _store, path: restored.append(path))

    with pytest.raises(CharacterCreationError, match="automatic rollback also failed"):
        create_character(
            CharacterCreationRequest(
                str(root),
                "fixture-account",
                "Fixture Pilot",
                False,
                backup_root=tmp_path / "backups",
            )
        )

    assert restored == []


def test_legacy_creation_failure_restores_its_scoped_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _game_store = _store(tmp_path)
    backup = tmp_path / "backups" / "fixture"
    restored: list[Path] = []
    monkeypatch.setattr(creation, "persistence_owner_checkpoint", lambda _path: None)
    monkeypatch.setattr(creation, "_create_backup", lambda *_args: backup)
    monkeypatch.setattr(
        creation,
        "_run_helper",
        lambda *_args: (_ for _ in ()).throw(CharacterCreationError("WORK_FAILURE")),
    )
    monkeypatch.setattr(creation, "_restore_backup", lambda _store, path: restored.append(path))

    with pytest.raises(CharacterCreationError, match="WORK_FAILURE"):
        create_character(
            CharacterCreationRequest(
                str(root),
                "fixture-account",
                "Fixture Pilot",
                False,
                backup_root=tmp_path / "backups",
            )
        )

    assert restored == [backup]
