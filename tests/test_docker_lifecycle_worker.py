"""Docker lifecycle worker affinity and safe-result contracts."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, qInstallMessageHandler
from PyQt6.QtTest import QTest

from src.core.runtime.docker_compose import ComposeTarget
from src.core.runtime.docker_controller import DockerLifecycleAction, DockerLifecycleResult
from src.core.service_status import DockerControlPolicy
from src.workers.docker_lifecycle_worker import DockerLifecycleWorker


def test_connect_only_never_runs_factories(qapp, tmp_path: Path) -> None:
    calls: list[str] = []
    worker = DockerLifecycleWorker(lambda: calls.append("target"), lambda _target: calls.append("controller"), DockerLifecycleAction.START_GAME, policy=DockerControlPolicy.CONNECT_ONLY)
    results: list[object] = []
    cleanup: list[str] = []
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))
    worker.run()
    assert calls == []
    assert len(results) == 1 and not results[0].succeeded
    assert cleanup == ["cleanup"]


def test_worker_exception_is_redacted_and_bounded(qapp) -> None:
    worker = DockerLifecycleWorker(lambda: (_ for _ in ()).throw(RuntimeError("password=sentinel Authorization: Bearer sentinel-token")), lambda _target: None, DockerLifecycleAction.START_GAME, policy=DockerControlPolicy.MANAGED)
    results: list[DockerLifecycleResult] = []
    worker.completed.connect(results.append)
    worker.run()
    assert len(results) == 1
    assert "sentinel" not in (results[0].error or "")
    assert "[REDACTED]" in (results[0].error or "")


def test_worker_rejects_a_controller_result_for_a_different_action(qapp, tmp_path: Path) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)

    class Controller:
        def execute(self, _action):
            return DockerLifecycleResult(DockerLifecycleAction.STOP_ALL, True)

    results: list[DockerLifecycleResult] = []
    worker = DockerLifecycleWorker(
        lambda: target,
        lambda _target: Controller(),
        DockerLifecycleAction.START_GAME,
        policy=DockerControlPolicy.MANAGED,
    )
    worker.completed.connect(results.append)

    worker.run()

    assert len(results) == 1
    assert results[0].action is DockerLifecycleAction.START_GAME
    assert not results[0].succeeded


def test_every_controller_outcome_emits_one_completion_and_one_cleanup(qapp, tmp_path: Path) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    outcomes = [
        DockerLifecycleResult(DockerLifecycleAction.START_GAME, True),
        DockerLifecycleResult(
            DockerLifecycleAction.START_GAME, False, error="preflight failure",
        ),
        DockerLifecycleResult(
            DockerLifecycleAction.START_GAME, False, error="command failure",
        ),
        DockerLifecycleResult(
            DockerLifecycleAction.START_GAME, False, error="poll failure",
        ),
        RuntimeError("poll exception"),
    ]

    for outcome in outcomes:
        class Controller:
            def execute(self, _action):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        results: list[DockerLifecycleResult] = []
        cleanup: list[str] = []
        worker = DockerLifecycleWorker(
            lambda: target,
            lambda _target: Controller(),
            DockerLifecycleAction.START_GAME,
            policy=DockerControlPolicy.MANAGED,
        )
        worker.completed.connect(results.append)
        worker.cleanup.connect(lambda: cleanup.append("cleanup"))

        worker.run()

        assert len(results) == 1
        assert cleanup == ["cleanup"]


def test_main_window_docker_worker_is_deleted_in_worker_thread_before_coordinator_release(qapp, tmp_path: Path) -> None:
    """Canonical coordinator wiring never uses thread.finished -> deleteLater."""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QMainWindow
    from src.app import MainWindow
    from src.core.service_status import RuntimeBackend, RuntimeSnapshot, ServiceState

    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose", "docker_control_policy": "managed",
        "evejs_root": str(tmp_path), "docker_compose_file": str(tmp_path / "compose.yaml"),
        "docker_project_name": "fixture",
    }
    window._tracker = type("Tracker", (), {"running_count": 0})()
    window._runtime_snapshot = RuntimeSnapshot(
        ServiceState.OFFLINE, ServiceState.OFFLINE, 0,
        backend=RuntimeBackend.DOCKER_COMPOSE, docker_control_policy=DockerControlPolicy.MANAGED,
    )
    window._monitor_generation = 0
    window._docker_lifecycle_generation = 0
    window._docker_lifecycle_target = window._docker_target_identity()
    window._docker_lifecycle_action = DockerLifecycleAction.START_GAME
    window._docker_lifecycle_snapshot = None
    window._docker_close_pending = False
    window._docker_close_stop_started = False
    window._docker_close_stop_succeeded = False
    window._close_in_progress = False
    window._lifecycle_after_thread_callback = None
    window._apply_runtime_snapshot = lambda _snapshot: None
    observed: list[QThread] = []
    completion_threads: list[QThread] = []
    qt_messages: list[str] = []
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)

    class Controller:
        def execute(self, action):
            observed.append(QThread.currentThread())
            return DockerLifecycleResult(action, True)

    worker = DockerLifecycleWorker(
        lambda: target, lambda _target: Controller(), DockerLifecycleAction.START_GAME,
        policy=DockerControlPolicy.MANAGED,
    )

    def completed(result: object) -> None:
        completion_threads.append(QThread.currentThread())
        window._on_docker_lifecycle_completed(result)

    previous_handler = qInstallMessageHandler(
        lambda _kind, _context, message: qt_messages.append(message)
    )
    try:
        window._begin_lifecycle_worker(worker, completed)
        for _ in range(100):
            qapp.processEvents()
            if window._lifecycle_thread is None:
                break
            QTest.qWait(5)
    finally:
        qInstallMessageHandler(previous_handler)

    assert observed and observed[0] is not QThread.currentThread()
    assert completion_threads == [qapp.thread()]
    assert sip.isdeleted(worker)
    assert window._lifecycle_worker is None
    assert window._lifecycle_thread is None
    assert not [
        message for message in qt_messages
        if "QObject::" in message or "QThread:" in message
    ]
    window.deleteLater()
