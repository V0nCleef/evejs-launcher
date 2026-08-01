"""One-shot worker for managed Docker Tool Deck operations."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.runtime.docker_tools import DockerToolAction, DockerToolResult
from src.core.service_status import DockerControlPolicy


class DockerToolWorker(QObject):
    """Construct and execute a Docker tool controller only off the GUI thread."""

    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        target_factory: Callable[[], Any],
        controller_factory: Callable[[Any], Any],
        action: DockerToolAction,
        *,
        policy: DockerControlPolicy,
        request_token: object | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_factory = target_factory
        self._controller_factory = controller_factory
        self._action = action
        self._policy = policy
        self._request_token = request_token
        self._emitted = False

    @pyqtSlot()
    def run(self) -> None:
        """Emit exactly one semantic result and one terminal cleanup signal."""
        try:
            if self._policy is not DockerControlPolicy.MANAGED:
                result = DockerToolResult(
                    self._action,
                    False,
                    error=(
                        "Connect-only Docker mode cannot run container Tool Deck "
                        "operations."
                    ),
                )
            else:
                target = self._target_factory()
                controller = self._controller_factory(target)
                result = controller.execute(self._action)
                if (
                    not isinstance(result, DockerToolResult)
                    or result.action is not self._action
                ):
                    raise TypeError("Docker controller returned an invalid tool result.")
        except Exception:  # noqa: BLE001 - isolate all factory/runtime boundaries
            result = DockerToolResult(
                self._action,
                False,
                error=(
                    "Docker tool setup or execution failed. Check Docker Desktop and "
                    "the selected Compose project."
                ),
            )
        finally:
            self._emit(
                locals().get(
                    "result",
                    DockerToolResult(
                        self._action,
                        False,
                        error="Docker tool operation failed unexpectedly.",
                    ),
                )
            )
            self.cleanup.emit()

    def _emit(self, result: DockerToolResult) -> None:
        if not self._emitted:
            self._emitted = True
            self.completed.emit(
                replace(result, request_token=self._request_token)
            )
