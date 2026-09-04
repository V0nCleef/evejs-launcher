"""Tests for routing every application server-start path through one resolver."""
from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.audio.events import VoiceEvent
from src.core.db import Account, Character
from src.workers.server_worker import ServiceStartResult, ServiceStopResult


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


def _write_valid_market_seed(root: Path) -> Path:
    market_dir = root / "externalservices" / "market-server"
    config_path = market_dir / "config" / "market-server.local.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '[storage]\ndatabase_path = "data/generated/market.sqlite"\n',
        encoding="utf-8",
    )
    database = market_dir / "data" / "generated" / "market.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO manifest (key, value) VALUES (?, ?)",
            ("manifest_json", "{}"),
        )
    return database


@pytest.fixture
def bare_window(qapp: QApplication) -> MainWindow:
    """Create a MainWindow wrapper without constructing unrelated pages/timers."""
    class FakeHero:
        @staticmethod
        def set_rotation_interval(_seconds: int) -> None:
            return None

        @staticmethod
        def set_animations_enabled(_enabled: bool) -> None:
            return None

    class FakeHomePage:
        def __init__(self) -> None:
            self.hero = FakeHero()

        @staticmethod
        def set_server_mode(_mode: str) -> None:
            return None

    class FakeToolsPage:
        @staticmethod
        def set_evejs_root(_root: str) -> None:
            return None

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = _minimal_window_config()
    window._server_proc = None
    window._market_proc = None
    window._market_intent = None
    window._market_error = None
    window._service_reachability = (False, False)
    window._service_thread = None
    window._service_monitor = None
    window._service_monitor_start_pending = False
    window._active_update_checkers = []
    window._update_install_worker = None
    window._close_in_progress = False
    window._home_page = FakeHomePage()
    window._tools_page = FakeToolsPage()
    yield window
    window._release_mod_lifecycle_lease()
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
        assert callable(window._settings_page._save_validator)
    finally:
        window.deleteLater()


