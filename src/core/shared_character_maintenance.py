"""Offline schema-35 character operations with detached execution and verified install."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from uuid import uuid4

from .host import IS_LINUX, external_process_environment
from .evejs_compatibility import require_completed_evejs_setup
from .mod_lifecycle_lock import acquire_mod_lifecycle_lease
from .platform import get_hidden_process_flags
from .save_migration import _copy_sqlite, _transfer_workspace
from .save_migration_storage import SaveMigrationError, _mount_string, _native, bundle_inventory, plain


_HELPERS = Path(__file__).resolve().with_name("helpers")
_WORKER = "shared_character_maintenance.js"
_MARKER = "launcher-character-maintenance"
_PREFIX = "EVEJS_LAUNCHER_RESULT="
_SUFFIXES = ("", "-wal", "-shm", "-journal")


class CharacterMaintenanceError(SaveMigrationError):
    def __init__(self, message, *, restart_safe):
        super().__init__(message)
        self.restart_safe = restart_safe


def _parse_result(stdout, code):
    if len(stdout) > 65536:
        raise ValueError("Oversized maintenance result.")
    (line,) = [line[len(_PREFIX):] for line in stdout.splitlines() if line.startswith(_PREFIX)]
    result = json.loads(line)
    if not isinstance(result, dict) or type(result.get("ok")) is not bool or (code == 0) != result["ok"]:
        raise ValueError("Unverified maintenance result.")
    return result


def _run(staged, storage, payload):
    if storage.runner:
        environment = {
            "EVEJS_DATA_ROOT": "/workspace",
            "EVEJS_GAMESTORE_DATA_DIR": "/workspace/gameStore/data",
            "EVEJS_GAMESTORE_OWNER_ROLE": "maintenance",
            "EVEJS_PERSISTENCE_OWNER_LEASE_MS": "180000",
            "EVEJS_LOG_LEVEL": "0",
            "SQLITE_TMPDIR": "/workspace/tmp",
            "TMPDIR": "/workspace/tmp",
            "NODE_OPTIONS": "",
            "NODE_PATH": "",
        }
        parameters = tuple(value for key, setting in environment.items() for value in ("--env", f"{key}={setting}"))
        owner = f"{os.getuid()}:{os.getgid()}" if IS_LINUX else "node"
        args = (
            "run", "--rm", "-i", "--pull", "never", "--network", "none", "--read-only",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--user", owner,
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m",
            "--workdir", "/app", "--entrypoint", "node",
            "--mount", _mount_string("bind", str(staged), "/workspace"),
            "--mount", _mount_string("bind", str(_HELPERS), "/helpers", read_only=True),
            *parameters, storage.node, f"/helpers/{_WORKER}", f"/workspace/{_MARKER}",
        )
        return storage.runner.run_with_input_parsed(
            args, cwd=storage.root, input_bytes=payload, parser=_parse_result, timeout=180,
        )
    env = {
        key: value for key, value in external_process_environment().items()
        if not key.upper().startswith("EVEJS_") and key.upper() not in {"NODE_OPTIONS", "NODE_PATH"}
    }
    env.update(
        EVEJS_DATA_ROOT=str(staged),
        EVEJS_GAMESTORE_DATA_DIR=str(staged / "gameStore" / "data"),
        EVEJS_GAMESTORE_OWNER_ROLE="maintenance",
        EVEJS_PERSISTENCE_OWNER_LEASE_MS="180000",
        EVEJS_LOG_LEVEL="0",
        SQLITE_TMPDIR=str(staged / "tmp"),
        TMPDIR=str(staged / "tmp"),
    )
    completed = subprocess.run(
        [storage.node, str(_HELPERS / _WORKER), str(staged / _MARKER)], cwd=storage.root,
        env=env, input=payload, capture_output=True, timeout=180, **get_hidden_process_flags(),
    )
    return _parse_result(completed.stdout.decode("utf-8"), completed.returncode)


def _stage(storage, backup, scratch, payload, prepare, verify):
    original = backup / "original"
    original.mkdir()
    before = storage.action("capture", original)
    if not before["game"] or not before["data"]:
        raise SaveMigrationError("Set up the selected GameStore before changing characters.")
    workspace = scratch / "detached"
    store = workspace / "gameStore"
    store.mkdir(parents=True)
    (workspace / "tmp").mkdir()
    (workspace / _MARKER).touch(exist_ok=False)
    (original / "data").replace(store / "data")
    if before["images"]:
        shutil.copytree(original / "images", store / "images")
    if before["manifest"]:
        shutil.copyfile(original / "manifest", store / "manifest.json")
    captured = scratch / "captured"
    captured.mkdir()
    database = store / "gamestore.sqlite"
    _copy_sqlite(original, "game", captured, database)
    selected = prepare(database) if prepare else None

    result = _run(workspace, storage, json.dumps(payload, ensure_ascii=False).encode())
    if not result["ok"]:
        raise SaveMigrationError(str(result.get("error") or "EveJS rejected the character change.")[:300])
    verified = verify(database, result, selected)

    incoming = scratch / "incoming"
    incoming.mkdir()
    (store / "data").replace(incoming / "data")
    for member, filename in (("images", "images"), ("manifest", "manifest.json")):
        staged = store / filename
        if staged.exists() or staged.is_symlink():
            staged.replace(incoming / member)
    detached = scratch / "sqlite"
    detached.mkdir()
    for suffix in _SUFFIXES:
        staged = store / ("gamestore.sqlite" + suffix)
        if staged.is_file():
            staged.replace(detached / ("game" + suffix))
    consolidated = scratch / "consolidated"
    consolidated.mkdir()
    _copy_sqlite(detached, "game", consolidated, incoming / "game")
    after = bundle_inventory(incoming, storage.files)
    if any(before[name] != after[name] for name in ("data", "manifest")):
        raise SaveMigrationError("The character operation unexpectedly changed the generated GameStore data.")
    return before, after, incoming, verified


def maintain_character(storage, payload, backup_root, *, require_offline, verify, prepare=None):
    """Execute stock code on copied data before atomically installing its verified result."""
    store = storage.files["game"].parent
    files = {name: path for name, path in storage.files.items() if name.startswith("game") or name == "images"}
    files.update(data=store / "data", manifest=store / "manifest.json")
    scoped = replace(storage, files=files)
    root = plain(Path(backup_root))
    if not scoped.runner and (root.is_relative_to(store) or store.is_relative_to(root)):
        raise SaveMigrationError("Use a separate directory for character backups.")
    with acquire_mod_lifecycle_lease(scoped.root):
        require_offline()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = root / f"{stamp}-{uuid4().hex[:8]}"
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        installing = False
        try:
            with _transfer_workspace(backup) as scratch:
                before, after, incoming, verified = _stage(scoped, backup, scratch, payload, prepare, verify)
                # Keep the durable original and transaction digests before touching the selected store.
                with (backup / "maintenance.json").open("x", encoding="utf-8") as receipt:
                    json.dump({"operation": payload["operation"], "before": before, "after": after}, receipt)
                    receipt.flush()
                    os.fsync(receipt.fileno())
                require_offline()
                installing = True
                scoped.action("replace", incoming, expected=before, incoming=after, keep=("data", "manifest"))
        except (OSError, RuntimeError, ValueError, sqlite3.Error, subprocess.TimeoutExpired) as error:
            restart_safe = not installing
            if installing:
                try:
                    require_offline()
                    restart_safe = scoped.action("inventory") == before
                except (OSError, RuntimeError, ValueError):
                    restart_safe = False
            raise CharacterMaintenanceError(
                f"{error}\n\nCharacter maintenance backup\n{backup}", restart_safe=restart_safe,
            ) from error
    return verified, backup


def maintain_native_character(root, payload, backup_root, *, verify, prepare=None):
    from .character_rename import _require_native_offline

    require_completed_evejs_setup(root)
    storage = _native(root, {})
    return maintain_character(
        storage, payload, backup_root, prepare=prepare, verify=verify,
        require_offline=lambda: _require_native_offline(storage, root),
    )
