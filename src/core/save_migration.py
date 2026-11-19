"""Offline Save Migration for registered EveJS worlds."""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from .mod_evejs_compatibility import installed_evejs_version
from .mod_lifecycle_lock import acquire_mod_lifecycle_lease
from .save_migration_storage import SaveMigrationError, bundle_inventory, plain, resolve_storage

_LOG = logging.getLogger(__name__)

_REQUIRED_GAME = frozenset({"accounts", "characters", "items"})
_REQUIRED_MARKET = frozenset({"manifest", "stations", "market_types", "seed_stock",
                              "market_orders", "market_order_events", "price_history"})
_OPEN_ORDER_BASE = 9_200_000_000
_OPEN_ORDER_LIMIT = 9_300_000_000
_MARKET_CONTRACT = ("externalservices/market-server/src/state.rs",
                    "externalservices/market-server/crates/market-common/src/lib.rs")
_MARKET_VERSION = re.compile(rb"\bMARKET_SCHEMA_VERSION:\s*u32\s*=\s*(\d+)")
_TABLES = re.compile(r"const SQLITE_TABLES = new Set\(\[(.*?)\]\);", re.S)
_TABLE_ROW = re.compile(r'^\s*["\']([a-zA-Z][a-zA-Z0-9_]*)["\'],?\s*(?://.*)?$')


@dataclass(frozen=True)
class SaveInstallation:
    name: str
    root: Path
    settings: dict

    @classmethod
    def from_profile(cls, entry):
        settings = entry["settings"].copy()
        return cls(entry["name"], Path(settings["evejs_root"]), settings)

    @property
    def version(self):
        return installed_evejs_version(self.root)


@dataclass(frozen=True)
class Transfer:
    source: SaveInstallation
    destination: SaveInstallation
    move: bool
    carry_market: bool
    fresh_seed: Path | None = None


@dataclass(frozen=True)
class TransferResult:
    backup: Path
    moved: bool
    note: str = ""


def _contract(root, files):
    digest = hashlib.sha256()
    for relative in files:
        data = plain(root / relative)
        if not data.is_file():
            raise SaveMigrationError("The installation is missing its save-format definitions.")
        digest.update(data.read_bytes().replace(b"\r\n", b"\n"))
    return digest.digest()


def declared_game_tables(root):
    game_code = plain(root / "server/src/gameStore/index.js")
    found = _TABLES.search(game_code.read_text(encoding="utf-8"))
    if found is None:
        raise SaveMigrationError("This EveJS release's GameStore layout could not be verified.")
    tables = set()
    for line in found.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        match = _TABLE_ROW.fullmatch(line)
        if match is None:
            raise SaveMigrationError("This EveJS release declares an unsupported GameStore layout.")
        tables.add(match[1])
    if not _REQUIRED_GAME.issubset(tables):
        raise SaveMigrationError("This EveJS release does not declare the required game save tables.")
    return tables


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}


def _is_idle_game(db):
    names = _tables(db)
    if not _REQUIRED_GAME.issubset(names):
        raise SaveMigrationError("Could not identify the accounts, characters and inventory in this game save.")
    for table in _REQUIRED_GAME | (names & {"marketEscrow", "marketRuntime"}):
        if tuple(row[1:3] for row in db.execute(f'PRAGMA table_info("{table}")')) != (
                ("key", "TEXT"), ("json", "TEXT")):
            raise SaveMigrationError(f"Unsupported game save layout in {table}.")
    if "_persistence_outbox" in names and db.execute("SELECT 1 FROM _persistence_outbox LIMIT 1").fetchone():
        raise SaveMigrationError("The game save has unfinished writes. Start and cleanly stop Game before retrying.")
    if "_persistence_owners" in names and db.execute(
            "SELECT 1 FROM _persistence_owners WHERE active=1 AND lease_expires_at>? LIMIT 1",
            (int(time.time() * 1000),)).fetchone():
        raise SaveMigrationError("Game still owns this save. Stop it and let its last write lease expire.")


