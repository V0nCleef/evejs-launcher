"""Acceptance contracts for Docker lifecycle presentation without a daemon."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import app as app_module
from src.app import MainWindow
from src.core.runtime.docker_compose import ContainerRecord
from src.core.runtime.docker_controller import (
    DockerLifecycleAction,
    DockerLifecycleResult,
)
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.pages.home_page import HomePage
from src.widgets.nav_panel import NavPanel
from src.widgets.status_bar import StatusBar
from src.workers.docker_monitor import DockerMonitor


class _Tracker:
    def __init__(self, running_count: int = 0) -> None:
        self.running_count = running_count

    def kill_all(self) -> int:
        stopped = self.running_count
        self.running_count = 0
        return stopped


class _CloseEvent:
    def __init__(self) -> None:
        self.accepted = False
        self.ignored = False

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


class _FinishedThread:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:
        self.deleted = True


def _record(
    service: str,
    state: ServiceState,
    *,
    raw_state: str,
    identity: str | None = None,
    health: str | None = None,
) -> ContainerRecord:
    return ContainerRecord(
        service,
        f"fixture-{service}" if identity else None,
        identity,
        state,
        health,
        None,
        (),
        raw_state=raw_state,
    )


def _docker_snapshot(
    game: ServiceState = ServiceState.OFFLINE,
    market: ServiceState = ServiceState.OFFLINE,
    **changes,
) -> RuntimeSnapshot:
    values = {
        "game": game,
        "market": market,
        "running_clients": 0,
        "backend": RuntimeBackend.DOCKER_COMPOSE,
        "docker_control_policy": DockerControlPolicy.MANAGED,
    }
    values.update(changes)
    return RuntimeSnapshot(**values)


def _bare_docker_window(tmp_path, snapshot: RuntimeSnapshot | None = None) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "managed",
        "evejs_root": str(tmp_path),
        "docker_compose_file": str(tmp_path / "compose.yaml"),
        "docker_project_name": "fixture",
        "docker_keep_running_on_exit": True,
    }
    window._tracker = _Tracker()
    window._runtime_snapshot = snapshot or _docker_snapshot()
    window._monitor_generation = 7
    window._lifecycle_thread = None
    window._lifecycle_worker = None
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    window._lifecycle_after_thread_callback = None
    window._docker_lifecycle_snapshot = None
    window._docker_lifecycle_generation = None
    window._docker_lifecycle_target = None
    window._docker_lifecycle_action = None
    window._docker_close_pending = False
    window._docker_close_stop_started = False
    window._docker_close_stop_succeeded = False
    window._close_in_progress = False
    window._docker_log_thread = None
    window._docker_log_worker = None
    window._update_install_worker = None
    window._active_update_checkers = []
    window._service_thread = None
    window._service_monitor = None
    window._service_monitor_start_pending = False
    applied: list[RuntimeSnapshot] = []
    window._apply_runtime_snapshot = applied.append
    window._captured_lifecycle = None
    window._begin_lifecycle_worker = (
        lambda worker, handler: setattr(window, "_captured_lifecycle", (worker, handler))
    )
    window._applied_snapshots = applied
    return window


@pytest.mark.parametrize(
    ("state", "label", "enabled"),
    [
        (ServiceState.OFFLINE, "Start Stack", True),
        (ServiceState.FAILED, "Retry Stack", True),
        (ServiceState.ONLINE, "Stop Stack", True),
        (ServiceState.STARTING, "Starting…", False),
        (ServiceState.STOPPING, "Stopping…", False),
        (ServiceState.UNKNOWN, "Docker unavailable", False),
    ],
)
def test_home_managed_docker_stack_actions(qapp, state, label, enabled) -> None:
    home = HomePage()
    home.apply_runtime_snapshot(RuntimeSnapshot(
        state, state, 0, backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.MANAGED,
    ))
    assert home.btn_start_servers.text() == label
    assert home.btn_start_servers.isEnabled() is enabled


def test_home_connect_only_is_observational_and_native_is_reversible(qapp) -> None:
    home = HomePage()
    home.apply_runtime_snapshot(RuntimeSnapshot(
        ServiceState.ONLINE, ServiceState.OFFLINE, 0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
    ))
    assert not home.btn_start_servers.isEnabled()
    assert "Connect-only" in home.btn_start_servers.toolTip()
    home.apply_runtime_snapshot(RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.OFFLINE, 0))
    assert home.btn_start_servers.text() == "Start Stack"
    assert home.btn_start_servers.isEnabled()


def test_managed_nav_uses_actions_and_connect_only_never_enables() -> None:
    assert MainWindow._service_action_text(
        "Server", ServiceState.OFFLINE, False, RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.MANAGED,
    ) == "▶ Start Server"
    assert MainWindow._service_action_text(
        "Market", ServiceState.ONLINE, False, RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.MANAGED,
    ) == "■ Stop Market"
    assert not MainWindow._service_action_enabled(
        ServiceState.ONLINE, False, RuntimeBackend.DOCKER_COMPOSE,
        DockerControlPolicy.CONNECT_ONLY,
    )


@pytest.mark.parametrize(("method", "action"), [
    ("_start_market", DockerLifecycleAction.START_MARKET),
    ("_start_server", DockerLifecycleAction.START_GAME),
    ("_start_all_servers", DockerLifecycleAction.START_STACK),
    ("_stop_server", DockerLifecycleAction.STOP_GAME),
    ("_stop_market", DockerLifecycleAction.STOP_MARKET),
    ("_stop_all_servers", DockerLifecycleAction.STOP_ALL),
    ("_restart_server", DockerLifecycleAction.RESTART_GAME),
])
def test_all_docker_routes_map_to_exact_allowlisted_action(method, action) -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {"runtime_backend": "docker_compose", "docker_control_policy": "managed"}
    calls: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = lambda received: calls.append(received) or True

    getattr(window, method)()

    assert calls == [action]


@pytest.mark.parametrize("method", [
    "_start_market", "_start_server", "_start_all_servers", "_stop_server",
    "_stop_market", "_stop_all_servers", "_restart_server",
])
def test_connect_only_routes_construct_no_worker_or_controller(method, monkeypatch: pytest.MonkeyPatch) -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {"runtime_backend": "docker_compose", "docker_control_policy": "connect_only"}
    denials: list[str] = []
    window._docker_unavailable = denials.append
    window._begin_docker_lifecycle = lambda _action: pytest.fail("lifecycle construction")
    monkeypatch.setattr("src.app.DockerLifecycleWorker", lambda *_args, **_kwargs: pytest.fail("worker construction"))
    monkeypatch.setattr("src.app.DockerCommandRunner", lambda *_args, **_kwargs: pytest.fail("runner construction"))

    getattr(window, method)()

    assert denials == ["Connect-only Docker mode cannot change containers."]


@pytest.mark.parametrize(("game", "market", "label"), [
    (ServiceState.FAILED, ServiceState.ONLINE, "Stop Stack"),
    (ServiceState.ONLINE, ServiceState.FAILED, "Stop Stack"),
    (ServiceState.FAILED, ServiceState.OFFLINE, "Retry Stack"),
    (ServiceState.UNKNOWN, ServiceState.ONLINE, "Docker unavailable"),
    (ServiceState.STARTING, ServiceState.ONLINE, "Starting…"),
    (ServiceState.STOPPING, ServiceState.ONLINE, "Stopping…"),
])
def test_home_managed_mixed_states_prioritize_safety_then_online(qapp, game, market, label) -> None:
    home = HomePage()
    home.apply_runtime_snapshot(RuntimeSnapshot(
        game, market, 0, backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.MANAGED,
    ))
    assert home.btn_start_servers.text() == label


@pytest.mark.parametrize(("action", "scope"), [
    (DockerLifecycleAction.START_MARKET, (False, True)),
    (DockerLifecycleAction.START_GAME, (True, True)),
    (DockerLifecycleAction.START_STACK, (True, True)),
    (DockerLifecycleAction.STOP_GAME, (True, False)),
    (DockerLifecycleAction.STOP_MARKET, (False, True)),
    (DockerLifecycleAction.STOP_ALL, (True, True)),
    (DockerLifecycleAction.RESTART_GAME, (True, False)),
    (DockerLifecycleAction.RECREATE_GAME, (True, False)),
])
def test_lifecycle_error_scope_is_exact(action, scope) -> None:
    assert MainWindow._docker_lifecycle_scope(action) == scope


def test_docker_controller_factory_shares_one_runner_and_is_deferred(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    window = MainWindow.__new__(MainWindow)
    window._cfg = {
        "runtime_backend": "docker_compose", "docker_control_policy": "managed",
        "evejs_root": "C:/EveJS", "docker_compose_file": "C:/EveJS/compose.yaml",
        "docker_project_name": "eve",
    }
    window._lifecycle_thread = None
    window._monitor_generation = 0
    window._runtime_snapshot = RuntimeSnapshot(ServiceState.OFFLINE, ServiceState.OFFLINE, 0)
    window._tracker = type("Tracker", (), {"running_count": 0})()
    window._apply_runtime_snapshot = lambda _snapshot: None
    captured: dict[str, object] = {}
    window._begin_lifecycle_worker = lambda worker, _handler: captured.setdefault("worker", worker)
    created: list[object] = []
    class Runner:
        executable = "docker"
        def __init__(self): created.append(self)
    monkeypatch.setattr("src.app.DockerCommandRunner", Runner)

    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_GAME)
    assert created == []
    worker = captured["worker"]
    controller = worker._controller_factory(None)  # type: ignore[attr-defined]
    assert len(created) == 1
    assert controller._runner is controller._inspector._runner


def test_docker_close_kill_clients_rechecks_stop_on_exit_policy(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill+Exit cannot bypass the managed STOP_ALL close contract."""
    from PyQt6.QtWidgets import QMainWindow
    class Tracker:
        running_count = 1
        def kill_all(self):
            self.running_count = 0
            return 1
    class Event:
        accepted = False
        ignored = False
        def accept(self): self.accepted = True
        def ignore(self): self.ignored = True

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose", "docker_control_policy": "managed",
        "docker_keep_running_on_exit": False,
    }
    window._tracker = Tracker()
    window._lifecycle_thread = None
    window._docker_close_pending = False
    window._docker_close_stop_started = False
    window._docker_close_stop_succeeded = False
    window._close_in_progress = False
    window._update_install_worker = None
    actions: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = lambda action: actions.append(action) or True
    monkeypatch.setattr(
        "src.app.QMessageBox.question",
        lambda *_args: __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes,
    )

    event = Event()
    window.closeEvent(event)

    assert actions == [DockerLifecycleAction.STOP_ALL]
    assert event.ignored and not event.accepted
    assert window._docker_close_pending and window._docker_close_stop_started


