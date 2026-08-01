"""One-shot Docker setup preflight worker contracts."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, Qt

from src.core.runtime.docker_compose import PreflightReport
from src.core.runtime.docker_setup import (
    DockerPreflightRequest,
    DockerSetupDraft,
    create_preflight_request,
)
from src.workers.docker_preflight_worker import DockerPreflightWorker


def _request(tmp_path: Path) -> DockerPreflightRequest:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    return create_preflight_request(
        DockerSetupDraft(
            evejs_root=str(tmp_path),
            compose_file=str(compose),
            project_name="fixture",
            control_policy="connect_only",
            keep_running_on_exit=True,
            client_path="",
        ),
        token=7,
    )


def test_preflight_worker_builds_inspector_only_in_worker_affinity(
    qapp,
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    observed: list[QThread] = []

    class Inspector:
        def preflight(self, _target):
            observed.append(QThread.currentThread())
            return PreflightReport(True, ("ready",))

    results: list[object] = []
    thread = QThread()
    worker = DockerPreflightWorker(request, inspector_factory=Inspector)
    worker.moveToThread(thread)
    worker.completed.connect(results.append)
    worker.cleanup.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    thread.started.connect(worker.run)
    thread.start()
    assert thread.wait(2_000)
    qapp.processEvents()

    assert observed and observed[0] is not qapp.thread()
    assert len(results) == 1
    assert results[0].request_token == 7
    assert results[0].draft_fingerprint == request.draft_fingerprint
    assert results[0].report.ok is True


def test_preflight_worker_returns_typed_local_validation_failure(
    qapp,
    tmp_path: Path,
) -> None:
    request = create_preflight_request(
        DockerSetupDraft(
            evejs_root=str(tmp_path / "missing"),
            compose_file=str(tmp_path / "missing" / "compose.yaml"),
            project_name="",
            control_policy="managed",
            keep_running_on_exit=False,
            client_path="",
        ),
        token=3,
    )
    factories: list[str] = []
    worker = DockerPreflightWorker(
        request,
        inspector_factory=lambda: factories.append("inspector"),
    )
    results: list[object] = []
    worker.completed.connect(results.append)

    worker.run()

    assert factories == []
    assert len(results) == 1
    assert results[0].report.ok is False
    assert results[0].report.failure_kind is not None
    assert "missing" not in results[0].report.diagnostics[0].casefold()