def test_launcher_created_character_is_exempt_from_automatic_hiding(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["hidden_characters"] = ["Created Pilot", "Other Pilot"]
    bare_window._cfg["never_hide_characters"] = []
    saved: list[dict] = []
    monkeypatch.setattr(config, "save", lambda cfg: saved.append(deepcopy(cfg)))

    bare_window._keep_created_character_visible("Created Pilot")

    assert bare_window._cfg["hidden_characters"] == ["Other Pilot"]
    assert bare_window._cfg["never_hide_characters"] == ["Created Pilot"]
    assert len(saved) == 1


def test_main_window_connects_mods_apply_to_central_restart(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    restarted: list[tuple[MainWindow, dict[str, object]]] = []

    def record_restart(window: MainWindow, **kwargs: object) -> None:
        restarted.append((window, kwargs))

    monkeypatch.setattr(MainWindow, "_restart_server", record_restart)

    window = MainWindow()
    try:
        window._mods_page.apply_restart_clicked.emit()
        assert len(restarted) == 1
        assert restarted[0][0] is window
        assert restarted[0][1]["allow_force_game_kill"] is False
        assert restarted[0][1]["on_ready"] is None
        assert restarted[0][1]["continuous_mod_lifecycle"] is True
        assert restarted[0][1]["mode_override"] == "modded"
    finally:
        window.deleteLater()


@pytest.mark.parametrize(
    "loader_names",
    [("Fixture Loader",), ()],
)
def test_native_mod_apply_always_uses_lock_owned_modded_discovery(
    bare_window: MainWindow,
    loader_names: tuple[str, ...],
) -> None:
    class FakeModsPage:
        @staticmethod
        def selected_loader_names() -> tuple[str, ...]:
            return loader_names

        @staticmethod
        def refresh_mods() -> None:
            return None

    restarts: list[dict[str, object]] = []
    bare_window._mods_page = FakeModsPage()
    bare_window._lifecycle_thread = None
    bare_window._restart_server = lambda **kwargs: restarts.append(kwargs)

    bare_window._on_mods_apply_restart()

    assert len(restarts) == 1
    assert restarts[0]["mode_override"] == "modded"
    assert restarts[0]["on_ready"] is None


def test_manual_start_delegates_resolved_mode_to_background_sequence(
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
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: events.append(("sequence", kwargs)) or True,
        raising=False,
    )

    bare_window._start_server()

    assert events == [
        "resolve",
        (
            "sequence",
            {
                "start_market": False,
                "start_game": True,
                "mode": "vanilla",
                "on_ready": None,
                "error_title": "Game Server Error",
                "voice_event": VoiceEvent.GAME_SERVER_LAUNCHING,
            },
        ),
    ]


def test_lifecycle_remains_active_until_gui_thread_teardown(
    bare_window: MainWindow,
) -> None:
    class FinishedThread:
        @staticmethod
        def isRunning() -> bool:
            return False

    bare_window._lifecycle_thread = FinishedThread()

    assert bare_window._lifecycle_active() is True


def test_native_game_start_holds_mod_lease_through_result_handling(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeLease:
        root = tmp_path.resolve()
        released = False

        def release(self) -> None:
            self.released = True
            events.append("release")

    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            events.append("thread-delete")

    lease = FakeLease()
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._lifecycle_thread = None
    bare_window._server_intent = None
    bare_window._server_error = None
    bare_window._service_reachability = (False, False)
    bare_window._publish_cached_runtime = lambda: events.append("result-handled")
    bare_window._snapshot_matches_native_plan = lambda *_args: True

    def acquire(_root: str) -> FakeLease:
        events.append("acquire")
        return lease

    def begin(_worker: object, _handler: object) -> None:
        events.append("begin")
        bare_window._lifecycle_thread = FinishedThread()
        bare_window._lifecycle_result_received = False
        bare_window._lifecycle_thread_finished = False

    monkeypatch.setattr(app_module, "acquire_mod_lifecycle_lease", acquire)
    bare_window._begin_lifecycle_worker = begin

    assert bare_window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="vanilla",
        on_ready=lambda: events.append("ready"),
        error_title="Game Server Error",
    )
    assert events[:2] == ["acquire", "begin"]
    assert lease.released is False

    bare_window._on_service_start_completed(
        ServiceStartResult(game_ready=True)
    )

    assert "result-handled" in events
    assert lease.released is False

    bare_window._on_lifecycle_thread_finished()

    assert lease.released is True
    assert events.index("release") < events.index("ready")


def test_native_game_worker_is_bound_to_the_lock_owned_runtime_plan(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = tmp_path / "mods" / "Fixture Mod" / "loader.js"
    loader.parent.mkdir(parents=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._cfg["game_port"] = 27555
    bare_window._lifecycle_thread = None
    bare_window._publish_cached_runtime = lambda: None
    captured: list[object] = []
    launches: list[tuple[str, str, object]] = []
    validations: list[tuple[object, object]] = []
    launched_process = object()
    verified_snapshot = object()

    def begin(worker: object, _handler: object) -> None:
        captured.append(worker)
        bare_window._lifecycle_thread = object()

    def launch(root: str, mode: str, *, mod_runtime_plan: object) -> object:
        launches.append((root, mode, mod_runtime_plan))
        return launched_process

    def verify(plan: object, process: object) -> object:
        validations.append((plan, process))
        return verified_snapshot

    bare_window._begin_lifecycle_worker = begin
    bare_window._verify_native_mod_runtime = verify
    monkeypatch.setattr(app_module, "start_game_server", launch)

    assert bare_window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="modded",
        on_ready=None,
        error_title="Game Server Error",
    )

    assert len(captured) == 1
    worker = captured[0]
    plan = bare_window._native_mod_runtime_plan
    assert plan is not None
    assert plan.selected_loader_ids == ("Fixture Mod",)
    assert bare_window._mod_lifecycle_lease is not None
    assert worker._start_game_fn(str(tmp_path), mode="modded") is launched_process
    assert worker._game_port == 27555
    assert launches == [(str(tmp_path), "modded", plan)]
    assert worker._game_runtime_validator(launched_process) is verified_snapshot
    assert validations == [(plan, launched_process)]

    bare_window._lifecycle_thread = None


def test_invalid_mod_manifest_blocks_native_process_start(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "server" / "mods" / "broken" / "evejs-launcher.mod.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{not-json}\n", encoding="utf-8")
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._lifecycle_thread = None
    failures: list[str] = []
    bare_window._begin_lifecycle_worker = lambda *_args: pytest.fail(
        "a Game worker was created from invalid mod metadata"
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    assert not bare_window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="modded",
        on_ready=None,
        error_title="Game Server Error",
    )

    assert bare_window._mod_lifecycle_lease is None
    assert bare_window._native_mod_runtime_plan is None
    assert failures and "No Game process was started" in failures[0]


def test_mod_restart_keeps_one_lease_across_stop_then_start(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    stop_callbacks: list[object] = []

    class FakeLease:
        root = tmp_path.resolve()
        released = False

        def release(self) -> None:
            self.released = True
            events.append("release")

    class LiveProcess:
        pid = 8765

        @staticmethod
        def poll() -> None:
            return None

    lease = FakeLease()
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = LiveProcess()
    bare_window._lifecycle_thread = None
    bare_window._server_intent = None
    bare_window._server_error = None
    bare_window._publish_cached_runtime = lambda: None
    bare_window._resolve_server_start = lambda: ("vanilla", None)

    def acquire(_root: str) -> FakeLease:
        events.append("acquire")
        return lease

    def stop(**kwargs: object) -> bool:
        events.append("stop")
        assert lease.released is False
        stop_callbacks.append(kwargs["on_complete"])
        return True

    def begin(_worker: object, _handler: object) -> None:
        events.append("start")
        bare_window._lifecycle_thread = object()

    monkeypatch.setattr(app_module, "acquire_mod_lifecycle_lease", acquire)
    bare_window._run_stop_sequence = stop
    bare_window._begin_lifecycle_worker = begin

    bare_window._restart_server(
        allow_force_game_kill=False,
        continuous_mod_lifecycle=True,
    )

    assert events == ["acquire", "stop"]
    assert lease.released is False
    assert len(stop_callbacks) == 1

    bare_window._lifecycle_thread = None
    stop_callbacks[0]()

    assert events == ["acquire", "stop", "start"]
    assert lease.released is False


def test_failed_mod_restart_stop_releases_continuous_lease_at_boundary(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released: list[str] = []

    class FakeLease:
        root = tmp_path.resolve()
        released = False

        def release(self) -> None:
            self.released = True
            released.append("release")

    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            return None

    bare_window._mod_lifecycle_lease = FakeLease()
    bare_window._mod_lifecycle_lease_token = None
    bare_window._release_mod_lease_after_lifecycle = False
    bare_window._lifecycle_thread = FinishedThread()
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    bare_window._lifecycle_stop_scope = (True, False)
    bare_window._lifecycle_stop_callback = lambda: pytest.fail(
        "failed stop continued into Game start"
    )
    bare_window._service_reachability = (True, False)
    bare_window._publish_cached_runtime = lambda: None
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    bare_window._on_service_stop_completed(
        ServiceStopResult(
            game_stopped=False,
            market_stopped=True,
            game_error="fixture stop failed",
        )
    )
    assert released == []

    bare_window._on_lifecycle_thread_finished()

    assert released == ["release"]


def test_mod_attestation_handoff_keeps_lease_through_corrective_stop(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class FakeLease:
        root = tmp_path.resolve()
        released = False

        def release(self) -> None:
            self.released = True
            events.append("release")

    class LiveProcess:
        pid = 8765

        @staticmethod
        def poll() -> None:
            return None

    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            return None

    lease = FakeLease()
    bare_window._mod_lifecycle_lease = lease
    bare_window._mod_lifecycle_lease_token = object()
    bare_window._release_mod_lease_after_lifecycle = True
    bare_window._server_proc = LiveProcess()
    bare_window._server_intent = None
    bare_window._server_error = None
    bare_window._service_reachability = (True, False)
    bare_window._lifecycle_thread = None
    bare_window._publish_cached_runtime = lambda: None

    def begin(_worker: object, _handler: object) -> None:
        events.append("stop-begin")
        bare_window._lifecycle_thread = FinishedThread()
        bare_window._lifecycle_result_received = False
        bare_window._lifecycle_thread_finished = False

    bare_window._begin_lifecycle_worker = begin

    assert bare_window._retain_mod_lifecycle_lease_for_continuation()
    assert bare_window._run_stop_sequence(
        stop_game=True,
        stop_market=False,
        on_complete=lambda: events.append("stopped"),
        allow_force_game_kill=False,
    )
    assert events == ["stop-begin"]
    assert lease.released is False

    bare_window._on_service_stop_completed(
        ServiceStopResult(game_stopped=True, market_stopped=True)
    )
    assert lease.released is False

    bare_window._on_lifecycle_thread_finished()

    assert events == ["stop-begin", "release", "stopped"]
    assert lease.released is True


def test_native_attestation_failure_starts_corrective_stop_after_thread_teardown(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class LiveProcess:
        pid = 8765

        @staticmethod
        def poll() -> None:
            return None

    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            events.append("thread-delete")

    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._lifecycle_thread = None
    bare_window._publish_cached_runtime = lambda: None
    bare_window._publish_mod_runtime_snapshot = (
        lambda snapshot: events.append("clear-evidence" if snapshot is None else "publish")
    )
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    def begin(_worker: object, _handler: object) -> None:
        bare_window._lifecycle_thread = FinishedThread()
        bare_window._lifecycle_result_received = False
        bare_window._lifecycle_thread_finished = False

    def stop(**_kwargs: object) -> bool:
        assert bare_window._mod_lifecycle_lease is not None
        events.append("corrective-stop")
        bare_window._lifecycle_thread = object()
        return True

    bare_window._begin_lifecycle_worker = begin
    bare_window._run_stop_sequence = stop

    assert bare_window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="vanilla",
        on_ready=lambda: pytest.fail("failed attestation ran ready callback"),
        error_title="Game Server Error",
    )
    events.clear()

    bare_window._on_service_start_completed(
        ServiceStartResult(
            game_process=LiveProcess(),
            game_ready=True,
            mod_runtime_error="fixture status marker was missing",
        )
    )

    assert "corrective-stop" not in events
    assert bare_window._mod_lifecycle_lease is not None
    bare_window._on_lifecycle_thread_finished()

    assert events.index("thread-delete") < events.index("corrective-stop")
    assert bare_window._mod_lifecycle_lease is not None
    bare_window._lifecycle_thread = None


def test_busy_mod_lifecycle_lock_blocks_native_game_worker_start(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._lifecycle_thread = None
    bare_window._server_error = None
    bare_window._begin_lifecycle_worker = lambda *_args: pytest.fail(
        "Game worker started without the lifecycle lock"
    )
    monkeypatch.setattr(
        app_module,
        "acquire_mod_lifecycle_lease",
        lambda _root: (_ for _ in ()).throw(
            app_module.ModLifecycleBusyError("installer owns fixture lock")
        ),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    assert not bare_window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="vanilla",
        on_ready=None,
        error_title="Game Server Error",
    )

    assert warnings
    assert "installer owns fixture lock" in warnings[0]


def test_market_only_start_does_not_acquire_mod_lifecycle_lock(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers: list[object] = []
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._lifecycle_thread = None
    bare_window._publish_cached_runtime = lambda: None
    bare_window._begin_lifecycle_worker = (
        lambda worker, _handler: workers.append(worker)
    )
    monkeypatch.setattr(
        app_module,
        "acquire_mod_lifecycle_lease",
        lambda _root: pytest.fail("Market-only start acquired the Game mod lock"),
    )

    assert bare_window._start_service_sequence(
        start_market=True,
        start_game=False,
        mode=None,
        on_ready=None,
        error_title="Market Server Error",
    )

    assert len(workers) == 1


def test_ready_callback_waits_for_service_result_and_thread_teardown(
    bare_window: MainWindow,
) -> None:
    class FinishedThread:
        deleted = False

        @classmethod
        def deleteLater(cls) -> None:
            cls.deleted = True

    callbacks: list[str] = []
    bare_window._lifecycle_thread = FinishedThread()
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    bare_window._lifecycle_start_scope = (True, True)
    bare_window._lifecycle_ready_callback = lambda: callbacks.append("ready")
    bare_window._service_reachability = (False, False)
    bare_window._publish_cached_runtime = lambda: None

    bare_window._on_service_start_completed(
        ServiceStartResult(market_ready=True, game_ready=True)
    )

    assert callbacks == []
    assert bare_window._lifecycle_thread is not None

    bare_window._on_lifecycle_thread_finished()

    assert callbacks == ["ready"]
    assert FinishedThread.deleted is True
    assert bare_window._lifecycle_thread is None


def test_ready_callback_survives_thread_finish_arriving_before_result(
    bare_window: MainWindow,
) -> None:
    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            return None

    callbacks: list[str] = []
    bare_window._lifecycle_thread = FinishedThread()
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    bare_window._lifecycle_start_scope = (True, False)
    bare_window._lifecycle_ready_callback = lambda: callbacks.append("ready")
    bare_window._service_reachability = (False, False)
    bare_window._publish_cached_runtime = lambda: None

    bare_window._on_lifecycle_thread_finished()
    bare_window._on_service_start_completed(ServiceStartResult(game_ready=True))

    assert callbacks == ["ready"]
    assert bare_window._lifecycle_thread is None


def test_stack_worker_is_allowed_to_start_game_after_optional_market_failure(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    captured: list[object] = []
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._lifecycle_thread = None
    bare_window._server_intent = None
    bare_window._server_error = None
    bare_window._publish_cached_runtime = lambda: None
    bare_window._begin_lifecycle_worker = (
        lambda worker, _handler: captured.append(worker)
    )

    assert bare_window._start_service_sequence(
        start_market=True,
        start_game=True,
        mode="vanilla",
        on_ready=None,
        error_title="Service Startup Failed",
    )

    assert len(captured) == 1
    assert captured[0]._continue_game_after_market_failure is True


def test_partial_stack_start_keeps_game_ready_and_runs_client_continuation(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FinishedThread:
        @staticmethod
        def deleteLater() -> None:
            return None

    callbacks: list[str] = []
    warnings: list[tuple[str, str]] = []
    bare_window._lifecycle_thread = FinishedThread()
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    bare_window._lifecycle_after_thread_callback = None
    bare_window._lifecycle_start_scope = (True, True)
    bare_window._lifecycle_start_token = object()
    bare_window._lifecycle_ready_callback = lambda: callbacks.append("ready")
    bare_window._lifecycle_start_voice_event = None
    bare_window._lifecycle_error_title = "Auto-start Services Failed"
    bare_window._server_intent = object()
    bare_window._server_error = None
    bare_window._publish_cached_runtime = lambda: None
    monkeypatch.setattr(
        "src.app.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(
        "src.app.QMessageBox.critical",
        lambda *_args: pytest.fail("a usable Game-only result is not a total failure"),
    )

    bare_window._on_service_start_completed(
        ServiceStartResult(
            market_ready=False,
            game_ready=True,
            market_error="Market service exited before readiness (code 1).",
        )
    )
    bare_window._on_lifecycle_thread_finished()

    assert bare_window._service_reachability == (True, False)
    assert bare_window._server_intent is None
    assert callbacks == ["ready"]
    assert warnings
    assert "Game is online" in warnings[0][1]


def test_manual_market_start_delegates_to_background_sequence(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    _write_valid_market_seed(tmp_path)
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr("src.app.start_market_server", lambda _root: FakeProcess())
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: calls.append(kwargs) or True,
        raising=False,
    )

    bare_window._start_market()

    assert calls == [
        {
            "start_market": True,
            "start_game": False,
            "mode": None,
            "on_ready": None,
            "error_title": "Market Server Error",
            "voice_event": VoiceEvent.MARKET_SERVER_LAUNCHING,
        }
    ]


def test_manual_market_start_skips_unavailable_seed_and_clears_stale_error(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._market_intent = object()
    bare_window._market_error = "previous Market startup failed"
    bare_window._service_reachability = (True, True)
    events: list[str] = []
    messages: list[tuple[str, str]] = []
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: events.append("status")
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **_kwargs: pytest.fail("unavailable Market reached startup worker"),
        raising=False,
    )

    bare_window._start_market()

    assert events == ["status"]
    assert len(messages) == 1
    assert messages[0][0] == "Optional Market Not Ready"
    assert "Tools > Market Seed Builder" in messages[0][1]
    assert bare_window._market_intent is None
    assert bare_window._market_error is None
    assert bare_window._service_reachability == (True, False)


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


def test_start_all_delegates_market_then_game_to_background_sequence(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    _write_valid_market_seed(tmp_path)
    events: list[object] = []
    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("modded", tmp_path / "StartServerWithMods.bat")
    )
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr("src.app.start_market_server", lambda _root: object())
    monkeypatch.setattr("src.app.start_game_server", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)
    monkeypatch.setattr("src.app.QMessageBox.information", lambda *_args: None)
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: events.append(("sequence", kwargs)) or True,
        raising=False,
    )

    bare_window._start_all_servers()

    assert events == [
        "resolve",
        (
            "sequence",
            {
                "start_market": True,
                "start_game": True,
                "mode": "modded",
                "on_ready": None,
                "error_title": "Service Startup Failed",
                "voice_event": VoiceEvent.SERVER_STACK_LAUNCHING,
            },
        ),
    ]


def test_start_all_starts_game_without_unseeded_optional_market(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    market_dir = tmp_path / "externalservices" / "market-server"
    config_path = market_dir / "config" / "market-server.local.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '[storage]\ndatabase_path = "data/generated/market.sqlite"\n',
        encoding="utf-8",
    )
    bare_window._market_intent = object()
    bare_window._market_error = "previous Market startup failed"
    bare_window._service_reachability = (False, True)
    events: list[object] = []
    messages: list[tuple[str, str]] = []
    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("vanilla", tmp_path / "StartServer.bat")
    )
    bare_window._is_market_running = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: events.append(("sequence", kwargs)) or True,
        raising=False,
    )

    bare_window._start_all_servers()

    assert events == [
        "resolve",
        (
            "sequence",
            {
                "start_market": False,
                "start_game": True,
                "mode": "vanilla",
                "on_ready": None,
                "error_title": "Service Startup Failed",
                "voice_event": VoiceEvent.GAME_SERVER_LAUNCHING,
            },
        ),
    ]
    assert len(messages) == 1
    assert "optional market" in messages[0][1].casefold()
    assert "game will start without it" in messages[0][1].casefold()
    assert "Tools > Market Seed Builder" in messages[0][1]
    assert bare_window._market_intent is None
    assert bare_window._market_error is None
    assert bare_window._service_reachability == (False, False)


def test_start_all_skips_unavailable_market_when_game_is_already_active(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._market_intent = object()
    bare_window._market_error = "previous Market startup failed"
    bare_window._service_reachability = (True, True)
    events: list[str] = []
    messages: list[tuple[str, str]] = []
    bare_window._server_process_alive = lambda: True
    bare_window._is_market_running = lambda: False
    bare_window._resolve_server_start = lambda: pytest.fail(
        "active Game should not resolve a new start"
    )
    bare_window._update_status_bar = lambda: events.append("status")
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **_kwargs: pytest.fail("empty startup request reached worker"),
        raising=False,
    )

    bare_window._start_all_servers()

    assert events == ["status"]
    assert len(messages) == 1
    assert messages[0][0] == "Optional Market Not Ready"
    assert "game is already online" in messages[0][1].casefold()
    assert "already running" not in messages[0][0].casefold()
    assert bare_window._market_intent is None
    assert bare_window._market_error is None
    assert bare_window._service_reachability == (True, False)


def test_owned_game_stop_delegates_wait_and_kill_to_background_sequence(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 1234

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.return_code = 0

    calls: list[dict[str, object]] = []
    bare_window._server_proc = LiveProcess()
    bare_window._service_reachability = (True, False)
    bare_window._update_status_bar = lambda: None
    bare_window._run_stop_sequence = lambda **kwargs: calls.append(kwargs) or True

    bare_window._stop_server()

    assert calls == [
        {
            "stop_game": True,
            "stop_market": False,
            "on_complete": None,
            "voice_event": VoiceEvent.GAME_SERVER_STOPPING,
        }
    ]


def test_owned_market_stop_delegates_wait_and_kill_to_background_sequence(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 4321

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.return_code = 0

    calls: list[dict[str, object]] = []
    bare_window._market_proc = LiveProcess()
    bare_window._service_reachability = (False, True)
    bare_window._update_status_bar = lambda: None
    bare_window._run_stop_sequence = lambda **kwargs: calls.append(kwargs) or True

    bare_window._stop_market()

    assert calls == [
        {
            "stop_game": False,
            "stop_market": True,
            "on_complete": None,
            "voice_event": VoiceEvent.MARKET_SERVER_STOPPING,
        }
    ]


def test_stop_all_delegates_one_ordered_background_sequence(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 4321

        def __init__(self) -> None:
            self.return_code: int | None = None

        def poll(self) -> int | None:
            return self.return_code

        def terminate(self) -> None:
            self.return_code = 0

    calls: list[dict[str, object]] = []
    bare_window._server_proc = LiveProcess()
    bare_window._market_proc = LiveProcess()
    bare_window._run_stop_sequence = lambda **kwargs: calls.append(kwargs) or True

    bare_window._stop_all_servers()

    assert calls == [
        {
            "stop_game": True,
            "stop_market": True,
            "on_complete": None,
            "voice_event": VoiceEvent.SERVER_STACK_STOPPING,
        }
    ]


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

    ready: list[str] = []

    assert bare_window._ensure_server_if_needed(lambda: ready.append("ready")) is False
    assert starts == []
    assert ready == []


def test_auto_start_probes_the_configured_native_game_port(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "game_port": 27555,
            "auto_start_market": False,
            "auto_start_server": False,
        }
    )
    probed_ports: list[int] = []
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: None
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port, **_kwargs: probed_ports.append(port) is not None,
    )

    ready: list[str] = []

    assert bare_window._ensure_server_if_needed(lambda: ready.append("ready")) is True
    assert probed_ports == [27555]
    assert ready == ["ready"]


def test_native_data_guard_uses_only_the_selected_game_port(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["game_port"] = 27555
    probed_ports: list[int] = []

    def probe(*, port: int, **_kwargs: object) -> bool:
        probed_ports.append(port)
        return port == int(app_module.Ports.GAME_TCP)

    monkeypatch.setattr("src.app.is_server_running", probe)

    assert bare_window._native_game_running(fail_closed=True) is False
    assert probed_ports == [27555]


def test_native_data_guard_fails_closed_for_invalid_game_port(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["game_port"] = 0
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda **_kwargs: pytest.fail("invalid endpoint must not be probed"),
    )

    assert bare_window._native_game_running(fail_closed=True) is True


def test_invalid_native_game_port_is_rejected_for_client_launch(
    bare_window: MainWindow,
) -> None:
    bare_window._cfg["game_port"] = 0

    context, reason = bare_window._resolve_client_launch_context()

    assert context is None
    assert "invalid" in reason.casefold()


def test_native_game_port_change_is_deferred_during_service_lifecycle(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._lifecycle_thread = object()
    warnings: list[str] = []
    monkeypatch.setattr(
        "src.app.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    bare_window._on_settings_saved({**bare_window._cfg, "game_port": 27555})

    assert bare_window._cfg["game_port"] == int(app_module.Ports.GAME_TCP)
    assert warnings
    assert "lifecycle" in warnings[0].casefold()


def test_native_game_port_change_is_rejected_for_owned_running_server(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._server_process_alive = lambda: True
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda **_kwargs: pytest.fail("owned process evidence must short-circuit"),
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 27555}
    )

    assert rejection is not None
    assert "26000" in rejection
    assert "nothing was saved" in rejection.casefold()


def test_native_game_port_change_probes_and_rejects_external_old_endpoint(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed_ports: list[int] = []
    bare_window._server_process_alive = lambda: False
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port: probed_ports.append(port) or port == 26000,
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 27555}
    )

    assert rejection is not None
    assert probed_ports == [26000]


def test_native_game_port_validation_allows_idle_and_equivalent_transitions(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed_ports: list[int] = []
    bare_window._server_process_alive = lambda: False
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port: probed_ports.append(port) or False,
    )

    equivalent = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": "26000"}
    )
    changed = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 27555}
    )

    assert equivalent is None
    assert changed is None
    assert probed_ports == [26000]


def test_native_game_port_validation_rejects_invalid_candidate_but_ignores_docker(
    bare_window: MainWindow,
) -> None:
    native_rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 0}
    )
    bare_window._cfg["runtime_backend"] = "docker_compose"
    docker_rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 0}
    )

    assert native_rejection is not None
    assert "invalid" in native_rejection.casefold()
    assert docker_rejection is None


@pytest.mark.parametrize(
    "change",
    [
        {"proxy_url": "http://127.0.0.1:27557"},
        {"evejs_root": "C:/Fixture/Other-EveJS"},
        {"runtime_backend": "docker_compose"},
    ],
)
def test_live_native_game_rejects_every_runtime_identity_change(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    bare_window._cfg["evejs_root"] = "C:/Fixture/EveJS"
    bare_window._server_process_alive = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda *, port: port == 26000)

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, **change}
    )

    assert rejection is not None
    assert "nothing was saved" in rejection.casefold()