def _read_market(db):
    if not _REQUIRED_MARKET.issubset(_tables(db)):
        raise SaveMigrationError("This market does not have a recognized EveJS market layout.")
    row = db.execute("SELECT value FROM manifest WHERE key='manifest_json'").fetchone()
    metadata = json.loads(row[0]) if row else {}
    if not isinstance(metadata, dict) or type(metadata.get("schema_version")) is not int:
        raise SaveMigrationError("Could not verify the market schema version.")
    return metadata["schema_version"]


def _market_version(root):
    source = plain(root / _MARKET_CONTRACT[-1]).read_bytes()
    found = _MARKET_VERSION.search(source)
    if found is None:
        raise SaveMigrationError("The destination market version cannot be verified.")
    return int(found[1])


def _market_shape(db):
    version = _read_market(db)
    tables = sorted(name for name in _tables(db) if not name.startswith("sqlite_"))
    shape = []
    for table in tables:
        literal = "'" + table.replace("'", "''") + "'"
        shape.append((table, tuple(db.execute(f"PRAGMA table_xinfo({literal})")),
                      tuple(db.execute(f"PRAGMA foreign_key_list({literal})"))))
    return version, tuple(shape)


def _clean_market(db):
    _read_market(db)
    if (db.execute("SELECT 1 FROM market_orders LIMIT 1").fetchone()
            or db.execute("SELECT 1 FROM market_order_events LIMIT 1").fetchone()):
        raise SaveMigrationError("This market has orders or trading history. Choose a clean destination market seed.")
    for table in ("market_fill_receipts", "market_fill_operations", "market_fills", "market_trade_receipts"):
        if table in _tables(db) and db.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
            raise SaveMigrationError("This market has fills from an existing playthrough. Choose a clean seed.")


def verify_stock_game(db, request, destination_tables):
    source_tables = declared_game_tables(request.source.root)
    if source_tables != destination_tables or _contract(
            request.source.root, ("server/src/gameStore/sqliteStore.js",)) != _contract(
                request.destination.root, ("server/src/gameStore/sqliteStore.js",)):
        raise SaveMigrationError("The game save formats differ. Use installations on the same save format.")


def _reset_market_state(db):
    # Only the detached copy is changed. These virtual locations are reserved
    # by EveJS's market for outstanding sell orders. Keeping the stacks would
    # let boot-time escrow reconciliation attach them to a different market.
    for table in ("marketEscrow", "marketRuntime"):
        if table in _tables(db):
            db.execute(f'DELETE FROM "{table}"')
    db.execute(
        "DELETE FROM items WHERE CAST(json_extract(json, '$.locationID') AS INTEGER) > ? "
        "AND CAST(json_extract(json, '$.locationID') AS INTEGER) < ?",
        (_OPEN_ORDER_BASE, _OPEN_ORDER_LIMIT),
    )
    db.commit()


def _copy_sqlite(snapshot, name, stage, destination):
    if not (snapshot / name).is_file():
        raise SaveMigrationError(f"The {'game save' if name == 'game' else 'market database'} is missing.")
    if (snapshot / (name + "-journal")).exists():
        raise SaveMigrationError("A database has an unfinished rollback journal. Recover and stop that installation first.")
    db_path = stage / name
    shutil.copyfile(snapshot / name, db_path)
    wal = snapshot / (name + "-wal")
    if wal.is_file():
        shutil.copyfile(wal, stage / (name + "-wal"))
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=0)) as source:
        with closing(sqlite3.connect(destination)) as prepared:
            source.backup(prepared)
            prepared.execute("PRAGMA journal_mode=DELETE").fetchone()
            if prepared.execute("PRAGMA integrity_check(1)").fetchone() != ("ok",):
                raise SaveMigrationError("SQLite reported a damaged save or market database.")
            if prepared.execute("PRAGMA foreign_key_check").fetchone():
                raise SaveMigrationError("A database failed its reference integrity check.")


