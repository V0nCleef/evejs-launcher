"""One-shot managed Docker character creation worker contracts."""
from __future__ import annotations

from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QThread, Qt, qInstallMessageHandler
from PyQt6.QtTest import QTest

from src.core.runtime.docker_character_creation import (
    DockerCharacterCreationRequest,
    DockerCharacterCreationResult,
)
from src.core.runtime.docker_compose import ComposeTarget
from src.core.service_status import DockerControlPolicy
from src.workers.docker_character_creation_worker import (
    DockerCharacterCreationWorker,
)


TARGET_IDENTITY = "docker:" + ("a" * 64)
REQUEST = DockerCharacterCreationRequest("captain_01", "Capsule Pilot")


def test_connect_only_denial_runs_no_factories_and_emits_once(qapp) -> None:
    calls: list[str] = []
    results: list[DockerCharacterCreationResult] = []
    cleanup: list[str] = []
    token = object()
    worker = DockerCharacterCreationWorker(
        lambda: calls.append("target"),
        lambda _target: calls.append("controller"),
        REQUEST,
        policy=DockerControlPolicy.CONNECT_ONLY,
        expected_target_identity=TARGET_IDENTITY,
        request_token=token,
    )
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))

    worker.run()
    worker.run()

    assert calls == []
    assert len(results) == 1
    assert not results[0].succeeded
    assert results[0].request_token is token
    assert results[0].target_identity == TARGET_IDENTITY
    assert cleanup == ["cleanup"]


def test_managed_worker_passes_request_and_stamps_token(qapp, tmp_path: Path) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    observed: list[object] = []
    results: list[DockerCharacterCreationResult] = []
    cleanup: list[str] = []
    token = object()

    class Controller:
        def execute(
            self,
            request: DockerCharacterCreationRequest,
        ) -> DockerCharacterCreationResult:
            observed.append(request)
            return DockerCharacterCreationResult(
                True,
                account_id=101,
                character_id=202,
                backup_created=True,
                target_identity=TARGET_IDENTITY,
            )

    worker = DockerCharacterCreationWorker(
        lambda: target,
        lambda created_target: observed.append(created_target) or Controller(),
        REQUEST,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity=TARGET_IDENTITY,
        request_token=token,
    )
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))

    worker.run()

    assert observed == [target, REQUEST]
    assert len(results) == 1
    assert results[0].succeeded
    assert results[0].request_token is token
    assert results[0].target_identity == TARGET_IDENTITY
    assert cleanup == ["cleanup"]


def test_factory_failure_is_private_safe_and_terminal(qapp) -> None:
    results: list[DockerCharacterCreationResult] = []
    cleanup: list[str] = []

    def fail_target():
        raise RuntimeError("captain_01 Capsule Pilot C:/private/compose.yaml")

    worker = DockerCharacterCreationWorker(
        fail_target,
        lambda _target: None,
        REQUEST,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity=TARGET_IDENTITY,
    )
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))

    worker.run()

    assert len(results) == 1
    assert not results[0].succeeded
    assert "captain_01" not in results[0].error
    assert "Capsule Pilot" not in results[0].error
    assert "private" not in results[0].error.casefold()
    assert results[0].target_identity == TARGET_IDENTITY
    assert cleanup == ["cleanup"]


def test_invalid_controller_result_becomes_one_safe_failure(qapp) -> None:
    class Controller:
        def execute(self, _request):
            return {"ok": True, "username": "captain_01"}

    results: list[DockerCharacterCreationResult] = []
    cleanup: list[str] = []
    worker = DockerCharacterCreationWorker(
        lambda: object(),
        lambda _target: Controller(),
        REQUEST,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity=TARGET_IDENTITY,
    )
    worker.completed.connect(results.append)
    worker.cleanup.connect(lambda: cleanup.append("cleanup"))

    worker.run()

    assert len(results) == 1
    assert not results[0].succeeded
    assert "captain_01" not in repr(results[0])
    assert cleanup == ["cleanup"]


def test_factories_and_execution_run_only_in_worker_thread(
    qapp,
    tmp_path: Path,
) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    observed: list[tuple[str, QThread]] = []
    results: list[DockerCharacterCreationResult] = []
    qt_messages: list[str] = []

    class Controller:
        def execute(self, _request) -> DockerCharacterCreationResult:
            observed.append(("execute", QThread.currentThread()))
            return DockerCharacterCreationResult(
                True,
                account_id=101,
                character_id=202,
                backup_created=True,
                target_identity=TARGET_IDENTITY,
            )

    def target_factory() -> ComposeTarget:
        observed.append(("target", QThread.currentThread()))
        return target

    def controller_factory(_target: ComposeTarget) -> Controller:
        observed.append(("controller", QThread.currentThread()))
        return Controller()

    thread = QThread()
    worker = DockerCharacterCreationWorker(
        target_factory,
        controller_factory,
        REQUEST,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity=TARGET_IDENTITY,
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
