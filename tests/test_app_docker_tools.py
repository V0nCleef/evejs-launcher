"""Application boundary contracts for semantic Docker Tool Deck dispatch."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QThread, qInstallMessageHandler
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import app as app_module
from src.app import MainWindow
from src.core.runtime.docker_tools import DockerToolAction, DockerToolResult
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.core.tool_catalog import (
    ResolvedTool,
    ResolvedToolAction,
    ToolDispatchKind,
    resolve_tools,
)
from src.workers.docker_tool_worker import DockerToolWorker


class _ToolsPageSpy:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, dict[str, object]]] = []

    def set_launch_result(self, tool_id: str, action_id: str, **kwargs) -> None:
        self.results.append((tool_id, action_id, kwargs))


def _docker_window(tmp_path: Path, *, policy: str = "managed") -> MainWindow:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": policy,
        "evejs_root": str(tmp_path),
        "docker_compose_file": str(compose),
        "docker_project_name": "fixture-project",
    }
    window._tools_page = _ToolsPageSpy()
    window._lifecycle_thread = None
    window._lifecycle_worker = None
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = False
    window._lifecycle_after_thread_callback = None
    window._monitor_generation = 7
    window._docker_tool_token = None
    window._docker_tool_generation = None
    window._docker_tool_target = None
    window._docker_tool_observed_target = None
    window._docker_tool_action = None
    window._docker_tool_request = None
    window._close_in_progress = False
    window._captured_tool_worker = None
    window._captured_tool_handler = None

    def capture(worker, handler) -> None:
        window._captured_tool_worker = worker
        window._captured_tool_handler = handler

    window._begin_lifecycle_worker = capture
    window._runtime_snapshot = RuntimeSnapshot(
        game=ServiceState.OFFLINE,
        market=ServiceState.OFFLINE,
        running_clients=0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy(policy),
        target_identity="docker:fixture-target-a",
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
    )
    return window


def _action(
    window: MainWindow,
    tool_id: str,
    action_id: str,
) -> tuple[ResolvedTool, ResolvedToolAction]:
    tools = resolve_tools(
        window._cfg["evejs_root"],
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_policy=DockerControlPolicy(window._cfg["docker_control_policy"]),
        compose_file=window._cfg["docker_compose_file"],
    )
    tool = next(item for item in tools if item.definition.id == tool_id)
    action = next(item for item in tool.actions if item.id == action_id)
    return tool, action


def test_managed_docker_action_creates_worker_without_gui_thread_docker_calls(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    monkeypatch.setattr(
        app_module,
        "DockerCommandRunner",
        lambda: pytest.fail("runner constructed on GUI thread"),
    )
    monkeypatch.setattr(
        app_module,
        "ComposeInspector",
        lambda _runner: pytest.fail("inspector constructed on GUI thread"),
    )
    monkeypatch.setattr(
        app_module,
        "ManagedDockerToolController",
        lambda *_args, **_kwargs: pytest.fail("controller constructed on GUI thread"),
    )

    window._on_tool_launch_requested(tool, action)

    assert isinstance(window._captured_tool_worker, DockerToolWorker)
    assert window._captured_tool_worker._action is DockerToolAction.MARKET_STATUS
    assert window._captured_tool_worker._policy is DockerControlPolicy.MANAGED
    assert window._tools_page.results == []


def test_docker_tool_confirmation_cancel_constructs_nothing(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(
        window,
        "tq-market-snapshot-seeder-v2",
        "rebuild-v2",
    )
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda _parent, title, body, *_args: (
            prompts.append((title, body))
            or QMessageBox.StandardButton.Cancel
        ),
    )
    monkeypatch.setattr(
        app_module,
        "DockerToolWorker",
        lambda *_args, **_kwargs: pytest.fail("worker constructed after cancellation"),
    )

    window._on_tool_launch_requested(tool, action)

    assert prompts == [(action.confirmation_title, action.confirmation_body)]
    assert window._captured_tool_worker is None


def test_connect_only_revalidation_cannot_construct_or_run_docker_tool(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    stale_tool, stale_action = _action(window, "market-seed-builder", "status")
    window._cfg["docker_control_policy"] = "connect_only"
    warnings: list[str] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda _parent, _title, message, *_args: warnings.append(message),
    )
    monkeypatch.setattr(
        app_module,
        "DockerToolWorker",
        lambda *_args, **_kwargs: pytest.fail("worker construction"),
    )
    monkeypatch.setattr(
        app_module,
        "DockerCommandRunner",
        lambda: pytest.fail("runner construction"),
    )
    monkeypatch.setattr(
        app_module,
        "ComposeInspector",
        lambda _runner: pytest.fail("inspector construction"),
    )
    monkeypatch.setattr(
        app_module,
        "ManagedDockerToolController",
        lambda *_args, **_kwargs: pytest.fail("controller construction"),
    )

    window._on_tool_launch_requested(stale_tool, stale_action)

    assert window._captured_tool_worker is None
    assert warnings


def test_invalid_docker_policy_cannot_fall_back_to_native_tool_wrapper(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "tools" / "ClientSETUP" / "StartClientSetup.bat"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\n", encoding="utf-8")
    window = _docker_window(tmp_path)
    tool, action = _action(window, "client-setup-wizard", "launch")
    window._cfg["docker_control_policy"] = "invalid-policy"
    native_calls: list[tuple[Path, tuple[str, ...]]] = []
    warnings: list[str] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: native_calls.append((entrypoint, arguments)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda _parent, _title, message, *_args: warnings.append(message),
    )
    monkeypatch.setattr(
        app_module,
        "DockerToolWorker",
        lambda *_args, **_kwargs: pytest.fail("worker construction"),
    )

    window._on_tool_launch_requested(tool, action)

    assert native_calls == []
    assert window._captured_tool_worker is None
    assert len(window._tools_page.results) == 1
    assert window._tools_page.results[0][2]["success"] is False
    assert len(warnings) == 1
    assert warnings[0] == window._tools_page.results[0][2]["message"]


def test_stale_or_forged_docker_action_constructs_no_worker(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    forged = ResolvedToolAction(
        action.action,
        ToolDispatchKind.DOCKER_COMPOSE,
        DockerToolAction.MARKET_BACKUP,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        app_module,
        "DockerToolWorker",
        lambda *_args, **_kwargs: pytest.fail("worker construction"),
    )

    window._on_tool_launch_requested(tool, forged)

    assert window._captured_tool_worker is None


def test_only_one_tool_or_lifecycle_operation_can_be_active(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    window._lifecycle_thread = object()
    notices: list[str] = []
    window._docker_unavailable = notices.append
    monkeypatch.setattr(
        app_module,
        "DockerToolWorker",
        lambda *_args, **_kwargs: pytest.fail("second worker construction"),
    )

    window._on_tool_launch_requested(tool, action)

    assert window._captured_tool_worker is None
    assert notices == ["Another service or Docker tool operation is already running."]


def test_current_tool_completion_reports_completed_and_refreshes_observer(
    qapp,
    tmp_path: Path,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    observations: list[str] = []
    window._docker_observe_requested.connect(lambda: observations.append("observe"))
    window._finish_lifecycle_if_complete = lambda: None

    window._on_tool_launch_requested(tool, action)
    token = window._docker_tool_token
    result = DockerToolResult(
        DockerToolAction.MARKET_STATUS,
        True,
        "Docker market status check completed.",
        request_token=token,
        target_identity=window._docker_tool_observed_target,
    )
    window._captured_tool_handler(result)

    assert window._tools_page.results == [
        (
            tool.definition.id,
            action.id,
            {
                "success": True,
                "message": "Docker market status check completed.",
                "completed": True,
            },
        )
    ]
    assert observations == ["observe"]
    assert window._lifecycle_result_received is True


def test_stale_tool_completion_after_target_change_has_no_ui_side_effect(
    qapp,
    tmp_path: Path,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    observations: list[str] = []
    window._docker_observe_requested.connect(lambda: observations.append("observe"))
    window._finish_lifecycle_if_complete = lambda: None

    window._on_tool_launch_requested(tool, action)
    token = window._docker_tool_token
    window._cfg["docker_project_name"] = "changed-project"
    window._captured_tool_handler(
        DockerToolResult(
            DockerToolAction.MARKET_STATUS,
            True,
            "must be ignored",
            request_token=token,
        )
    )

    assert window._tools_page.results == []
    assert observations == []
    assert window._lifecycle_result_received is True


def test_tool_completion_after_effective_target_change_is_stale_even_when_settings_and_generation_match(
    qapp,
    tmp_path: Path,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    observations: list[str] = []
    window._docker_observe_requested.connect(lambda: observations.append("observe"))
    window._finish_lifecycle_if_complete = lambda: None

    window._on_tool_launch_requested(tool, action)
    token = window._docker_tool_token
    window._runtime_snapshot = replace(
        window._runtime_snapshot,
        target_identity="docker:fixture-target-b",
    )
    window._captured_tool_handler(
        DockerToolResult(
            DockerToolAction.MARKET_STATUS,
            True,
            "must be ignored",
            request_token=token,
        )
    )

    assert window._tools_page.results == []
    assert observations == []
    assert window._docker_tool_token is None
    assert window._docker_tool_observed_target is None
    assert window._lifecycle_result_received is True


def test_stale_tool_completion_after_generation_change_has_no_ui_side_effect(
    qapp,
    tmp_path: Path,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    window._finish_lifecycle_if_complete = lambda: None

    window._on_tool_launch_requested(tool, action)
    token = window._docker_tool_token
    window._monitor_generation += 1
    window._captured_tool_handler(
        DockerToolResult(
            DockerToolAction.MARKET_STATUS,
            False,
            error="must be ignored",
            request_token=token,
        )
    )

    assert window._tools_page.results == []


def test_stale_tool_completion_token_has_no_ui_side_effect(
    qapp,
    tmp_path: Path,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    window._finish_lifecycle_if_complete = lambda: None

    window._on_tool_launch_requested(tool, action)
    window._captured_tool_handler(
        DockerToolResult(
            DockerToolAction.MARKET_STATUS,
            True,
            "must be ignored",
            request_token=object(),
        )
    )

    assert window._tools_page.results == []


def test_current_tool_failure_reports_semantic_error_without_launch_claim(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    tool, action = _action(window, "market-seed-builder", "status")
    failures: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, title, message: failures.append((title, message)),
    )
    window._finish_lifecycle_if_complete = lambda: None

    window._on_tool_launch_requested(tool, action)
    window._captured_tool_handler(
        DockerToolResult(
            DockerToolAction.MARKET_STATUS,
            False,
            error="Docker tool preflight failed.",
            request_token=window._docker_tool_token,
            target_identity=window._docker_tool_observed_target,
        )
    )

    assert window._tools_page.results[0][2] == {
        "success": False,
        "message": "Docker tool preflight failed.",
        "completed": True,
    }
    assert failures == [
        ("Docker Tool Operation Failed", "Docker tool preflight failed.")
    ]


def test_managed_docker_host_wrapper_preserves_native_wrapper_dispatch(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "tools" / "ClientSETUP" / "StartClientSetup.bat"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\n", encoding="utf-8")
    window = _docker_window(tmp_path)
    tool, action = _action(window, "client-setup-wizard", "launch")
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: calls.append((entrypoint, arguments)),
    )

    window._on_tool_launch_requested(tool, action)

    assert calls == [(wrapper.resolve(), ())]
    assert window._captured_tool_worker is None


def test_app_owns_tool_worker_until_qthread_cleanup_without_qt_warnings(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _docker_window(tmp_path)
    window._begin_lifecycle_worker = (
        MainWindow._begin_lifecycle_worker.__get__(window, MainWindow)
    )
    tool, action = _action(window, "market-seed-builder", "status")
    observed_threads: list[QThread] = []
    qt_messages: list[str] = []

    class FakeRunner:
        def __init__(self) -> None:
            observed_threads.append(QThread.currentThread())

    class FakeInspector:
        def __init__(self, _runner) -> None:
            observed_threads.append(QThread.currentThread())

    class FakeController:
        def __init__(self, *_args, **_kwargs) -> None:
            observed_threads.append(QThread.currentThread())

        def execute(self, docker_action: DockerToolAction) -> DockerToolResult:
            observed_threads.append(QThread.currentThread())
            return DockerToolResult(
                docker_action,
                True,
                "completed",
                target_identity=window._docker_tool_observed_target,
            )

    monkeypatch.setattr(app_module, "DockerCommandRunner", FakeRunner)
    monkeypatch.setattr(app_module, "ComposeInspector", FakeInspector)
    monkeypatch.setattr(app_module, "ManagedDockerToolController", FakeController)
    previous_handler = qInstallMessageHandler(
        lambda _kind, _context, message: qt_messages.append(message)
    )
    try:
        window._on_tool_launch_requested(tool, action)
        worker = window._lifecycle_worker
        thread = window._lifecycle_thread
        for _ in range(200):
            qapp.processEvents()
            if window._lifecycle_thread is None:
                break
            QTest.qWait(5)
        qapp.processEvents()
    finally:
        qInstallMessageHandler(previous_handler)

    assert len(observed_threads) == 4
    assert all(seen is not qapp.thread() for seen in observed_threads)
    assert window._tools_page.results[0][2]["success"] is True
    assert window._lifecycle_thread is None
    assert window._lifecycle_worker is None
    assert worker is not None and sip.isdeleted(worker)
    assert thread is not None and sip.isdeleted(thread)
    assert not [
        message
        for message in qt_messages
        if "QObject::" in message or "QThread:" in message
    ]
