"""One-shot, read-only Docker setup preflight worker."""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.runtime.docker_cli import DockerCommandRunner
from src.core.runtime.docker_compose import (
    ComposeInspector,
    PreflightFailureKind,
    PreflightReport,
)
from src.core.runtime.docker_setup import (
    DockerPreflightRequest,
    DockerPreflightResult,
    build_compose_target,
)


class DockerPreflightWorker(QObject):
    """Construct all blocking Docker objects only in worker affinity."""

    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        request: DockerPreflightRequest,
        *,
        inspector_factory: Callable[[], object] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._request = request
        self._inspector_factory = inspector_factory or (
            lambda: ComposeInspector(DockerCommandRunner())
        )
        self._emitted = False

    @pyqtSlot()
    def run(self) -> None:
        """Emit exactly one immutable, bounded, private-safe result."""
        try:
            target = build_compose_target(self._request.draft)
            inspector = self._inspector_factory()
            report = inspector.preflight(target)
            if not isinstance(report, PreflightReport):
                raise TypeError("Docker inspector returned an invalid result.")
        except FileNotFoundError:
            report = PreflightReport.failed(PreflightFailureKind.CLI_MISSING)
        except Exception:  # noqa: BLE001 - isolate local and runtime boundaries
            report = PreflightReport.failed(
                PreflightFailureKind.COMPOSE_CONFIG_INVALID
            )
        finally:
            self._emit(
                DockerPreflightResult(
                    self._request.token,
                    self._request.draft_fingerprint,
                    locals().get(
                        "report",
                        PreflightReport.failed(
                            PreflightFailureKind.COMPOSE_CONFIG_INVALID
                        ),
                    ),
                )
            )
            self.cleanup.emit()

    def _emit(self, result: DockerPreflightResult) -> None:
        if not self._emitted:
            self._emitted = True
            self.completed.emit(result)