@pytest.mark.parametrize(
    ("action", "initial_game", "initial_market", "expected_game", "expected_market"),
    [
        (
            DockerLifecycleAction.START_MARKET,
            ServiceState.ONLINE,
            ServiceState.OFFLINE,
            ServiceState.ONLINE,
            ServiceState.STARTING,
        ),
        (
            DockerLifecycleAction.START_GAME,
            ServiceState.OFFLINE,
            ServiceState.OFFLINE,
            ServiceState.STARTING,
            ServiceState.STARTING,
        ),
        (
            DockerLifecycleAction.START_GAME,
            ServiceState.OFFLINE,
            ServiceState.ONLINE,
            ServiceState.STARTING,
            ServiceState.ONLINE,
        ),
        (
            DockerLifecycleAction.START_STACK,
            ServiceState.OFFLINE,
            ServiceState.OFFLINE,
            ServiceState.STARTING,
            ServiceState.STARTING,
        ),
        (
            DockerLifecycleAction.START_STACK,
            ServiceState.OFFLINE,
            ServiceState.ONLINE,
            ServiceState.STARTING,
            ServiceState.ONLINE,
        ),
        (
            DockerLifecycleAction.STOP_GAME,
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            ServiceState.STOPPING,
            ServiceState.ONLINE,
        ),
        (
            DockerLifecycleAction.STOP_MARKET,
            ServiceState.OFFLINE,
            ServiceState.ONLINE,
            ServiceState.OFFLINE,
            ServiceState.STOPPING,
        ),
        (
            DockerLifecycleAction.STOP_ALL,
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            ServiceState.STOPPING,
            ServiceState.STOPPING,
        ),
        (
            DockerLifecycleAction.RESTART_GAME,
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            ServiceState.STARTING,
            ServiceState.ONLINE,
        ),
        (
            DockerLifecycleAction.RECREATE_GAME,
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            ServiceState.STARTING,
            ServiceState.ONLINE,
        ),
    ],
)
def test_lifecycle_transition_matrix_preserves_dependencies_and_scrubs_native_ownership(
    qapp,
    tmp_path,
    action: DockerLifecycleAction,
    initial_game: ServiceState,
    initial_market: ServiceState,
    expected_game: ServiceState,
    expected_market: ServiceState,
) -> None:
    snapshot = _docker_snapshot(
        initial_game,
        initial_market,
        game_pid=101,
        market_pid=202,
        game_owned=True,
        market_owned=True,
    )
    window = _bare_docker_window(tmp_path, snapshot)

    assert window._begin_docker_lifecycle(action)

    transition = window._runtime_snapshot
    assert (transition.game, transition.market) == (expected_game, expected_market)
    assert transition.game_pid is None and transition.market_pid is None
    assert not transition.game_owned and not transition.market_owned
    assert len(window._applied_snapshots) == 1
    window.deleteLater()


