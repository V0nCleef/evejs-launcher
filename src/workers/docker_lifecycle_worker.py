"""One-shot worker for managed Docker Compose lifecycle operations."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.runtime.docker_controller import DockerLifecycleAction, DockerLifecycleResult
from src.core.runtime.docker_cli import redact_docker_diagnostic
from src.core.service_status import DockerControlPolicy


class DockerLifecycleWorker(QObject):
    """Create and execute a managed controller only from its worker thread."""

    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        target_factory: Callable[[], Any],
        controller_factory: Callable[[Any], Any],
        action: DockerLifecycleAction,
        *,
        policy: DockerControlPolicy,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_factory = target_factory
        self._controller_factory = controller_factory
        self._action = action
        self._policy = policy
        self._emitted = False

    @pyqtSlot()
    def run(self) -> None:
        """Emit exactly one bounded result; Connect-only executes no factories."""
        try:
            if self._policy is not DockerControlPolicy.MANAGED:
                result = DockerLifecycleResult(
                    self._action, False,
                    error="Connect-only Docker mode cannot change containers.",
                )
            else:
                target = self._target_factory()
                controller = self._controller_factory(target)
                result = controller.execute(self._action)
                if (
                    not isinstance(result, DockerLifecycleResult)
                    or result.action is not self._action
                ):
                    raise TypeError("Docker controller returned an invalid lifecycle result.")
        except Exception as exc:  # noqa: BLE001 - isolate factory/runtime boundaries
            result = DockerLifecycleResult(
                self._action,
                False,
                error=redact_docker_diagnostic(f"Docker lifecycle failed: {exc}"),
            )
        finally:
            self._emit(locals().get("result", DockerLifecycleResult(
                self._action, False, error="Docker lifecycle failed unexpectedly."
            )))
            self.cleanup.emit()

    def _emit(self, result: DockerLifecycleResult) -> None:
        if not self._emitted:
            self._emitted = True
            self.completed.emit(result)
