"""Docker Tool Deck worker affinity and one-shot result contracts."""
from __future__ import annotations

from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QThread, Qt, qInstallMessageHandler
from PyQt6.QtTest import QTest

from src.core.runtime.docker_compose import ComposeTarget
from src.core.runtime.docker_tools import DockerToolAction, DockerToolResult
from src.core.service_status import DockerControlPolicy
from src.workers.docker_tool_worker import DockerToolWorker


def test_connect_only_tool_worker_runs_no_factories(qapp) -> None:
    calls: list[str] = []
    results: list[DockerToolResult] = []
    cleanup: list[str] = []
    worker = DockerToolWorker(
        lambda: calls.append("target"),
        lambda _target: calls.append("controller"),
        DockerToolAction.MARKET_STATUS,
        policy=DockerControlPolicy.CONNECT_ONLY,
    )
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))

    worker.run()

    assert calls == []
    assert len(results) == 1
    assert not results[0].succeeded
    assert cleanup == ["cleanup"]


def test_tool_worker_returns_one_private_safe_result_for_factory_failure(qapp) -> None:
    results: list[DockerToolResult] = []
    cleanup: list[str] = []
    worker = DockerToolWorker(
        lambda: (_ for _ in ()).throw(
            FileNotFoundError(
                "password=fixture-secret C:/Private/fixture-compose.yaml"
            )
        ),
        lambda _target: None,
        DockerToolAction.MARKET_STATUS,
        policy=DockerControlPolicy.MANAGED,
    )
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))

    worker.run()

    assert len(results) == 1
    assert not results[0].succeeded
    assert "fixture-secret" not in results[0].error
    assert "Private" not in results[0].error
    assert len(results[0].error) <= 512
    assert cleanup == ["cleanup"]


def test_tool_worker_rejects_result_for_a_different_action(
    qapp,
    tmp_path: Path,
) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)

    class Controller:
        def execute(self, _action):
            return DockerToolResult(DockerToolAction.MARKET_BACKUPS, True)

    results: list[DockerToolResult] = []
    worker = DockerToolWorker(
        lambda: target,
        lambda _target: Controller(),
        DockerToolAction.MARKET_STATUS,
        policy=DockerControlPolicy.MANAGED,
    )
    worker.completed.connect(results.append)

    worker.run()

    assert len(results) == 1
    assert results[0].action is DockerToolAction.MARKET_STATUS
    assert not results[0].succeeded


def test_every_tool_worker_outcome_emits_one_completion_and_one_cleanup(
    qapp,
    tmp_path: Path,
) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    outcomes: list[DockerToolResult | Exception] = [
        DockerToolResult(DockerToolAction.MARKET_STATUS, True, "completed"),
        DockerToolResult(
            DockerToolAction.MARKET_STATUS,
            False,
            error="preflight failed",
        ),
        RuntimeError("controller failure"),
    ]

    for outcome in outcomes:
        class Controller:
            def execute(self, _action):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        results: list[DockerToolResult] = []
        cleanup: list[str] = []
        worker = DockerToolWorker(
            lambda: target,
            lambda _target: Controller(),
            DockerToolAction.MARKET_STATUS,
            policy=DockerControlPolicy.MANAGED,
        )
        worker.completed.connect(results.append)
        worker.cleanup.connect(lambda: cleanup.append("cleanup"))

        worker.run()

        assert len(results) == 1
        assert cleanup == ["cleanup"]


def test_tool_worker_constructs_and_executes_only_in_worker_thread(
    qapp,
    tmp_path: Path,
) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    observed: list[tuple[str, QThread]] = []
    results: list[DockerToolResult] = []
    qt_messages: list[str] = []

    class Controller:
        def execute(self, action: DockerToolAction) -> DockerToolResult:
            observed.append(("execute", QThread.currentThread()))
            return DockerToolResult(action, True, "completed")

    def target_factory() -> ComposeTarget:
        observed.append(("target", QThread.currentThread()))
        return target

    def controller_factory(_target: ComposeTarget) -> Controller:
        observed.append(("controller", QThread.currentThread()))
        return Controller()

    thread = QThread()
    worker = DockerToolWorker(
        target_factory,
        controller_factory,
        DockerToolAction.MARKET_STATUS,
        policy=DockerControlPolicy.MANAGED,
    )
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(results.append)
    worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
    worker.destroyed.connect(thread.quit)
    previous_handler = qInstallMessageHandler(
        lambda _kind, _context, message: qt_messages.append(message)
    )
    try:
        thread.start()
        for _ in range(100):
            qapp.processEvents()
            if thread.isFinished():
                break
            QTest.qWait(5)
    finally:
        qInstallMessageHandler(previous_handler)

    assert [name for name, _thread in observed] == [
        "target",
        "controller",
        "execute",
    ]
    assert all(thread_seen is not qapp.thread() for _, thread_seen in observed)
    assert len(results) == 1 and results[0].succeeded
    assert sip.isdeleted(worker)
    assert thread.isFinished()
    assert not [
        message
        for message in qt_messages
        if "QObject::" in message or "QThread:" in message
    ]
    thread.deleteLater()