def test_active_lifecycle_slot_serializes_before_worker_or_presentation_changes(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _docker_snapshot(ServiceState.OFFLINE, ServiceState.ONLINE)
    window = _bare_docker_window(tmp_path, initial)
    window._lifecycle_thread = object()
    monkeypatch.setattr(
        app_module,
        "DockerLifecycleWorker",
        lambda *_args, **_kwargs: pytest.fail("second worker was constructed"),
    )
    monkeypatch.setattr(
        app_module,
        "DockerCommandRunner",
        lambda *_args, **_kwargs: pytest.fail("runner was constructed"),
    )

    assert not window._begin_docker_lifecycle(DockerLifecycleAction.START_GAME)
    assert window._runtime_snapshot is initial
    assert window._applied_snapshots == []
    window.deleteLater()


def test_record_success_renders_container_state_and_requests_one_observation(
    qapp,
    tmp_path,
) -> None:
    window = _bare_docker_window(tmp_path, _docker_snapshot())
    observed: list[str] = []
    window._docker_observe_requested.connect(lambda: observed.append("observe"))
    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_STACK)
    window._applied_snapshots.clear()
    records = {
        "server": _record(
            "server", ServiceState.ONLINE, raw_state="running",
            identity="game-id", health="healthy",
        ),
        "market": _record(
            "market", ServiceState.ONLINE, raw_state="running",
            identity="market-id", health="healthy",
        ),
    }

    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.START_STACK,
        True,
        records=records,
    ))

    rendered = window._runtime_snapshot
    assert (rendered.game, rendered.market) == (ServiceState.ONLINE, ServiceState.ONLINE)
    assert (rendered.game_container, rendered.market_container) == ("game-id", "market-id")
    assert (rendered.game_health, rendered.market_health) == ("healthy", "healthy")
    assert rendered.game_pid is None and rendered.market_pid is None
    assert not rendered.game_owned and not rendered.market_owned
    assert rendered.game_error is None and rendered.market_error is None
    assert window._applied_snapshots == [rendered]
    assert observed == ["observe"]
    window.deleteLater()


