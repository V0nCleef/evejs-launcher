"""Lease-aware safety barrier for offline Native GameStore maintenance."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import threading
import time
from typing import Callable, Iterator

from .platform import get_hidden_process_flags


DEFAULT_OWNER_WAIT_TIMEOUT_SEC = 45.0
DEFAULT_OWNER_POLL_INTERVAL_SEC = 0.1
DEFAULT_OWNER_EXPIRY_GRACE_SEC = 5.0
DEFAULT_GUARD_START_TIMEOUT_SEC = 60.0
DEFAULT_GUARD_STOP_TIMEOUT_SEC = 30.0
_READY_PREFIX = "EVEJS_LAUNCHER_LEASE_READY="
_RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="


class PersistenceMaintenanceError(RuntimeError):
    """Base error for Native maintenance ownership failures."""


class PersistenceOwnerWaitError(PersistenceMaintenanceError):
    """Raised when the Native store cannot be proven quiescent in time."""


class PersistenceMaintenanceLeaseError(PersistenceMaintenanceError):
    """Raised when the atomic maintenance lease cannot be held or released."""


@dataclass(frozen=True)
class ActivePersistenceOwner:
    role: str
    lease_expires_at_ms: int


def _active_persistence_owners(
    database_path: Path,
    *,
    now_ms: int,
) -> tuple[ActivePersistenceOwner, ...]:
    """Read live v0.12.5+ owner leases without mutating private store state."""
    try:
        connection = sqlite3.connect(
            f"file:{database_path}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 1000")
            owner_table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = '_persistence_owners'"
            ).fetchone()
            if owner_table is None:
                # EveJS v0.12.4 has no durable owner schema.
                return ()
            rows = connection.execute(
                "SELECT owner_role, lease_expires_at "
                "FROM _persistence_owners "
                "WHERE active = 1 AND lease_expires_at > ? "
                "ORDER BY owner_role",
                (now_ms,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise PersistenceOwnerWaitError(
            f"Unable to inspect EveJS persistence ownership: {exc}"
        ) from exc

    owners: list[ActivePersistenceOwner] = []
    for role, lease_expires_at in rows:
        try:
            expiry = int(lease_expires_at)
        except (TypeError, ValueError) as exc:
            raise PersistenceOwnerWaitError(
                "The EveJS persistence-owner table contains an invalid lease."
            ) from exc
        owners.append(ActivePersistenceOwner(str(role), expiry))
    return tuple(owners)


def persistence_owner_checkpoint(database_path: Path) -> dict[str, int] | None:
    """Capture monotonic owner epochs while a maintenance guard is held."""
    try:
        connection = sqlite3.connect(
            f"file:{Path(database_path)}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        try:
            owner_table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = '_persistence_owners'"
            ).fetchone()
            if owner_table is None:
                return None
            rows = connection.execute(
                "SELECT owner_role, epoch FROM _persistence_owners "
                "ORDER BY owner_role"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise PersistenceMaintenanceLeaseError(
            f"Unable to capture EveJS persistence ownership: {exc}"
        ) from exc

    checkpoint: dict[str, int] = {}
    for role, epoch in rows:
        try:
            normalized_epoch = int(epoch)
        except (TypeError, ValueError) as exc:
            raise PersistenceMaintenanceLeaseError(
                "The EveJS persistence-owner table contains an invalid epoch."
            ) from exc
        if normalized_epoch < 1:
            raise PersistenceMaintenanceLeaseError(
                "The EveJS persistence-owner table contains an invalid epoch."
            )
        checkpoint[str(role)] = normalized_epoch
    if "maintenance" not in checkpoint:
        raise PersistenceMaintenanceLeaseError(
            "EveJS did not persist the acquired maintenance owner."
        )
    return checkpoint


def assert_persistence_owner_checkpoint(
    database_path: Path,
    checkpoint: dict[str, int] | None,
    *,
    maintenance_epoch_advance: int,
) -> None:
    """Reject a stale backup while an exclusive maintenance guard is held."""
    advance = int(maintenance_epoch_advance)
    if advance < 0:
        raise ValueError("maintenance_epoch_advance must not be negative")

    actual = persistence_owner_checkpoint(database_path)
    if checkpoint is None:
        if actual is None:
            return
        raise PersistenceMaintenanceLeaseError(
            "EveJS persistence ownership changed after the maintenance backup."
        )
    if actual is None or set(actual) != set(checkpoint):
        raise PersistenceMaintenanceLeaseError(
            "EveJS persistence ownership changed after the maintenance backup."
        )

    for role, epoch in checkpoint.items():
        try:
            expected_epoch = int(epoch)
        except (TypeError, ValueError) as exc:
            raise PersistenceMaintenanceLeaseError(
                "The saved EveJS persistence-owner checkpoint is invalid."
            ) from exc
        if expected_epoch < 1:
            raise PersistenceMaintenanceLeaseError(
                "The saved EveJS persistence-owner checkpoint is invalid."
            )
        if role == "maintenance":
            expected_epoch += advance
        if actual.get(role) != expected_epoch:
            raise PersistenceMaintenanceLeaseError(
                "EveJS persistence ownership changed after the maintenance backup."
            )


def _has_persistence_owner_schema(database_path: Path) -> bool:
    """Return whether this store supports durable v0.12.5+ owner fencing."""
    try:
        connection = sqlite3.connect(
            f"file:{Path(database_path)}?mode=ro",
            uri=True,
            timeout=1.0,
        )
        try:
            return connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = '_persistence_owners'"
            ).fetchone() is not None
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise PersistenceMaintenanceLeaseError(
            f"Unable to inspect EveJS persistence ownership: {exc}"
        ) from exc


def wait_for_persistence_owners(
    database_path: Path,
    *,
    timeout_sec: float = DEFAULT_OWNER_WAIT_TIMEOUT_SEC,
    poll_interval_sec: float = DEFAULT_OWNER_POLL_INTERVAL_SEC,
    expiry_grace_sec: float = DEFAULT_OWNER_EXPIRY_GRACE_SEC,
    monotonic_fn: Callable[[], float] = time.monotonic,
    wall_time_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until no unexpired durable owner lease can conflict with maintenance.

    Windows cannot deliver EveJS's normal POSIX shutdown signal through
    ``Popen.terminate()``.  The root process can therefore exit before its
    world, wallet, and scheduler leases expire.  Port closure is not an
    ownership barrier, so provisioning waits on the durable lease records
    before it reads or backs up mutable game data.  The helper still performs
    the authoritative exclusive acquisition immediately before mutation.
    """
    database_path = Path(database_path)
    timeout = max(0.0, float(timeout_sec))
    poll_interval = max(0.001, float(poll_interval_sec))
    expiry_grace = max(0.0, float(expiry_grace_sec))
    deadline: float | None = None

    while True:
        now_ms = int(wall_time_fn() * 1000)
        owners = _active_persistence_owners(database_path, now_ms=now_ms)
        if not owners:
            return

        now_monotonic = monotonic_fn()
        if deadline is None:
            # Respect a configured lease longer than the normal 30 seconds,
            # but anchor the deadline to the first observation. A live process
            # that keeps renewing cannot extend this wait indefinitely.
            initial_expiry_wait = max(
                0.0,
                (max(owner.lease_expires_at_ms for owner in owners) - now_ms)
                / 1000,
            )
            deadline = now_monotonic + max(
                timeout,
                initial_expiry_wait + expiry_grace,
            )

        remaining = deadline - now_monotonic
        if remaining <= 0:
            roles = ", ".join(owner.role for owner in owners)
            raise PersistenceOwnerWaitError(
                "EveJS persistence is still active for "
                f"{roles}. Verify that every game-server Node process is stopped, "
                "then try again."
            )

        next_expiry_sec = max(
            0.001,
            (min(owner.lease_expires_at_ms for owner in owners) - now_ms) / 1000,
        )
        sleep_fn(min(poll_interval, next_expiry_sec, remaining))


