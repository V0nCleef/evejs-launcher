"""Acceptance contracts for Docker lifecycle presentation without a daemon."""
from __future__ import annotations

from dataclasses import replace

import pytest
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import app as app_module
from src.app import MainWindow
from src.core.runtime.docker_compose import ContainerRecord
from src.core.runtime.docker_controller import (
    DockerLifecycleAction,
    DockerLifecycleResult,
)
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.pages.home_page import HomePage
from src.widgets.nav_panel import NavPanel
from src.widgets.status_bar import StatusBar
from src.workers.docker_monitor import DockerMonitor, DockerObservation


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


class _ModsBusyState:
    def __init__(self) -> None:
        self.busy = True
        self.changes: list[bool] = []

    def set_lifecycle_busy(self, busy: bool) -> None:
        self.busy = bool(busy)
        self.changes.append(self.busy)


def _endpoints() -> RuntimeEndpoints:
    def endpoint(service: str, target: int, port: int) -> Endpoint:
        return Endpoint(service, "127.0.0.1", port, target, "tcp")

    return RuntimeEndpoints(
        game=endpoint("server", 26000, 32600),
        image=endpoint("server", 26001, 32601),
        proxy=endpoint("server", 26002, 32602),
        assets=endpoint("server", 26003, 34443),
        xmpp=endpoint("server", 5222, 35222),
        market=endpoint("market", 40110, 40110),
    )


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
    window._pending_docker_mod_plan = None
    window._pending_docker_mods = ()
    window._pending_docker_mod_lifecycle_result = None
    window._pending_docker_mod_observation_token = None
    window._pending_docker_mod_observation_floor_ns = None
    window._pending_docker_mod_observation_completion = None
    window._docker_mod_quarantined_targets = {}
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
    window._docker_mod_quarantined_targets = {}
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


def test_quarantined_target_blocks_start_until_another_target_is_authoritative(
    qapp,
    tmp_path,
) -> None:
    window = _bare_docker_window(tmp_path, _docker_snapshot())
    window._docker_mod_quarantined_targets["docker:rejected-target"] = 1
    denials: list[str] = []
    window._docker_unavailable = denials.append

    assert not window._begin_docker_lifecycle(
        DockerLifecycleAction.START_GAME
    )
    assert denials and "unverified" in denials[0]
    assert window._captured_lifecycle is None

    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        target_identity="docker:different-target",
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
    )
    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_GAME)
    assert window._captured_lifecycle is not None
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


def test_recreate_result_stamps_exact_runtime_identity_before_monitor_poll(
    qapp,
    tmp_path,
) -> None:
    window = _bare_docker_window(
        tmp_path,
        _docker_snapshot(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            target_identity="docker:old-target",
            game_runtime_identity="a" * 64,
        ),
    )
    assert window._begin_docker_lifecycle(DockerLifecycleAction.RECREATE_GAME)
    records = {
        "server": _record(
            "server",
            ServiceState.STARTING,
            raw_state="running",
            identity="new-game-id",
            health=None,
        ),
        "market": _record(
            "market",
            ServiceState.ONLINE,
            raw_state="running",
            identity="market-id",
            health="healthy",
        ),
    }

    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(
            DockerLifecycleAction.RECREATE_GAME,
            True,
            records=records,
            target_identity="docker:new-target",
            game_runtime_identity="b" * 64,
        )
    )

    assert window._runtime_snapshot.game is ServiceState.ONLINE
    assert window._runtime_snapshot.game_container == "new-game-id"
    assert window._runtime_snapshot.target_identity == "docker:new-target"
    assert window._runtime_snapshot.game_runtime_identity == "b" * 64
    window.deleteLater()


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("target_identity", "docker:observed-target-b"),
        ("game_runtime_identity", "c" * 64),
    ],
)
def test_newer_monitor_binding_before_recreate_result_is_not_overwritten(
    qapp,
    tmp_path,
    changed_field: str,
    changed_value: str,
) -> None:
    prior = _docker_snapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        target_identity="docker:prior-target",
        game_container="prior-game",
        game_runtime_identity="a" * 64,
    )
    window = _bare_docker_window(tmp_path, prior)
    completion_outcomes: list[bool] = []
    assert window._begin_docker_lifecycle(
        DockerLifecycleAction.RECREATE_GAME,
        on_complete=completion_outcomes.append,
    )
    observed = _docker_snapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        target_identity="docker:result-target",
        game_container="result-game",
        game_runtime_identity="b" * 64,
    )
    observed = replace(observed, **{changed_field: changed_value})
    window._runtime_snapshot = observed
    records = {
        "server": _record(
            "server",
            ServiceState.ONLINE,
            raw_state="running",
            identity="result-game",
            health="healthy",
        )
    }

    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(
            DockerLifecycleAction.RECREATE_GAME,
            True,
            records=records,
            target_identity="docker:result-target",
            game_runtime_identity="b" * 64,
        )
    )

    assert window._runtime_snapshot == observed
    assert getattr(window._runtime_snapshot, changed_field) == changed_value
    assert callable(window._lifecycle_after_thread_callback)
    window._lifecycle_after_thread_callback()
    assert completion_outcomes == [False]
    window.deleteLater()


