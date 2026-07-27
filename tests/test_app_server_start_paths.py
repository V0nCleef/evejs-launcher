"""Tests for routing every application server-start path through one resolver."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow


def _minimal_window_config() -> dict:
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


@pytest.fixture
def bare_window(qapp: QApplication) -> MainWindow:
    """Create a MainWindow wrapper without constructing unrelated pages/timers."""
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = _minimal_window_config()
    window._server_proc = None
    window._market_proc = None
    yield window
    window.deleteLater()


def test_main_window_constructs_without_legacy_settings_prompt(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)

    window = MainWindow()
    try:
        assert window._settings_page is not None
    finally:
        window.deleteLater()


def test_main_window_connects_mods_apply_to_central_restart(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    restarted: list[MainWindow] = []

    def record_restart(window: MainWindow) -> None:
        restarted.append(window)

    monkeypatch.setattr(MainWindow, "_restart_server", record_restart)

    window = MainWindow()
    try:
        window._mods_page.apply_restart_clicked.emit()
        assert restarted == [window]
    finally:
        window.deleteLater()


def test_manual_start_uses_resolved_explicit_mode(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "StartServer.bat"
    bare_window._cfg["evejs_root"] = str(tmp_path)
    events: list[object] = []

    class FakeProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("vanilla", script)
    )
    bare_window._update_status_bar = lambda: events.append("status")
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda root, *, mode: events.append(("start", root, mode)) or FakeProcess(),
    )
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._start_server()

    assert events[:2] == [
        "resolve",
        ("start", str(tmp_path), "vanilla"),
    ]
    assert bare_window._server_proc is not None


def test_manual_start_does_not_duplicate_alive_starting_process(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AliveProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = AliveProcess()
    resolver_calls: list[str] = []
    launch_calls: list[str] = []
    bare_window._resolve_server_start = lambda: (
        resolver_calls.append("resolve") or ("modded", None)
    )
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda *_args, **_kwargs: launch_calls.append("start"),
    )
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._start_server()

    assert resolver_calls == []
    assert launch_calls == []
    assert bare_window._server_proc.pid == 4321


def test_start_all_cancellation_occurs_before_market_side_effects(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._resolve_server_start = lambda: None
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    starts: list[str] = []

    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_market_server",
        lambda _root: starts.append("market") or object(),
    )
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda *_args, **_kwargs: starts.append("game") or object(),
    )

    bare_window._start_all_servers()

    assert starts == []
    assert bare_window._market_proc is None
    assert bare_window._server_proc is None


def test_auto_start_cancellation_occurs_before_market_side_effects(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "auto_start_market": True,
            "auto_start_server": True,
        }
    )
    bare_window._resolve_server_start = lambda: None
    bare_window._is_market_running = lambda: False
    starts: list[str] = []

    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.start_market_server",
        lambda _root: starts.append("market") or object(),
    )
    monkeypatch.setattr(
        "src.app.start_game_server",
        lambda *_args, **_kwargs: starts.append("game") or object(),
    )

    assert bare_window._ensure_server_if_needed() is False
    assert starts == []


def test_single_client_launch_aborts_when_auto_start_is_cancelled(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IdleTracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "client_path": str(tmp_path / "tq"),
        }
    )
    bare_window._tracker = IdleTracker()
    bare_window._ensure_server_if_needed = lambda: False
    downstream_calls: list[str] = []
    monkeypatch.setattr(
        "src.app.profile_exists",
        lambda _username: downstream_calls.append("profile") or False,
    )
    monkeypatch.setattr(
        "src.app.create_profile",
        lambda *_args: downstream_calls.append("create-profile"),
    )
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._on_character_launch("account", "character")

    assert downstream_calls == []


def test_launch_all_aborts_when_auto_start_is_cancelled(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "client_path": str(tmp_path / "tq"),
        }
    )
    bare_window._accounts = []
    bare_window._ensure_server_if_needed = lambda: False
    bare_window._refresh_characters = lambda: None
    bare_window._update_status_bar = lambda: None
    messages: list[str] = []
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, _title, message: messages.append(message),
    )

    bare_window._launch_all()

    assert messages == []


def test_restart_cancellation_preserves_the_running_server(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    class AliveProcess:
        pid = 8765

        @staticmethod
        def poll() -> None:
            return None

    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = AliveProcess()
    bare_window._resolve_server_start = lambda: None
    stop_calls: list[str] = []
    start_calls: list[str] = []
    bare_window._stop_server = lambda: stop_calls.append("stop")
    bare_window._start_resolved_server = (
        lambda *_args, **_kwargs: start_calls.append("start") or True
    )

    bare_window._restart_server()

    assert stop_calls == []
    assert start_calls == []
    assert bare_window._server_proc.pid == 8765


def test_settings_root_change_refreshes_the_mods_page(
    bare_window: MainWindow,
) -> None:
    class FakeModsPage:
        refresh_count = 0

        def refresh_mods(self) -> None:
            self.refresh_count += 1

    mods_page = FakeModsPage()
    bare_window._cfg["evejs_root"] = "old-root"
    bare_window._mods_page = mods_page
    bare_window._refresh_characters = lambda: None

    bare_window._on_settings_saved({"evejs_root": "new-root"})

    assert mods_page.refresh_count == 1


def test_settings_root_change_clears_root_dependent_caches(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeModsPage:
        @staticmethod
        def refresh_mods() -> None:
            return None

    class FakePortraitCache:
        @staticmethod
        def clear() -> None:
            cleared.append("portraits")

    cleared: list[str] = []
    bare_window._cfg["evejs_root"] = "old-root"
    bare_window._mods_page = FakeModsPage()
    bare_window._refresh_characters = lambda: None
    monkeypatch.setattr(
        app_module,
        "clear_solar_system_name_cache",
        lambda: cleared.append("locations"),
        raising=False,
    )
    monkeypatch.setattr(
        app_module,
        "PortraitCache",
        FakePortraitCache,
        raising=False,
    )

    bare_window._on_settings_saved({"evejs_root": "new-root"})

    assert cleared == ["locations", "portraits"]
