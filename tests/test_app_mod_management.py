"""Application-level orchestration for launcher-native mod removal."""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QMainWindow

from src import app as app_module
from src.app import MainWindow
from src.core.mod_management import (
    INNO_USER_PROVIDER,
    MANAGED_MOD_SCHEMA_VERSION,
    ManagedModRegistration,
    ManagedModRemovalRequest,
    ManagedModRemovalResult,
    ModDataPolicy,
    RemovalInventoryEntry,
)
from src.core.mod_manager import Mod
from src.workers.mod_management_worker import ManagedModRemovalWorker


class _FakeModsPage:
    def __init__(self) -> None:
        self.refresh_count = 0
        self.lifecycle_busy = False

    def refresh_mods(self) -> None:
        self.refresh_count += 1

    def set_lifecycle_busy(self, busy: bool) -> None:
        self.lifecycle_busy = busy


class _LiveProcess:
    @staticmethod
    def poll() -> None:
        return None


class _FinishedThread:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:
        self.deleted = True


def _window() -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {"evejs_root": r"C:\fixture\EveJS"}
    window._server_proc = None
    window._market_proc = None
    window._lifecycle_thread = None
    window._mods_page = _FakeModsPage()
    return window


def _mod(tmp_path: Path) -> Mod:
    return Mod(
        name="EveJS Temp NPC",
        path=tmp_path / "_local" / "evejs-temp-npc",
        active=True,
        id="evejs-temp-npc",
        version="0.4.2",
        evejs_root=tmp_path,
    )


def _registration(tmp_path: Path) -> ManagedModRegistration:
    return ManagedModRegistration(
        schema_version=MANAGED_MOD_SCHEMA_VERSION,
        provider=INNO_USER_PROVIDER,
        app_id="{3CB3F7D0-7068-4C88-98A9-41A38C52B672}",
        mod_id="evejs-temp-npc",
        display_name="EveJS Temp NPC",
        package_version="0.4.2",
        evejs_root=tmp_path,
        activation_contract_sha256="a" * 64,
        bundle_sha256="b" * 64,
        expand_helper_sha256="c" * 64,
        current_pointer_sha256="d" * 64,
        removal_inventory_path=tmp_path / "removal-inventory.json",
        removal_inventory_sha256="e" * 64,
        removal_inventory=(RemovalInventoryEntry("server/mods/evejs-temp-npc/evejs-launcher.mod.json", "absent"),),
        uninstaller_path=tmp_path / "unins000.exe",
        uninstaller_sha256="f" * 64,
        uninstaller_data_path=tmp_path / "unins000.dat",
        uninstaller_data_sha256="1" * 64,
        supports_purge_state=True,
    )


def test_removal_stops_launcher_owned_stack_then_starts_worker(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window()
    registration = _registration(tmp_path)
    candidate = _mod(tmp_path)
    window._server_proc = _LiveProcess()
    window._market_proc = _LiveProcess()
    window._docker_mode = lambda: False
    window._lifecycle_active = lambda: False
    window._server_process_alive = lambda: True
    window._ask_mod_removal_policy = lambda _registration: ModDataPolicy.KEEP
    started: list[ManagedModRemovalRequest] = []
    window._begin_managed_mod_removal = started.append
    stop_calls: list[dict[str, object]] = []

    def stop(**kwargs: object) -> bool:
        stop_calls.append(kwargs)
        callback = kwargs["on_complete"]
        assert callable(callback)
        callback()
        return True

    window._run_stop_sequence = stop
    monkeypatch.setattr(
        app_module,
        "read_managed_mod_registration",
        lambda _candidate: registration,
    )

    try:
        window._on_mod_remove_requested(candidate)
        assert len(stop_calls) == 1
        assert stop_calls[0]["stop_game"] is True
        assert stop_calls[0]["stop_market"] is True
        assert stop_calls[0]["allow_force_game_kill"] is False
        assert started == [
            ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
        ]
    finally:
        window.deleteLater()


def test_removal_refuses_server_started_outside_launcher(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window()
    registration = _registration(tmp_path)
    candidate = _mod(tmp_path)
    window._docker_mode = lambda: False
    window._lifecycle_active = lambda: False
    window._server_process_alive = lambda: False
    window._ask_mod_removal_policy = lambda _registration: (_ for _ in ()).throw(
        AssertionError("external services must be rejected before prompting")
    )
    window._run_stop_sequence = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("external services must never be stopped or modified")
    )
    warnings: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        app_module,
        "read_managed_mod_registration",
        lambda _candidate: registration,
    )
    monkeypatch.setattr(
        app_module,
        "is_server_running",
        lambda *, port: port == int(app_module.Ports.GAME_TCP),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )

    try:
        window._on_mod_remove_requested(candidate)
        assert len(warnings) == 1
        assert warnings[0][1] == "Stop External EveJS Services"
    finally:
        window.deleteLater()


