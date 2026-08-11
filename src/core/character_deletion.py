"""Backup-first, offline character and account deletion for Native EveJS."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Any
import uuid

from .platform import get_hidden_process_flags


_RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="

# Exact union of EveJS v0.12.4 characterDeletionRuntime.FLUSH_TABLES plus the
# account record removed by the launcher. The server's deletion routine owns
# the cleanup semantics; this allowlist owns backup and rollback boundaries.
_MUTATED_TABLES = (
    "accounts",
    "characters",
    "items",
    "mail",
    "notifications",
    "characterNotes",
    "lpWallets",
    "skillQueues",
    "skillPlans",
    "skillTradingState",
    "skills",
    "characterExpertSystems",
    "bookmarkRuntimeState",
    "bookmarks",
    "bookmarkFolders",
    "bookmarkSubfolders",
    "bookmarkKnownFolders",
    "bookmarkGroups",
    "savedFittings",
    "calendarEvents",
    "calendarResponses",
    "missionRuntimeState",
    "probeRuntimeState",
)


class CharacterDeletionError(RuntimeError):
    """Raised when an offline deletion cannot be completed or verified safely."""


class CharacterDeletionScope(str, Enum):
    CHARACTER = "character"
    ACCOUNT = "account"


@dataclass(frozen=True)
class CharacterDeletionRequest:
    evejs_root: str
    username: str
    account_id: int
    character_id: int
    character_name: str
    scope: CharacterDeletionScope
    backup_root: Path | None = None


@dataclass(frozen=True)
class CharacterDeletionResult:
    request: CharacterDeletionRequest
    deleted_character_ids: tuple[int, ...]
    deleted_character_names: tuple[str, ...]
    account_deleted: bool
    backup_path: Path


@dataclass(frozen=True)
class _DeletionTarget:
    character_id: int
    character_name: str


def normalize_deletion_request(
    request: CharacterDeletionRequest,
) -> CharacterDeletionRequest:
    root = Path(request.evejs_root).expanduser()
    username = request.username
    character_name = request.character_name
    try:
        scope = CharacterDeletionScope(request.scope)
    except (TypeError, ValueError) as exc:
        raise CharacterDeletionError("Select a valid deletion scope.") from exc
    if not root.is_dir():
        raise CharacterDeletionError("Select a valid EveJS root first.")
    if (
        not isinstance(username, str)
        or not username
        or username != username.strip()
        or any(ord(character) < 32 for character in username)
    ):
        raise CharacterDeletionError("The account target is invalid.")
    if (
        not isinstance(character_name, str)
        or not character_name
        or character_name != character_name.strip()
        or any(ord(character) < 32 for character in character_name)
    ):
        raise CharacterDeletionError("The character target is invalid.")
    for value, label in (
        (request.account_id, "account"),
        (request.character_id, "character"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CharacterDeletionError(f"A positive {label} ID is required.")
    return CharacterDeletionRequest(
        evejs_root=str(root.resolve()),
        username=username,
        account_id=request.account_id,
        character_id=request.character_id,
        character_name=character_name,
        scope=scope,
        backup_root=request.backup_root,
    )


def _helper_path() -> Path:
    return Path(__file__).resolve().parent / "helpers" / "delete_character.js"


def _game_store(root: Path) -> Path:
    return root / "_local" / "gameStore"


def _require_store_layout(game_store: Path) -> tuple[Path, Path]:
    database_path = game_store / "gamestore.sqlite"
    data_path = game_store / "data"
    if not database_path.is_file() or not data_path.is_dir():
        raise CharacterDeletionError(
            "The Native EveJS game store could not be found at the configured root."
        )
    if not _helper_path().is_file():
        raise CharacterDeletionError("The bundled character deletion helper is missing.")
    return database_path, data_path


def _default_backup_root() -> Path:
    from ..config import CONFIG_DIR

    return CONFIG_DIR / "backups" / "character_deletion"


def _read_json_row(
    connection: sqlite3.Connection,
    table: str,
    key: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        f'SELECT json FROM "{table}" WHERE key = ?',
        (key,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise CharacterDeletionError(
            f'The EveJS "{table}" record for "{key}" is invalid.'
        ) from exc
    return value if isinstance(value, dict) else None


def _inspect_targets(
    database_path: Path,
    request: CharacterDeletionRequest,
) -> tuple[_DeletionTarget, ...]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CharacterDeletionError("The EveJS database did not pass quick_check.")
        account = _read_json_row(connection, "accounts", request.username)
        if account is None or int(account.get("id", 0) or 0) != request.account_id:
            raise CharacterDeletionError(
                "The selected account no longer matches the EveJS database."
            )
        targets: list[_DeletionTarget] = []
        selected_found = False
        for key, blob in connection.execute('SELECT key, json FROM "characters"'):
            try:
                record = json.loads(blob)
            except (TypeError, json.JSONDecodeError) as exc:
                raise CharacterDeletionError(
                    f'The EveJS character record "{key}" is invalid.'
                ) from exc
            if not isinstance(record, dict) or record.get("isDeleted") is True:
                continue
            try:
                account_id = int(record.get("accountId", 0) or 0)
                character_id = int(key)
            except (TypeError, ValueError):
                continue
            if account_id != request.account_id or character_id <= 0:
                continue
            name = str(record.get("characterName") or "")
            target = _DeletionTarget(character_id, name)
            targets.append(target)
            if (
                character_id == request.character_id
                and name == request.character_name
            ):
                selected_found = True
        if not selected_found:
            raise CharacterDeletionError(
                "The selected character no longer matches the EveJS database."
            )
        targets.sort(key=lambda target: target.character_id)
        if request.scope is CharacterDeletionScope.CHARACTER:
            if len(targets) <= 1:
                raise CharacterDeletionError(
                    "This is the account's only character. Delete the account instead."
                )
            return tuple(
                target for target in targets if target.character_id == request.character_id
            )
        return tuple(targets)
    except sqlite3.Error as exc:
        raise CharacterDeletionError(f"Unable to inspect the EveJS game store: {exc}") from exc
    finally:
        connection.close()


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _portrait_sources(root: Path, game_store: Path) -> dict[str, Path]:
    return {
        "runtime": game_store / "images" / "Character",
        "legacy": root
        / "server"
        / "src"
        / "_secondary"
        / "image"
        / "generated"
        / "Character",
    }


def _create_backup(
    root: Path,
    game_store: Path,
    database_path: Path,
    data_path: Path,
    backup_root: Path,
    targets: tuple[_DeletionTarget, ...],
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
    backup_path.mkdir(parents=True, exist_ok=False)

    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CharacterDeletionError("The EveJS database did not pass quick_check.")
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        tables: dict[str, dict[str, Any]] = {}
        for table in _MUTATED_TABLES:
            if table not in existing_tables:
                raise CharacterDeletionError(
                    f'The EveJS game store is missing the required "{table}" table.'
                )
            tables[table] = {
                "rows": connection.execute(
                    f'SELECT key, json FROM "{table}"'
                ).fetchall()
            }
    finally:
        connection.close()

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

    portraits: list[dict[str, str]] = []
    portrait_backup_root = backup_path / "portraits"
    target_ids = {target.character_id for target in targets}
    for source_kind, source_root in _portrait_sources(root, game_store).items():
        if not source_root.is_dir():
            continue
        for character_id in sorted(target_ids):
            for source in source_root.glob(f"{character_id}_*.*"):
                if not source.is_file():
                    continue
                destination = portrait_backup_root / source_kind / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                portraits.append({"root": source_kind, "name": source.name})

    (backup_path / "metadata.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tables": list(_MUTATED_TABLES),
                "dataDirectories": present_data_dirs,
                "manifestPresent": manifest_present,
                "portraits": portraits,
                "characters": [
                    {
                        "characterID": target.character_id,
                        "characterName": target.character_name,
                    }
                    for target in targets
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return backup_path


def _restore_backup(root: Path, game_store: Path, backup_path: Path) -> None:
    database_path = game_store / "gamestore.sqlite"
    data_path = game_store / "data"
    tables = json.loads((backup_path / "tables.json").read_text(encoding="utf-8"))
    metadata = json.loads(
        (backup_path / "metadata.json").read_text(encoding="utf-8")
    )

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in _MUTATED_TABLES:
            connection.execute(f'DELETE FROM "{table}"')
            connection.executemany(
                f'INSERT INTO "{table}"(key, json) VALUES (?, ?)',
                tables[table]["rows"],
            )
        connection.commit()
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CharacterDeletionError("Rollback completed but quick_check failed.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    files_root = backup_path / "files"
    originally_present = set(metadata.get("dataDirectories", []))
    for table in _MUTATED_TABLES:
        target = data_path / table
        if not _is_inside(target, data_path):
            raise CharacterDeletionError("Refusing an unsafe rollback target.")
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

    portrait_roots = _portrait_sources(root, game_store)
    for record in metadata.get("portraits", []):
        if not isinstance(record, dict):
            continue
        source_kind = record.get("root")
        name = record.get("name")
        if source_kind not in portrait_roots or not isinstance(name, str):
            continue
        source = backup_path / "portraits" / source_kind / name
        destination_root = portrait_roots[source_kind]
        destination = destination_root / name
        if not _is_inside(destination, destination_root) or not source.is_file():
            raise CharacterDeletionError("Refusing an unsafe portrait rollback target.")
        destination_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _parse_helper_result(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            payload = json.loads(line[len(_RESULT_PREFIX) :])
            if isinstance(payload, dict):
                return payload
    raise CharacterDeletionError(
        "The EveJS helper did not return a verifiable deletion result."
    )


def _run_helper(
    request: CharacterDeletionRequest,
    root: Path,
    game_store: Path,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["EVEJS_GAMESTORE_SQLITE_PATH"] = str(
        game_store / "gamestore.sqlite"
    )
    environment["EVEJS_GAMESTORE_DATA_DIR"] = str(game_store / "data")
    payload = json.dumps(
        {
            "scope": request.scope.value,
            "username": request.username,
            "accountId": request.account_id,
            "characterId": request.character_id,
            "characterName": request.character_name,
        }
    )
    try:
        completed = subprocess.run(
            ["node", str(_helper_path())],
            cwd=str(root),
            env=environment,
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
        raise CharacterDeletionError(
            "Node.js was not found. Install Node.js or add it to PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CharacterDeletionError(
            "EveJS did not finish deleting the selected data within three minutes."
        ) from exc
    result = _parse_helper_result(completed.stdout)
    if completed.returncode != 0 or result.get("ok") is not True:
        message = str(result.get("error") or completed.stderr).strip()
        raise CharacterDeletionError(message or "EveJS character deletion failed.")
    return result


def _verify_result(
    database_path: Path,
    request: CharacterDeletionRequest,
    targets: tuple[_DeletionTarget, ...],
    helper_result: dict[str, Any],
) -> None:
    reported = helper_result.get("deletedCharacters")
    if not isinstance(reported, list):
        raise CharacterDeletionError("EveJS returned an invalid deletion result.")
    try:
        reported_ids = tuple(sorted(int(item["characterID"]) for item in reported))
    except (KeyError, TypeError, ValueError) as exc:
        raise CharacterDeletionError("EveJS returned invalid deleted character IDs.") from exc
    expected_ids = tuple(sorted(target.character_id for target in targets))
    if reported_ids != expected_ids:
        raise CharacterDeletionError("EveJS deleted a different character set than requested.")
    if bool(helper_result.get("accountDeleted")) is not (
        request.scope is CharacterDeletionScope.ACCOUNT
    ):
        raise CharacterDeletionError("EveJS returned an invalid account deletion result.")

    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        account = _read_json_row(connection, "accounts", request.username)
        if request.scope is CharacterDeletionScope.ACCOUNT:
            if account is not None:
                raise CharacterDeletionError("The deleted account is still present.")
        elif account is None or int(account.get("id", 0) or 0) != request.account_id:
            raise CharacterDeletionError("The retained account failed verification.")
        for target in targets:
            character = _read_json_row(
                connection,
                "characters",
                str(target.character_id),
            )
            if (
                character is None
                or character.get("isDeleted") is not True
                or character.get("accountId") is not None
                or int(character.get("deletedByAccountId", 0) or 0)
                != request.account_id
            ):
                raise CharacterDeletionError(
                    f'The deleted character "{target.character_name}" failed verification.'
                )
        check = connection.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CharacterDeletionError(
                "The EveJS database failed quick_check after deletion."
            )
    finally:
        connection.close()


def delete_character_or_account(
    request: CharacterDeletionRequest,
) -> CharacterDeletionResult:
    """Delete through EveJS's native cleanup while services and clients are offline.

    The caller owns service lifecycle coordination. This function validates the
    exact account/character identity, snapshots every table the EveJS v0.12.4
    deletion runtime can mutate, retains affected portraits, invokes the native
    cleanup, verifies the durable result, and automatically rolls back failures.
    """
    normalized = normalize_deletion_request(request)
    root = Path(normalized.evejs_root)
    game_store = _game_store(root)
    database_path, data_path = _require_store_layout(game_store)
    targets = _inspect_targets(database_path, normalized)
    if not targets:
        raise CharacterDeletionError("No character matched the deletion request.")
    backup_root = Path(normalized.backup_root or _default_backup_root())
    backup_path = _create_backup(
        root,
        game_store,
        database_path,
        data_path,
        backup_root,
        targets,
    )
    try:
        helper_result = _run_helper(normalized, root, game_store)
        _verify_result(database_path, normalized, targets, helper_result)
    except Exception as exc:
        try:
            _restore_backup(root, game_store, backup_path)
        except Exception as rollback_exc:
            raise CharacterDeletionError(
                "Deletion failed and automatic rollback also failed. "
                f"Backup retained at {backup_path}. Rollback error: {rollback_exc}"
            ) from exc
        if isinstance(exc, CharacterDeletionError):
            raise
        raise CharacterDeletionError(str(exc) or type(exc).__name__) from exc
    return CharacterDeletionResult(
        request=normalized,
        deleted_character_ids=tuple(target.character_id for target in targets),
        deleted_character_names=tuple(target.character_name for target in targets),
        account_deleted=normalized.scope is CharacterDeletionScope.ACCOUNT,
        backup_path=backup_path,
    )