def test_no_healthcheck_lifecycle_completion_stays_unready_until_monitor_probe(
    qapp,
    tmp_path,
) -> None:
    window = _bare_docker_window(tmp_path, _docker_snapshot())
    observed: list[str] = []
    window._docker_observe_requested.connect(lambda: observed.append("observe"))
    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_STACK)
    window._applied_snapshots.clear()
    records = {
        "server": _record(
            "server",
            ServiceState.ONLINE,
            raw_state="running",
            identity="game-id",
            health=None,
        ),
        "market": _record(
            "market",
            ServiceState.ONLINE,
            raw_state="running",
            identity="market-id",
            health=None,
        ),
    }

    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(
            DockerLifecycleAction.START_STACK,
            True,
            records=records,
        )
    )

    assert window._runtime_snapshot.game is ServiceState.STARTING
    assert window._runtime_snapshot.market is ServiceState.STARTING
    assert observed == ["observe"]
    window.deleteLater()


def test_record_failure_targets_only_the_action_scope(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _docker_snapshot(
        ServiceState.OFFLINE,
        ServiceState.ONLINE,
        game_error="keep game diagnostic",
        market_error="old market diagnostic",
    )
    window = _bare_docker_window(tmp_path, initial)
    shown: list[str] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: shown.append(message),
    )
    assert window._begin_docker_lifecycle(DockerLifecycleAction.STOP_MARKET)
    records = {
        "server": _record("server", ServiceState.OFFLINE, raw_state="exited"),
        "market": _record(
            "market", ServiceState.FAILED, raw_state="running", health="unhealthy",
        ),
    }

    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.STOP_MARKET,
        False,
        records=records,
        error="market sentinel failure",
    ))

    assert window._runtime_snapshot.game_error == "keep game diagnostic"
    assert window._runtime_snapshot.market_error == "market sentinel failure"
    assert shown == ["market sentinel failure"]
    window.deleteLater()


