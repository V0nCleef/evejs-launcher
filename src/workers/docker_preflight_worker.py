"""One-shot, read-only Docker setup preflight worker."""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.runtime.docker_cli import DockerCommandRunner, redact_docker_diagnostic
from src.core.runtime.docker_compose import (
    ComposeInspector,
    PreflightFailureKind,
    PreflightReport,
)
from src.core.runtime.docker_mod_images import DockerModImageError
from src.core.runtime.docker_mods import (
    DockerModBridgeError,
    attach_docker_mod_override,
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
        mod_warning = None
        try:
            target = build_compose_target(self._request.draft)
            inspector = self._inspector_factory()
            report = inspector.preflight(target)
            if not isinstance(report, PreflightReport):
                raise TypeError("Docker inspector returned an invalid result.")
            if report.ok:
                mod_warning = _mod_override_warning(target)
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
                    mod_warning,
                )
            )
            self.cleanup.emit()

    def _emit(self, result: DockerPreflightResult) -> None:
        if not self._emitted:
            self._emitted = True
            self.completed.emit(result)


def _mod_override_warning(target) -> str | None:
    """Check the same mod attachment the main screen uses; never write."""
    try:
        attach_docker_mod_override(target)
    except (DockerModBridgeError, DockerModImageError) as exc:
        detail = redact_docker_diagnostic(exc, limit=160).splitlines()[0].strip()
        return detail or "The launcher mod configuration is unavailable."
    return None
