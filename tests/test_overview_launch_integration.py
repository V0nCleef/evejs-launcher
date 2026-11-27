"""Launch-path integration for overview capture and pending apply commands."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.db import Account, Character
from src.core.launcher import ClientLaunchContext
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus
from src.core.overview_state import OverviewBridgeLaunch, pending_overview_source
from src.core.runtime.data import RuntimeDataSelection
from src.core.runtime.docker_compose import ComposeTarget
from src.core.service_status import RuntimeBackend, RuntimeSnapshot, ServiceState
from src.ui import overview_copy
from src.workers.overview_patch_worker import OverviewPatchAction


class _Tracker:
    @staticmethod
    def is_account_running(_username: str) -> bool:
        return False


def test_native_launch_request_carries_verified_overview_command(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "runtime_backend": "native",
            "evejs_root": str(tmp_path / "evejs"),
            "client_path": str(tmp_path / "client"),
        }
    )
    window._cfg = cfg
    window._resolve_configured_client_path = (
        lambda client_path, _evejs_root: Path(client_path)
    )
    window._tracker = _Tracker()
    window._pending_client_launches = set()
    window._accounts = []
    bridge = OverviewBridgeLaunch("capture|140000007", tmp_path / "capture.ack")
    monkeypatch.setattr(app_module, "pending_overview_source", lambda _id: None)
    monkeypatch.setattr(
        app_module,
        "inspect_overview_patch",
        lambda _path: OverviewPatchStatus(
            OverviewPatchState.PATCHED,
            "patched",
            3396210,
        ),
    )
    monkeypatch.setattr(
        app_module,
        "prepare_overview_launch",
        lambda _id, *, separate_settings: bridge if separate_settings else None,
    )
    try:
        request = window._make_client_launch_request(
            "fixture-account",
            "Fixture Pilot",
            140000007,
        )
    finally:
        window.deleteLater()

    assert request is not None
    assert request.overview_bridge == bridge


@pytest.fixture(params=("managed", "connect_only"))
def docker_overviews(qapp, tmp_path, monkeypatch, request):
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = dict(config.DEFAULT_CONFIG, runtime_backend="docker_compose",
                       docker_control_policy=request.param, auto_login_enabled=False,
                       evejs_root=str(tmp_path / "evejs"), client_path=str(tmp_path / "tq"))
    window._monitor_generation = 1
    window._docker_monitor_settings_identity = lambda: "docker-settings"
    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.ONLINE, 0, backend=RuntimeBackend.DOCKER_COMPOSE,
        target_identity="docker:fixture", settings_identity="docker-settings", monitor_generation=1,
        game_runtime_identity="a" * 64,
    )
    window._data_selection = RuntimeDataSelection(
        None, None, "docker:fixture", "docker-settings", 1, "docker-game-store:" + "b" * 64,
    )
    window._tracker = SimpleNamespace(running_count=0, is_account_running=lambda _: False)
    window._new_character_dialog = None
    window._accounts = [Account(f"account-{cid}", cid, "", False, [Character(cid, f"Pilot {cid}")])
                        for cid in (10, 20, 30)]
    window._resolve_configured_client_path = lambda path, _: Path(path)
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    window._docker_log_target_factory = lambda: lambda: target
    window._resolve_client_launch_context = lambda: (ClientLaunchContext(
        "127.0.0.1", 43010, "http://127.0.0.1:43012", target_identity=window._runtime_snapshot.target_identity,
        settings_identity="docker-settings", monitor_generation=1,
    ), "")
    monkeypatch.setattr(app_module, "PROFILES_ROOT", tmp_path / "profile-state/Profiles")
    patch = lambda _: OverviewPatchStatus(OverviewPatchState.PATCHED, "patched", 3396210)
    monkeypatch.setattr(app_module, "inspect_overview_patch", patch)
    monkeypatch.setattr(overview_copy, "inspect_overview_patch", patch)
    monkeypatch.setattr(overview_copy, "is_eve_client_running", lambda: False)
    notices = []
    monkeypatch.setattr(overview_copy.QMessageBox, "information", lambda *args: notices.append(args[2]))
    try:
        yield window, notices
    finally:
        window.deleteLater()


def _launch(window, cid):
    request = window._make_client_launch_request(f"account-{cid}", f"Pilot {cid}", cid)
    assert request is not None
    return request


def _capture(window):
    request = _launch(window, 10)
    assert not request.auto_login_enabled and request.docker_auto_login_probe is not None
    assert request.overview_bridge.command == "capture|10"
    request.overview_bridge.ack_path.write_text(f"capture|10|{'a' * 40}|7", encoding="utf-8")
    window._poll_overview_acks()


def _accept_copy(monkeypatch, callback=lambda: None):
    class SelectRecipients(overview_copy.OverviewCopyDialog):
        def exec(self):
            assert self.source.currentData() == 10
            for index in range(self.recipients.count()):
                self.recipients.item(index).setCheckState(Qt.CheckState.Checked)
            callback()
            return QDialog.DialogCode.Accepted
    monkeypatch.setattr(overview_copy, "OverviewCopyDialog", SelectRecipients)


def test_docker_overview_capture_multi_copy_and_acknowledgement(docker_overviews, monkeypatch):
    window, notices = docker_overviews
    _capture(window)
    _accept_copy(monkeypatch)
    overview_copy.open_overview_copy(window)
    assert notices == [overview_copy.QUEUED]
    scope = window._overview_kwargs()
    first = _launch(window, 20).overview_bridge
    second = _launch(window, 30).overview_bridge
    assert first.command == f"apply|20|{'a' * 40}|7"
    assert second.command == f"apply|30|{'a' * 40}|7"
    assert first.ack_path != second.ack_path
    first.ack_path.write_text(first.command, encoding="utf-8")
    window._poll_overview_acks()
    assert _launch(window, 20).overview_bridge.command == "capture|20"
    assert pending_overview_source(20, **scope) is None
    assert pending_overview_source(30, **scope) == 10
    assert _launch(window, 30).overview_bridge == second


def test_docker_overviews_follow_save_storage_and_keep_native_separate(docker_overviews):
    window, notices = docker_overviews
    _capture(window)
    scope = window._overview_kwargs()
    window._runtime_snapshot = replace(window._runtime_snapshot, target_identity="docker:new-config")
    assert window._overview_kwargs() is None
    overview_copy.open_overview_copy(window)
    assert notices == [overview_copy.DOCKER_ROSTER_REQUIRED]
    assert _launch(window, 20).overview_bridge is None
    window._data_selection = replace(window._data_selection, target_identity="docker:new-config")
    assert window._overview_kwargs() == scope
    assert window._snapshot_ready_ids() == {10}
    window._data_selection = replace(window._data_selection, group_identity="docker-game-store:" + "c" * 64)
    assert window._overview_kwargs() != scope
    assert window._snapshot_ready_ids() == set()
    window._cfg["runtime_backend"] = "native"
    assert window._overview_kwargs() == {}


def test_docker_overview_copy_rejects_target_switch_in_dialog(docker_overviews, monkeypatch):
    window, notices = docker_overviews
    _capture(window)
    scope = window._overview_kwargs()

    def switch_target():
        window._runtime_snapshot = replace(window._runtime_snapshot, target_identity="docker:new-config")
        window._data_selection = replace(window._data_selection, target_identity="docker:new-config")

    _accept_copy(monkeypatch, switch_target)
    overview_copy.open_overview_copy(window)
    assert "changed" in notices[0]
    assert pending_overview_source(20, **scope) is None


@pytest.mark.parametrize(("state", "action"), (
    (OverviewPatchState.READY, OverviewPatchAction.PATCH),
    (OverviewPatchState.LEGACY, OverviewPatchAction.RESTORE),
))
def test_docker_settings_offers_local_bridge_patch(docker_overviews, monkeypatch, state, action):
    window, _ = docker_overviews
    monkeypatch.setattr(overview_copy, "inspect_overview_patch", lambda _: OverviewPatchStatus(state, "", 3396210))
    operations = []
    window._begin_overview_patch = lambda operation, *, from_settings: operations.append((operation, from_settings))
    overview_copy.open_overview_copy(window)
    assert operations == [(action, True)]