def test_no_record_failure_rolls_back_states_without_restoring_native_identity(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _docker_snapshot(
        ServiceState.OFFLINE,
        ServiceState.ONLINE,
        game_pid=101,
        market_pid=202,
        game_owned=True,
        market_owned=True,
        market_error="keep market diagnostic",
    )
    window = _bare_docker_window(tmp_path, initial)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)
    assert window._begin_docker_lifecycle(DockerLifecycleAction.STOP_GAME)

    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.STOP_GAME,
        False,
        error="game stop failed",
    ))

    restored = window._runtime_snapshot
    assert (restored.game, restored.market) == (ServiceState.OFFLINE, ServiceState.ONLINE)
    assert restored.game_error == "game stop failed"
    assert restored.market_error == "keep market diagnostic"
    assert restored.game_pid is None and restored.market_pid is None
    assert not restored.game_owned and not restored.market_owned
    window.deleteLater()


@pytest.mark.parametrize(
    ("component", "replacement"),
    [
        ("generation", None),
        ("runtime_backend", "native"),
        ("docker_control_policy", "connect_only"),
        ("evejs_root", "different-root"),
        ("docker_compose_file", "different-compose.yaml"),
        ("docker_project_name", "different-project"),
    ],
)
def test_every_stale_target_identity_component_suppresses_render_and_observe(
    qapp,
    tmp_path,
    component: str,
    replacement: str | None,
) -> None:
    window = _bare_docker_window(tmp_path, _docker_snapshot())
    observed: list[str] = []
    window._docker_observe_requested.connect(lambda: observed.append("observe"))
    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_MARKET)
    transition = window._runtime_snapshot
    window._applied_snapshots.clear()
    if component == "generation":
        window._monitor_generation += 1
    else:
        window._cfg[component] = replacement
    records = {
        "market": _record(
            "market", ServiceState.ONLINE, raw_state="running",
            identity="new-market", health="healthy",
        ),
    }

    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.START_MARKET,
        True,
        records=records,
    ))

    assert window._runtime_snapshot is transition
    assert window._applied_snapshots == []
    assert observed == []
    window.deleteLater()


def test_current_target_completion_renders_once_and_observes_once(qapp, tmp_path) -> None:
    window = _bare_docker_window(tmp_path, _docker_snapshot())
    observed: list[str] = []
    window._docker_observe_requested.connect(lambda: observed.append("observe"))
    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_MARKET)
    window._applied_snapshots.clear()
    records = {
        "market": _record(
            "market", ServiceState.ONLINE, raw_state="running",
            identity="market-id", health="healthy",
        ),
    }

    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.START_MARKET,
        True,
        records=records,
    ))

    assert len(window._applied_snapshots) == 1
    assert observed == ["observe"]
    window.deleteLater()


