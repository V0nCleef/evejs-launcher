"""Focused integration coverage for truthful shipboard announcements."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.audio.events import (
    VoiceEvent,
    render_announcement,
    service_start_result_event,
    service_stop_result_event,
)
from src.core.db import Account, Character
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
from src.workers.client_launch_worker import ClientLaunchFailure
from src.workers.server_worker import ServiceStartResult, ServiceStopResult


class _RecordingAudio:
    def __init__(self) -> None:
        self.calls: list[tuple[VoiceEvent, dict[str, object]]] = []

    def announce(self, event: VoiceEvent, **context: object) -> bool:
        self.calls.append((event, context))
        return False


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback, *_args) -> None:
        self.callbacks.append(callback)


class _DeferredWorker:
    def __init__(self) -> None:
        self.completed = _Signal()
        self.failed = _Signal()
        self.cleanup = _Signal()
        self.destroyed = _Signal()

    def moveToThread(self, _thread) -> None:  # noqa: N802 - Qt compatibility
        return None

    def run(self) -> None:
        return None

    def deleteLater(self) -> None:  # noqa: N802 - Qt compatibility
        return None


class _DeferredThread:
    def __init__(self, _parent=None) -> None:
        self.started = _Signal()
        self.finished = _Signal()
        self.start_count = 0

    def start(self) -> None:
        self.start_count += 1

    def quit(self) -> None:
        return None

    def deleteLater(self) -> None:  # noqa: N802 - Qt compatibility
        return None


class _LaunchQueue:
    def __init__(self, *_args, **_kwargs) -> None:
        self.progress = _Signal()
        self.finished = _Signal()
        self.start_count = 0

    def start(self) -> None:
        self.start_count += 1

    def cancel(self) -> None:
        return None


class _LaunchPage:
    def set_launch_progress(self, *_args) -> None:
        return None

    def set_group_launch_progress(self, *_args) -> None:
        return None

    def finish_launch_progress(self, *_args) -> None:
        return None

    def finish_group_launch_progress(self) -> None:
        return None


class _LiveProcess:
    pid = 4242

    @staticmethod
    def poll() -> None:
        return None


@pytest.fixture
def announcement_window(qapp) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._audio_controller = _RecordingAudio()
    window._close_in_progress = False
    yield window
    window.deleteLater()


def test_spoken_catalog_ignores_private_runtime_labels() -> None:
    announcement = render_announcement(
        VoiceEvent.CHARACTER_LAUNCHING,
        character_name="Fixture.Pilot\n<speak> STOP!!!",
    )
    bounded = render_announcement(
        VoiceEvent.GROUP_LAUNCHING,
        group_name="X" * 100,
    )

    assert announcement is not None
    assert announcement.text == "Launching selected character."
    assert bounded is not None
    assert bounded.text == "Launching character group."


def test_launch_sequence_result_uses_a_fixed_prerecordable_line() -> None:
    announcement = render_announcement(
        VoiceEvent.LAUNCH_SEQUENCE_COMPLETE,
        launched_count=1,
        failed_count=2,
        cancelled=True,
    )

    assert announcement is not None
    assert announcement.text == "Launch sequence cancelled."


@pytest.mark.parametrize(
    ("event", "expected_text"),
    [
        (VoiceEvent.GAME_SERVER_LAUNCHING, "Launching game server."),
        (VoiceEvent.GAME_SERVER_ONLINE, "Game server online."),
        (VoiceEvent.GAME_SERVER_LAUNCH_FAILED, "Game server launch failed."),
        (VoiceEvent.MARKET_SERVER_LAUNCHING, "Launching market server."),
        (VoiceEvent.MARKET_SERVER_ONLINE, "Market server online."),
        (VoiceEvent.MARKET_SERVER_LAUNCH_FAILED, "Market server launch failed."),
        (VoiceEvent.SERVER_STACK_STOPPING, "Stopping server stack."),
        (VoiceEvent.SERVER_STACK_OFFLINE, "Server stack offline."),
        (VoiceEvent.GAME_SERVER_STOPPING, "Stopping game server."),
        (VoiceEvent.GAME_SERVER_OFFLINE, "Game server offline."),
        (VoiceEvent.MARKET_SERVER_STOPPING, "Stopping market server."),
        (VoiceEvent.MARKET_SERVER_OFFLINE, "Market server offline."),
        (VoiceEvent.SERVICE_STOP_FAILED, "Service shutdown failed."),
        (VoiceEvent.CLIENTS_TERMINATING, "Terminating all clients."),
        (VoiceEvent.CLIENTS_TERMINATED, "All clients terminated."),
    ],
)
def test_service_and_kill_events_are_fixed_and_prerecordable(
    event: VoiceEvent,
    expected_text: str,
) -> None:
    announcement = render_announcement(
        event,
        character_name="Private Pilot",
        group_name="Private Group",
        launched_count=999,
    )

    assert announcement is not None
    assert announcement.text == expected_text


@pytest.mark.parametrize(
    ("launching_event", "online_event", "failed_event"),
    [
        (
            VoiceEvent.SERVER_STACK_LAUNCHING,
            VoiceEvent.SERVER_STACK_ONLINE,
            VoiceEvent.SERVER_STACK_FAILED,
        ),
        (
            VoiceEvent.GAME_SERVER_LAUNCHING,
            VoiceEvent.GAME_SERVER_ONLINE,
            VoiceEvent.GAME_SERVER_LAUNCH_FAILED,
        ),
        (
            VoiceEvent.MARKET_SERVER_LAUNCHING,
            VoiceEvent.MARKET_SERVER_ONLINE,
            VoiceEvent.MARKET_SERVER_LAUNCH_FAILED,
        ),
    ],
)
def test_service_start_results_retain_the_accepted_scope(
    launching_event: VoiceEvent,
    online_event: VoiceEvent,
    failed_event: VoiceEvent,
) -> None:
    assert service_start_result_event(launching_event, succeeded=True) is online_event
    assert service_start_result_event(launching_event, succeeded=False) is failed_event


def test_service_start_result_rejects_an_unattributed_event() -> None:
    with pytest.raises(ValueError):
        service_start_result_event(
            VoiceEvent.GAME_SERVER_STOPPING,
            succeeded=True,
        )


@pytest.mark.parametrize(
    ("stopping_event", "completed_event"),
    [
        (VoiceEvent.SERVER_STACK_STOPPING, VoiceEvent.SERVER_STACK_OFFLINE),
        (VoiceEvent.GAME_SERVER_STOPPING, VoiceEvent.GAME_SERVER_OFFLINE),
        (VoiceEvent.MARKET_SERVER_STOPPING, VoiceEvent.MARKET_SERVER_OFFLINE),
    ],
)
def test_service_stop_results_retain_the_accepted_scope(
    stopping_event: VoiceEvent,
    completed_event: VoiceEvent,
) -> None:
    assert service_stop_result_event(stopping_event, succeeded=True) is completed_event
    assert (
        service_stop_result_event(stopping_event, succeeded=False)
        is VoiceEvent.SERVICE_STOP_FAILED
    )


def test_service_stop_result_rejects_an_unattributed_event() -> None:
    with pytest.raises(ValueError):
        service_stop_result_event(
            VoiceEvent.SERVER_STACK_LAUNCHING,
            succeeded=True,
        )


def test_audio_entry_points_are_inert_after_shutdown_begins() -> None:
    class Audio:
        calls: list[str] = []
        music_track_name = "Fixture ambience"

        @classmethod
        def announce(cls, *_args, **_kwargs) -> bool:
            cls.calls.append("announce")
            return True

        @classmethod
        def start_music(cls) -> bool:
            cls.calls.append("music")
            return True

    class Title:
        @staticmethod
        def set_audio_status(*_args) -> None:
            Audio.calls.append("title")

    window = SimpleNamespace(
        _close_in_progress=True,
        _audio_controller=Audio(),
        _title_bar=Title(),
    )

    assert MainWindow._announce_shipboard(
        window,
        VoiceEvent.SERVER_STACK_LAUNCHING,
    ) is False
    MainWindow._start_launcher_ambience(window)

    assert Audio.calls == []


def test_audio_entry_points_are_inert_after_shutdown_begins() -> None:
    class Audio:
        calls: list[str] = []
        music_track_name = "Fixture ambience"

        @classmethod
        def announce(cls, *_args, **_kwargs) -> bool:
            cls.calls.append("announce")
            return True

        @classmethod
        def start_music(cls) -> bool:
            cls.calls.append("music")
            return True

    class Title:
        @staticmethod
        def set_audio_status(*_args) -> None:
            Audio.calls.append("title")

    window = SimpleNamespace(
        _close_in_progress=True,
        _audio_controller=Audio(),
        _title_bar=Title(),
    )

    assert MainWindow._announce_shipboard(
        window,
        VoiceEvent.SERVER_STACK_LAUNCHING,
    ) is False
    MainWindow._start_launcher_ambience(window)

    assert Audio.calls == []


def _prepare_native_start(window: MainWindow) -> None:
    window._cfg.update(
        {
            "runtime_backend": "native",
            "evejs_root": "C:/Fixture/EveJS",
        }
    )
    window._server_proc = None
    window._market_proc = None
    window._server_intent = None
    window._market_intent = None
    window._server_error = None
    window._market_error = None
    window._service_reachability = (False, False)
    window._lifecycle_thread = None
    window._lifecycle_start_scope = (False, False)
    window._lifecycle_start_token = None
    window._lifecycle_start_voice_event = None
    window._lifecycle_ready_callback = None
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    window._begin_lifecycle_worker = lambda *_args: None
    window._publish_cached_runtime = lambda: None
    window._finish_lifecycle_if_complete = lambda: None


@pytest.mark.parametrize(
    ("start_market", "start_game", "launching", "result", "expected"),
    [
        (
            False,
            True,
            VoiceEvent.GAME_SERVER_LAUNCHING,
            ServiceStartResult(game_ready=True),
            VoiceEvent.GAME_SERVER_ONLINE,
        ),
        (
            False,
            True,
            VoiceEvent.GAME_SERVER_LAUNCHING,
            ServiceStartResult(game_error="fixture failure"),
            VoiceEvent.GAME_SERVER_LAUNCH_FAILED,
        ),
        (
            True,
            False,
            VoiceEvent.MARKET_SERVER_LAUNCHING,
            ServiceStartResult(market_ready=True),
            VoiceEvent.MARKET_SERVER_ONLINE,
        ),
        (
            True,
            False,
            VoiceEvent.MARKET_SERVER_LAUNCHING,
            ServiceStartResult(market_error="fixture failure"),
            VoiceEvent.MARKET_SERVER_LAUNCH_FAILED,
        ),
        (
            True,
            True,
            VoiceEvent.SERVER_STACK_LAUNCHING,
            ServiceStartResult(market_ready=True, game_ready=True),
            VoiceEvent.SERVER_STACK_ONLINE,
        ),
        (
            True,
            True,
            VoiceEvent.SERVER_STACK_LAUNCHING,
            ServiceStartResult(game_error="fixture failure"),
            VoiceEvent.SERVER_STACK_FAILED,
        ),
    ],
)
def test_native_service_start_announces_accepted_scope_and_matching_result_once(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    start_market: bool,
    start_game: bool,
    launching: VoiceEvent,
    result: ServiceStartResult,
    expected: VoiceEvent,
) -> None:
    window = announcement_window
    _prepare_native_start(window)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    assert window._start_service_sequence(
        start_market=start_market,
        start_game=start_game,
        mode="vanilla" if start_game else None,
        on_ready=None,
        error_title="Fixture Start Error",
        voice_event=launching,
    ) is True
    window._on_service_start_completed(result)
    window._on_service_start_completed(result)

    assert [event for event, _context in window._audio_controller.calls] == [
        launching,
        expected,
    ]


def test_native_internal_and_rejected_start_paths_are_silent(
    announcement_window: MainWindow,
) -> None:
    window = announcement_window
    _prepare_native_start(window)

    assert window._start_service_sequence(
        start_market=True,
        start_game=True,
        mode="vanilla",
        on_ready=None,
        error_title="Internal Maintenance",
    ) is True
    window._on_service_start_completed(
        ServiceStartResult(market_ready=True, game_ready=True)
    )
    assert window._audio_controller.calls == []

    _prepare_native_start(window)
    assert window._start_service_sequence(
        start_market=False,
        start_game=False,
        mode=None,
        on_ready=None,
        error_title="Empty Start",
        voice_event=VoiceEvent.SERVER_STACK_LAUNCHING,
    ) is False
    assert window._audio_controller.calls == []

    _prepare_native_start(window)
    window._lifecycle_thread = object()
    assert window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="vanilla",
        on_ready=None,
        error_title="Rejected Start",
        voice_event=VoiceEvent.GAME_SERVER_LAUNCHING,
    ) is False
    assert window._audio_controller.calls == []


def test_native_worker_binding_suppresses_duplicate_and_stale_start_results(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = announcement_window
    _prepare_native_start(window)
    handlers: list[object] = []
    window._begin_lifecycle_worker = (
        lambda _worker, handler: handlers.append(handler)
    )
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    assert window._start_service_sequence(
        start_market=False,
        start_game=True,
        mode="vanilla",
        on_ready=None,
        error_title="Game Start",
        voice_event=VoiceEvent.GAME_SERVER_LAUNCHING,
    ) is True
    first_handler = handlers[-1]

    # Simulate a later accepted operation before a delayed signal from the
    # prior binding is delivered. The old result must not consume the newer
    # operation's attribution or readiness scope.
    assert window._start_service_sequence(
        start_market=True,
        start_game=False,
        mode=None,
        on_ready=None,
        error_title="Market Start",
        voice_event=VoiceEvent.MARKET_SERVER_LAUNCHING,
    ) is True
    second_handler = handlers[-1]

    first_handler(ServiceStartResult(game_ready=True))
    second_handler(ServiceStartResult(market_ready=True))
    second_handler(ServiceStartResult(market_ready=True))

    assert [event for event, _context in window._audio_controller.calls] == [
        VoiceEvent.GAME_SERVER_LAUNCHING,
        VoiceEvent.MARKET_SERVER_LAUNCHING,
        VoiceEvent.MARKET_SERVER_ONLINE,
    ]

def _prepare_native_stop(
    window: MainWindow,
    *,
    game: bool,
    market: bool,
) -> None:
    window._cfg["runtime_backend"] = "native"
    window._lifecycle_thread = None
    window._server_proc = _LiveProcess() if game else None
    window._market_proc = _LiveProcess() if market else None
    window._server_intent = None
    window._market_intent = None
    window._server_error = None
    window._market_error = None
    window._service_reachability = (game, market)
    window._lifecycle_stop_scope = (False, False)
    window._lifecycle_stop_callback = None
    window._lifecycle_stop_voice_event = None
    window._close_after_lifecycle = False
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    window._begin_lifecycle_worker = lambda *_args: None
    window._publish_cached_runtime = lambda: None
    window._finish_lifecycle_if_complete = lambda: None


@pytest.mark.parametrize(
    (
        "game",
        "market",
        "requested_event",
        "accepted_event",
        "completed_event",
    ),
    [
        (
            True,
            False,
            VoiceEvent.GAME_SERVER_STOPPING,
            VoiceEvent.GAME_SERVER_STOPPING,
            VoiceEvent.GAME_SERVER_OFFLINE,
        ),
        (
            False,
            True,
            VoiceEvent.MARKET_SERVER_STOPPING,
            VoiceEvent.MARKET_SERVER_STOPPING,
            VoiceEvent.MARKET_SERVER_OFFLINE,
        ),
        (
            True,
            True,
            VoiceEvent.SERVER_STACK_STOPPING,
            VoiceEvent.SERVER_STACK_STOPPING,
            VoiceEvent.SERVER_STACK_OFFLINE,
        ),
        (
            True,
            False,
            VoiceEvent.SERVER_STACK_STOPPING,
            VoiceEvent.GAME_SERVER_STOPPING,
            VoiceEvent.GAME_SERVER_OFFLINE,
        ),
        (
            False,
            True,
            VoiceEvent.SERVER_STACK_STOPPING,
            VoiceEvent.MARKET_SERVER_STOPPING,
            VoiceEvent.MARKET_SERVER_OFFLINE,
        ),
    ],
)
def test_native_stop_announces_only_the_accepted_owned_scope_once(
    announcement_window: MainWindow,
    game: bool,
    market: bool,
    requested_event: VoiceEvent,
    accepted_event: VoiceEvent,
    completed_event: VoiceEvent,
) -> None:
    window = announcement_window
    _prepare_native_stop(window, game=game, market=market)

    assert window._run_stop_sequence(
        stop_game=game,
        stop_market=market,
        on_complete=None,
        voice_event=requested_event,
    ) is True
    assert window._audio_controller.calls == [(accepted_event, {})]

    result = ServiceStopResult(
        game_stopped=True,
        market_stopped=True,
    )
    window._on_service_stop_completed(result)
    window._on_service_stop_completed(result)

    assert window._audio_controller.calls == [
        (accepted_event, {}),
        (completed_event, {}),
    ]


def test_native_stop_failure_uses_one_common_fixed_result(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = announcement_window
    _prepare_native_stop(window, game=True, market=True)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    assert window._run_stop_sequence(
        stop_game=True,
        stop_market=True,
        on_complete=None,
        voice_event=VoiceEvent.SERVER_STACK_STOPPING,
    ) is True
    window._on_service_stop_completed(
        ServiceStopResult(
            game_stopped=False,
            market_stopped=True,
            game_error="fixture stop failure",
        )
    )

    assert [event for event, _context in window._audio_controller.calls] == [
        VoiceEvent.SERVER_STACK_STOPPING,
        VoiceEvent.SERVICE_STOP_FAILED,
    ]


def test_native_noop_and_internal_maintenance_stops_are_silent(
    announcement_window: MainWindow,
) -> None:
    window = announcement_window
    _prepare_native_stop(window, game=False, market=False)
    callbacks: list[str] = []

    assert window._run_stop_sequence(
        stop_game=True,
        stop_market=True,
        on_complete=lambda: callbacks.append("no-op"),
        voice_event=VoiceEvent.SERVER_STACK_STOPPING,
    ) is True
    assert callbacks == ["no-op"]
    assert window._audio_controller.calls == []

    _prepare_native_stop(window, game=True, market=True)
    assert window._run_stop_sequence(
        stop_game=True,
        stop_market=True,
        on_complete=lambda: callbacks.append("maintenance"),
    ) is True
    window._on_service_stop_completed(
        ServiceStopResult(game_stopped=True, market_stopped=True)
    )

    assert window._audio_controller.calls == []


def _prepare_docker_stop(window: MainWindow) -> RuntimeSnapshot:
    window._cfg.update(
        {
            "runtime_backend": "docker_compose",
            "docker_control_policy": "managed",
            "evejs_root": "C:/Fixture/EveJS",
            "docker_compose_file": "C:/Fixture/EveJS/compose.yaml",
            "docker_project_name": "fixture",
        }
    )
    snapshot = RuntimeSnapshot(
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
        running_clients=0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.MANAGED,
    )
    window._runtime_snapshot = snapshot
    window._lifecycle_thread = None
    window._monitor_generation = 3
    window._docker_lifecycle_snapshot = None
    window._docker_lifecycle_generation = None
    window._docker_lifecycle_target = None
    window._docker_lifecycle_action = None
    window._docker_close_pending = False
    window._docker_close_stop_started = False
    window._docker_close_stop_succeeded = False
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    window._docker_cached_snapshot = lambda: snapshot
    window._docker_lifecycle_target_factory = lambda: (lambda: object())
    window._apply_runtime_snapshot = lambda _snapshot: None
    window._begin_lifecycle_worker = lambda *_args: None
    window._finish_lifecycle_if_complete = lambda: None
    return snapshot


@pytest.mark.parametrize(
    ("action", "launching_event", "online_event", "failed_event"),
    [
        (
            DockerLifecycleAction.START_GAME,
            VoiceEvent.GAME_SERVER_LAUNCHING,
            VoiceEvent.GAME_SERVER_ONLINE,
            VoiceEvent.GAME_SERVER_LAUNCH_FAILED,
        ),
        (
            DockerLifecycleAction.START_MARKET,
            VoiceEvent.MARKET_SERVER_LAUNCHING,
            VoiceEvent.MARKET_SERVER_ONLINE,
            VoiceEvent.MARKET_SERVER_LAUNCH_FAILED,
        ),
        (
            DockerLifecycleAction.START_STACK,
            VoiceEvent.SERVER_STACK_LAUNCHING,
            VoiceEvent.SERVER_STACK_ONLINE,
            VoiceEvent.SERVER_STACK_FAILED,
        ),
    ],
)
@pytest.mark.parametrize("succeeded", [True, False])
def test_managed_docker_start_announces_accepted_scope_and_result_once(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    action: DockerLifecycleAction,
    launching_event: VoiceEvent,
    online_event: VoiceEvent,
    failed_event: VoiceEvent,
    succeeded: bool,
) -> None:
    window = announcement_window
    _prepare_docker_stop(window)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    assert window._begin_docker_lifecycle(action) is True
    result = DockerLifecycleResult(
        action,
        succeeded,
        error=None if succeeded else "fixture failure",
    )
    window._on_docker_lifecycle_completed(result)
    window._on_docker_lifecycle_completed(result)

    assert [event for event, _context in window._audio_controller.calls] == [
        launching_event,
        online_event if succeeded else failed_event,
    ]


def test_managed_docker_stale_start_completion_does_not_claim_a_result(
    announcement_window: MainWindow,
) -> None:
    window = announcement_window
    _prepare_docker_stop(window)

    assert window._begin_docker_lifecycle(DockerLifecycleAction.START_MARKET) is True
    window._monitor_generation += 1
    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(DockerLifecycleAction.START_MARKET, True)
    )

    assert window._audio_controller.calls == [
        (VoiceEvent.MARKET_SERVER_LAUNCHING, {}),
    ]


@pytest.mark.parametrize(
    ("action", "stopping_event", "completed_event"),
    [
        (
            DockerLifecycleAction.STOP_GAME,
            VoiceEvent.GAME_SERVER_STOPPING,
            VoiceEvent.GAME_SERVER_OFFLINE,
        ),
        (
            DockerLifecycleAction.STOP_MARKET,
            VoiceEvent.MARKET_SERVER_STOPPING,
            VoiceEvent.MARKET_SERVER_OFFLINE,
        ),
        (
            DockerLifecycleAction.STOP_ALL,
            VoiceEvent.SERVER_STACK_STOPPING,
            VoiceEvent.SERVER_STACK_OFFLINE,
        ),
    ],
)
def test_managed_docker_stop_announces_accepted_scope_and_result_once(
    announcement_window: MainWindow,
    action: DockerLifecycleAction,
    stopping_event: VoiceEvent,
    completed_event: VoiceEvent,
) -> None:
    window = announcement_window
    _prepare_docker_stop(window)

    assert window._begin_docker_lifecycle(action) is True
    result = DockerLifecycleResult(action, True)
    window._on_docker_lifecycle_completed(result)
    window._on_docker_lifecycle_completed(result)

    assert [event for event, _context in window._audio_controller.calls] == [
        stopping_event,
        completed_event,
    ]


def test_managed_docker_failure_noop_and_restart_are_truthfully_silent_or_bounded(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = announcement_window
    _prepare_docker_stop(window)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    assert window._begin_docker_lifecycle(DockerLifecycleAction.STOP_GAME) is True
    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(
            DockerLifecycleAction.STOP_GAME,
            False,
            error="fixture failure",
        )
    )
    assert [event for event, _context in window._audio_controller.calls] == [
        VoiceEvent.GAME_SERVER_STOPPING,
        VoiceEvent.SERVICE_STOP_FAILED,
    ]

    window._audio_controller.calls.clear()
    _prepare_docker_stop(window)
    window._lifecycle_thread = object()  # an existing lifecycle owns the slot
    assert window._begin_docker_lifecycle(DockerLifecycleAction.STOP_ALL) is False
    assert window._audio_controller.calls == []

    _prepare_docker_stop(window)
    assert window._begin_docker_lifecycle(DockerLifecycleAction.RESTART_GAME) is True
    window._on_docker_lifecycle_completed(
        DockerLifecycleResult(DockerLifecycleAction.RESTART_GAME, True)
    )
    assert window._audio_controller.calls == []


def test_kill_all_announces_only_a_real_accepted_termination_once(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Tracker:
        running_count = 2

        def kill_all(self) -> int:
            killed = self.running_count
            self.running_count = 0
            return killed

    window = announcement_window
    window._tracker = Tracker()
    window._refresh_characters = lambda: None
    window._update_status_bar = lambda: None
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *_args: None)

    window._kill_all_clients()
    window._kill_all_clients()

    assert [event for event, _context in window._audio_controller.calls] == [
        VoiceEvent.CLIENTS_TERMINATING,
        VoiceEvent.CLIENTS_TERMINATED,
    ]


def test_kill_all_noop_is_silent_and_unconfirmed_result_is_not_claimed(
    announcement_window: MainWindow,
) -> None:
    class Tracker:
        running_count = 0

        @staticmethod
        def kill_all() -> int:
            return 0

    window = announcement_window
    window._tracker = Tracker()
    window._refresh_characters = lambda: None
    window._update_status_bar = lambda: None

    window._kill_all_clients()
    assert window._audio_controller.calls == []

    window._tracker.running_count = 1
    window._kill_all_clients()
    assert window._audio_controller.calls == [(VoiceEvent.CLIENTS_TERMINATING, {})]


def test_single_character_announcement_is_accepted_once_and_failure_is_not_repeated(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = announcement_window
    request = SimpleNamespace(
        username="fixture-account",
        character_name="Fixture Pilot",
    )
    window._client_launch_thread = None
    window._pending_client_launches = set()
    window._make_client_launch_request = lambda *_args, **_kwargs: request
    window._client_launch_worker_factory = lambda _request: _DeferredWorker()
    window._set_client_launch_pending = lambda *_args: None
    monkeypatch.setattr(app_module, "QThread", _DeferredThread)
    monkeypatch.setattr(app_module.QMessageBox, "critical", lambda *_args: None)

    assert window._start_client_launch(
        request.username,
        request.character_name,
    ) is True
    assert window._start_client_launch(
        request.username,
        request.character_name,
    ) is False

    window._client_launch_show_errors = False
    window._refresh_character_views = lambda: None
    window._update_status_bar = lambda: None
    window._finish_client_launch_if_complete = lambda: None
    failure = ClientLaunchFailure(request, "RuntimeError", "fixture failure")
    window._on_client_launch_failed(failure)
    window._on_client_launch_failed(failure)

    assert window._audio_controller.calls == [
        (
            VoiceEvent.CHARACTER_LAUNCHING,
            {"character_name": "Fixture Pilot"},
        ),
        (
            VoiceEvent.CHARACTER_LAUNCH_FAILED,
            {"character_name": "Fixture Pilot"},
        ),
    ]


def test_group_launch_uses_one_group_line_and_one_bounded_result(
    announcement_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = announcement_window
    window._launch_queue = None
    window._client_launch_thread = None
    window._cfg["stagger_delay_sec"] = 0
    window._resolve_client_launch_context = lambda: (object(), "")
    window._home_page = _LaunchPage()
    window._characters_page = _LaunchPage()
    window._refresh_character_views = lambda: None
    window._update_status_bar = lambda: None
    monkeypatch.setattr(app_module, "AsyncClientLaunchQueue", _LaunchQueue)
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda *_args: None)
    candidates = [
        (
            Account(
                username="fixture-account",
                account_id=1,
                role="player",
                banned=False,
                characters=[Character(char_id=101, name="Fixture Pilot")],
            ),
            Character(char_id=101, name="Fixture Pilot"),
        )
    ]

    window._begin_client_launch_queue(candidates, group_name="Fixture.Group!")
    window._begin_client_launch_queue(candidates, group_name="Fixture.Group!")
    window._on_launch_queue_finished(3, 1, False)
    window._on_launch_queue_finished(3, 1, False)

    assert window._audio_controller.calls == [
        (VoiceEvent.GROUP_LAUNCHING, {"group_name": "Fixture.Group!"}),
        (
            VoiceEvent.LAUNCH_SEQUENCE_COMPLETE,
            {
                "group_name": "Fixture.Group!",
                "launched_count": 1,
                "failed_count": 2,
                "cancelled": False,
            },
        ),
    ]
