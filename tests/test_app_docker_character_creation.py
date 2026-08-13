"""Daemonless MainWindow contracts for managed Docker character creation."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import app as app_module
from src.app import MainWindow
from src.core.runtime.docker_character_creation import (
    DockerCharacterCreationRequest,
    DockerCharacterCreationResult,
)
from src.core.runtime.docker_controller import DockerLifecycleAction
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.widgets.new_character_dialog import NewCharacterDraft
from src.workers.docker_character_creation_worker import (
    DockerCharacterCreationWorker,
)


_TARGET_IDENTITY = "docker:" + ("1" * 64)
_OTHER_TARGET_IDENTITY = "docker:" + ("2" * 64)


class _Signal:
    def __init__(self) -> None:
        self.slots: list[object] = []

    def connect(self, slot: object) -> None:
        self.slots.append(slot)


class _DialogSpy:
    def __init__(self) -> None:
        self.create_requested = _Signal()
        self.patch_requested = _Signal()
        self.restore_requested = _Signal()
        self.finished = _Signal()
        self.shown = False
        self.raised = False
        self.activated = False
        self.accepted = False
        self.errors: list[str] = []
        self.busy: list[tuple[bool, str]] = []

    def setAttribute(self, *_args: object) -> None:  # noqa: N802
        return

    def show(self) -> None:
        self.shown = True

    def raise_(self) -> None:
        self.raised = True

    def activateWindow(self) -> None:  # noqa: N802
        self.activated = True

    def show_error(self, message: str) -> None:
        self.errors.append(message)

    def set_busy(self, busy: bool, label: str = "") -> None:
        self.busy.append((busy, label))

    def set_patch_status(self, _status: object) -> None:
        return

    def accept(self) -> None:
        self.accepted = True


class _CharactersPageSpy:
    def __init__(self) -> None:
        self.availability: list[tuple[bool, str]] = []

    def set_character_creation_available(
        self,
        enabled: bool,
        reason: str = "",
    ) -> None:
        self.availability.append((enabled, reason))


class _Tracker:
    running_count = 0


class _ThreadStub:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:  # noqa: N802
        self.deleted = True


def _draft() -> NewCharacterDraft:
    return NewCharacterDraft(
        "local-account",
        "Local Pilot",
        False,
        None,
    )


def _window(
    tmp_path: Path,
    *,
    policy: DockerControlPolicy = DockerControlPolicy.MANAGED,
    game: ServiceState = ServiceState.OFFLINE,
    market: ServiceState = ServiceState.OFFLINE,
) -> MainWindow:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    compose_file = project / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    client_path = tmp_path / "client.exe"
    client_path.write_bytes(b"")

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": policy.value,
        "evejs_root": str(project),
        "docker_compose_file": str(compose_file),
        "docker_project_name": "local-project",
        "client_path": str(client_path),
        "docker_keep_running_on_exit": True,
    }
    window._monitor_generation = 7
    window._runtime_snapshot = RuntimeSnapshot(
        game,
        market,
        0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=policy,
        target_identity=_TARGET_IDENTITY,
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
    )
    window._accounts = []
    window._tracker = _Tracker()
    window._new_character_dialog = _DialogSpy()
    window._characters_page = _CharactersPageSpy()
    window._overview_patch_thread = None
    window._character_creation_thread = None
    window._character_deletion_thread = None
    window._client_launch_thread = None
    window._launch_queue = None
    window._lifecycle_thread = None
    window._lifecycle_worker = None
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    window._lifecycle_after_thread_callback = None
    window._docker_close_pending = False
    window._docker_close_stop_started = False
    window._docker_close_stop_succeeded = False
    window._close_in_progress = False
    window._docker_character_token = None
    window._docker_character_generation = None
    window._docker_character_target = None
    window._docker_character_observed_target = None
    window._docker_character_request = None
    window._docker_character_overview_source_id = None
    window._docker_character_result = None
    window._docker_character_restore_game = False
    window._docker_character_restore_market = False
    return window


def _forbid_gui_thread_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_module,
        "DockerCommandRunner",
        lambda: pytest.fail("Docker runner constructed on the GUI thread"),
    )
    monkeypatch.setattr(
        app_module,
        "ComposeInspector",
        lambda _runner: pytest.fail("Compose inspector constructed on the GUI thread"),
    )
    monkeypatch.setattr(
        app_module,
        "ManagedDockerCharacterCreationController",
        lambda *_args, **_kwargs: pytest.fail(
            "Docker character controller constructed on the GUI thread"
        ),
    )


def _install_sequence_spies(
    window: MainWindow,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lifecycle_calls: list[dict[str, Any]] = []
    worker_capture: dict[str, Any] = {}

    def begin_lifecycle(action: DockerLifecycleAction, **kwargs: object) -> bool:
        lifecycle_calls.append({"action": action, **kwargs})
        return True

    def worker_factory(
        request: DockerCharacterCreationRequest,
        observed_target: str,
        token: object,
    ) -> object:
        worker = object()
        worker_capture.update(
            worker=worker,
            request=request,
            observed_target=observed_target,
            token=token,
        )
        return worker

    def begin_worker(worker: object, handler: object) -> None:
        thread = _ThreadStub()
        worker_capture.update(handler=handler, thread=thread)
        window._lifecycle_thread = thread  # type: ignore[assignment]
        window._lifecycle_worker = worker  # type: ignore[assignment]
        window._lifecycle_result_received = False
        window._lifecycle_thread_finished = False

    window._begin_docker_lifecycle = begin_lifecycle  # type: ignore[method-assign]
    window._docker_character_creation_worker_factory = worker_factory
    window._begin_lifecycle_worker = begin_worker  # type: ignore[method-assign]
    return lifecycle_calls, worker_capture


def _advance_stop_to_worker(
    window: MainWindow,
    lifecycle_calls: list[dict[str, Any]],
) -> None:
    window._on_new_character_create(_draft())
    assert [call["action"] for call in lifecycle_calls] == [
        DockerLifecycleAction.STOP_ALL
    ]
    stop_callback = lifecycle_calls[0]["on_complete"]
    assert callable(stop_callback)
    stop_callback(True)


def _complete_retained_worker(
    window: MainWindow,
    worker_capture: dict[str, Any],
    result: DockerCharacterCreationResult,
) -> _ThreadStub:
    handler = worker_capture["handler"]
    assert callable(handler)
    handler(result)
    assert window._lifecycle_result_received is True
    thread = worker_capture["thread"]
    assert isinstance(thread, _ThreadStub)
    assert thread.deleted is False
    window._lifecycle_thread_finished = True
    window._finish_lifecycle_if_complete()
    return thread


def test_managed_docker_enables_and_opens_character_dialog(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    page = window._characters_page
    assert isinstance(page, _CharactersPageSpy)
    opened = _DialogSpy()
    constructor: dict[str, object] = {}

    def create_dialog(*args: object, **kwargs: object) -> _DialogSpy:
        constructor.update(args=args, kwargs=kwargs)
        return opened

    monkeypatch.setattr(app_module, "NewCharacterDialog", create_dialog)
    monkeypatch.setattr(app_module, "inspect_overview_patch", lambda _path: object())
    window._snapshot_ready_ids = lambda: set()  # type: ignore[method-assign]
    window._new_character_dialog = None

    window._refresh_character_creation_availability()
    window._show_new_character_dialog()

    assert page.availability[-1] == (True, "")
    assert opened.shown is True
    assert window._new_character_dialog is opened
    assert constructor["kwargs"]["runtime_label"] == "MANAGED DOCKER COMPOSE"  # type: ignore[index]
    assert opened.create_requested.slots == [window._on_new_character_create]


def test_connect_only_disables_and_denies_character_dialog(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, policy=DockerControlPolicy.CONNECT_ONLY)
    page = window._characters_page
    assert isinstance(page, _CharactersPageSpy)
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda _parent, title, message, *_args: notices.append((title, message)),
    )
    monkeypatch.setattr(
        app_module,
        "NewCharacterDialog",
        lambda *_args, **_kwargs: pytest.fail("Connect-only dialog construction"),
    )
    window._new_character_dialog = None

    window._refresh_character_creation_availability()
    window._show_new_character_dialog()

    assert page.availability[-1][0] is False
    assert "Managed Docker" in page.availability[-1][1]
    assert notices and "Connect-only" in notices[-1][1]
    assert window._new_character_dialog is None


def test_offline_create_enters_docker_worker_through_shared_lifecycle_slot(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    captured: dict[str, object] = {}
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)

    def capture(worker: object, handler: object) -> None:
        captured.update(worker=worker, handler=handler)

    window._begin_lifecycle_worker = capture  # type: ignore[method-assign]

    window._on_new_character_create(_draft())

    worker = captured["worker"]
    assert isinstance(worker, DockerCharacterCreationWorker)
    assert captured["handler"] == window._on_docker_character_creation_completed
    assert worker._request == DockerCharacterCreationRequest(
        "local-account",
        "Local Pilot",
        False,
    )
    assert worker._policy is DockerControlPolicy.MANAGED
    assert worker._expected_target_identity == _TARGET_IDENTITY
    assert worker._request_token is window._docker_character_token
    assert window._docker_character_restore_game is False
    assert window._docker_character_restore_market is False
    assert window._new_character_dialog.busy[-1] == (  # type: ignore[union-attr]
        True,
        "CREATING & VERIFYING\N{HORIZONTAL ELLIPSIS}",
    )
    worker.deleteLater()


def test_game_only_online_state_is_rejected_before_stop_or_mutation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(
        tmp_path,
        game=ServiceState.ONLINE,
        market=ServiceState.OFFLINE,
    )
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: pytest.fail(
            "An unrestorable service state must fail before confirmation."
        ),
    )

    window._on_new_character_create(_draft())

    dialog = window._new_character_dialog
    assert isinstance(dialog, _DialogSpy)
    assert lifecycle_calls == []
    assert worker_capture == {}
    assert window._docker_character_token is None
    assert dialog.busy == []
    assert len(dialog.errors) == 1
    assert "game is online while market is offline" in dialog.errors[0].casefold()
    assert "start market or stop game" in dialog.errors[0].casefold()


@pytest.mark.parametrize(
    "invalid_name",
    [
        "Fixture\x7fPilot",
        "🚀" * 19,
    ],
    ids=["del-control", "utf16-over-limit"],
)
def test_invalid_character_name_is_rejected_before_docker_authority(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_name: str,
) -> None:
    window = _window(
        tmp_path,
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
    )
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: pytest.fail(
            "An invalid name must fail before confirmation."
        ),
    )

    window._on_new_character_create(
        NewCharacterDraft("local-account", invalid_name, False, None)
    )

    dialog = window._new_character_dialog
    assert isinstance(dialog, _DialogSpy)
    assert lifecycle_calls == []
    assert worker_capture == {}
    assert window._docker_character_token is None
    assert dialog.busy == []
    assert len(dialog.errors) == 1
    assert "3-37 characters" in dialog.errors[0]
    assert "control characters" in dialog.errors[0]


@pytest.mark.parametrize(
    ("game", "market", "restore_action"),
    [
        (
            ServiceState.ONLINE,
            ServiceState.ONLINE,
            DockerLifecycleAction.START_STACK,
        ),
        (
            ServiceState.OFFLINE,
            ServiceState.ONLINE,
            DockerLifecycleAction.START_MARKET,
        ),
    ],
)
def test_online_create_sequences_stop_worker_and_exact_prior_state_restore(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    game: ServiceState,
    market: ServiceState,
    restore_action: DockerLifecycleAction,
) -> None:
    window = _window(tmp_path, game=game, market=market)
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    _advance_stop_to_worker(window, lifecycle_calls)
    token = worker_capture["token"]
    assert worker_capture["observed_target"] == _TARGET_IDENTITY
    assert isinstance(worker_capture["request"], DockerCharacterCreationRequest)
    result = DockerCharacterCreationResult(
        True,
        account_id=101,
        character_id=202,
        backup_created=True,
        cleanup_confirmed=True,
        restart_safe=True,
        request_token=token,
        target_identity=_TARGET_IDENTITY,
    )

    thread = _complete_retained_worker(window, worker_capture, result)

    assert thread.deleted is True
    assert window._lifecycle_thread is None
    assert [call["action"] for call in lifecycle_calls] == [
        DockerLifecycleAction.STOP_ALL,
        restore_action,
    ]
    restore = lifecycle_calls[-1]
    assert restore["expected_target_identity"] == _TARGET_IDENTITY
    assert restore["suppress_failure_dialog"] is True


def test_rollback_unconfirmed_failure_never_restarts_prior_services(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(
        tmp_path,
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
    )
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    _advance_stop_to_worker(window, lifecycle_calls)
    token = worker_capture["token"]
    result = DockerCharacterCreationResult(
        False,
        backup_created=True,
        rollback_confirmed=False,
        restart_safe=False,
        error="Character creation failed and rollback was not confirmed.",
        request_token=token,
        target_identity=_TARGET_IDENTITY,
    )

    thread = _complete_retained_worker(window, worker_capture, result)

    dialog = window._new_character_dialog
    assert isinstance(dialog, _DialogSpy)
    assert thread.deleted is True
    assert [call["action"] for call in lifecycle_calls] == [
        DockerLifecycleAction.STOP_ALL
    ]
    assert dialog.busy[-1] == (False, "")
    assert "rollback was not confirmed" in dialog.errors[-1]
    assert window._docker_character_token is None


def test_verified_commit_with_unconfirmed_cleanup_does_not_restart_or_offer_retry(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(
        tmp_path,
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
    )
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda _parent, title, message, *_args: notices.append((title, message)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda *_args, **_kwargs: pytest.fail(
            "Unconfirmed cleanup must not be presented as an ordinary success."
        ),
    )
    refreshed: list[str] = []
    window._keep_created_character_visible = (  # type: ignore[method-assign]
        lambda _name: refreshed.append("visibility")
    )
    window._refresh_characters = (  # type: ignore[method-assign]
        lambda: refreshed.append("refresh")
    )
    _advance_stop_to_worker(window, lifecycle_calls)
    token = worker_capture["token"]
    result = DockerCharacterCreationResult(
        True,
        account_id=101,
        character_id=202,
        backup_created=True,
        restart_safe=False,
        cleanup_confirmed=False,
        request_token=token,
        target_identity=_TARGET_IDENTITY,
    )

    thread = _complete_retained_worker(window, worker_capture, result)

    dialog = window._new_character_dialog
    assert isinstance(dialog, _DialogSpy)
    assert thread.deleted is True
    assert [call["action"] for call in lifecycle_calls] == [
        DockerLifecycleAction.STOP_ALL
    ]
    assert dialog.accepted is True
    assert dialog.busy[-1] == (False, "")
    assert dialog.errors[-1] == ""
    assert refreshed == ["visibility", "refresh"]
    assert len(notices) == 1
    title, message = notices[0]
    assert "cleanup unconfirmed" in title.casefold()
    assert "do not retry" in message.casefold()
    assert "kept stopped" in message.casefold()
    assert window._docker_character_token is None


def test_stale_target_suppresses_ui_and_restore_but_completes_teardown(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(
        tmp_path,
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
    )
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    rendered: list[str] = []
    window._refresh_characters = lambda: rendered.append("refresh")  # type: ignore[method-assign]
    window._keep_created_character_visible = (  # type: ignore[method-assign]
        lambda _name: rendered.append("visibility")
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda *_args, **_kwargs: rendered.append("information"),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: rendered.append("warning"),
    )
    _advance_stop_to_worker(window, lifecycle_calls)
    dialog = window._new_character_dialog
    assert isinstance(dialog, _DialogSpy)
    dialog.errors.clear()
    dialog.busy.clear()
    token = worker_capture["token"]
    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        target_identity=_OTHER_TARGET_IDENTITY,
    )
    result = DockerCharacterCreationResult(
        True,
        account_id=101,
        character_id=202,
        backup_created=True,
        cleanup_confirmed=True,
        restart_safe=True,
        request_token=token,
        target_identity=_TARGET_IDENTITY,
    )

    thread = _complete_retained_worker(window, worker_capture, result)

    assert thread.deleted is True
    assert window._lifecycle_thread is None
    assert window._lifecycle_worker is None
    assert window._lifecycle_result_received is False
    assert window._lifecycle_thread_finished is False
    assert window._docker_character_token is None
    assert [call["action"] for call in lifecycle_calls] == [
        DockerLifecycleAction.STOP_ALL
    ]
    assert dialog.accepted is False
    assert dialog.busy == [(False, "")]
    assert len(dialog.errors) == 1
    assert "target context changed" in dialog.errors[0].casefold()
    assert "local-account" not in dialog.errors[0]
    assert "Local Pilot" not in dialog.errors[0]
    assert rendered == []


def test_close_during_helper_restores_prior_stack_when_keep_running_enabled(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(
        tmp_path,
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
    )
    lifecycle_calls, worker_capture = _install_sequence_spies(window)
    _forbid_gui_thread_docker(monkeypatch)
    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    _advance_stop_to_worker(window, lifecycle_calls)
    token = worker_capture["token"]
    window._close_in_progress = True
    result = DockerCharacterCreationResult(
        True,
        account_id=101,
        character_id=202,
        backup_created=True,
        cleanup_confirmed=True,
        restart_safe=True,
        request_token=token,
        target_identity=_TARGET_IDENTITY,
    )

    _complete_retained_worker(window, worker_capture, result)

    assert [call["action"] for call in lifecycle_calls] == [
        DockerLifecycleAction.STOP_ALL,
        DockerLifecycleAction.START_STACK,
    ]
    assert window._docker_character_token is token