def _real_widget_window() -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._nav = NavPanel(window)
    window._status_bar = StatusBar(window)
    window._home_page = HomePage(window)
    return window


@pytest.mark.parametrize(
    ("state", "label", "enabled", "tooltip"),
    [
        (ServiceState.OFFLINE, "▶ Start Server", True, ""),
        (ServiceState.ONLINE, "■ Stop Server", True, ""),
        (ServiceState.FAILED, "↻ Retry Server", True, ""),
        (ServiceState.STARTING, "⏳ Starting Server…", False, "Server is changing state"),
        (ServiceState.STOPPING, "⏳ Stopping Server…", False, "Server is changing state"),
        (ServiceState.UNKNOWN, "Server: Docker unavailable", False, "Docker state is unavailable"),
    ],
)
def test_real_widget_managed_server_navigation_matrix(
    qapp,
    state: ServiceState,
    label: str,
    enabled: bool,
    tooltip: str,
) -> None:
    window = _real_widget_window()

    window._apply_runtime_snapshot(_docker_snapshot(state, ServiceState.OFFLINE))

    assert window._nav.btn_server.text() == label
    assert window._nav.btn_server.isEnabled() is enabled
    assert window._nav.btn_server.toolTip() == tooltip
    window.deleteLater()


@pytest.mark.parametrize(
    ("game", "market", "label", "enabled", "tooltip"),
    [
        (ServiceState.OFFLINE, ServiceState.OFFLINE, "▶ Start Market", True, ""),
        (ServiceState.OFFLINE, ServiceState.ONLINE, "■ Stop Market", True, ""),
        (ServiceState.OFFLINE, ServiceState.FAILED, "↻ Retry Market", True, ""),
        (ServiceState.ONLINE, ServiceState.OFFLINE, "▶ Start Market", False, "Stop Server first"),
        (ServiceState.STARTING, ServiceState.OFFLINE, "▶ Start Market", False, "Stop Server first"),
        (ServiceState.STOPPING, ServiceState.OFFLINE, "▶ Start Market", False, "Stop Server first"),
        (ServiceState.FAILED, ServiceState.OFFLINE, "▶ Start Market", False, "Stop Server first"),
        (ServiceState.UNKNOWN, ServiceState.OFFLINE, "▶ Start Market", False, "Stop Server first"),
    ],
)
def test_real_widget_managed_market_navigation_dependency_matrix(
    qapp,
    game: ServiceState,
    market: ServiceState,
    label: str,
    enabled: bool,
    tooltip: str,
) -> None:
    window = _real_widget_window()

    window._apply_runtime_snapshot(_docker_snapshot(game, market))

    assert window._nav.btn_market.text() == label
    assert window._nav.btn_market.isEnabled() is enabled
    assert window._nav.btn_market.toolTip() == tooltip
    window.deleteLater()


def test_real_widget_connect_only_navigation_is_observational_for_every_state(qapp) -> None:
    window = _real_widget_window()
    expected_labels = {
        ServiceState.OFFLINE: "Server: Offline",
        ServiceState.STARTING: "Server: Starting…",
        ServiceState.ONLINE: "Server: Online",
        ServiceState.STOPPING: "Server: Stopping…",
        ServiceState.FAILED: "Server: Failed",
        ServiceState.UNKNOWN: "Server: Docker unavailable",
    }

    for state, label in expected_labels.items():
        snapshot = _docker_snapshot(
            state,
            state,
            docker_control_policy=DockerControlPolicy.CONNECT_ONLY,
        )
        window._apply_runtime_snapshot(snapshot)
        assert window._nav.btn_server.text() == label
        assert not window._nav.btn_server.isEnabled()
        assert window._nav.btn_server.toolTip() == (
            "Connect-only Docker mode cannot change containers."
        )
        assert not window._nav.btn_market.isEnabled()
        assert window._nav.btn_market.toolTip() == (
            "Connect-only Docker mode cannot change containers."
        )
    window.deleteLater()