def test_mod_recreate_waits_for_fresh_exact_observation_and_keeps_controls_gated(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _docker_snapshot(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        target_identity="docker:prior-target",
        game_container="prior-game",
        game_runtime_identity="a" * 64,
        endpoints=_endpoints(),
    )
    window = _bare_docker_window(tmp_path, prior)
    settings_identity = window._docker_monitor_settings_identity()
    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        settings_identity=settings_identity,
        monitor_generation=window._monitor_generation,
    )
    window._pending_docker_mod_plan = object()
    window._mods_page = _ModsBusyState()
    window._settings_page = _ModsBusyState()
    completion_outcomes: list[bool] = []
    timeout_callbacks: list[object] = []
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda _delay, callback: timeout_callbacks.append(callback),
    )

    assert window._begin_docker_lifecycle(
        DockerLifecycleAction.RECREATE_GAME,
        on_complete=completion_outcomes.append,
    )
    lifecycle_thread = _FinishedThread()
    window._lifecycle_thread = lifecycle_thread
    # A poll can observe the force-recreate removal gap while the worker is
    # still inspecting the replacement. This must not defeat its later result.
    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        game=ServiceState.UNKNOWN,
        target_identity="docker:transient-target",
        game_container=None,
        game_runtime_identity=None,
    )
    result = DockerLifecycleResult(
        DockerLifecycleAction.RECREATE_GAME,
        True,
        records={
            "server": _record(
                "server",
                ServiceState.ONLINE,
                raw_state="running",
                identity="result-game",
                health="healthy",
            )
        },
        target_identity="docker:result-target",
        server_node_options_sha256="d" * 64,
        game_runtime_identity="b" * 64,
    )

    window._on_docker_lifecycle_completed(result)
    floor_ns = window._pending_docker_mod_observation_floor_ns
    assert type(floor_ns) is int
    assert window._runtime_snapshot.target_identity == "docker:result-target"
    assert window._runtime_snapshot.game_runtime_identity == "b" * 64
    assert window._runtime_snapshot.game is ServiceState.STARTING
    assert completion_outcomes == []
    assert len(timeout_callbacks) == 1

    # QThread teardown alone must not release the lifecycle slot. Otherwise a
    # Home action can race the pending runtime verification (yes, really).
    window._on_lifecycle_thread_finished()
    assert window._lifecycle_active()
    assert not lifecycle_thread.deleted
    assert window._mods_page.busy
    assert window._settings_page.busy
    captured_lifecycle = window._captured_lifecycle
    assert not window._begin_docker_lifecycle(DockerLifecycleAction.STOP_GAME)
    assert window._captured_lifecycle == captured_lifecycle
    client_continuations: list[bool] = []
    launch_denials: list[str] = []
    window._docker_unavailable = launch_denials.append
    assert not window._ensure_server_if_needed(
        lambda: client_continuations.append(True)
    )
    assert client_continuations == []
    assert launch_denials and "being verified" in launch_denials[0]

    # This sample started before the attestation floor but reached the GUI
    # afterward. Its mismatch is stale and must be ignored completely.
    stale = DockerObservation(
        ServiceState.UNKNOWN,
        ServiceState.ONLINE,
        target_identity="docker:stale-target",
        settings_identity=settings_identity,
        monitor_generation=window._monitor_generation,
        sample_started_monotonic_ns=floor_ns,
    )
    window._on_docker_mod_runtime_observation_sample(
        stale,
        window._monitor_generation,
    )
    # The monitor also emits its deduplicated presentation signal for this
    # same poll. That second queued path must obey the attestation floor too.
    window._on_docker_observation(stale, window._monitor_generation)
    assert window._lifecycle_active()
    assert completion_outcomes == []
    assert window._runtime_snapshot.target_identity == "docker:result-target"

    fresh = DockerObservation(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        game_identity="result-game",
        game_runtime_identity="b" * 64,
        target_identity="docker:result-target",
        endpoints=_endpoints(),
        settings_identity=settings_identity,
        monitor_generation=window._monitor_generation,
        sample_started_monotonic_ns=floor_ns + 1,
    )
    # Model retrying Apply after an earlier corrective stop failed. The exact
    # current result is allowed to supersede only its own target quarantine.
    window._docker_mod_quarantined_targets["docker:result-target"] = 1
    applied_before_fresh = len(window._applied_snapshots)
    window._on_docker_mod_runtime_observation_sample(
        fresh,
        window._monitor_generation,
    )

    assert completion_outcomes == [True]
    assert not window._lifecycle_active()
    assert lifecycle_thread.deleted
    assert not window._mods_page.busy
    assert not window._settings_page.busy
    assert window._mods_page.changes == [False]
    assert window._settings_page.changes == [False]
    assert len(window._applied_snapshots) == applied_before_fresh + 2
    assert "docker:result-target" not in window._docker_mod_quarantined_targets
    context, reason = window._resolve_client_launch_context()
    assert context is not None and reason == ""
    # The bounded timeout is tokenized; a late callback cannot complete twice.
    timeout_callbacks[0]()
    assert completion_outcomes == [True]
    window.deleteLater()


