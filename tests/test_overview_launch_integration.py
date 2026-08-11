"""Launch-path integration for overview capture and pending apply commands."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus
from src.core.overview_state import OverviewBridgeLaunch


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
        lambda _id: bridge,
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