@pytest.mark.parametrize(
    "change",
    [
        {"proxy_url": "http://127.0.0.1:27557"},
        {"evejs_root": "C:/Fixture/Other-EveJS"},
        {"runtime_backend": "docker_compose"},
    ],
)
def test_idle_native_game_allows_runtime_identity_change(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    bare_window._cfg["evejs_root"] = "C:/Fixture/EveJS"
    bare_window._server_process_alive = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)

    assert (
        bare_window._settings_save_rejection({**bare_window._cfg, **change})
        is None
    )


def test_equivalent_native_proxy_origin_does_not_probe_running_game(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["proxy_url"] = "http://LOCALHOST:26002"
    bare_window._server_process_alive = lambda: False
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda **_kwargs: pytest.fail("equivalent identity must not be probed"),
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "proxy_url": "http://localhost:26002/"}
    )

    assert rejection is None


def test_invalid_old_native_port_uses_runtime_fallback_for_safe_repair(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["game_port"] = 0
    bare_window._server_process_alive = lambda: True
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda **_kwargs: pytest.fail("equivalent fallback repair must not probe"),
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 26000}
    )

    assert rejection is None


def test_invalid_old_native_port_retarget_probes_runtime_fallback(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._cfg["game_port"] = 0
    bare_window._server_process_alive = lambda: False
    probed_ports: list[int] = []
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port: probed_ports.append(port) or True,
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "game_port": 27555}
    )

    assert rejection is not None
    assert probed_ports == [26000]


