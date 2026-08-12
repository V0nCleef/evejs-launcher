"""Offline, verified account and character creation for a Native EveJS store."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from typing import Any
import uuid

from .client_autologin import LOCAL_DUMMY_PASSWORD
from .platform import get_hidden_process_flags


_RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="
_MUTATED_TABLES = (
    "accounts",
    "characters",
    "identityState",
    "items",
    "skills",
    "mail",
    "notifications",
)
_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")


class CharacterCreationError(RuntimeError):
    """Raised when an offline creation operation cannot be completed safely."""


@dataclass(frozen=True)
class CharacterCreationRequest:
    evejs_root: str
    username: str
    character_name: str
    is_gm: bool
    overview_source_character_id: int | None = None
    backup_root: Path | None = None


@dataclass(frozen=True)
class CharacterCreationResult:
    request: CharacterCreationRequest
    account_id: int
    character_id: int
    backup_path: Path


def normalize_creation_request(
    request: CharacterCreationRequest,
) -> CharacterCreationRequest:
    """Validate and normalize the values sent to EveJS internals."""
    root = Path(request.evejs_root).expanduser()
    username = request.username.strip()
    character_name = " ".join(request.character_name.strip().split())
    if not root.is_dir():
        raise CharacterCreationError("Select a valid EveJS root first.")
    if not _ACCOUNT_PATTERN.fullmatch(username):
        raise CharacterCreationError(
            "Account names must be 3-32 characters and use only letters, "
            "numbers, dots, dashes, or underscores."
        )
    if not 3 <= len(character_name) <= 37:
        raise CharacterCreationError(
            "Character names must contain between 3 and 37 characters."
        )
    if any(ord(character) < 32 for character in character_name):
        raise CharacterCreationError("The character name contains invalid characters.")
    source_id = request.overview_source_character_id
    if source_id is not None and (
        isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0
    ):
        raise CharacterCreationError("The overview source character is invalid.")
    return CharacterCreationRequest(
        evejs_root=str(root.resolve()),
        username=username,
        character_name=character_name,
        is_gm=bool(request.is_gm),
        overview_source_character_id=source_id,
        backup_root=request.backup_root,
    )


def _helper_path() -> Path:
    return Path(__file__).resolve().parent / "helpers" / "create_character.js"


def _game_store(root: Path) -> Path:
    return root / "_local" / "gameStore"


def _require_store_layout(game_store: Path) -> tuple[Path, Path]:
    database_path = game_store / "gamestore.sqlite"
    data_path = game_store / "data"
    if not database_path.is_file() or not data_path.is_dir():
        raise CharacterCreationError(
            "The Native EveJS game store could not be found at the configured root."
        )
    helper = _helper_path()
    if not helper.is_file():
        raise CharacterCreationError("The bundled character creation helper is missing.")
    return database_path, data_path


def _default_backup_root() -> Path:
    from ..config import CONFIG_DIR

    return CONFIG_DIR / "backups" / "character_creation"


def _create_backup(
    game_store: Path,
    database_path: Path,
    data_path: Path,
    backup_root: Path,
) -> Path:
    """Snapshot only the tables this operation is allowed to mutate."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
    backup_path.mkdir(parents=True, exist_ok=False)

    con = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        quick_check = con.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise CharacterCreationError("The EveJS database did not pass quick_check.")
        existing_tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        tables: dict[str, dict[str, Any]] = {}
        for table in _MUTATED_TABLES:
            if table not in existing_tables:
                raise CharacterCreationError(
                    f'The EveJS game store is missing the required "{table}" table.'
                )
            rows = con.execute(f'SELECT key, json FROM "{table}"').fetchall()
            tables[table] = {"rows": rows}
    finally:
        con.close()

    (backup_path / "tables.json").write_text(
        json.dumps(tables, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    files_root = backup_path / "files"
    files_root.mkdir()
    present_data_dirs: list[str] = []
    for table in _MUTATED_TABLES:
        source = data_path / table
        if source.is_dir():
            shutil.copytree(source, files_root / table)
            present_data_dirs.append(table)
    manifest = game_store / "manifest.json"
    manifest_present = manifest.is_file()
    if manifest_present:
        shutil.copy2(manifest, files_root / "manifest.json")
    (backup_path / "metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tables": list(_MUTATED_TABLES),
                "dataDirectories": present_data_dirs,
                "manifestPresent": manifest_present,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return backup_path


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _restore_backup(game_store: Path, backup_path: Path) -> None:
    """Restore the exact mutation allowlist after a failed helper run."""
    database_path = game_store / "gamestore.sqlite"
    data_path = game_store / "data"
    tables = json.loads((backup_path / "tables.json").read_text(encoding="utf-8"))
    metadata = json.loads((backup_path / "metadata.json").read_text(encoding="utf-8"))

    con = sqlite3.connect(database_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        for table in _MUTATED_TABLES:
            rows = tables[table]["rows"]
            con.execute(f'DELETE FROM "{table}"')
            con.executemany(
                f'INSERT INTO "{table}"(key, json) VALUES (?, ?)',
                rows,
            )
        con.commit()
        check = con.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CharacterCreationError("Rollback completed but quick_check failed.")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    files_root = backup_path / "files"
    originally_present = set(metadata.get("dataDirectories", []))
    for table in _MUTATED_TABLES:
        target = data_path / table
        if not _is_inside(target, data_path):
            raise CharacterCreationError("Refusing an unsafe rollback target.")
        if target.exists():
            shutil.rmtree(target)
        source = files_root / table
        if table in originally_present and source.is_dir():
            shutil.copytree(source, target)

    manifest = game_store / "manifest.json"
    manifest_backup = files_root / "manifest.json"
    if metadata.get("manifestPresent") and manifest_backup.is_file():
        shutil.copy2(manifest_backup, manifest)
    elif manifest.exists():
        manifest.unlink()


def _parse_helper_result(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            payload = json.loads(line[len(_RESULT_PREFIX) :])
            if isinstance(payload, dict):
                return payload
    raise CharacterCreationError(
        "The EveJS helper did not return a verifiable creation result."
    )


def _run_helper(request: CharacterCreationRequest, root: Path, game_store: Path) -> dict:
    env = os.environ.copy()
    env["EVEJS_GAMESTORE_SQLITE_PATH"] = str(game_store / "gamestore.sqlite")
    env["EVEJS_GAMESTORE_DATA_DIR"] = str(game_store / "data")
    env["EVEJS_GAMESTORE_OWNER_ROLE"] = "maintenance"
    payload = json.dumps(
        {
            "username": request.username,
            "characterName": request.character_name,
            "isGM": request.is_gm,
            "password": LOCAL_DUMMY_PASSWORD,
        }
    )
    try:
        completed = subprocess.run(
            ["node", str(_helper_path())],
            cwd=str(root),
            env=env,
            input=payload,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=180,
            check=False,
            **get_hidden_process_flags(),
        )
    except FileNotFoundError as exc:
        raise CharacterCreationError(
            "Node.js was not found. Install Node.js or add it to PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CharacterCreationError(
            "EveJS did not finish creating the character within three minutes."
        ) from exc
    result = _parse_helper_result(completed.stdout)
    if completed.returncode != 0 or result.get("ok") is not True:
        message = str(result.get("error") or completed.stderr).strip()
        shutdown_message = str(result.get("shutdownError") or "").strip()
        if shutdown_message:
            message = (
                f"{message}; GameStore shutdown also failed: {shutdown_message}"
                if message
                else f"GameStore shutdown failed: {shutdown_message}"
            )
        raise CharacterCreationError(message or "EveJS character creation failed.")
    return result


def _verify_result(
    database_path: Path,
    request: CharacterCreationRequest,
    helper_result: dict,
) -> tuple[int, int]:
    account_id = helper_result.get("accountId")
    character_id = helper_result.get("characterId")
    if (
        isinstance(account_id, bool)
        or not isinstance(account_id, int)
        or account_id <= 0
        or isinstance(character_id, bool)
        or not isinstance(character_id, int)
        or character_id <= 0
    ):
        raise CharacterCreationError("EveJS returned invalid account or character IDs.")

    con = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        account_row = con.execute(
            'SELECT json FROM "accounts" WHERE key = ?', (request.username,)
        ).fetchone()
        character_row = con.execute(
            'SELECT json FROM "characters" WHERE key = ?', (str(character_id),)
        ).fetchone()
        check = con.execute("PRAGMA quick_check").fetchone()
    finally:
        con.close()
    if account_row is None or character_row is None:
        raise CharacterCreationError("The created account was not persisted completely.")
    account = json.loads(account_row[0])
    character = json.loads(character_row[0])
    if (
        int(account.get("id", 0)) != account_id
        or bool(account.get("isGM")) is not request.is_gm
        or int(character.get("accountId", 0)) != account_id
        or character.get("characterName") != request.character_name
        or int(character.get("shipID", 0)) <= 0
    ):
        raise CharacterCreationError("The persisted EveJS character failed verification.")
    if helper_result.get("rookieShipVerified") is not True:
        raise CharacterCreationError("The rookie ship could not be verified.")
    if check is None or check[0] != "ok":
        raise CharacterCreationError("The EveJS database failed quick_check after creation.")
    return account_id, character_id


def create_character(request: CharacterCreationRequest) -> CharacterCreationResult:
    """Create one account and character while the Native store is offline.

    The caller owns service lifecycle coordination. This function snapshots its
    exact table allowlist, runs the hidden Node helper, verifies persistence, and
    restores the snapshot automatically if any step fails.
    """
    normalized = normalize_creation_request(request)
    root = Path(normalized.evejs_root)
    game_store = _game_store(root)
    database_path, data_path = _require_store_layout(game_store)
    backup_root = Path(normalized.backup_root or _default_backup_root())
    backup_path = _create_backup(
        game_store,
        database_path,
        data_path,
        backup_root,
    )
    try:
        helper_result = _run_helper(normalized, root, game_store)
        account_id, character_id = _verify_result(
            database_path,
            normalized,
            helper_result,
        )
    except Exception as exc:
        try:
            _restore_backup(game_store, backup_path)
        except Exception as rollback_exc:
            raise CharacterCreationError(
                f"Character creation failed and automatic rollback also failed. "
                f"Backup retained at {backup_path}. Rollback error: {rollback_exc}"
            ) from exc
        if isinstance(exc, CharacterCreationError):
            raise
        raise CharacterCreationError(str(exc) or type(exc).__name__) from exc
    return CharacterCreationResult(
        request=normalized,
        account_id=account_id,
        character_id=character_id,
        backup_path=backup_path,
    )
