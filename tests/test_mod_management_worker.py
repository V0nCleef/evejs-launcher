"""One-shot worker contracts for launcher-native mod removal."""
from __future__ import annotations

from pathlib import Path

from src.core.mod_management import (
    INNO_USER_PROVIDER,
    MANAGED_MOD_SCHEMA_VERSION,
    ManagedModRegistration,
    ManagedModRemovalRequest,
    ManagedModRemovalResult,
    ModDataPolicy,
    RemovalInventoryEntry,
)
from src.workers.mod_management_worker import ManagedModRemovalWorker


def _request(tmp_path: Path) -> ManagedModRemovalRequest:
    registration = ManagedModRegistration(
        schema_version=MANAGED_MOD_SCHEMA_VERSION,
        provider=INNO_USER_PROVIDER,
        app_id="{3CB3F7D0-7068-4C88-98A9-41A38C52B672}",
        mod_id="evejs-temp-npc",
        display_name="EveJS Temp NPC",
        package_version="0.4.2-prototype",
        evejs_root=tmp_path / "eve",
        activation_contract_sha256="a" * 64,
        bundle_sha256="b" * 64,
        expand_helper_sha256="c" * 64,
        current_pointer_sha256="d" * 64,
        removal_inventory_path=tmp_path / "kit" / "removal-inventory.json",
        removal_inventory_sha256="e" * 64,
        removal_inventory=(RemovalInventoryEntry("server/mods/evejs-temp-npc/evejs-launcher.mod.json", "absent"),),
        uninstaller_path=tmp_path / "kit" / "unins000.exe",
        uninstaller_sha256="f" * 64,
        uninstaller_data_path=tmp_path / "kit" / "unins000.dat",
        uninstaller_data_sha256="1" * 64,
        supports_purge_state=True,
    )
    return ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)


def test_worker_emits_the_executor_terminal_result(qapp, tmp_path: Path) -> None:
    request = _request(tmp_path)
    expected = ManagedModRemovalResult(
        request=request,
        success=True,
        message="removed",
        log_path=tmp_path / "remove.log",
    )
    results: list[ManagedModRemovalResult] = []
    worker = ManagedModRemovalWorker(request, executor=lambda _request: expected)
    worker.completed.connect(results.append)

    worker.run()

    assert results == [expected]


def test_worker_converts_executor_failure_to_one_failure_result(
    qapp,
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    results: list[ManagedModRemovalResult] = []

    def fail(_request: ManagedModRemovalRequest) -> ManagedModRemovalResult:
        raise RuntimeError("fixture uninstaller failed")

    worker = ManagedModRemovalWorker(request, executor=fail)
    worker.completed.connect(results.append)

    worker.run()

    assert len(results) == 1
    assert results[0].request == request
    assert results[0].success is False
    assert results[0].message == "fixture uninstaller failed"


def test_worker_rejects_non_result_executor_output(qapp, tmp_path: Path) -> None:
    request = _request(tmp_path)
    results: list[ManagedModRemovalResult] = []
    worker = ManagedModRemovalWorker(
        request,
        executor=lambda _request: None,  # type: ignore[arg-type,return-value]
    )
    worker.completed.connect(results.append)

    worker.run()

    assert len(results) == 1
    assert results[0].success is False
    assert "invalid result" in results[0].message
