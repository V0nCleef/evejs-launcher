"""Durable-owner barriers for Native offline maintenance."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import time

import pytest

from src.core.native_maintenance import (
    PersistenceMaintenanceLeaseError,
    PersistenceOwnerWaitError,
    assert_persistence_owner_checkpoint,
    hold_persistence_maintenance,
    persistence_owner_checkpoint,
    wait_for_persistence_owners,
)


def _owner_database(tmp_path: Path) -> Path:
    database = tmp_path / "gamestore.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            CREATE TABLE _persistence_owners (
              owner_role TEXT PRIMARY KEY,
              epoch INTEGER NOT NULL DEFAULT 1,
              active INTEGER NOT NULL,
              lease_expires_at INTEGER NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()
    return database


def test_waits_for_live_owner_leases_before_returning(tmp_path: Path) -> None:
    database = _owner_database(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.executemany(
            "INSERT INTO _persistence_owners"
            "(owner_role, active, lease_expires_at) VALUES (?, 1, ?)",
            (("scheduler", 100_200), ("wallet", 100_300), ("world", 100_100)),
        )
        connection.commit()
    finally:
        connection.close()

    clock = {"monotonic": 0.0, "wall": 100.0}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["monotonic"] += seconds
        clock["wall"] += seconds

    wait_for_persistence_owners(
        database,
        timeout_sec=1,
        poll_interval_sec=0.1,
        monotonic_fn=lambda: clock["monotonic"],
        wall_time_fn=lambda: clock["wall"],
        sleep_fn=sleep,
    )

    assert sum(sleeps) == pytest.approx(0.3)


def test_wait_honors_an_initial_lease_longer_than_the_minimum_timeout(
    tmp_path: Path,
) -> None:
    database = _owner_database(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO _persistence_owners"
            "(owner_role, active, lease_expires_at) VALUES ('world', 1, 100600)"
        )
        connection.commit()
    finally:
        connection.close()

    clock = {"monotonic": 0.0, "wall": 100.0}

    def sleep(seconds: float) -> None:
        clock["monotonic"] += seconds
        clock["wall"] += seconds

    wait_for_persistence_owners(
        database,
        timeout_sec=0.2,
        poll_interval_sec=0.1,
        expiry_grace_sec=0.05,
        monotonic_fn=lambda: clock["monotonic"],
        wall_time_fn=lambda: clock["wall"],
        sleep_fn=sleep,
    )

    assert clock["monotonic"] == pytest.approx(0.6, abs=0.002)


def test_times_out_when_an_owner_renews_past_the_initial_deadline(
    tmp_path: Path,
) -> None:
    database = _owner_database(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO _persistence_owners"
            "(owner_role, active, lease_expires_at) VALUES ('world', 1, 100100)"
        )
        connection.commit()
    finally:
        connection.close()

    clock = {"monotonic": 0.0, "wall": 100.0}
    renewed = False

    def sleep(seconds: float) -> None:
        nonlocal renewed
        clock["monotonic"] += seconds
        clock["wall"] += seconds
        if not renewed:
            renewed = True
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    "UPDATE _persistence_owners SET lease_expires_at = 200000 "
                    "WHERE owner_role = 'world'"
                )
                connection.commit()
            finally:
                connection.close()

    with pytest.raises(
        PersistenceOwnerWaitError,
        match="still active for world.*then try again",
    ):
        wait_for_persistence_owners(
            database,
            timeout_sec=0.2,
            poll_interval_sec=0.1,
            expiry_grace_sec=0.05,
            monotonic_fn=lambda: clock["monotonic"],
            wall_time_fn=lambda: clock["wall"],
            sleep_fn=sleep,
        )

    assert clock["monotonic"] == pytest.approx(0.2)


def test_pre_owner_schema_returns_without_sleeping(tmp_path: Path) -> None:
    database = tmp_path / "gamestore.sqlite"
    sqlite3.connect(database).close()

    wait_for_persistence_owners(
        database,
        sleep_fn=lambda _seconds: pytest.fail("v0.12.4 must not wait"),
    )


def test_checkpoint_captures_every_owner_epoch_under_the_guard(tmp_path: Path) -> None:
    database = _owner_database(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.executemany(
            "INSERT INTO _persistence_owners"
            "(owner_role, epoch, active, lease_expires_at) VALUES (?, ?, 0, 0)",
            (("maintenance", 4), ("world", 11)),
        )
        connection.commit()
    finally:
        connection.close()

    assert persistence_owner_checkpoint(database) == {
        "maintenance": 4,
        "world": 11,
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_guard_holds_public_maintenance_lease_until_context_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evejs"
    module = root / "server" / "src" / "gameStore" / "index.js"
    module.parent.mkdir(parents=True)
    game_store = root / "_local" / "gameStore"
    (game_store / "data").mkdir(parents=True)
    connection = sqlite3.connect(game_store / "gamestore.sqlite")
    try:
        connection.execute(
            "CREATE TABLE _persistence_owners "
            "(owner_role TEXT PRIMARY KEY, epoch INTEGER, active INTEGER, "
            "lease_expires_at INTEGER)"
        )
        connection.commit()
    finally:
        connection.close()
    events = tmp_path / "events.txt"
    module.write_text(
        """
        "use strict";
        const fs = require("fs");
        function event(value) {
          fs.appendFileSync(process.env.FIXTURE_EVENTS, `${value}\n`);
        }
        module.exports = {
          acquirePersistenceOwnerLease(options) {
            event(`acquire:${options.recover}`);
          },
          async shutdown(reason) {
            event(`shutdown:${reason}`);
            return { success: true, released: true };
          },
        };
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("FIXTURE_EVENTS", os.fspath(events))

    with hold_persistence_maintenance(root, game_store):
        assert events.read_text(encoding="utf-8").splitlines() == [
            "acquire:false"
        ]

    assert events.read_text(encoding="utf-8").splitlines() == [
        "acquire:false",
        "shutdown:launcher-maintenance-guard-release",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_guard_rejects_shutdown_without_a_confirmed_lease_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "evejs"
    module = root / "server" / "src" / "gameStore" / "index.js"
    module.parent.mkdir(parents=True)
    game_store = root / "_local" / "gameStore"
    (game_store / "data").mkdir(parents=True)
    connection = sqlite3.connect(game_store / "gamestore.sqlite")
    try:
        connection.execute(
            "CREATE TABLE _persistence_owners "
            "(owner_role TEXT PRIMARY KEY, epoch INTEGER, active INTEGER, "
            "lease_expires_at INTEGER)"
        )
        connection.commit()
    finally:
        connection.close()
    module.write_text(
        """
        "use strict";
        module.exports = {
          acquirePersistenceOwnerLease() {},
          async shutdown() { return { success: true, released: false }; },
        };
        """,
        encoding="utf-8",
    )

    with pytest.raises(PersistenceMaintenanceLeaseError, match="shutdown failed"):
        with hold_persistence_maintenance(root, game_store):
            pass


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_guard_surfaces_acquisition_conflict_before_ready_without_timing_out(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evejs"
    module = root / "server" / "src" / "gameStore" / "index.js"
    module.parent.mkdir(parents=True)
    game_store = root / "_local" / "gameStore"
    (game_store / "data").mkdir(parents=True)
    connection = sqlite3.connect(game_store / "gamestore.sqlite")
    try:
        connection.execute(
            "CREATE TABLE _persistence_owners "
            "(owner_role TEXT PRIMARY KEY, epoch INTEGER, active INTEGER, "
            "lease_expires_at INTEGER)"
        )
        connection.commit()
    finally:
        connection.close()
    module.write_text(
        """
        "use strict";
        module.exports = {
          acquirePersistenceOwnerLease() {
            const error = new Error("OWNER_CONFLICT_FIXTURE");
            error.code = "PERSISTENCE_OWNER_CONFLICT";
            throw error;
          },
          async shutdown() { return { success: true, released: true }; },
        };
        """,
        encoding="utf-8",
    )

    started = time.monotonic()
    with pytest.raises(
        PersistenceMaintenanceLeaseError,
        match="OWNER_CONFLICT_FIXTURE",
    ):
        with hold_persistence_maintenance(
            root,
            game_store,
            start_timeout_sec=5,
        ):
            pass

    assert time.monotonic() - started < 3


def test_pre_owner_schema_guard_does_not_import_legacy_gamestore(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evejs"
    game_store = root / "_local" / "gameStore"
    (game_store / "data").mkdir(parents=True)
    sqlite3.connect(game_store / "gamestore.sqlite").close()
    module = root / "server" / "src" / "gameStore" / "index.js"
    module.parent.mkdir(parents=True)
    module.write_text(
        'throw new Error("legacy GameStore must not be imported before backup");',
        encoding="utf-8",
    )

    with hold_persistence_maintenance(root, game_store):
        pass


def test_rollback_checkpoint_accepts_only_the_expected_owner_epochs(
    tmp_path: Path,
) -> None:
    database = _owner_database(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.executemany(
            "INSERT INTO _persistence_owners"
            "(owner_role, epoch, active, lease_expires_at) VALUES (?, ?, 0, 0)",
            (("maintenance", 6), ("world", 11)),
        )
        connection.commit()
    finally:
        connection.close()

    checkpoint = {"maintenance": 4, "world": 11}
    assert_persistence_owner_checkpoint(
        database,
        checkpoint,
        maintenance_epoch_advance=2,
    )

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE _persistence_owners SET epoch = 12 WHERE owner_role = 'world'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        PersistenceMaintenanceLeaseError,
        match="ownership changed after the maintenance backup",
    ):
        assert_persistence_owner_checkpoint(
            database,
            checkpoint,
            maintenance_epoch_advance=2,
        )
