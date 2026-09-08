"""Native Market inspection must release SQLite handles and distinguish Busy."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import time

import pytest
from PyQt6.QtCore import QThread

from src.core import server_launcher
from src.core.server_launcher import NativeMarketDatabaseState
from src.workers.server_worker import ServiceStartWorker


@pytest.fixture
def seeded_market(tmp_path: Path) -> tuple[Path, Path]:
    market_dir = tmp_path / "externalservices" / "market-server"
    config_dir = market_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "market-server.local.toml").write_text(
        '[storage]\ndatabase_path = "market.sqlite"\n', encoding="utf-8",
    )
    database = market_dir / "market.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO manifest VALUES ('manifest_json', '{}')")
        connection.commit()
    return tmp_path, database


@pytest.mark.parametrize("valid_seed", [True, False])
def test_preflight_closes_database_before_returning(
    seeded_market: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, valid_seed: bool,
) -> None:
    root, database = seeded_market
    if not valid_seed:
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DROP TABLE manifest")
            connection.commit()

    connections: list[sqlite3.Connection] = []
    connect = sqlite3.connect

    def remember_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(server_launcher.sqlite3, "connect", remember_connection)
    # Keep every connection referenced so GC cannot make a leaked handle look safe.
    available, reason = server_launcher.native_market_database_status(root)
    assert available is valid_seed
    assert bool(reason) is not valid_seed
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    database.unlink()


def test_locked_database_is_busy_not_a_rebuild_recommendation(
    seeded_market: tuple[Path, Path],
) -> None:
    root, database = seeded_market
    with closing(sqlite3.connect(database)) as owner:
        owner.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        available, reason = server_launcher.native_market_database_status(root)
        duration = time.monotonic() - started
        owner.rollback()

    assert available is False
    assert "database is busy:" in reason.casefold()
    assert "rebuild" not in reason.casefold()
    assert "seed builder" not in reason.casefold()
    assert duration < 1.5
    assert server_launcher.native_market_database_status(root) == (True, "")


@pytest.mark.parametrize("error_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED | (1 << 8)])
def test_sqlite_busy_categories_are_typed_and_release_the_failed_connection(
    seeded_market: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, error_code: int,
) -> None:
    root, _database = seeded_market
    error = sqlite3.OperationalError("locked")
    error.sqlite_errorcode = error_code
    closed = []

    class LockedConnection:
        def execute(self, *_args):
            raise error

        def close(self):
            closed.append(True)

    monkeypatch.setattr(server_launcher.sqlite3, "connect", lambda *_args, **_kwargs: LockedConnection())
    result = server_launcher.inspect_native_market_database(root)

    assert result.state is NativeMarketDatabaseState.BUSY
    assert result.available is False
    assert closed == [True]


def test_missing_and_invalid_seed_results_remain_distinct(
    seeded_market: tuple[Path, Path],
) -> None:
    root, database = seeded_market
    assert server_launcher.inspect_native_market_database(root).state is NativeMarketDatabaseState.READY
    database.write_bytes(b"not a SQLite database")
    invalid = server_launcher.inspect_native_market_database(root)
    assert invalid.state is NativeMarketDatabaseState.INVALID
    assert "Rebuild" in invalid.reason
    database.unlink()
    missing = server_launcher.inspect_native_market_database(root)
    assert missing.state is NativeMarketDatabaseState.MISSING


def test_worker_inspects_captured_root_only_when_run_and_emits_once(qapp) -> None:
    calls = []
    results = []

    def failed_inspection(root):
        calls.append(root)
        raise PermissionError("fixture access denied")

    worker = ServiceStartWorker(
        "C:/Fixture/CapturedRoot", mode=None, start_market=True, start_game=False,
        market_preflight_fn=failed_inspection,
        start_market_fn=lambda root: pytest.fail("Invalid Market started"),
    )
    worker.completed.connect(results.append)
    assert calls == []
    worker.run()

    assert calls == ["C:/Fixture/CapturedRoot"]
    assert len(results) == 1
    assert results[0].market_preflight.state is NativeMarketDatabaseState.INVALID
    assert "fixture access denied" in results[0].market_preflight.reason
    assert results[0].market_process is None


def test_worker_runs_real_database_inspection_off_the_gui_thread(
    qapp, seeded_market: tuple[Path, Path],
) -> None:
    root, _database = seeded_market
    threads = []
    results = []

    def inspect(target):
        threads.append(QThread.currentThread())
        return server_launcher.inspect_native_market_database(target)

    class ReadyProcess:
        def poll(self):
            return None

    worker = ServiceStartWorker(
        str(root), mode=None, start_market=True, start_game=False,
        market_preflight_fn=inspect, start_market_fn=lambda root: ReadyProcess(),
        probe=lambda port: True,
    )
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(results.append)
    worker.completed.connect(worker.deleteLater)
    worker.completed.connect(thread.quit)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while thread.isRunning() and time.monotonic() < deadline:
            qapp.processEvents()
            thread.wait(10)
        qapp.processEvents()
        assert not thread.isRunning()
        assert threads == [thread]
        assert threads[0] is not qapp.thread()
        assert len(results) == 1
        assert results[0].market_preflight.state is NativeMarketDatabaseState.READY
        assert results[0].market_ready
    finally:
        thread.quit()
        assert thread.wait(1000)


@pytest.mark.parametrize("state", [NativeMarketDatabaseState.MISSING, NativeMarketDatabaseState.BUSY])
def test_optional_market_skip_still_starts_game_in_the_same_worker(qapp, state):
    roots, results = [], []

    class ReadyProcess:
        def poll(self):
            return None

    def start_game(root, *, mode):
        roots.append((root, mode))
        return ReadyProcess()

    worker = ServiceStartWorker(
        "C:/CapturedRoot", mode="vanilla", start_market=True, start_game=True,
        market_preflight_fn=lambda root: server_launcher.NativeMarketDatabaseResult(state, "Fixture unavailable"),
        start_market_fn=lambda root: pytest.fail("Unavailable Market was started"),
        start_game_fn=start_game, probe=lambda port: True,
    )
    worker.completed.connect(results.append)
    worker.run()
    assert roots == [("C:/CapturedRoot", "vanilla")]
    assert len(results) == 1 and results[0].game_ready
    assert results[0].market_process is None and results[0].succeeded
    assert results[0].market_preflight.state is state