def _prepare_market(request, source, target, incoming, scratch):
    destination_market = target / "market"
    if request.carry_market:
        if _contract(request.source.root, _MARKET_CONTRACT) != _contract(request.destination.root, _MARKET_CONTRACT):
            raise SaveMigrationError("These installations use different market formats. Start with a fresh market instead.")
        with TemporaryDirectory(dir=scratch) as stage:
            _copy_sqlite(source, "market", Path(stage), incoming / "market")
        with closing(sqlite3.connect(incoming / "market")) as db:
            if _read_market(db) != _market_version(request.destination.root):
                raise SaveMigrationError("The source market version does not match the destination.")
        return

    if request.fresh_seed is not None:
        seed = plain(request.fresh_seed)
        if seed.name in ("", ".", "..") or not seed.is_file():
            raise SaveMigrationError("Choose a fresh market SQLite file from the destination's seed builder.")
        with TemporaryDirectory(dir=scratch) as seed_stage:
            portable = Path(seed_stage)
            shutil.copyfile(seed, portable / "market")
            for suffix in ("-wal", "-journal"):
                sidecar = plain(seed.with_name(seed.name + suffix))
                if sidecar.is_file():
                    shutil.copyfile(sidecar, portable / ("market" + suffix))
            with TemporaryDirectory(dir=scratch) as stage:
                _copy_sqlite(portable, "market", Path(stage), incoming / "market")
    elif destination_market.is_file():
        with TemporaryDirectory(dir=scratch) as stage:
            _copy_sqlite(target, "market", Path(stage), incoming / "market")
    else:
        raise SaveMigrationError("Choose a clean market seed built for the destination installation.")

    with closing(sqlite3.connect(incoming / "market")) as new_market:
        _clean_market(new_market)
        if _read_market(new_market) != _market_version(request.destination.root):
            raise SaveMigrationError("The clean seed uses a different market version from the destination.")
        if request.fresh_seed is not None and destination_market.is_file():
            with TemporaryDirectory(dir=scratch) as reference:
                reference = Path(reference)
                with TemporaryDirectory(dir=scratch) as stage:
                    _copy_sqlite(target, "market", Path(stage), reference / "market")
                with closing(sqlite3.connect(reference / "market")) as existing:
                    if _market_shape(existing) != _market_shape(new_market):
                        raise SaveMigrationError("The chosen seed has a different format from the destination market.")


def _prepare(request, source, target, incoming, scratch, verify_game):
    # Offline ports alone are insufficient if a stopped or unreachable game
    # still has pending persistence work in the destination database.
    if (target / "game").is_file():
        with TemporaryDirectory(dir=scratch) as reference:
            with TemporaryDirectory(dir=scratch) as stage:
                _copy_sqlite(target, "game", Path(stage), Path(reference) / "game")
            with closing(sqlite3.connect(Path(reference) / "game")) as existing:
                _is_idle_game(existing)
    with TemporaryDirectory(dir=scratch) as stage:
        _copy_sqlite(source, "game", Path(stage), incoming / "game")
    for name in ("images", "skins"):
        if (source / name).is_dir():
            shutil.copytree(source / name, incoming / name)
    with closing(sqlite3.connect(incoming / "game")) as game:
        _is_idle_game(game)
        verify_game(game, request, declared_game_tables(request.destination.root))
        if not request.carry_market:
            _reset_market_state(game)
    _prepare_market(request, source, target, incoming, scratch)


def _check_routes(request):
    left, right = request.source.root, request.destination.root
    plain(left)
    plain(right)
    if not left.is_dir() or not right.is_dir():
        raise SaveMigrationError("Both installations must be available on this computer.")
    if left == right or left in right.parents or right in left.parents:
        raise SaveMigrationError("Choose two separate installation folders.")
    if not request.source.version or request.source.version != request.destination.version:
        raise SaveMigrationError("Both installations need the same verified EveJS base version for this transfer.")


def review_transfer(request):
    """Check release metadata before asking the user to replace a save."""
    _check_routes(request)
    if request.carry_market and _contract(request.source.root, _MARKET_CONTRACT) != _contract(
            request.destination.root, _MARKET_CONTRACT):
        raise SaveMigrationError("These installations use different market formats. Start with a fresh market instead.")