def test_mod_recreate_rejects_post_attestation_container_replacement(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _bare_docker_window(
        tmp_path,
        _docker_snapshot(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            target_identity="docker:prior-target",
            game_container="prior-game",
            game_runtime_identity="a" * 64,
        ),
    )
    settings_identity = window._docker_monitor_settings_identity()
    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        settings_identity=settings_identity,
        monitor_generation=window._monitor_generation,
    )
    window._pending_docker_mod_plan = object()
    completion_outcomes: list[bool] = []

    def complete_recreate(succeeded: bool) -> None:
        completion_outcomes.append(succeeded)
        if not succeeded:
            assert window._begin_docker_lifecycle(
                DockerLifecycleAction.STOP_GAME
            )

    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda *_args: None)
    assert window._begin_docker_lifecycle(
        DockerLifecycleAction.RECREATE_GAME,
        on_complete=complete_recreate,
    )
    lifecycle_thread = _FinishedThread()
    window._lifecycle_thread = lifecycle_thread
    result = DockerLifecycleResult(
        DockerLifecycleAction.RECREATE_GAME,
        True,
        records={
            "server": _record(
                "server",
                ServiceState.ONLINE,
                raw_state="running",
                identity="result-game",
                health="healthy",
            )
        },
        target_identity="docker:result-target",
        server_node_options_sha256="d" * 64,
        game_runtime_identity="b" * 64,
    )
    window._on_docker_lifecycle_completed(result)
    floor_ns = window._pending_docker_mod_observation_floor_ns
    assert type(floor_ns) is int
    window._on_lifecycle_thread_finished()
    corrective_thread = _FinishedThread()

    def begin_corrective(worker, handler) -> None:
        window._captured_lifecycle = (worker, handler)
        window._lifecycle_thread = corrective_thread

    window._begin_lifecycle_worker = begin_corrective

    replaced = DockerObservation(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        game_identity="replacement-game",
        game_runtime_identity="c" * 64,
        target_identity="docker:result-target",
        settings_identity=settings_identity,
        monitor_generation=window._monitor_generation,
        sample_started_monotonic_ns=floor_ns + 1,
    )
    # DockerMonitor emits presentation first and verification second. Driving
    # both paths here prevents a regression where a late duplicate ONLINE
    # presentation overwrites the corrective STOPPING transition.
    window._on_docker_observation(replaced, window._monitor_generation)
    window._on_docker_mod_runtime_observation_sample(
        replaced,
        window._monitor_generation,
    )

    assert completion_outcomes == [False]
    assert window._lifecycle_active()
    assert lifecycle_thread.deleted
    assert window._lifecycle_thread is corrective_thread
    assert window._runtime_snapshot.game is ServiceState.STOPPING
    assert window._runtime_snapshot.game_container == "replacement-game"
    assert window._runtime_snapshot.game_runtime_identity == "c" * 64
    window._lifecycle_thread = None
    window.deleteLater()