def test_removal_rechecks_external_ports_after_confirmation_before_worker(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window()
    registration = _registration(tmp_path)
    candidate = _mod(tmp_path)
    window._docker_mode = lambda: False
    window._lifecycle_active = lambda: False
    window._mod_removal_conflict_active = lambda: False
    window._server_process_alive = lambda: False
    window._ask_mod_removal_policy = lambda _registration: ModDataPolicy.KEEP
    warnings: list[tuple[object, ...]] = []
    probes: list[int] = []

    def server_running(*, port: int) -> bool:
        probes.append(port)
        # Initial Game/Market checks are clear. The Game port becomes reachable
        # only after confirmation, at the final worker-launch boundary.
        return len(probes) == 3

    def stop(**kwargs: object) -> bool:
        assert kwargs["stop_game"] is False
        assert kwargs["stop_market"] is False
        callback = kwargs["on_complete"]
        assert callable(callback)
        callback()
        return True

    window._run_stop_sequence = stop
    window._managed_mod_removal_worker_factory = lambda _request: pytest.fail(
        "removal worker must not be created while an EveJS port is live"
    )
    monkeypatch.setattr(
        app_module,
        "read_managed_mod_registration",
        lambda _candidate: registration,
    )
    monkeypatch.setattr(app_module, "is_server_running", server_running)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )

    try:
        window._on_mod_remove_requested(candidate)

        assert probes == [
            int(app_module.Ports.GAME_TCP),
            int(app_module.Ports.MARKET_RPC),
            int(app_module.Ports.GAME_TCP),
            int(app_module.Ports.MARKET_RPC),
        ]
        assert warnings and warnings[-1][1] == "Stop External EveJS Services"
        assert "reachable again" in warnings[-1][2]
        assert window._lifecycle_thread is None
    finally:
        window.deleteLater()


@pytest.mark.parametrize(
    "active_attribute",
    (
        "_character_creation_thread",
        "_character_creation_request",
        "_character_deletion_thread",
        "_character_deletion_request",
    ),
)
def test_removal_refuses_active_native_character_maintenance(
    qapp,
    tmp_path: Path,
    monkeypatch,
    active_attribute: str,
) -> None:
    window = _window()
    candidate = _mod(tmp_path)
    setattr(window, active_attribute, object())
    window._docker_mode = lambda: False
    window._lifecycle_active = lambda: False
    window._ask_mod_removal_policy = lambda _registration: (_ for _ in ()).throw(
        AssertionError("maintenance must be rejected before prompting")
    )
    window._run_stop_sequence = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("maintenance must be rejected before server shutdown")
    )
    monkeypatch.setattr(
        app_module,
        "read_managed_mod_registration",
        lambda _candidate: (_ for _ in ()).throw(
            AssertionError("maintenance must be rejected before registration reads")
        ),
    )
    notices: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda *args: notices.append(args),
    )

    try:
        window._on_mod_remove_requested(candidate)
        assert len(notices) == 1
        assert notices[0][1] == "Mod Removal Busy"
    finally:
        window.deleteLater()


def test_successful_worker_result_refreshes_mods_and_releases_lifecycle(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window()
    request = ManagedModRemovalRequest(
        _registration(tmp_path),
        ModDataPolicy.KEEP,
    )
    result = ManagedModRemovalResult(
        request=request,
        success=True,
        message="EveJS Temp NPC was removed from EveJS.",
        log_path=tmp_path / "remove.log",
    )
    published: list[object] = []
    finished: list[bool] = []
    notices: list[tuple[object, ...]] = []
    window._publish_mod_runtime_snapshot = published.append
    window._finish_lifecycle_if_complete = lambda: finished.append(True)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda *args: notices.append(args),
    )

    try:
        window._on_managed_mod_removal_completed(result)
        assert published == [None]
        assert window._mods_page.refresh_count == 1
        assert window._lifecycle_result_received is True
        assert finished == [True]
        assert notices[0][1] == "Mod Removed"
    finally:
        window.deleteLater()


def test_missing_removal_worker_result_releases_slot_with_terminal_failure(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window()
    request = ManagedModRemovalRequest(
        _registration(tmp_path),
        ModDataPolicy.KEEP,
    )
    worker = ManagedModRemovalWorker(request)
    thread = _FinishedThread()
    failures: list[str] = []
    window._lifecycle_worker = worker
    window._lifecycle_thread = thread
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    try:
        window._on_lifecycle_thread_finished()
        assert window._lifecycle_thread is thread

        window._recover_missing_mod_removal_result(worker)

        assert window._lifecycle_thread is None
        assert window._lifecycle_worker is None
        assert thread.deleted
        assert window._mods_page.refresh_count == 1
        assert failures and "without a terminal result" in failures[-1]
    finally:
        window.deleteLater()


def test_managed_removal_worker_start_exception_does_not_claim_lifecycle_slot(
    qapp,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = _window()
    request = ManagedModRemovalRequest(
        _registration(tmp_path),
        ModDataPolicy.KEEP,
    )
    failures: list[str] = []
    window._lifecycle_active = lambda: False

    class FailingStartThread(QThread):
        def start(self, *args, **kwargs) -> None:  # type: ignore[override]
            del args, kwargs
            raise RuntimeError("simulated QThread start failure")

    monkeypatch.setattr(app_module, "QThread", FailingStartThread)
    monkeypatch.setattr(
        app_module,
        "is_server_running",
        lambda *, port: False,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    try:
        window._begin_managed_mod_removal(request)

        assert window._lifecycle_thread is None
        assert window._lifecycle_worker is None
        assert window._lifecycle_result_received is False
        assert window._lifecycle_thread_finished is False
        assert window._mods_page.lifecycle_busy is False
        assert failures and "simulated QThread start failure" in failures[-1]
    finally:
        window.deleteLater()