def test_switching_docker_to_native_rejects_invalid_client_endpoints(
    bare_window: MainWindow,
) -> None:
    bare_window._cfg["runtime_backend"] = "docker_compose"

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "runtime_backend": "native", "game_port": 0}
    )

    assert rejection is not None
    assert "invalid" in rejection.casefold()


def test_owned_market_blocks_native_root_change_when_game_is_offline(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveMarket:
        @staticmethod
        def poll() -> None:
            return None

    bare_window._cfg["evejs_root"] = "C:/Fixture/EveJS"
    bare_window._server_process_alive = lambda: False
    bare_window._market_proc = LiveMarket()
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port: port == int(app_module.Ports.MARKET_RPC),
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "evejs_root": "C:/Fixture/Other-EveJS"}
    )

    assert rejection is not None
    assert "market" in rejection.casefold()


@pytest.mark.parametrize(
    "change",
    [
        {"evejs_root": "C:/Fixture/Other-EveJS"},
        {"runtime_backend": "docker_compose"},
    ],
)
def test_external_market_blocks_native_root_or_backend_change(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    bare_window._cfg["evejs_root"] = "C:/Fixture/EveJS"
    bare_window._server_process_alive = lambda: False
    probed_ports: list[int] = []
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port: (
            probed_ports.append(port)
            or port == int(app_module.Ports.MARKET_RPC)
        ),
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, **change}
    )

    assert rejection is not None
    assert "market" in rejection.casefold()
    assert probed_ports == [26000, int(app_module.Ports.MARKET_RPC)]