def test_mod_recreate_observation_timeout_fails_closed_and_releases_gate(
    qapp,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _bare_docker_window(
        tmp_path,
        _docker_snapshot(
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            target_identity="docker:prior-target",
            game_container="prior-game",
            game_runtime_identity="a" * 64,
        ),
    )
    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
    )
    window._pending_docker_mod_plan = object()
    completion_outcomes: list[bool] = []

    def complete_recreate(succeeded: bool) -> None:
        completion_outcomes.append(succeeded)
        if not succeeded:
            assert window._begin_docker_lifecycle(
                DockerLifecycleAction.STOP_GAME
            )

    timeout_callbacks: list[object] = []
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda _delay, callback: timeout_callbacks.append(callback),
    )
    assert window._begin_docker_lifecycle(
        DockerLifecycleAction.RECREATE_GAME,
        on_complete=complete_recreate,
    )
    lifecycle_thread = _FinishedThread()
    window._lifecycle_thread = lifecycle_thread
    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(
            DockerLifecycleAction.RECREATE_GAME,
            True,
            records={
                "server": _record(
                    "server",
                    ServiceState.ONLINE,
                    raw_state="running",
                    identity="result-game",
                    health="healthy",
                )
            },
            target_identity="docker:result-target",
            server_node_options_sha256="d" * 64,
            game_runtime_identity="b" * 64,
        )
    )
    window._on_lifecycle_thread_finished()

    assert window._lifecycle_active()
    assert completion_outcomes == []
    assert len(timeout_callbacks) == 1
    corrective_thread = _FinishedThread()

    def begin_corrective(worker, handler) -> None:
        window._captured_lifecycle = (worker, handler)
        window._lifecycle_thread = corrective_thread

    window._begin_lifecycle_worker = begin_corrective
    timeout_callbacks[0]()

    assert completion_outcomes == [False]
    assert window._lifecycle_active()
    assert lifecycle_thread.deleted
    assert window._lifecycle_thread is corrective_thread
    assert window._runtime_snapshot.game is ServiceState.STOPPING
    quarantine_floor_ns = window._docker_mod_quarantined_targets[
        "docker:result-target"
    ]
    assert window._pending_docker_mod_observation_token is None
    late_online = DockerObservation(
        ServiceState.ONLINE,
        ServiceState.ONLINE,
        game_identity="result-game",
        game_runtime_identity="b" * 64,
        target_identity="docker:result-target",
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
        sample_started_monotonic_ns=quarantine_floor_ns + 1,
    )
    window._on_docker_observation(late_online, window._monitor_generation)
    window._on_docker_mod_runtime_observation_sample(
        late_online,
        window._monitor_generation,
    )
    assert window._runtime_snapshot.game is ServiceState.STOPPING
    assert "docker:result-target" in window._docker_mod_quarantined_targets
    late_stopped = replace(
        late_online,
        game=ServiceState.OFFLINE,
        game_identity=None,
        game_runtime_identity=None,
        sample_started_monotonic_ns=quarantine_floor_ns + 2,
    )
    window._on_docker_mod_runtime_observation_sample(
        late_stopped,
        window._monitor_generation,
    )
    assert window._runtime_snapshot.game is ServiceState.OFFLINE
    assert "docker:result-target" not in window._docker_mod_quarantined_targets
    window._lifecycle_thread = None
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
