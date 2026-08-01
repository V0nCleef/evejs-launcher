"""MainWindow ownership of one-shot Docker setup preflight."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMainWindow

from src.app import MainWindow
from src.core.runtime.docker_compose import PreflightReport
from src.core.runtime.docker_setup import (
    DockerSetupDraft,
    create_preflight_request,
)
from src.workers.docker_preflight_worker import DockerPreflightWorker


def _request(tmp_path: Path):
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
        token=1,
    )


def test_main_window_owns_preflight_until_worker_and_thread_are_released(
    qapp,
    tmp_path: Path,
) -> None:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    results: list[object] = []
    observed: list[QThread] = []
    window._settings_page = type(
        "SettingsSink",
        (),
        {"apply_docker_preflight_result": results.append},
    )()
    window._docker_preflight_thread = None
    window._docker_preflight_worker = None
    window._close_in_progress = False

    class Inspector:
        def preflight(self, _target):
            observed.append(QThread.currentThread())
            return PreflightReport(True, ("ready",))

    window._docker_preflight_worker_factory = lambda request: (
        DockerPreflightWorker(request, inspector_factory=Inspector)
    )

    window._begin_docker_preflight(_request(tmp_path))
    for _ in range(100):
        qapp.processEvents()
        if window._docker_preflight_thread is None:
            break
        QTest.qWait(5)

    assert observed and observed[0] is not qapp.thread()
    assert len(results) == 1 and results[0].report.ok is True
    assert window._docker_preflight_worker is None
    assert window._docker_preflight_thread is None
    window.deleteLater()


def test_close_event_is_deferred_while_preflight_thread_is_owned(qapp) -> None:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._docker_preflight_thread = QThread(window)
    window._close_in_progress = False

    class Event:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    event = Event()
    window.closeEvent(event)

    assert event.ignored is True
    assert window._close_in_progress is True
    window._docker_preflight_thread = None
    window.deleteLater()