def test_market_only_does_not_block_native_proxy_change(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveMarket:
        @staticmethod
        def poll() -> None:
            return None

    bare_window._market_proc = LiveMarket()
    bare_window._server_process_alive = lambda: False
    probed_ports: list[int] = []
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port: probed_ports.append(port) or False,
    )

    rejection = bare_window._settings_save_rejection(
        {**bare_window._cfg, "proxy_url": "http://127.0.0.1:27557"}
    )

    assert rejection is None
    assert probed_ports == [26000]


def test_native_game_port_change_restarts_the_service_monitor(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    bare_window._cancel_launch_queue = lambda: None
    bare_window._clear_data_load_error = lambda: None
    bare_window._stop_docker_log_stream = lambda: None
    bare_window._stop_service_monitor = lambda: events.append("stop") or True
    bare_window._schedule_service_monitor_start = lambda: events.append("start")
    bare_window._apply_runtime_settings = lambda: None
    bare_window._refresh_characters = lambda: None
    bare_window._server_process_alive = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)

    bare_window._on_settings_saved({**bare_window._cfg, "game_port": 27555})

    assert bare_window._cfg["game_port"] == 27555
    assert events == ["stop", "start"]


def test_auto_start_defers_the_ready_callback_until_the_sequence_finishes(
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
    _write_valid_market_seed(tmp_path)
    events: list[str] = []
    sequence_calls: list[dict[str, object]] = []
    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("modded", tmp_path / "StartServerWithMods.bat")
    )
    bare_window._is_market_running = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: sequence_calls.append(kwargs) or True,
        raising=False,
    )

    assert bare_window._ensure_server_if_needed(lambda: events.append("ready")) is True

    assert events == ["resolve"]
    assert len(sequence_calls) == 1
    assert sequence_calls[0]["start_market"] is True
    assert sequence_calls[0]["start_game"] is True
    assert sequence_calls[0]["mode"] == "modded"
    assert sequence_calls[0]["error_title"] == "Auto-start Services Failed"
    callback = sequence_calls[0]["on_ready"]
    assert callable(callback)
    callback()
    assert events == ["resolve", "ready"]