def _receipt(directory, state, request, **evidence):
    receipt = directory / "transfer.json"
    pending = directory / "transfer.pending"
    history = json.loads(receipt.read_text(encoding="utf-8")) if receipt.is_file() else {}
    payload = {
        **history, "state": state, "action": "move" if request.move else "copy",
        "source": str(request.source.root), "destination": str(request.destination.root),
        "market": "bring" if request.carry_market else "fresh", **evidence,
    }
    with pending.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(receipt)


def _layout_record(storage):
    return {
        "project": storage.project,
        "mounts": [{"kind": mount.kind, "source": mount.source} for mount in storage.mounts],
        "files": {name: str(path) for name, path in storage.files.items()},
    }


def migrate(request, *, progress=lambda _: None, verify_game=verify_stock_game):
    """Verify and back up both storage sets before publishing the new save."""
    review_transfer(request)
    progress("Checking that both installations are stopped…")
    source = resolve_storage(request.source.root, request.source.settings)
    target = resolve_storage(request.destination.root, request.destination.settings)
    if source.shares_save_storage(target):
        raise SaveMigrationError("These installations share save storage. Choose independent worlds.")

    def offline():
        source.require_offline()
        target.require_offline()

    with ExitStack() as locks:
        for root in sorted((source.root, target.root)):
            locks.enter_context(acquire_mod_lifecycle_lease(root))
        offline()
        token = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:10]
        area = plain(target.root / ".evejs-launcher" / "save-migrations")
        area.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = area / token
        backup.mkdir(mode=0o700)
        _receipt(backup, "backing-up", request, source_layout=_layout_record(source),
                 destination_layout=_layout_record(target))
        try:
            result = _transfer(request, source, target, backup, offline, progress, verify_game)
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            _receipt(backup, "stopped", request)
            raise SaveMigrationError(f"{error}\n\nTransfer record and any completed backups\n{backup}") from error
        _receipt(backup, "moved" if result.moved else ("copied" if not request.move else "copy-only"), request)
        return result


@contextmanager
def _transfer_workspace(backup):
    workspace = TemporaryDirectory(prefix="work-", dir=backup)
    try:
        yield Path(workspace.name)
    finally:
        try:
            workspace.cleanup()
        except OSError:
            _LOG.warning("Could not remove save transfer workspace %s", workspace.name, exc_info=True)


def _transfer(request, source, target, backup, offline, progress, verify_game):
    with _transfer_workspace(backup) as scratch:
        original = (backup if request.move else scratch) / "source"
        destination = backup / "destination"
        incoming = scratch / "incoming"
        for folder in (original, destination, incoming):
            folder.mkdir(mode=0o700)
        progress("Backing up and verifying the playthrough and market…")
        source_before = source.action("capture", original)
        target_before = target.action("capture", destination)
        _receipt(backup, "backups-verified", request, source_files=source_before, destination_files=target_before)
        progress("Checking the game and market formats…")
        _prepare(request, original, destination, incoming, scratch, verify_game)
        if (incoming / "skins").exists() and "skins" not in target.files:
            raise SaveMigrationError("The destination has no persistent storage for saved ship skin designs.")
        expected = bundle_inventory(incoming, target.files)
        offline()
        if source.action("inventory") != source_before:
            raise SaveMigrationError("The source save changed during preparation. Stop Game and Market and retry.")
        _receipt(backup, "installing-destination", request, destination_files=target_before, expected=expected)
        progress("Installing the verified save at the destination…")
        target.action("replace", incoming, expected=target_before, incoming=expected)
        _receipt(backup, "destination-verified", request, destination_files=target_before, installed=expected)
        if not request.move:
            return TransferResult(backup, False)
        progress("Removing the original save after verification…")
        try:
            offline()
            if target.action("inventory") != expected:
                raise SaveMigrationError("The destination save changed during verification.")
            keep = tuple(name for name in source.files if name.startswith("market") and not request.carry_market)
            after = {name: source_before[name] if name in keep else None for name in source.files}
            source.action("replace", expected=source_before, incoming=after, keep=keep)
        except (ValueError, OSError, RuntimeError) as error:
            return TransferResult(backup, False, f"The destination copy passed. Source removal stopped. {error}")
        return TransferResult(backup, True)
