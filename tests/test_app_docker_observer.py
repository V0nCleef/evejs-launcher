"""Acceptance coverage for Docker observer replacement and read-only presentation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.constants import Page
from src.core.launcher import ClientLaunchContext
from src.core.runtime.data import docker_settings_identity
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import DockerControlPolicy, RuntimeBackend, RuntimeSnapshot, ServiceState
from src.workers.docker_monitor import DockerObservation, DockerMonitor


class _Tracker:
    running_count = 0

    def kill_all(self) -> int:
        raise AssertionError("native client mutation must not run")


def _endpoints(offset: int = 0) -> RuntimeEndpoints:
    def endpoint(service: str, target: int, port: int) -> Endpoint:
        return Endpoint(service, "127.0.0.1", port + offset, target, "tcp")

    return RuntimeEndpoints(
        game=endpoint("server", 26000, 32600),
        image=endpoint("server", 26001, 32601),
        proxy=endpoint("server", 26002, 32602),
        assets=endpoint("server", 26003, 34443),
        xmpp=endpoint("server", 5222, 35222),
        market=endpoint("market", 40110, 40110),
    )


def _settings_identity(window: MainWindow) -> str:
    return docker_settings_identity(
        str(window._cfg.get("evejs_root", "")),
        str(window._cfg.get("docker_compose_file", "")),
        str(window._cfg.get("docker_project_name", "")),
    )


def _identity_fields(window: MainWindow) -> dict[str, object]:
    return {
        "target_identity": "docker:fixture-target",
        "settings_identity": _settings_identity(window),
        "monitor_generation": window._monitor_generation,
    }


class _Button:
    def __init__(self) -> None:
        self.text = ""
        self.enabled = True
        self.tooltip = ""

    def setText(self, value: str) -> None:
        self.text = value

    def setEnabled(self, value: bool) -> None:
        self.enabled = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value


@pytest.fixture
def docker_window(qapp: QApplication) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._cfg.update({"runtime_backend": "docker_compose", "evejs_root": "C:/EveJS"})
    window._tracker = _Tracker()
    window._runtime_snapshot = RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.OFFLINE, 0)
    window._server_proc = None
    window._market_proc = None
    window._server_intent = None
    window._market_intent = None
    window._server_error = None
    window._market_error = None
    window._service_reachability = (False, False)
    window._service_thread = None
    window._service_monitor = None
    window._service_monitor_start_pending = False
    window._service_monitor_restart_pending = False
    window._monitor_generation = 0
    window._close_in_progress = False
    yield window
    assert window._service_thread is None
    assert window._service_monitor is None
    assert window._service_monitor_start_pending is False
    assert window._service_monitor_restart_pending is False
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def test_docker_refresh_preserves_online_container_without_native_probe(docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.STARTING, 0, backend=RuntimeBackend.DOCKER_COMPOSE,
        game_container="server-id", market_container="market-id",
    )
    docker_window._apply_runtime_snapshot = lambda snapshot: setattr(docker_window, "published", snapshot)
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: pytest.fail("native probe"))

    docker_window._update_status_bar()

    assert docker_window.published.game is ServiceState.ONLINE
    assert docker_window.published.game_container == "server-id"


def test_docker_observation_propagates_exact_effective_endpoints(
    docker_window: MainWindow,
) -> None:
    endpoints = _endpoints()
    docker_window._monitor_generation = 4
    docker_window._apply_runtime_snapshot = lambda snapshot: setattr(
        docker_window, "published", snapshot
    )

    docker_window._on_docker_observation(
        DockerObservation(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            game_identity="server-id",
            market_identity="market-id",
            endpoints=endpoints,
            **_identity_fields(docker_window),
        ),
        4,
    )

    assert docker_window.published.endpoints is endpoints
    assert docker_window.published.target_identity == "docker:fixture-target"
    assert docker_window.published.settings_identity == _settings_identity(
        docker_window
    )
    assert docker_window.published.monitor_generation == 4


def test_docker_observation_rejects_wrong_embedded_generation_or_settings(
    docker_window: MainWindow,
) -> None:
    docker_window._monitor_generation = 4
    original = docker_window._runtime_snapshot
    docker_window._apply_runtime_snapshot = lambda _snapshot: pytest.fail(
        "stale observation rendered"
    )
    current_settings = _settings_identity(docker_window)

    docker_window._on_docker_observation(
        DockerObservation(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            endpoints=_endpoints(),
            target_identity="docker:wrong-settings",
            settings_identity="docker-settings:wrong",
            monitor_generation=4,
        ),
        4,
    )
    docker_window._on_docker_observation(
        DockerObservation(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            endpoints=_endpoints(),
            target_identity="docker:old-generation",
            settings_identity=current_settings,
            monitor_generation=3,
        ),
        4,
    )

    assert docker_window._runtime_snapshot is original


def test_observation_target_change_immediately_invalidates_old_runtime_state(
    docker_window: MainWindow,
) -> None:
    class Worker:
        cancelled = False

        def request_cancel(self) -> None:
            self.cancelled = True

    docker_window._monitor_generation = 4
    settings_identity = _settings_identity(docker_window)
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        endpoints=_endpoints(),
        target_identity="docker:target-a",
        settings_identity=settings_identity,
        monitor_generation=4,
    )
    docker_window._data_selection = object()
    docker_window._accounts = [object()]
    docker_window._account_request_token = object()
    docker_window._pending_account_request = (object(), object())
    docker_window._account_start_scheduled = True
    docker_window._account_thread = None
    docker_window._account_worker = Worker()
    docker_window._detail_request_token = object()
    docker_window._pending_detail_request = (object(), object(), 1)
    docker_window._detail_start_scheduled = True
    docker_window._detail_thread = None
    docker_window._detail_worker = Worker()
    queue_cancellations: list[str] = []
    docker_window._launch_queue = type(
        "Queue",
        (),
        {"cancel": lambda _self: queue_cancellations.append("queue")},
    )()
    events: list[str] = []
    docker_window._characters_page = type(
        "Characters",
        (),
        {
            "invalidate_portrait_target": lambda _self: events.append("portrait")
        },
    )()
    docker_window._stop_docker_log_stream = lambda: events.append("logs")
    docker_window._refresh_character_views = lambda: events.append("views")
    docker_window._refresh_characters = lambda: events.append("reload")
    docker_window._apply_runtime_snapshot = lambda snapshot: events.append("snapshot")

    docker_window._on_docker_observation(
        DockerObservation(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            endpoints=_endpoints(100),
            target_identity="docker:target-b",
            settings_identity=settings_identity,
            monitor_generation=4,
        ),
        4,
    )

    assert queue_cancellations == ["queue"]
    assert docker_window._account_worker.cancelled is True
    assert docker_window._detail_worker.cancelled is True
    assert docker_window._account_request_token is None
    assert docker_window._detail_request_token is None
    assert docker_window._pending_account_request is None
    assert docker_window._pending_detail_request is None
    assert docker_window._data_selection is None
    assert docker_window._accounts == []
    assert events == ["portrait", "logs", "views", "snapshot", "reload"]
    assert docker_window._runtime_snapshot.target_identity == "docker:target-b"


def test_observation_image_endpoint_change_refreshes_portrait_context_only(
    docker_window: MainWindow,
) -> None:
    docker_window._monitor_generation = 4
    settings_identity = _settings_identity(docker_window)
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        endpoints=_endpoints(),
        target_identity="docker:target-a",
        settings_identity=settings_identity,
        monitor_generation=4,
    )
    selection = object()
    accounts = [object()]
    docker_window._data_selection = selection
    docker_window._accounts = accounts
    events: list[str] = []
    docker_window._refresh_character_views = lambda: events.append("views")
    docker_window._apply_runtime_snapshot = lambda snapshot: events.append("snapshot")
    docker_window._refresh_characters = lambda: pytest.fail("data reloaded")
    docker_window._stop_docker_log_stream = lambda: pytest.fail("logs stopped")
    docker_window._cancel_launch_queue = lambda: pytest.fail("queue cancelled")

    docker_window._on_docker_observation(
        DockerObservation(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            endpoints=_endpoints(100),
            target_identity="docker:target-a",
            settings_identity=settings_identity,
            monitor_generation=4,
        ),
        4,
    )

    assert docker_window._data_selection is selection
    assert docker_window._accounts is accounts
    assert docker_window._runtime_snapshot.endpoints == _endpoints(100)
    assert events == ["views", "snapshot"]


def test_native_snapshot_switch_to_docker_bootstrap_drops_pid_and_ownership(docker_window: MainWindow) -> None:
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.FAILED, 3, game_pid=123, market_pid=456,
        game_owned=True, market_owned=True, game_error="native error", market_error="native error",
    )
    docker_window._apply_runtime_snapshot = lambda snapshot: setattr(docker_window, "published", snapshot)

    docker_window._publish_cached_runtime()

    assert docker_window.published.backend is RuntimeBackend.DOCKER_COMPOSE
    assert docker_window.published.game is ServiceState.UNKNOWN
    assert docker_window.published.game_pid is None
    assert docker_window.published.market_pid is None
    assert not docker_window.published.game_owned and not docker_window.published.market_owned
    assert docker_window.published.game_error is None
    assert docker_window.published.endpoints is None
    assert docker_window.published.target_identity is None
    assert docker_window.published.settings_identity == _settings_identity(
        docker_window
    )
    assert docker_window.published.monitor_generation == docker_window._monitor_generation


def test_docker_online_actions_never_render_external_or_mutation_labels() -> None:
    for state in ServiceState:
        label = MainWindow._service_action_text("Server", state, False, RuntimeBackend.DOCKER_COMPOSE)
        assert "External" not in label
        assert "▶ Start" not in label and "■ Stop" not in label and "↻ Retry" not in label
    assert MainWindow._service_action_text("Server", ServiceState.ONLINE, False, RuntimeBackend.DOCKER_COMPOSE) == "Server: Online"


def test_docker_to_native_snapshot_restores_navigation_and_tooltips(docker_window: MainWindow) -> None:
    class Nav:
        btn_server = _Button(); btn_market = _Button(); btn_characters = _Button(); btn_mods = _Button(); btn_tools = _Button(); btn_kill_all = _Button()
        def set_badge_count(self, *_args) -> None: pass
    class Status:
        def set_server_state(self, *_args, **_kwargs) -> None: pass
        def set_market_state(self, *_args, **_kwargs) -> None: pass
        def set_client_count(self, *_args) -> None: pass
    class Home:
        btn_start_servers = _Button(); btn_stop_servers = _Button()
        def apply_runtime_snapshot(self, *_args) -> None: pass
    docker_window._nav, docker_window._status_bar, docker_window._home_page = Nav(), Status(), Home()

    docker_window._apply_runtime_snapshot(RuntimeSnapshot(ServiceState.ONLINE, ServiceState.ONLINE, 0, backend=RuntimeBackend.DOCKER_COMPOSE, docker_control_policy=DockerControlPolicy.CONNECT_ONLY))
    docker_window._apply_runtime_snapshot(RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.OFFLINE, 0))

    assert docker_window._nav.btn_tools.enabled is True
    assert docker_window._nav.btn_tools.tooltip == ""


def test_async_docker_monitor_finish_schedules_exactly_one_replacement_without_wait(docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    class Thread:
        wait_called = False
        def requestInterruption(self) -> None: pass
        def quit(self) -> None: pass
        def wait(self, *_args) -> bool:
            self.wait_called = True
            return True
        def deleteLater(self) -> None: pass
    class Monitor(DockerMonitor):
        def __init__(self) -> None:
            super().__init__(lambda: object(), inspector_factory=lambda: object())
        def request_shutdown(self) -> None: pass
    thread, monitor = Thread(), Monitor()
    docker_window._service_thread, docker_window._service_monitor = thread, monitor
    docker_window._apply_runtime_snapshot = lambda _snapshot: None
    docker_window._apply_runtime_settings = lambda: None
    docker_window._home_page = type("Home", (), {"set_server_mode": lambda *_args: None})()
    docker_window._refresh_characters = lambda: None
    docker_window._mods_page = type("Mods", (), {"refresh_mods": lambda *_args: None})()
    docker_window._tools_page = type("Tools", (), {"set_evejs_root": lambda *_args: None})()
    scheduled: list[str] = []
    monkeypatch.setattr(docker_window, "_schedule_service_monitor_start", lambda: scheduled.append("start"))

    docker_window._on_settings_saved({**docker_window._cfg, "docker_project_name": "replacement"})
    assert thread.wait_called is False
    assert scheduled == []
    docker_window._on_service_monitor_thread_finished(thread)
    docker_window._on_service_monitor_thread_finished(thread)
    assert scheduled == ["start"]


def test_switching_docker_to_native_restores_native_snapshot_before_old_monitor_finishes(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Thread:
        wait_called = False

        def requestInterruption(self) -> None: pass
        def quit(self) -> None: pass
        def wait(self, *_args) -> bool:
            self.wait_called = True
            return True
        def deleteLater(self) -> None: pass

    class Monitor(DockerMonitor):
        def __init__(self) -> None:
            super().__init__(lambda: object(), inspector_factory=lambda: object())
        def request_shutdown(self) -> None: pass

    class Nav:
        btn_server = _Button(); btn_market = _Button(); btn_characters = _Button(); btn_mods = _Button(); btn_tools = _Button(); btn_kill_all = _Button()
        def set_badge_count(self, *_args) -> None: pass

    class Status:
        def set_server_state(self, *_args, **_kwargs) -> None: pass
        def set_market_state(self, *_args, **_kwargs) -> None: pass
        def set_client_count(self, *_args) -> None: pass

    class Home:
        btn_start_servers = _Button(); btn_stop_servers = _Button()
        def apply_runtime_snapshot(self, snapshot) -> None: self.snapshot = snapshot
        def set_server_mode(self, *_args) -> None: pass

    thread, monitor = Thread(), Monitor()
    docker_window._service_thread, docker_window._service_monitor = thread, monitor
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.ONLINE, 0,
        backend=RuntimeBackend.DOCKER_COMPOSE, game_container="game-id",
        market_container="market-id", game_health="healthy", market_health="healthy",
        endpoints=_endpoints(),
    )
    docker_window._nav, docker_window._status_bar, docker_window._home_page = Nav(), Status(), Home()
    docker_window._apply_runtime_settings = lambda: None
    docker_window._refresh_characters = lambda: None
    scheduled: list[str] = []
    monkeypatch.setattr(
        docker_window,
        "_schedule_service_monitor_start",
        lambda: scheduled.append("start"),
    )

    docker_window._on_settings_saved({**docker_window._cfg, "runtime_backend": "native"})

    snapshot = docker_window._runtime_snapshot
    assert snapshot.backend is RuntimeBackend.NATIVE
    assert snapshot.game_container is None and snapshot.market_container is None
    assert snapshot.game_health is None and snapshot.market_health is None
    assert snapshot.endpoints is None
    assert docker_window._nav.btn_tools.enabled is True
    assert docker_window._nav.btn_tools.tooltip == ""
    assert thread.wait_called is False

    docker_window._on_service_monitor_thread_finished(thread)
    assert scheduled == ["start"]


def test_close_wins_over_pending_docker_monitor_restart(docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    class Thread:
        def deleteLater(self) -> None: pass
    docker_window._service_thread = Thread()
    docker_window._service_monitor_restart_pending = True
    docker_window._close_in_progress = True
    scheduled: list[str] = []
    queued: list[tuple[int, object]] = []
    monkeypatch.setattr(docker_window, "_schedule_service_monitor_start", lambda: scheduled.append("start"))
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda delay, callback: queued.append((delay, callback)),
    )

    docker_window._on_service_monitor_thread_finished(docker_window._service_thread)

    assert scheduled == []
    assert docker_window._service_monitor_restart_pending is False
    assert len(queued) == 1
    assert queued[0][0] == 0
    assert getattr(queued[0][1], "__self__", None) is docker_window


@pytest.mark.parametrize("policy", ["managed", "connect_only"])
def test_docker_tools_page_remains_navigable_and_refreshes_backend_view(
    docker_window: MainWindow,
    policy: str,
) -> None:
    class Stack:
        current = int(Page.HOME)
        def currentIndex(self) -> int: return self.current
        def setCurrentIndex(self, index: int) -> None: self.current = index
    refreshed: list[str] = []
    docker_window._cfg["docker_control_policy"] = policy
    docker_window._docker_unavailable = lambda _message: None
    docker_window._stack = Stack()
    docker_window._tools_page = type(
        "Tools",
        (),
        {"refresh_tools": lambda _self, root: refreshed.append(root)},
    )()
    docker_window._nav = type(
        "Nav",
        (),
        {"nav_group": type("Group", (), {"button": lambda *_args: None})()},
    )()

    docker_window._switch_page(int(Page.TOOLS))

    assert docker_window._stack.current == int(Page.TOOLS)
    assert refreshed == [str(docker_window._cfg["evejs_root"])]


@pytest.mark.parametrize("policy", ["managed", "connect_only"])
def test_docker_mods_page_remains_navigable(
    docker_window: MainWindow,
    policy: str,
) -> None:
    class Stack:
        current = int(Page.HOME)
        def currentIndex(self) -> int: return self.current
        def setCurrentIndex(self, index: int) -> None: self.current = index
    docker_window._cfg["docker_control_policy"] = policy
    docker_window._docker_unavailable = lambda _message: None
    docker_window._stack = Stack()
    docker_window._nav = type(
        "Nav",
        (),
        {"nav_group": type("Group", (), {"button": lambda *_args: None})()},
    )()

    docker_window._switch_page(int(Page.MODS))

    assert docker_window._stack.current == int(Page.MODS)


def test_docker_central_lifecycle_tool_and_console_guards_precede_native_calls(docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    docker_window._docker_unavailable = lambda message: None
    monkeypatch.setattr("src.app.is_server_running", lambda **_kwargs: pytest.fail("native probe"))
    docker_window._resolve_server_start = lambda: pytest.fail("native resolver")
    docker_window._tracker = _Tracker()
    docker_window._refresh_characters = lambda: pytest.fail("native character refresh")
    docker_window._update_status_bar = lambda: pytest.fail("native status refresh")

    docker_window._start_all_servers()
    docker_window._ensure_server_if_needed(lambda: pytest.fail("launch continuation"))
    docker_window._on_tool_launch_requested(None, None)  # type: ignore[arg-type]
    docker_window._console_panel = type("Console", (), {"isVisible": lambda _self: pytest.fail("console access")})()
    docker_window._on_console_toggled("server")


def test_stale_native_probe_is_ignored_after_docker_switch_and_during_close(docker_window: MainWindow) -> None:
    docker_window._monitor_generation = 4
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.UNKNOWN, ServiceState.UNKNOWN, 0, backend=RuntimeBackend.DOCKER_COMPOSE
    )
    docker_window._apply_runtime_snapshot = lambda snapshot: pytest.fail("stale Native snapshot applied")
    probe = type("Probe", (), {"game_reachable": True, "market_reachable": True})()

    docker_window._on_service_probe(probe, 3)
    docker_window._close_in_progress = True
    docker_window._on_service_probe(probe, 4)

    assert docker_window._runtime_snapshot.backend is RuntimeBackend.DOCKER_COMPOSE


def test_docker_identity_change_publishes_clean_unknown_and_rejects_old_observation(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker_window._monitor_generation = 7
    old_settings_identity = _settings_identity(docker_window)
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.ONLINE, 0,
        backend=RuntimeBackend.DOCKER_COMPOSE, game_container="old-game",
        market_container="old-market", game_health="healthy", market_health="healthy",
        game_error="old error", market_error="old error",
        endpoints=_endpoints(),
        **_identity_fields(docker_window),
    )
    docker_window._apply_runtime_snapshot = lambda snapshot: setattr(docker_window, "published", snapshot)
    docker_window._service_thread = None
    page_refreshes: list[tuple[str, str]] = []
    docker_window._mods_page = type("Mods", (), {"refresh_mods": lambda _self: page_refreshes.append(("mods", "C:/NewEveJS"))})()
    docker_window._tools_page = type("Tools", (), {"set_evejs_root": lambda _self, root: page_refreshes.append(("tools", root))})()
    docker_window._home_page = type("Home", (), {"set_server_mode": lambda *_args: None})()
    docker_window._refresh_characters = lambda: None
    docker_window._apply_runtime_settings = lambda: None
    portrait_invalidations: list[str] = []
    queue_cancellations: list[str] = []
    docker_window._launch_queue = type(
        "Queue",
        (),
        {"cancel": lambda _self: queue_cancellations.append("queue")},
    )()
    docker_window._characters_page = type(
        "Characters",
        (),
        {
            "invalidate_portrait_target": lambda _self: portrait_invalidations.append(
                "portrait"
            )
        },
    )()
    scheduled: list[str] = []
    monkeypatch.setattr(
        docker_window,
        "_schedule_service_monitor_start",
        lambda: scheduled.append("start"),
    )
    monkeypatch.setattr("src.app.clear_solar_system_name_cache", lambda: None)
    monkeypatch.setattr("src.app.PortraitCache.clear", lambda: None)

    docker_window._on_settings_saved({**docker_window._cfg, "docker_project_name": "new-project", "evejs_root": "C:/NewEveJS"})

    assert docker_window.published.game is ServiceState.UNKNOWN
    assert docker_window.published.market is ServiceState.UNKNOWN
    assert docker_window.published.game_pid is None and docker_window.published.market_pid is None
    assert not docker_window.published.game_owned and not docker_window.published.market_owned
    assert docker_window.published.game_error is None and docker_window.published.market_error is None
    assert docker_window.published.game_health is None and docker_window.published.market_health is None
    assert docker_window.published.game_container is None and docker_window.published.market_container is None
    assert docker_window.published.endpoints is None
    assert docker_window.published.target_identity is None
    assert docker_window.published.settings_identity == _settings_identity(
        docker_window
    )
    assert docker_window.published.monitor_generation == 8
    assert portrait_invalidations == ["portrait"]
    assert queue_cancellations == ["queue"]
    assert page_refreshes == [
        ("mods", "C:/NewEveJS"),
        ("tools", "C:/NewEveJS"),
    ]
    assert scheduled == ["start"]

    docker_window._on_docker_observation(
        DockerObservation(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            game_identity="old-game",
            endpoints=_endpoints(),
            target_identity="docker:fixture-target",
            settings_identity=old_settings_identity,
            monitor_generation=7,
        ),
        7,
    )

    assert docker_window._runtime_snapshot.game is ServiceState.UNKNOWN
    assert docker_window._runtime_snapshot.endpoints is None

    docker_window._docker_unavailable = lambda message: setattr(
        docker_window,
        "launch_error",
        message,
    )
    monkeypatch.setattr(
        app_module,
        "profile_exists",
        lambda *_args: pytest.fail("stale context reached profile work"),
    )
    assert not docker_window._launch_account(
        "fixture-account",
        "Fixture Character",
        show_errors=True,
    )
    assert "endpoint" in docker_window.launch_error.casefold()


def test_docker_direct_page_change_and_root_save_refresh_runtime_pages(
    docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker_window._monitor_generation = 0
    docker_window._nav = type("Nav", (), {"nav_group": type("Group", (), {"button": lambda *_args: None})()})()
    unavailable: list[str] = []
    tools_refreshes: list[str] = []
    tools_roots: list[str] = []
    mods_refreshes: list[str] = []
    docker_window._docker_unavailable = unavailable.append
    docker_window._tools_page = type("Tools", (), {"refresh_tools": lambda _self, root: tools_refreshes.append(root), "set_evejs_root": lambda _self, root: tools_roots.append(root)})()
    docker_window._mods_page = type("Mods", (), {"refresh_mods": lambda _self: mods_refreshes.append("refresh")})()
    docker_window._home_page = type("Home", (), {"set_server_mode": lambda *_args: None})()
    docker_window._apply_runtime_settings = lambda: None
    docker_window._refresh_characters = lambda: None
    docker_window._apply_runtime_snapshot = lambda snapshot: setattr(docker_window, "published", snapshot)
    scheduled: list[str] = []
    monkeypatch.setattr(
        docker_window,
        "_schedule_service_monitor_start",
        lambda: scheduled.append("start"),
    )
    monkeypatch.setattr("src.app.clear_solar_system_name_cache", lambda: None)
    monkeypatch.setattr("src.app.PortraitCache.clear", lambda: None)

    docker_window._on_page_changed(int(Page.TOOLS))
    docker_window._on_settings_saved({**docker_window._cfg, "evejs_root": "C:/DifferentEveJS"})

    assert unavailable == []
    assert tools_refreshes == ["C:/EveJS"]
    assert tools_roots == ["C:/DifferentEveJS"]
    assert mods_refreshes == ["refresh"]
    assert scheduled == ["start"]


def test_docker_policy_change_republishes_policy_without_restarting_monitor(docker_window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    docker_window._monitor_generation = 3
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.OFFLINE, 0, backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.CONNECT_ONLY, game_container="game",
        endpoints=_endpoints(),
        **_identity_fields(docker_window),
    )
    docker_window._apply_runtime_snapshot = lambda snapshot: setattr(docker_window, "published", snapshot)
    docker_window._home_page = type("Home", (), {"set_server_mode": lambda *_args: None})()
    docker_window._apply_runtime_settings = lambda: None
    docker_window._refresh_characters = lambda: None
    monkeypatch.setattr(docker_window, "_stop_service_monitor", lambda: pytest.fail("monitor stopped"))
    monkeypatch.setattr(docker_window, "_schedule_service_monitor_start", lambda: pytest.fail("monitor restarted"))

    docker_window._on_settings_saved({**docker_window._cfg, "docker_control_policy": "managed"})

    assert docker_window.published.docker_control_policy is DockerControlPolicy.MANAGED
    assert docker_window.published.game is ServiceState.ONLINE
    assert docker_window.published.game_container == "game"
    assert docker_window.published.endpoints == _endpoints()
    assert docker_window.published.target_identity == "docker:fixture-target"
    assert docker_window.published.settings_identity == _settings_identity(
        docker_window
    )
    assert docker_window.published.monitor_generation == 3


def test_docker_missing_endpoints_fail_before_profile_and_client_work(
    docker_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
        **_identity_fields(docker_window),
    )
    messages: list[str] = []
    docker_window._docker_unavailable = messages.append
    monkeypatch.setattr("src.app.profile_exists", lambda *_args: pytest.fail("profile work"))
    monkeypatch.setattr("src.app.launch_client", lambda **_kwargs: pytest.fail("client work"))

    assert docker_window._launch_account("account", "character", show_errors=True) is False
    assert len(messages) == 1
    assert "endpoint" in messages[0].casefold()


def test_complete_connect_only_endpoints_use_one_context_for_profile_and_client(
    docker_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    endpoints = _endpoints()
    docker_window._cfg.update(
        {
            "docker_control_policy": "connect_only",
            "client_path": str(tmp_path / "client"),
            "proxy_url": "http://127.0.0.1:26002",
            "game_port": 26000,
        }
    )
    docker_window._resolve_configured_client_path = (
        lambda client_path, _evejs_root: Path(client_path)
    )
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
        endpoints=endpoints,
        **_identity_fields(docker_window),
    )
    events: list[tuple[object, ...]] = []

    class Tracker:
        running_count = 0

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

    docker_window._tracker = Tracker()
    profile_root = tmp_path / "profiles" / "fixture-account"
    (profile_root / "tq").mkdir(parents=True)
    monkeypatch.setattr(app_module, "PROFILES_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(
        app_module,
        "profile_exists",
        lambda username: events.append(("profile", username)) or True,
    )
    monkeypatch.setattr(
        app_module,
        "prefill_username",
        lambda username: events.append(("prefill", username)),
    )
    def configure_profile(
        username: str,
        profile_tq_path,
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

    assert docker_window._launch_account("fixture-account", "Fixture Character") is True

    context = next(event[1] for event in events if event[0] == "launch")
    assert isinstance(context, ClientLaunchContext)
    assert (context.game_host, context.game_port) == (
        endpoints.game.host,
        endpoints.game.port,
    )
    assert context.proxy_url == "http://127.0.0.1:32602"
    assert context.image_url == "http://127.0.0.1:32601"
    assert context.target_identity == "docker:fixture-target"
    assert context.settings_identity == _settings_identity(docker_window)
    assert context.monitor_generation == docker_window._monitor_generation
    assert events[:2] == [
        ("profile", "fixture-account"),
        ("prefill", "fixture-account"),
    ]
    assert events[2] == (
        "configure",
        "fixture-account",
        profile_root / "tq",
        context.game_host,
        context.game_port,
    )


def test_captured_docker_context_is_rejected_after_generation_changes(
    docker_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker_window._monitor_generation = 8
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
        endpoints=_endpoints(),
        target_identity="docker:fixture-target",
        settings_identity=_settings_identity(docker_window),
        monitor_generation=8,
    )
    stale_context = ClientLaunchContext.from_docker(
        _endpoints(),
        target_identity="docker:fixture-target",
        settings_identity=_settings_identity(docker_window),
        monitor_generation=7,
    )
    messages: list[str] = []
    docker_window._docker_unavailable = messages.append
    monkeypatch.setattr(
        app_module,
        "profile_exists",
        lambda *_args: pytest.fail("stale queue context reached profile work"),
    )

    assert not docker_window._launch_account(
        "fixture-account",
        "Fixture Character",
        show_errors=True,
        launch_context=stale_context,
    )
    assert len(messages) == 1
    assert "endpoint" in messages[0].casefold()


@pytest.mark.parametrize("policy", ["connect_only", "managed"])
def test_ready_docker_server_with_complete_endpoints_runs_launch_continuation(
    docker_window: MainWindow,
    policy: str,
) -> None:
    docker_window._cfg["docker_control_policy"] = policy
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.ONLINE,
        ServiceState.OFFLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy(policy),
        endpoints=_endpoints(),
        **_identity_fields(docker_window),
    )
    ready: list[str] = []

    assert docker_window._ensure_server_if_needed(lambda: ready.append("ready")) is True
    assert ready == ["ready"]


def test_stopped_connect_only_server_requires_external_start_without_mutation(
    docker_window: MainWindow,
) -> None:
    docker_window._cfg["docker_control_policy"] = "connect_only"
    docker_window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.OFFLINE,
        ServiceState.OFFLINE,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
        endpoints=_endpoints(),
        **_identity_fields(docker_window),
    )
    messages: list[str] = []
    docker_window._docker_unavailable = messages.append
    docker_window._begin_docker_lifecycle = lambda _action: pytest.fail(
        "Connect-only launch gate mutated containers"
    )

    assert docker_window._ensure_server_if_needed(lambda: pytest.fail("not ready")) is False
    assert len(messages) == 1
    assert "externally" in messages[0].casefold()


def test_docker_host_client_kill_control_remains_available(
    docker_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Tracker:
        running_count = 2

        def kill_all(self) -> int:
            calls.append("kill")
            self.running_count = 0
            return 2

    docker_window._tracker = Tracker()
    docker_window._refresh_characters = lambda: calls.append("refresh")
    docker_window._update_status_bar = lambda: calls.append("status")
    docker_window._docker_unavailable = lambda _message: pytest.fail(
        "Host clients are not Docker containers"
    )
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *_args: None)

    docker_window._kill_all_clients()

    assert calls == ["kill", "refresh", "status"]