def test_auto_start_continues_game_without_unavailable_optional_market(
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
    bare_window._market_intent = object()
    bare_window._market_error = "previous Market startup failed"
    bare_window._service_reachability = (False, True)
    events: list[str] = []
    sequence_calls: list[dict[str, object]] = []
    bare_window._resolve_server_start = lambda: (
        events.append("resolve") or ("vanilla", tmp_path / "StartServer.bat")
    )
    bare_window._is_market_running = lambda: False
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: False)
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda *_args: pytest.fail("auto-start must not pause on an optional service"),
    )
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **kwargs: sequence_calls.append(kwargs) or True,
        raising=False,
    )

    assert bare_window._ensure_server_if_needed(lambda: events.append("ready")) is True

    assert events == ["resolve"]
    assert len(sequence_calls) == 1
    assert sequence_calls[0]["start_market"] is False
    assert sequence_calls[0]["start_game"] is True
    assert sequence_calls[0]["mode"] == "vanilla"
    assert sequence_calls[0]["error_title"] == "Auto-start Services Failed"
    callback = sequence_calls[0]["on_ready"]
    assert callable(callback)
    callback()
    assert events == ["resolve", "ready"]
    assert bare_window._market_intent is None
    assert bare_window._market_error is None
    assert bare_window._service_reachability == (False, False)


def test_auto_start_continues_client_when_only_unavailable_market_was_requested(
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
    bare_window._market_intent = object()
    bare_window._market_error = "previous Market startup failed"
    bare_window._service_reachability = (True, True)
    events: list[str] = []
    bare_window._server_process_alive = lambda: True
    bare_window._is_market_running = lambda: False
    bare_window._update_status_bar = lambda: events.append("status")
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda *_args: pytest.fail("client continuation must not wait on optional Market"),
    )
    monkeypatch.setattr(
        bare_window,
        "_start_service_sequence",
        lambda **_kwargs: pytest.fail("empty startup request reached worker"),
        raising=False,
    )

    assert bare_window._ensure_server_if_needed(lambda: events.append("ready")) is True

    assert events == ["status", "ready"]
    assert bare_window._market_intent is None
    assert bare_window._market_error is None
    assert bare_window._service_reachability == (True, False)


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
    bare_window._ensure_server_if_needed = lambda _on_ready: False
    downstream_calls: list[str] = []
    monkeypatch.setattr(
        "src.app.create_profile",
        lambda *_args: downstream_calls.append("create-profile"),
    )
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._on_character_launch("account", "character")

    assert downstream_calls == []


def test_single_client_launch_waits_for_the_auto_start_continuation(
    bare_window: MainWindow,
    tmp_path: Path,
) -> None:
    class IdleTracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

    callbacks: list[object] = []
    launches: list[tuple[str, str, int | None, bool]] = []
    bare_window._cfg.update(
        {
            "evejs_root": str(tmp_path),
            "client_path": str(tmp_path / "tq"),
        }
    )
    bare_window._tracker = IdleTracker()
    bare_window._ensure_server_if_needed = lambda on_ready: callbacks.append(on_ready) or True
    bare_window._start_client_launch = (
        lambda username, character, character_id=None, *, show_errors: (
            launches.append((username, character, character_id, show_errors)) or True
        )
    )
    bare_window._refresh_characters = lambda: None
    bare_window._update_status_bar = lambda: None

    bare_window._on_character_launch("account", "character", 101)

    assert launches == []
    assert len(callbacks) == 1
    callback = callbacks[0]
    assert callable(callback)
    callback()
    assert launches == [("account", "character", 101, True)]


