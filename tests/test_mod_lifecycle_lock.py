from pathlib import Path

import pytest

from src.core.mod_lifecycle_lock import (
    LOCK_RELATIVE_PATH,
    ModLifecycleBusyError,
    acquire_mod_lifecycle_lease,
    acquire_mod_lifecycle_lock,
)


def _evejs_root(tmp_path: Path) -> Path:
    root = tmp_path / "EveJS"
    (root / "_local").mkdir(parents=True)
    return root


def test_mod_lifecycle_lock_is_handle_owned_and_leaves_stable_file(
    tmp_path: Path,
) -> None:
    root = _evejs_root(tmp_path)

    with acquire_mod_lifecycle_lock(root) as lock_path:
        assert lock_path == root / LOCK_RELATIVE_PATH
        assert lock_path.is_file()

    assert (root / LOCK_RELATIVE_PATH).is_file()
    assert (root / LOCK_RELATIVE_PATH).read_bytes() == b"\0"


def test_mod_lifecycle_lock_rejects_nested_same_root_operation(
    tmp_path: Path,
) -> None:
    root = _evejs_root(tmp_path)

    with acquire_mod_lifecycle_lock(root):
        with pytest.raises(ModLifecycleBusyError, match="already using"):
            with acquire_mod_lifecycle_lock(root):
                pytest.fail("nested lifecycle unexpectedly acquired")


def test_explicit_lifecycle_lease_holds_until_idempotent_release(
    tmp_path: Path,
) -> None:
    root = _evejs_root(tmp_path)

    lease = acquire_mod_lifecycle_lease(root)
    assert lease.root == root.resolve()
    assert lease.lock_path == root / LOCK_RELATIVE_PATH
    assert lease.released is False
    with pytest.raises(ModLifecycleBusyError, match="already using"):
        acquire_mod_lifecycle_lease(root)

    lease.release()
    lease.release()
    assert lease.released is True

    second = acquire_mod_lifecycle_lease(root)
    second.release()


def test_mod_lifecycle_lock_creates_missing_local_root(tmp_path: Path) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()

    with acquire_mod_lifecycle_lock(root) as lock_path:
        assert lock_path == root / LOCK_RELATIVE_PATH

    assert (root / "_local").is_dir()
    assert (root / LOCK_RELATIVE_PATH).read_bytes() == b"\0"


def test_mod_lifecycle_lock_rejects_negative_timeout(tmp_path: Path) -> None:
    root = _evejs_root(tmp_path)

    with pytest.raises(ValueError, match="cannot be negative"):
        with acquire_mod_lifecycle_lock(root, timeout_sec=-1):
            pytest.fail("negative timeout unexpectedly accepted")