def _guard_helper_path() -> Path:
    return Path(__file__).resolve().parent / "helpers" / "maintenance_lease_guard.js"


class _MaintenanceLeaseGuard:
    """Own one helper process that retains EveJS's exclusive lease."""

    def __init__(
        self,
        evejs_root: Path,
        game_store: Path,
        *,
        start_timeout_sec: float,
        stop_timeout_sec: float,
    ) -> None:
        self._evejs_root = Path(evejs_root)
        self._game_store = Path(game_store)
        self._start_timeout_sec = max(0.1, float(start_timeout_sec))
        self._stop_timeout_sec = max(0.1, float(stop_timeout_sec))
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None

    @staticmethod
    def _result_message(payload: object, fallback: str) -> str:
        if isinstance(payload, dict):
            message = str(payload.get("error") or "").strip()
            if message:
                return message
        return fallback

    def _start_reader(self, process: subprocess.Popen[str]) -> None:
        def read_output() -> None:
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    self._lines.put(line.rstrip("\r\n"))
            finally:
                self._lines.put(None)

        self._reader = threading.Thread(
            target=read_output,
            name="evejs-maintenance-lease-output",
            daemon=True,
        )
        self._reader.start()

    def _next_protocol_payload(
        self,
        prefixes: tuple[str, ...],
        *,
        timeout_sec: float,
    ) -> tuple[str, dict[str, object]]:
        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PersistenceMaintenanceLeaseError(
                    "Timed out while coordinating EveJS maintenance ownership."
                )
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise PersistenceMaintenanceLeaseError(
                    "Timed out while coordinating EveJS maintenance ownership."
                ) from exc
            if line is None:
                raise PersistenceMaintenanceLeaseError(
                    "The EveJS maintenance lease helper exited unexpectedly."
                )
            prefix = next(
                (candidate for candidate in prefixes if line.startswith(candidate)),
                None,
            )
            if prefix is None:
                continue
            try:
                payload = json.loads(line[len(prefix) :])
            except json.JSONDecodeError as exc:
                raise PersistenceMaintenanceLeaseError(
                    "The EveJS maintenance lease helper returned invalid data."
                ) from exc
            if not isinstance(payload, dict):
                raise PersistenceMaintenanceLeaseError(
                    "The EveJS maintenance lease helper returned invalid data."
                )
            return prefix, payload

    def _abort(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            # Closing stdin lets a helper that acquired just before our timeout
            # follow its normal public shutdown path and release the lease.
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=min(self._stop_timeout_sec, 10.0))
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def acquire(self) -> None:
        helper = _guard_helper_path()
        if not helper.is_file():
            raise PersistenceMaintenanceLeaseError(
                "The bundled EveJS maintenance lease helper is missing."
            )
        environment = os.environ.copy()
        environment["EVEJS_GAMESTORE_SQLITE_PATH"] = str(
            self._game_store / "gamestore.sqlite"
        )
        environment["EVEJS_GAMESTORE_DATA_DIR"] = str(self._game_store / "data")
        environment["EVEJS_GAMESTORE_OWNER_ROLE"] = "maintenance"
        try:
            process = subprocess.Popen(
                ["node", str(helper)],
                cwd=str(self._evejs_root),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **get_hidden_process_flags(),
            )
        except FileNotFoundError as exc:
            raise PersistenceMaintenanceLeaseError(
                "Node.js was not found. Install Node.js or add it to PATH."
            ) from exc
        self._process = process
        self._start_reader(process)
        try:
            prefix, payload = self._next_protocol_payload(
                (_READY_PREFIX, _RESULT_PREFIX),
                timeout_sec=self._start_timeout_sec,
            )
            if prefix != _READY_PREFIX or payload.get("ok") is not True:
                raise PersistenceMaintenanceLeaseError(
                    self._result_message(
                        payload,
                        "EveJS maintenance ownership could not be acquired.",
                    )
                )
        except Exception:
            self._abort()
            raise

    def release(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.poll() is not None or process.stdin is None:
                raise PersistenceMaintenanceLeaseError(
                    "The EveJS maintenance lease ended before cleanup completed."
                )
            process.stdin.write("release\n")
            process.stdin.flush()
            process.stdin.close()
            _prefix, payload = self._next_protocol_payload(
                (_RESULT_PREFIX,),
                timeout_sec=self._stop_timeout_sec,
            )
            try:
                return_code = process.wait(timeout=self._stop_timeout_sec)
            except subprocess.TimeoutExpired as exc:
                raise PersistenceMaintenanceLeaseError(
                    "EveJS did not release maintenance ownership in time."
                ) from exc
            if (
                return_code != 0
                or payload.get("ok") is not True
                or payload.get("released") is not True
            ):
                raise PersistenceMaintenanceLeaseError(
                    self._result_message(
                        payload,
                        "EveJS maintenance ownership cleanup failed.",
                    )
                )
        except Exception:
            self._abort()
            raise
        finally:
            self._process = None


@contextmanager
def hold_persistence_maintenance(
    evejs_root: Path,
    game_store: Path,
    *,
    start_timeout_sec: float = DEFAULT_GUARD_START_TIMEOUT_SEC,
    stop_timeout_sec: float = DEFAULT_GUARD_STOP_TIMEOUT_SEC,
) -> Iterator[None]:
    """Atomically retain the EveJS maintenance lease across Python file I/O."""
    # EveJS v0.12.4 has no durable owner authority. Importing its GameStore can
    # perform recovery, so preserve the legacy backup-before-import ordering.
    if not _has_persistence_owner_schema(Path(game_store) / "gamestore.sqlite"):
        yield
        return

    guard = _MaintenanceLeaseGuard(
        evejs_root,
        game_store,
        start_timeout_sec=start_timeout_sec,
        stop_timeout_sec=stop_timeout_sec,
    )
    guard.acquire()
    body_error: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        body_error = exc
        raise
    finally:
        try:
            guard.release()
        except PersistenceMaintenanceLeaseError as release_error:
            if body_error is not None:
                raise PersistenceMaintenanceLeaseError(
                    "Maintenance work failed and EveJS ownership cleanup also failed: "
                    f"{release_error}"
                ) from body_error
            raise