@pytest.mark.parametrize(
    ("policy", "keep_running"),
    [
        ("connect_only", False),
        ("managed", True),
    ],
)
def test_close_without_container_stop_orders_monitor_before_updater(
    qapp,
    tmp_path,
    policy: str,
    keep_running: bool,
) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_control_policy"] = policy
    window._cfg["docker_keep_running_on_exit"] = keep_running
    events: list[str] = []
    window._stop_service_monitor = lambda: not events.append("monitor")
    window._has_running_update_checker = lambda: bool(events.append("updater"))
    window._begin_docker_lifecycle = (
        lambda _action: pytest.fail("container lifecycle must not start")
    )
    event = _CloseEvent()

    window.closeEvent(event)

    assert events == ["monitor", "updater"]
    assert event.accepted and not event.ignored
    window.deleteLater()


def test_close_stops_active_log_follower_before_any_lifecycle_or_monitor_work(
    qapp,
    tmp_path,
) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    window._docker_log_thread = object()
    events: list[str] = []
    window._stop_docker_log_stream = lambda: events.append("log")
    window._begin_docker_lifecycle = lambda _action: events.append("lifecycle")
    window._stop_service_monitor = lambda: not events.append("monitor")
    event = _CloseEvent()

    window.closeEvent(event)

    assert events == ["log"]
    assert event.ignored and not event.accepted
    window.deleteLater()


def test_repeated_close_does_not_start_a_second_stop_all(qapp, tmp_path) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    actions: list[DockerLifecycleAction] = []

    def begin(action: DockerLifecycleAction) -> bool:
        actions.append(action)
        window._lifecycle_thread = object()
        return True

    window._begin_docker_lifecycle = begin

    first = _CloseEvent()
    second = _CloseEvent()
    window.closeEvent(first)
    window.closeEvent(second)

    assert actions == [DockerLifecycleAction.STOP_ALL]
    assert first.ignored and second.ignored
    assert window._docker_close_pending and window._docker_close_stop_started
    window.deleteLater()


def test_close_during_unrelated_lifecycle_defers_stop_all_until_thread_release(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_GAME)
    thread = _FinishedThread()
    window._lifecycle_thread = thread
    scheduled: list[object] = []
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda _delay, callback: scheduled.append(callback),
    )
    event = _CloseEvent()

    window.closeEvent(event)

    assert event.ignored
    assert window._docker_close_pending
    assert not window._docker_close_stop_started
    records = {
        "server": _record(
            "server", ServiceState.ONLINE, raw_state="running",
            identity="game", health="healthy",
        ),
        "market": _record(
            "market", ServiceState.ONLINE, raw_state="running",
            identity="market", health="healthy",
        ),
    }
    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.START_GAME,
        True,
        records=records,
    ))
    assert scheduled == []
    window._on_lifecycle_thread_finished()

    assert thread.deleted
    assert len(scheduled) == 1
    actions: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = lambda action: not actions.append(action)
    continuation = _CloseEvent()
    window.closeEvent(continuation)
    assert actions == [DockerLifecycleAction.STOP_ALL]
    assert continuation.ignored
    window.deleteLater()


@pytest.mark.parametrize("ordering", ["result_first", "thread_first"])
def test_close_stop_all_releases_only_after_result_and_thread_in_either_order(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    ordering: str,
) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    close_event = _CloseEvent()
    window.closeEvent(close_event)
    assert window._docker_close_stop_started
    thread = _FinishedThread()
    window._lifecycle_thread = thread
    scheduled: list[object] = []
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda _delay, callback: scheduled.append(callback),
    )
    stopped = {
        "server": _record("server", ServiceState.OFFLINE, raw_state="exited"),
        "market": _record("market", ServiceState.OFFLINE, raw_state="exited"),
    }
    result = DockerLifecycleResult(
        DockerLifecycleAction.STOP_ALL,
        True,
        records=stopped,
    )

    if ordering == "result_first":
        window._on_docker_lifecycle_completed(result)
        assert scheduled == []
        window._on_lifecycle_thread_finished()
    else:
        window._on_lifecycle_thread_finished()
        assert scheduled == []
        window._on_docker_lifecycle_completed(result)

    assert thread.deleted
    assert window._lifecycle_thread is None
    assert window._docker_close_stop_succeeded
    assert len(scheduled) == 1
    window.deleteLater()