def test_native_client_launch_preserves_configured_endpoint_context(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    profile_root = tmp_path / "profiles" / "fixture-account"
    (profile_root / "tq").mkdir(parents=True)
    bare_window._cfg.update(
        {
            "runtime_backend": "native",
            "evejs_root": str(tmp_path / "evejs"),
            "client_path": str(tmp_path / "client" / "tq"),
            "game_port": 27555,
            "proxy_url": "http://127.0.0.1:27557",
        }
    )
    bare_window._resolve_configured_client_path = (
        lambda client_path, _evejs_root: Path(client_path)
    )

    class Tracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

        def add(self, username, character, process) -> None:
            events.append(("track", username, character, process.pid))

    class Process:
        pid = 4242

    class Thread:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            pass

    bare_window._tracker = Tracker()
    monkeypatch.setattr(app_module, "PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(app_module, "wait_for_client_endpoints", lambda _context: None)
    monkeypatch.setattr(
        app_module,
        "create_profile",
        lambda _username, _client_path, _profiles_root: profile_root,
    )
    monkeypatch.setattr(
        app_module,
        "prefill_username",
        lambda username: events.append(("prefill", username)),
    )
    def configure_profile(
        username: str,
        profile_tq_path: Path,
        *,
        host: str,
        port: int,
    ) -> None:
        events.append(
            ("configure", username, profile_tq_path, host, port)
        )

    monkeypatch.setattr(
        app_module,
        "configure_profile_game_endpoint",
        configure_profile,
    )

    def launch_client(**kwargs):
        events.append(("launch", kwargs["launch_context"]))
        return Process()

    monkeypatch.setattr(app_module, "launch_client", launch_client)
    monkeypatch.setattr(app_module.threading, "Thread", Thread)

    assert bare_window._launch_account("fixture-account", "Fixture Character") is True

    context = next(event[1] for event in events if event[0] == "launch")
    assert (context.game_host, context.game_port) == ("127.0.0.1", 27555)
    assert context.proxy_url == "http://127.0.0.1:27557"
    configured = next(event for event in events if event[0] == "configure")
    assert configured == (
        "configure",
        "fixture-account",
        profile_root / "tq",
        context.game_host,
        context.game_port,
    )


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
    bare_window._accounts = [
        Account(
            username="account-a",
            account_id=1,
            role="player",
            banned=False,
            characters=[Character(char_id=1, name="Character A")],
        )
    ]

    class FakeTracker:
        @staticmethod
        def is_account_running(_username: str) -> bool:
            return False

    callbacks: list[object] = []
    bare_window._tracker = FakeTracker()
    bare_window._effective_hidden_characters = lambda: set()
    bare_window._ensure_server_if_needed = (
        lambda on_ready: callbacks.append(on_ready) or False
    )
    bare_window._refresh_characters = lambda: None
    bare_window._update_status_bar = lambda: None
    messages: list[str] = []
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, _title, message: messages.append(message),
    )

    bare_window._launch_all()

    assert len(callbacks) == 1
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


def test_restart_mode_override_replaces_resolved_vanilla_mode(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    starts: list[dict[str, object]] = []
    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = None
    bare_window._resolve_server_start = lambda: ("vanilla", None)
    bare_window._start_service_sequence = (
        lambda **kwargs: starts.append(kwargs) or True
    )
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    bare_window._restart_server(mode_override="modded")

    assert len(starts) == 1
    assert starts[0]["mode"] == "modded"


def test_mod_restart_disables_forced_game_cleanup(
    bare_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AliveProcess:
        pid = 8765

        @staticmethod
        def poll() -> None:
            return None

    captured: dict[str, object] = {}

    def build_worker(
        game_process: object,
        market_process: object,
        **kwargs: object,
    ) -> object:
        captured.update(
            game_process=game_process,
            market_process=market_process,
            **kwargs,
        )
        return object()

    bare_window._cfg["evejs_root"] = str(tmp_path)
    bare_window._server_proc = AliveProcess()
    bare_window._resolve_server_start = lambda: ("vanilla", None)
    bare_window._publish_cached_runtime = lambda: None
    bare_window._begin_lifecycle_worker = lambda _worker, _handler: None
    monkeypatch.setattr(app_module, "ServiceStopWorker", build_worker)

    bare_window._restart_server(allow_force_game_kill=False)

    assert captured["game_process"] is bare_window._server_proc
    assert captured["market_process"] is None
    assert captured["allow_force_game_kill"] is False


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


def test_stop_server_does_not_force_kill_an_external_game_process(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._service_reachability = (True, False)
    bare_window._update_status_bar = lambda: None
    forced_ports: list[int] = []
    messages: list[str] = []
    monkeypatch.setattr(
        "src.app.is_server_running",
        lambda *, port, **_kwargs: port == int(app_module.Ports.GAME_TCP),
    )
    monkeypatch.setattr(
        bare_window,
        "_kill_process_on_port",
        lambda port: forced_ports.append(port),
        raising=False,
    )
    monkeypatch.setattr(
        "src.app.QMessageBox.information",
        lambda _parent, _title, message: messages.append(message),
    )

    bare_window._stop_server()

    assert forced_ports == []
    assert messages
    assert "outside this launcher" in messages[-1].casefold()


def test_close_defers_window_acceptance_until_owned_service_stop_completes(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    class IdleTracker:
        running_count = 0

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = CloseEvent()
    stop_requests: list[dict[str, object]] = []
    monitor_stops: list[str] = []
    audio_stops: list[str] = []
    bare_window._tracker = IdleTracker()
    bare_window._server_proc = LiveProcess()
    bare_window._market_proc = None
    bare_window._service_thread = None
    bare_window._stop_service_monitor = lambda: monitor_stops.append("monitor")
    bare_window._shutdown_audio_for_close = lambda: audio_stops.append("audio")
    bare_window._run_stop_sequence = (
        lambda **kwargs: stop_requests.append(kwargs) or True
    )

    bare_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert monitor_stops == []
    assert audio_stops == []
    assert len(stop_requests) == 1
    assert stop_requests[0]["stop_game"] is True
    assert stop_requests[0]["stop_market"] is True
    assert callable(stop_requests[0]["on_complete"])


def test_close_stops_only_owned_game_when_market_is_externally_reachable(
    bare_window: MainWindow,
) -> None:
    class LiveProcess:
        pid = 1234

        @staticmethod
        def poll() -> None:
            return None

    class IdleTracker:
        running_count = 0

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = CloseEvent()
    stop_requests: list[dict[str, object]] = []
    bare_window._tracker = IdleTracker()
    bare_window._server_proc = LiveProcess()
    bare_window._market_proc = None
    bare_window._service_reachability = (True, True)
    bare_window._service_thread = None
    bare_window._stop_service_monitor = lambda: True
    bare_window._run_stop_sequence = lambda **kwargs: stop_requests.append(kwargs) or True

    snapshot = bare_window._build_runtime_snapshot()
    bare_window.closeEvent(event)

    assert snapshot.game_owned is True
    assert snapshot.market_owned is False
    assert event.ignored is True
    assert event.accepted is False
    assert stop_requests == [
        {
            "stop_game": True,
            "stop_market": True,
            "on_complete": bare_window._complete_deferred_close,
        }
    ]
    assert bare_window._market_proc is None


def test_failed_deferred_shutdown_keeps_the_window_open(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare_window._lifecycle_stop_scope = (True, False)
    bare_window._lifecycle_stop_callback = lambda: None
    bare_window._service_reachability = (True, False)
    bare_window._close_after_lifecycle = True
    bare_window._publish_cached_runtime = lambda: None
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *_args: None)

    bare_window._on_service_stop_completed(
        ServiceStopResult(
            game_stopped=False,
            market_stopped=True,
            game_error="Game service did not exit.",
        )
    )

    assert bare_window._close_after_lifecycle is False


def test_nonzero_graceful_exit_clears_owned_game_and_reports_failure(
    bare_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    owned_process = object()
    bare_window._server_proc = owned_process
    bare_window._lifecycle_stop_scope = (True, False)
    bare_window._lifecycle_stop_callback = None
    bare_window._service_reachability = (True, False)
    bare_window._publish_cached_runtime = lambda: None
    bare_window._lifecycle_result_received = False
    bare_window._lifecycle_thread_finished = False
    monkeypatch.setattr(
        "src.app.QMessageBox.critical",
        lambda _parent, _title, message: messages.append(message),
    )

    bare_window._on_service_stop_completed(
        ServiceStopResult(
            game_stopped=True,
            market_stopped=True,
            game_error=(
                "Game service exited with code 17 during graceful shutdown."
            ),
        )
    )

    assert bare_window._server_proc is None
    assert bare_window._service_reachability == (False, False)
    assert messages
    assert "code 17" in messages[-1]


def test_service_monitor_shutdown_intent_precedes_thread_release(
    bare_window: MainWindow,
) -> None:
    """The GUI records shutdown intent before releasing the monitor thread."""
    events: list[str] = []

    class FakeMonitor:
        @staticmethod
        def request_shutdown() -> None:
            events.append("shutdown")

        @staticmethod
        def stop() -> None:
            events.append("stop")

    class FakeThread:
        @staticmethod
        def requestInterruption() -> None:
            events.append("interrupt")

        @staticmethod
        def quit() -> None:
            events.append("quit")

        @staticmethod
        def wait(_timeout_ms: int) -> bool:
            events.append("wait")
            return True

    monitor = FakeMonitor()
    bare_window._service_monitor = monitor
    bare_window._service_thread = FakeThread()
    bare_window._service_monitor_stop_requested.connect(monitor.stop)

    bare_window._stop_service_monitor()

    assert events == ["shutdown", "interrupt", "stop", "quit", "wait"]
    assert bare_window._service_monitor is None
    assert bare_window._service_thread is None


def test_service_monitor_keeps_references_when_thread_remains_alive(
    bare_window: MainWindow,
) -> None:
    """A timed-out monitor thread must not be released for widget teardown."""
    class FakeMonitor:
        @staticmethod
        def request_shutdown() -> None:
            return None

        @staticmethod
        def stop() -> None:
            return None

    class StuckThread:
        @staticmethod
        def requestInterruption() -> None:
            return None

        @staticmethod
        def quit() -> None:
            return None

        @staticmethod
        def wait(_timeout_ms: int) -> bool:
            return False

    monitor = FakeMonitor()
    thread = StuckThread()
    bare_window._service_monitor = monitor
    bare_window._service_thread = thread
    bare_window._service_monitor_stop_requested.connect(monitor.stop)

    bare_window._stop_service_monitor()

    assert bare_window._service_monitor is monitor
    assert bare_window._service_thread is thread


def test_show_event_defers_service_monitor_start_until_the_gui_event_loop(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible window must yield to the GUI loop before it creates the worker."""
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    starts: list[str] = []
    window = MainWindow()
    window._start_service_monitor = lambda: starts.append("start")

    window.show()
    try:
        assert starts == []

        qapp.processEvents()

        assert starts == ["start"]
    finally:
        window.hide()
        window.deleteLater()
        qapp.processEvents()


def test_close_before_deferred_start_cancels_service_monitor_creation(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close request must cancel a monitor callback that has not run yet."""
    cfg = _minimal_window_config()
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    starts: list[str] = []
    window = MainWindow()
    window._start_service_monitor = lambda: starts.append("start")

    window.show()
    try:
        assert starts == []

        window.close()
        qapp.processEvents()

        assert starts == []
    finally:
        if window.isVisible():
            window.hide()
        window.deleteLater()
        qapp.processEvents()


def test_close_keeps_window_open_until_retained_monitor_thread_exits(
    bare_window: MainWindow,
) -> None:
    """A live monitor thread must not be destroyed by an accepted window close."""
    class IdleTracker:
        running_count = 0

    class FakeMonitor:
        @staticmethod
        def request_shutdown() -> None:
            return None

        @staticmethod
        def stop() -> None:
            return None

    class StuckThread:
        @staticmethod
        def requestInterruption() -> None:
            return None

        @staticmethod
        def quit() -> None:
            return None

        @staticmethod
        def wait(_timeout_ms: int) -> bool:
            return False

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    monitor = FakeMonitor()
    thread = StuckThread()
    event = CloseEvent()
    bare_window._tracker = IdleTracker()
    bare_window._service_monitor = monitor
    bare_window._service_thread = thread
    bare_window._service_monitor_stop_requested.connect(monitor.stop)

    bare_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert bare_window._service_monitor is monitor
    assert bare_window._service_thread is thread


def test_close_defers_window_acceptance_until_running_update_check_finishes(
    bare_window: MainWindow,
) -> None:
    """A live updater QThread must finish before its parent window is destroyed."""
    class IdleTracker:
        running_count = 0

    class RunningChecker:
        @staticmethod
        def isRunning() -> bool:
            return True

    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = CloseEvent()
    bare_window._tracker = IdleTracker()
    bare_window._active_update_checkers = [RunningChecker()]

    bare_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert bare_window._close_in_progress is True
