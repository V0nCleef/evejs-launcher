"""QObject worker for validated Native or Docker portrait loading."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from src import config
from src.core.runtime.portraits import (
    PortraitImageResult,
    PortraitLoadError,
    PortraitProvider,
    PortraitRequest,
    PortraitTarget,
)


@dataclass(frozen=True)
class PortraitLoadFailure:
    """Private-safe failure attributed to an exact portrait request."""

    request: PortraitRequest
    code: str
    message: str


class PortraitLoader(QObject):
    """Load and decode one portrait without constructing GUI-only pixmaps."""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(
        self,
        target: PortraitTarget,
        request: PortraitRequest,
        *,
        cache_dir: Path | None = None,
        provider_factory: Callable[..., PortraitProvider] = PortraitProvider,
    ) -> None:
        super().__init__()
        self._target = target
        self._request = request
        self._cache_dir = cache_dir or config.CONFIG_DIR / "cache" / "portraits"
        self._provider_factory = provider_factory
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    @pyqtSlot()
    def run(self) -> None:
        try:
            if self._cancel.is_set():
                return
            provider = self._provider_factory(
                self._target,
                cache_dir=self._cache_dir,
            )
            result = provider.load(self._request)
            if not self._cancel.is_set():
                self.loaded.emit(result)
        except PortraitLoadError as exc:
            if not self._cancel.is_set():
                self.failed.emit(
                    PortraitLoadFailure(self._request, exc.code, str(exc)[:160])
                )
        except Exception:  # noqa: BLE001 - privacy boundary for worker faults
            if not self._cancel.is_set():
                self.failed.emit(
                    PortraitLoadFailure(
                        self._request,
                        "unexpected_error",
                        "The portrait could not be loaded safely.",
                    )
                )
        finally:
            self.cleanup.emit()


__all__ = [
    "PortraitImageResult",
    "PortraitLoadFailure",
    "PortraitLoader",
    "PortraitRequest",
    "PortraitTarget",
]