@pytest.mark.parametrize(
    "outcome",
    ["failure", "stale", "invalid", "mismatched_action"],
)
def test_close_stop_all_cancels_and_resets_on_non_success_outcomes(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    event = _CloseEvent()
    window.closeEvent(event)
    window._lifecycle_thread = _FinishedThread()
    messages: list[str] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: messages.append(message),
    )

    if outcome == "failure":
        result: object = DockerLifecycleResult(
            DockerLifecycleAction.STOP_ALL,
            False,
            error="stop failed",
        )
    elif outcome == "stale":
        window._monitor_generation += 1
        result = DockerLifecycleResult(DockerLifecycleAction.STOP_ALL, True)
    elif outcome == "mismatched_action":
        result = DockerLifecycleResult(DockerLifecycleAction.STOP_GAME, True)
    else:
        result = object()

    window._on_docker_lifecycle_completed(result)

    assert not window._docker_close_pending
    assert not window._docker_close_stop_started
    assert not window._docker_close_stop_succeeded
    assert not window._close_in_progress
    assert messages == [
        "Docker shutdown could not be confirmed. The launcher remains open; "
        "check Docker status and retry."
    ]
    window.deleteLater()


def test_monitor_and_updater_shutdown_wait_until_stop_all_lifecycle_finishes(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    events: list[str] = []
    window._stop_service_monitor = lambda: not events.append("monitor")
    window._has_running_update_checker = lambda: bool(events.append("updater"))
    scheduled: list[object] = []
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda _delay, callback: scheduled.append(callback),
    )
    first = _CloseEvent()
    window.closeEvent(first)
    assert events == []
    thread = _FinishedThread()
    window._lifecycle_thread = thread
    stopped = {
        "server": _record("server", ServiceState.OFFLINE, raw_state="exited"),
        "market": _record("market", ServiceState.OFFLINE, raw_state="exited"),
    }
    window._on_docker_lifecycle_completed(DockerLifecycleResult(
        DockerLifecycleAction.STOP_ALL,
        True,
        records=stopped,
    ))
    assert events == []
    window._on_lifecycle_thread_finished()
    assert events == [] and len(scheduled) == 1

    final = _CloseEvent()
    window.closeEvent(final)

    assert events == ["monitor", "updater"]
    assert final.accepted and not final.ignored
    window.deleteLater()


def test_docker_monitor_close_path_never_calls_qthread_wait(qapp, tmp_path) -> None:
    class ExplodingWaitThread:
        def requestInterruption(self) -> None:
            return None

        def quit(self) -> None:
            return None

        def wait(self, *_args) -> bool:
            pytest.fail("Docker GUI shutdown must not call QThread.wait")

    class Monitor(DockerMonitor):
        def __init__(self) -> None:
            super().__init__(lambda: object(), inspector_factory=lambda: object())

        def request_shutdown(self) -> None:
            return None

    window = _bare_docker_window(tmp_path)
    window._cfg["docker_control_policy"] = "connect_only"
    window._service_thread = ExplodingWaitThread()
    window._service_monitor = Monitor()
    event = _CloseEvent()

    window.closeEvent(event)

    assert event.ignored and not event.accepted
    window.deleteLater()


def test_stop_on_exit_never_runs_the_docker_command_on_the_gui_thread(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoGuiCommandRunner:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def run(self, *_args, **_kwargs):
            pytest.fail("Docker command process ran on the GUI thread")

    monkeypatch.setattr(app_module, "DockerCommandRunner", NoGuiCommandRunner)
    window = _bare_docker_window(tmp_path)
    window._cfg["docker_keep_running_on_exit"] = False
    event = _CloseEvent()

    window.closeEvent(event)

    assert event.ignored and not event.accepted
    assert window._captured_lifecycle is not None
    window.deleteLater()
