"""Cross-process serialization for EveJS mod lifecycle mutations.

The lock file is intentionally stable and is never used as a sentinel.  The
open handle owns the lock, so a crashed launcher or installer releases it
automatically while the harmless file remains in ``_local``.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
from typing import BinaryIO, Iterator


LOCK_RELATIVE_PATH = Path("_local") / ".evejs-mod-lifecycle.lock"


class ModLifecycleBusyError(RuntimeError):
    """Raised when another launcher or installer owns the mod lifecycle."""


_PROCESS_GUARD = threading.Lock()
_PROCESS_HELD_ROOTS: set[str] = set()


class ModLifecycleLease:
    """One explicitly owned cross-process lifecycle lock.

    Async launcher operations cannot use a lexical ``with`` block because the
    lock must survive across worker completion signals.  This small lease keeps
    the file handle alive until the GUI reaches the matching terminal boundary.
    ``release`` is idempotent so every error and close path can safely call it.
    """

    def __init__(self, root: Path, lock_path: Path, stream: BinaryIO) -> None:
        self.root = root
        self.lock_path = lock_path
        self._stream: BinaryIO | None = stream

    @property
    def released(self) -> bool:
        return self._stream is None

    def release(self) -> None:
        """Release the OS handle and in-process claim exactly once."""

        stream = self._stream
        if stream is None:
            return
        self._stream = None
        unlock_error: BaseException | None = None
        try:
            try:
                _unlock_first_byte(stream)
            except BaseException as exc:  # still close: handle closure releases it
                unlock_error = exc
            finally:
                stream.close()
        finally:
            _release_in_process(self.root)
        if unlock_error is not None:
            raise unlock_error

    def __enter__(self) -> "ModLifecycleLease":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root))


def _claim_in_process(root: Path) -> None:
    key = _root_key(root)
    with _PROCESS_GUARD:
        if key in _PROCESS_HELD_ROOTS:
            raise ModLifecycleBusyError(
                "Another mod lifecycle operation is already using this EveJS root."
            )
        _PROCESS_HELD_ROOTS.add(key)


def _release_in_process(root: Path) -> None:
    with _PROCESS_GUARD:
        _PROCESS_HELD_ROOTS.discard(_root_key(root))


def _open_lock_file(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        if os.fstat(descriptor).st_size == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(descriptor)
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


def _lock_first_byte(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl  # pragma: no cover - launcher production is Windows

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_first_byte(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl  # pragma: no cover - launcher production is Windows

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _validated_lock_path(evejs_root: str | Path) -> tuple[Path, Path]:
    try:
        root = Path(evejs_root).resolve(strict=True)
    except OSError as exc:
        raise ModLifecycleBusyError(
            "The selected EveJS root is unavailable."
        ) from exc
    if not root.is_dir():
        raise ModLifecycleBusyError("The selected EveJS root is not a directory.")
    local_candidate = root / "_local"
    try:
        local_candidate.mkdir(exist_ok=True)
        local_root = local_candidate.resolve(strict=True)
    except OSError as exc:
        raise ModLifecycleBusyError(
            "The EveJS _local directory could not be prepared."
        ) from exc
    try:
        local_root.relative_to(root)
    except ValueError as exc:
        raise ModLifecycleBusyError(
            "The EveJS _local directory escapes the selected root."
        ) from exc
    if not local_root.is_dir():
        raise ModLifecycleBusyError("The EveJS _local path is not a directory.")
    lock_path = local_root / LOCK_RELATIVE_PATH.name
    if lock_path.is_symlink():
        raise ModLifecycleBusyError("The mod lifecycle lock path is unsafe.")
    return root, lock_path


def acquire_mod_lifecycle_lease(
    evejs_root: str | Path,
    *,
    timeout_sec: float = 0.25,
    poll_interval_sec: float = 0.05,
) -> ModLifecycleLease:
    """Acquire an explicitly released fixed per-root lifecycle lease."""

    if timeout_sec < 0:
        raise ValueError("Mod lifecycle lock timeout cannot be negative.")
    root, lock_path = _validated_lock_path(evejs_root)
    _claim_in_process(root)
    stream: BinaryIO | None = None
    deadline = time.monotonic() + timeout_sec
    try:
        while True:
            try:
                stream = _open_lock_file(lock_path)
                _lock_first_byte(stream)
                break
            except OSError as exc:
                if stream is not None:
                    stream.close()
                    stream = None
                if time.monotonic() >= deadline:
                    raise ModLifecycleBusyError(
                        "Another launcher or installer is changing mods for this EveJS root."
                    ) from exc
                time.sleep(min(poll_interval_sec, max(0.0, deadline - time.monotonic())))
        return ModLifecycleLease(root, lock_path, stream)
    except BaseException:
        if stream is not None:
            stream.close()
        _release_in_process(root)
        raise


@contextmanager
def acquire_mod_lifecycle_lock(
    evejs_root: str | Path,
    *,
    timeout_sec: float = 0.25,
    poll_interval_sec: float = 0.05,
) -> Iterator[Path]:
    """Acquire the fixed per-root lock for a lexical mutation transaction."""

    lease = acquire_mod_lifecycle_lease(
        evejs_root,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
    )
    try:
        yield lease.lock_path
    finally:
        lease.release()
