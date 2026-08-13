"""One-shot worker for managed Docker character creation."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src.core.runtime.docker_character_creation import (
    DockerCharacterCreationRequest,
    DockerCharacterCreationResult,
)
from src.core.service_status import DockerControlPolicy


class DockerCharacterCreationWorker(QObject):
    """Construct and execute the managed controller only off the GUI thread."""

    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        target_factory: Callable[[], Any],
        controller_factory: Callable[[Any], Any],
        request: DockerCharacterCreationRequest,
        *,
        policy: DockerControlPolicy,
        expected_target_identity: str,
        request_token: object | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if (
            not isinstance(expected_target_identity, str)
            or not expected_target_identity.startswith("docker:")
        ):
            raise ValueError("A Docker target identity is required.")
        self._target_factory = target_factory
        self._controller_factory = controller_factory
        self._request = request
        self._policy = policy
        self._expected_target_identity = expected_target_identity
        self._request_token = request_token
        self._finished = False

    @pyqtSlot()
    def run(self) -> None:
        """Emit exactly one target-stamped completion and one cleanup signal."""
        if self._finished:
            return
        self._finished = True
        try:
            if self._policy is not DockerControlPolicy.MANAGED:
                result = self._failure(
                    "Connect-only Docker mode cannot create characters."
                )
            else:
                target = self._target_factory()
                controller = self._controller_factory(target)
                result = controller.execute(self._request)
                if not isinstance(result, DockerCharacterCreationResult):
                    raise TypeError(
                        "Docker controller returned an invalid character result."
                    )
        except Exception:  # noqa: BLE001 - isolate factory/runtime boundaries
            result = self._failure(
                "Docker character creation setup or execution failed. Check "
                "Docker Desktop and the selected Compose project."
            )

        stamped = replace(
            result,
            request_token=self._request_token,
            target_identity=(
                result.target_identity or self._expected_target_identity
            ),
        )
        try:
            self.completed.emit(stamped)
        finally:
            self.cleanup.emit()

    def _failure(self, error: str) -> DockerCharacterCreationResult:
        return DockerCharacterCreationResult(
            False,
            error=error,
            target_identity=self._expected_target_identity,
        )
