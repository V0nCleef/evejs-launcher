"""Regression tests for Home/footer runtime status agreement."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from src import config
from src import app as app_module
from src.app import MainWindow
from src.audio.events import VoiceEvent
from src.constants import COLORS, Ports
from src.core.mod_runtime_state import (
    NATIVE_BACKEND,
    RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    ModRuntimeSnapshot,
)
from src.core.service_status import ServiceState
from src.workers.server_worker import ServiceProbe, ServiceStartResult


def _window_config() -> dict:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "",
            "client_path": "",
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    return cfg


def _native_runtime_snapshot(*, pid: int) -> ModRuntimeSnapshot:
    return ModRuntimeSnapshot(
        schema_version=RUNTIME_SNAPSHOT_SCHEMA_VERSION,
        root=Path("C:/Games/EveJS"),
        backend=NATIVE_BACKEND,
        mode="modded",
        runtime_identity="native-runtime-fixture",
        plan_sha256="a" * 64,
        docker_override_path=None,
        docker_override_sha256=None,
        docker_node_options_sha256=None,
        selected_loader_ids=(),
        pid=pid,
        observed_at=datetime.now(timezone.utc),
        mods=(),
    )


@pytest.fixture
def status_window(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MainWindow, dict[str, bool]]:
    cfg = _window_config()
    probes = {"game": False, "market": False}
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])
    monkeypatch.setattr(
        app_module,
        "is_server_running",
        lambda _host="127.0.0.1", port=int(Ports.GAME_TCP), **_kwargs: (
            probes["game"] if port == int(Ports.GAME_TCP) else probes["market"]
        ),
    )

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    yield window, probes
    window.deleteLater()


def test_online_game_probe_updates_footer_and_home(
    status_window: tuple[MainWindow, dict[str, bool]],
) -> None:
    window, probes = status_window
    probes["game"] = True

    window._update_status_bar()

    assert window._status_bar.server_section.label.text() == "Server: Online"
    assert window._home_page.server_card._state_label.text() == "Online"
    assert COLORS["green"] in window._home_page.server_card._dot.styleSheet()


def test_external_game_service_is_not_advertised_as_stoppable(
    status_window: tuple[MainWindow, dict[str, bool]],
) -> None:
    window, probes = status_window
    probes["game"] = True

    window._update_status_bar()

    assert window._runtime_snapshot.game_owned is False
    assert window._nav.btn_server.text() == "Server: External"
    assert window._nav.btn_server.isEnabled() is False
    assert window._home_page.btn_start_servers.text() == "Start Managed Services"


def test_owned_game_and_external_market_remain_independently_represented(
    status_window: tuple[MainWindow, dict[str, bool]],
) -> None:
    window, probes = status_window
    probes["game"] = True
    probes["market"] = True

    class AliveProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    window._server_proc = AliveProcess()
    window._update_status_bar()

    assert window._runtime_snapshot.game_owned is True
    assert window._runtime_snapshot.market_owned is False
    assert window._nav.btn_server.text() == "■ Stop Server"
    assert window._nav.btn_market.text() == "Market: External"
    assert window._nav.btn_market.isEnabled() is False
    assert window._home_page.services_card.game_row.detail_text == "PID 4321"
    assert window._home_page.services_card.market_row.detail_text == ""
    assert window._home_page.btn_start_servers.text() == "Stop Managed Services"


def test_offline_game_probe_updates_footer_and_home(
    status_window: tuple[MainWindow, dict[str, bool]],
) -> None:
    window, probes = status_window
    probes["game"] = False
    window._server_proc = None

    window._update_status_bar()

    assert window._status_bar.server_section.label.text() == "Server: Offline"
    assert window._home_page.server_card._state_label.text() == "Offline"
    assert COLORS["red"] in window._home_page.server_card._dot.styleSheet()
    assert COLORS["red"] in window._home_page.server_card._state_label.styleSheet()
    assert window._home_page.server_card._ring.signal_color == COLORS["red"]


def test_owned_live_process_reports_starting_everywhere(
    status_window: tuple[MainWindow, dict[str, bool]],
) -> None:
    window, probes = status_window
    probes["game"] = False

    class AliveProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    window._server_proc = AliveProcess()

    window._update_status_bar()

    assert window._status_bar.server_section.label.text() == "Server: Starting..."
    assert window._home_page.server_card._state_label.text() == "Starting…"
    assert COLORS["gold"] in window._home_page.server_card._dot.styleSheet()


def test_native_start_immediately_reports_starting_before_worker_returns(
    status_window: tuple[MainWindow, dict[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    window, _probes = status_window
    window._cfg["evejs_root"] = str(tmp_path)
    workers: list[object] = []
    monkeypatch.setattr(
        window,
        "_begin_lifecycle_worker",
        lambda worker, _completed_handler: workers.append(worker),
    )

    started = window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="vanilla",
        on_ready=None,
        error_title="Game Server Error",
    )

    assert started is True
    assert len(workers) == 1
    assert window._runtime_snapshot.game is ServiceState.STARTING
    assert window._runtime_snapshot.game_pid is None
    assert window._status_bar.server_section.label.text() == "Server: Starting..."
    assert window._nav.btn_server.text() == "⏳ Starting Server…"
    assert window._nav.btn_server.isEnabled() is False
    assert window._home_page.server_card._state_label.text() == "Starting…"
    assert window._home_page.btn_start_servers.text() == "Starting…"
    assert window._home_page.btn_start_servers.isEnabled() is False


def test_service_probe_fans_out_game_and_market_without_another_socket_probe(
    status_window: tuple[MainWindow, dict[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _probes = status_window
    monkeypatch.setattr(
        app_module,
        "is_server_running",
        lambda **_kwargs: pytest.fail("probe result must be reused by every surface"),
    )

    window._on_service_probe(
        ServiceProbe(game_reachable=True, market_reachable=True)
    )

    assert window._status_bar.server_section.label.text() == "Server: Online"
    assert window._status_bar.market_section.label.text() == "Market: Online"
    assert window._home_page.server_card._state_label.text() == "Online"
    assert window._nav.btn_server.text() == "Server: External"
    assert window._nav.btn_market.text() == "Market: External"
    assert window._nav.btn_server.isEnabled() is False
    assert window._nav.btn_market.isEnabled() is False
    assert window._home_page.btn_start_servers.text() == "Managed Externally"


@pytest.mark.parametrize(
    ("owned_pid", "owned_return_code"),
    [
        (4321, 1),
        (9876, None),
    ],
    ids=("owned-process-exited", "owned-process-replaced"),
)
def test_stable_native_observation_clears_stale_process_bound_mod_evidence(
    status_window: tuple[MainWindow, dict[str, bool]],
    owned_pid: int,
    owned_return_code: int | None,
) -> None:
    window, _probes = status_window

    class OwnedProcess:
        pid = owned_pid

        @staticmethod
        def poll() -> int | None:
            return owned_return_code

    window._server_proc = OwnedProcess()
    window._service_reachability = (True, False)
    window._current_mod_runtime_snapshot = _native_runtime_snapshot(pid=4321)

    window._on_native_service_observation(
        ServiceProbe(game_reachable=True, market_reachable=False)
    )

    assert window._current_mod_runtime_snapshot is None


def test_failed_game_start_is_retained_as_failed(
    status_window: tuple[MainWindow, dict[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, probes = status_window
    probes["game"] = False
    window._cfg["evejs_root"] = "C:/Games/EveJS"
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)
    window._lifecycle_start_scope = (False, True)

    window._on_service_start_completed(ServiceStartResult(game_error="boom"))
    window._update_status_bar()

    assert window._status_bar.server_section.label.text() == "Server: Failed"
    assert window._home_page.server_card._state_label.text() == "Failed"
    assert window._runtime_snapshot.game_error == "boom"


def test_home_service_row_routes_to_the_existing_console_panel(
    status_window: tuple[MainWindow, dict[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    window, _probes = status_window
    window._cfg["evejs_root"] = "C:/Games/EveJS"
    monkeypatch.setattr(
        app_module,
        "get_market_console_log",
        lambda: tmp_path / "market_console.log",
    )

    window._home_page.console_requested.emit("market")

    assert window._console_panel.isHidden() is False


def test_home_stop_stack_signal_routes_to_service_shutdown(
    status_window: tuple[MainWindow, dict[str, bool]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _probes = status_window
    stopped: list[str] = []
    sequences: list[dict[str, object]] = []

    class AliveProcess:
        @staticmethod
        def poll() -> None:
            return None

    window._server_proc = AliveProcess()
    window._market_proc = AliveProcess()
    monkeypatch.setattr(window, "_stop_server", lambda: stopped.append("server"))
    monkeypatch.setattr(window, "_stop_market", lambda: stopped.append("market"))
    monkeypatch.setattr(
        window,
        "_run_stop_sequence",
        lambda **kwargs: sequences.append(kwargs) or True,
    )

    window._home_page.stop_servers_clicked.emit()

    assert stopped == []
    assert sequences == [
        {
            "stop_game": True,
            "stop_market": True,
            "on_complete": None,
            "voice_event": VoiceEvent.SERVER_STACK_STOPPING,
        }
    ]


def test_runtime_snapshot_disables_destructive_actions_at_zero_clients(
    status_window: tuple[MainWindow, dict[str, bool]],
) -> None:
    window, _probes = status_window

    window._on_service_probe(
        ServiceProbe(game_reachable=False, market_reachable=False)
    )

    assert window._home_page.btn_kill_all.isEnabled() is False
    assert window._nav.btn_kill_all.isEnabled() is False


def test_settings_save_refreshes_the_home_server_mode_label(
    status_window: tuple[MainWindow, dict[str, bool]],
    tmp_path: Path,
) -> None:
    window, _probes = status_window
    (tmp_path / "StartServerWithMods.bat").write_text("", encoding="utf-8")
    window._cfg["evejs_root"] = str(tmp_path)

    window._on_settings_saved(
        {"server_start_preference": "StartServerWithMods.bat"}
    )

    assert window._home_page.services_card.mode_label.text() == "MODDED"
