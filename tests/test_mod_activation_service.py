"""Atomic configured-state and durable activation-intent transactions."""
from __future__ import annotations

from pathlib import Path

import pytest

from src import config
from src.core import mod_activation_service as service
from src.core.mod_activation_state import (
    ActivationPhase,
    ModActivationStateWriteError,
    read_mod_activation_state,
)
from src.core.mod_lifecycle_lock import (
    ModLifecycleBusyError,
    acquire_mod_lifecycle_lease,
)
from src.core.mod_manifest import ModActivationError, scan_mods


def _loader(root: Path, *, active: bool = True, name: str = "fixture-loader"):
    folder = root / "mods" / name
    folder.mkdir(parents=True)
    filename = "loader.js" if active else "loader.js.disabled"
    (folder / filename).write_text("module.exports = {};\n", encoding="utf-8")
    return scan_mods(root)[0]


def _use_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher-state")


def test_request_holds_one_lock_across_prepare_mutation_and_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_state_dir(monkeypatch, tmp_path)
    mod = _loader(tmp_path)
    events: list[str] = []
    original_prepare = service.prepare_mod_activation
    original_mutate = service.set_mod_active_locked
    original_pending = service.mark_mod_activation_pending

    def assert_owned(label: str) -> None:
        with pytest.raises(ModLifecycleBusyError):
            acquire_mod_lifecycle_lease(tmp_path, timeout_sec=0)
        events.append(label)

    def prepare(*args, **kwargs):
        assert_owned("prepare")
        return original_prepare(*args, **kwargs)

    def mutate(*args, **kwargs):
        assert_owned("mutate")
        return original_mutate(*args, **kwargs)

    def pending(*args, **kwargs):
        assert_owned("pending")
        return original_pending(*args, **kwargs)

    monkeypatch.setattr(service, "prepare_mod_activation", prepare)
    monkeypatch.setattr(service, "set_mod_active_locked", mutate)
    monkeypatch.setattr(service, "mark_mod_activation_pending", pending)

    assert service.request_mod_activation(mod, False) is False

    assert events == ["prepare", "mutate", "pending"]
    assert (mod.path / "loader.js.disabled").is_file()
    intent = read_mod_activation_state(tmp_path).for_mod(mod.id)
    assert intent is not None
    assert intent.desired is False
    assert intent.phase is ActivationPhase.PENDING_RESTART
    # The transaction released its lease at the exact terminal boundary.
    lease = acquire_mod_lifecycle_lease(tmp_path, timeout_sec=0)
    lease.release()


def test_mutation_failure_is_durable_and_preserves_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_state_dir(monkeypatch, tmp_path)
    mod = _loader(tmp_path)

    def fail(_mod, _desired: bool) -> bool:
        raise PermissionError("fixture denied")

    monkeypatch.setattr(service, "set_mod_active_locked", fail)

    with pytest.raises(PermissionError, match="fixture denied"):
        service.request_mod_activation(mod, False)

    assert (mod.path / "loader.js").is_file()
    intent = read_mod_activation_state(tmp_path).for_mod(mod.id)
    assert intent is not None
    assert intent.phase is ActivationPhase.FAILED
    assert intent.error_code == "activation-mutation-failed"


def test_pending_commit_failure_leaves_recoverable_prepared_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_state_dir(monkeypatch, tmp_path)
    mod = _loader(tmp_path)

    def fail_pending(*_args, **_kwargs):
        raise ModActivationStateWriteError("fixture journal failure")

    monkeypatch.setattr(service, "mark_mod_activation_pending", fail_pending)

    with pytest.raises(ModActivationStateWriteError, match="fixture journal failure"):
        service.request_mod_activation(mod, False)

    assert mod.active is False
    assert (mod.path / "loader.js.disabled").is_file()
    intent = read_mod_activation_state(tmp_path).for_mod(mod.id)
    assert intent is not None
    assert intent.phase is ActivationPhase.PREPARED
    assert intent.desired is False


def test_existing_lifecycle_owner_blocks_before_journal_or_payload_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_state_dir(monkeypatch, tmp_path)
    mod = _loader(tmp_path)
    lease = acquire_mod_lifecycle_lease(tmp_path, timeout_sec=0)
    try:
        with pytest.raises(ModActivationError, match="lifecycle operation"):
            service.request_mod_activation(mod, False)
    finally:
        lease.release()

    assert (mod.path / "loader.js").is_file()
    assert read_mod_activation_state(tmp_path).intents == ()


def test_legacy_loader_name_with_uppercase_and_space_is_journalled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_state_dir(monkeypatch, tmp_path)
    mod = _loader(tmp_path, name="Zeta Mod")

    assert service.request_mod_activation(mod, False) is False

    intent = read_mod_activation_state(tmp_path).for_mod("Zeta Mod")
    assert intent is not None
    assert intent.phase is ActivationPhase.PENDING_RESTART
