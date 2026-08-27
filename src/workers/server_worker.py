"""Background lifecycle and semantic endpoint workers for EveJS services.

Workers own blocking readiness and termination waits.  ``MainWindow`` remains
the only owner of process handles and all widget updates.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import subprocess
import time
import threading
from typing import Protocol

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from ..constants import Ports
from ..core import server_launcher
from ..core.platform import request_graceful_server_shutdown
from ..core.runtime.endpoints import validate_port

GAME_GRACEFUL_SHUTDOWN_TIMEOUT_SEC = 120.0
MARKET_GRACEFUL_SHUTDOWN_TIMEOUT_SEC = 15.0
MARKET_READINESS_TIMEOUT_SEC = 300.0


@dataclass(frozen=True)
class ServiceProbe:
    """Reachability observation for the two independently monitored services."""

    game_reachable: bool
    market_reachable: bool
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ManagedProcess(Protocol):
    """Minimal process API required by lifecycle workers."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...


@dataclass(frozen=True)
class ServiceStartResult:
    """Outcome of one ordered Market/Game start sequence."""

    market_process: ManagedProcess | None = None
    game_process: ManagedProcess | None = None
    market_ready: bool = False
    game_ready: bool = False
    market_error: str | None = None
    game_error: str | None = None
    mod_runtime_snapshot: object | None = None
    mod_runtime_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.market_error is None
            and self.game_error is None
            and self.mod_runtime_error is None
        )


@dataclass(frozen=True)
class ServiceStopResult:
    """Outcome of stopping launcher-owned Game then Market processes."""

    game_stopped: bool
    market_stopped: bool
    game_error: str | None = None
    market_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.game_error is None and self.market_error is None


class ServiceStartWorker(QObject):
    """Start requested services and wait for readiness outside the GUI thread."""

    phase_changed = pyqtSignal(str, str)
    completed = pyqtSignal(object)

    def __init__(
        self,
        evejs_root: str,
        *,
        mode: str | None,
        start_market: bool,
        start_game: bool,
        game_port: int = int(Ports.GAME_TCP),
        readiness_timeout_sec: float = 60,
        market_readiness_timeout_sec: float | None = None,
        continue_game_after_market_failure: bool = False,
        poll_interval_sec: float = 0.25,
        probe: Callable[[int], bool] | None = None,
        start_market_fn: Callable[[str], ManagedProcess] | None = None,
        start_game_fn: Callable[[str], ManagedProcess] | None = None,
        game_runtime_validator: Callable[[ManagedProcess], object] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._evejs_root = evejs_root
        self._mode = mode
        self._start_market = start_market
        self._start_game = start_game
        self._game_port = validate_port(game_port, label="Game service")
        self._readiness_timeout_sec = max(0.01, float(readiness_timeout_sec))
        self._market_readiness_timeout_sec = (
            self._readiness_timeout_sec
            if market_readiness_timeout_sec is None
            else max(0.01, float(market_readiness_timeout_sec))
        )
        self._continue_game_after_market_failure = bool(
            continue_game_after_market_failure
        )
        self._poll_interval_sec = max(0.0, float(poll_interval_sec))
        self._probe = probe or self._default_probe
        self._start_market_fn = start_market_fn or server_launcher.start_market_server
        self._start_game_fn = start_game_fn or server_launcher.start_game_server
        self._game_runtime_validator = game_runtime_validator
        self._sleep = sleep_fn
        self._clock = clock_fn

    @staticmethod
    def _default_probe(port: int) -> bool:
        return server_launcher.is_server_running(port=port)

    def _wait_for_ports(
        self,
        process: ManagedProcess,
        ports: tuple[int, ...],
        service_name: str,
        *,
        timeout_sec: float,
    ) -> tuple[bool, str | None]:
        deadline = self._clock() + timeout_sec
        probe_error: str | None = None
        while self._clock() < deadline:
            return_code = process.poll()
            if return_code is not None:
                return False, f"{service_name} exited before readiness (code {return_code})."
            try:
                ready = all(self._probe(port) for port in ports)
            except Exception as exc:  # noqa: BLE001 - socket adapters can vary
                ready = False
                probe_error = str(exc)
            else:
                probe_error = None
            if ready:
                return True, None
            self._sleep(self._poll_interval_sec)
        if process.poll() is not None:
            return_code = process.poll()
            return False, f"{service_name} exited before readiness (code {return_code})."
        message = f"{service_name} did not become ready before the timeout."
        if probe_error:
            message = f"{message} Last probe error: {probe_error}"
        return False, message

    @pyqtSlot()
    def run(self) -> None:
        """Run Market → Game startup and emit one terminal result."""
        market_process: ManagedProcess | None = None
        game_process: ManagedProcess | None = None
        market_ready = False
        market_error: str | None = None
        mod_runtime_snapshot: object | None = None
        mod_runtime_error: str | None = None

        if self._start_market:
            self.phase_changed.emit("market", "starting")
            try:
                market_process = self._start_market_fn(self._evejs_root)
            except Exception as exc:  # noqa: BLE001 - propagated as UI state
                market_error = f"Unable to start Market service: {exc}"
                if not (
                    self._start_game
                    and self._continue_game_after_market_failure
                ):
                    self.completed.emit(
                        ServiceStartResult(
                            market_error=market_error,
                            game_error=(
                                "Game start skipped because Market did not start."
                                if self._start_game
                                else None
                            ),
                        )
                    )
                    return
            else:
                self.phase_changed.emit("market", "waiting")
                market_ready, market_error = self._wait_for_ports(
                    market_process,
                    (int(Ports.MARKET_HTTP), int(Ports.MARKET_RPC)),
                    "Market service",
                    timeout_sec=self._market_readiness_timeout_sec,
                )
                if not market_ready and not (
                    self._start_game
                    and self._continue_game_after_market_failure
                ):
                    self.completed.emit(
                        ServiceStartResult(
                            market_process=market_process,
                            market_error=market_error,
                            game_error=(
                                "Game start skipped because Market did not become ready."
                                if self._start_game
                                else None
                            ),
                        )
                    )
                    return
                if market_ready:
                    self.phase_changed.emit("market", "ready")

        if self._start_game:
            if self._mode is None:
                self.completed.emit(
                    ServiceStartResult(
                        market_process=market_process,
                        market_ready=market_ready,
                        market_error=market_error,
                        game_error="Game start requires a resolved server mode.",
                    )
                )
                return
            self.phase_changed.emit("game", "starting")
            try:
                game_process = self._start_game_fn(self._evejs_root, mode=self._mode)
            except Exception as exc:  # noqa: BLE001 - propagated as UI state
                self.completed.emit(
                    ServiceStartResult(
                        market_process=market_process,
                        market_ready=market_ready,
                        market_error=market_error,
                        game_error=f"Unable to start Game service: {exc}",
                    )
                )
                return
            self.phase_changed.emit("game", "waiting")
            game_ready, game_error = self._wait_for_ports(
                game_process,
                (self._game_port,),
                "Game service",
                timeout_sec=self._readiness_timeout_sec,
            )
            if not game_ready:
                self.completed.emit(
                    ServiceStartResult(
                        market_process=market_process,
                        game_process=game_process,
                        market_ready=market_ready,
                        market_error=market_error,
                        game_error=game_error,
                    )
                )
                return
            if self._game_runtime_validator is not None:
                self.phase_changed.emit("game", "verifying_mods")
                try:
                    mod_runtime_snapshot = self._game_runtime_validator(game_process)
                    if mod_runtime_snapshot is None:
                        raise RuntimeError(
                            "The mod runtime validator returned no evidence."
                        )
                except Exception as exc:  # noqa: BLE001 - reported as lifecycle state
                    mod_runtime_error = f"Mod runtime verification failed: {exc}"
                    self.phase_changed.emit("game", "verification_failed")
            if mod_runtime_error is None:
                self.phase_changed.emit("game", "ready")

        self.completed.emit(
            ServiceStartResult(
                market_process=market_process,
                game_process=game_process,
                market_ready=market_ready,
                game_ready=game_process is not None,
                market_error=market_error,
                mod_runtime_snapshot=mod_runtime_snapshot,
                mod_runtime_error=mod_runtime_error,
            )
        )


class ServiceStopWorker(QObject):
    """Stop launcher-owned Game then Market processes outside the GUI thread."""

    completed = pyqtSignal(object)

    def __init__(
        self,
        game_process: ManagedProcess | None,
        market_process: ManagedProcess | None,
        *,
        game_graceful_timeout_sec: float = GAME_GRACEFUL_SHUTDOWN_TIMEOUT_SEC,
        market_graceful_timeout_sec: float = MARKET_GRACEFUL_SHUTDOWN_TIMEOUT_SEC,
        poll_interval_sec: float = 0.25,
        force_kill_fn: Callable[[ManagedProcess], bool] | None = None,
        graceful_game_stop_fn: Callable[[ManagedProcess], bool] | None = None,
        allow_force_game_kill: bool = True,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._game_process = game_process
        self._market_process = market_process
        # EveJS currently permits seven sequential 10-second hooks, then a
        # 10-second persistence-worker drain and a 1-second close, before final
        # flush/release overhead.  Two minutes covers that full server bound;
        # Market retains its existing 15-second bound.
        self._game_graceful_timeout_sec = max(
            0.01,
            float(game_graceful_timeout_sec),
        )
        self._market_graceful_timeout_sec = max(
            0.01,
            float(market_graceful_timeout_sec),
        )
        self._poll_interval_sec = max(0.0, float(poll_interval_sec))
        self._force_kill = force_kill_fn or self._default_force_kill
        self._graceful_game_stop = (
            graceful_game_stop_fn or self._default_graceful_game_stop
        )
        self._allow_force_game_kill = bool(allow_force_game_kill)
        self._sleep = sleep_fn
        self._clock = clock_fn

    @staticmethod
    def _default_force_kill(process: ManagedProcess) -> bool:
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=5,
        )
        return result.returncode == 0

    @staticmethod
    def _default_graceful_game_stop(process: ManagedProcess) -> bool:
        return request_graceful_server_shutdown(process.pid)

    @staticmethod
    def _request_terminate(process: ManagedProcess) -> bool:
        process.terminate()
        return True

    def _stop_process(
        self,
        process: ManagedProcess | None,
        request_stop: Callable[[ManagedProcess], bool],
        *,
        graceful_timeout_sec: float,
        require_clean_exit: bool = False,
        forced_shutdown_error: str | None = None,
        allow_force_kill: bool = True,
    ) -> tuple[bool, str | None]:
        if process is None or process.poll() is not None:
            return True, None
        request_error: str | None = None
        try:
            requested = request_stop(process)
        except Exception as exc:  # noqa: BLE001 - process adapters can vary
            requested = False
            request_error = f"Failed to request service shutdown: {exc}"

        if requested:
            deadline = self._clock() + graceful_timeout_sec
            while self._clock() < deadline:
                return_code = process.poll()
                if return_code is not None:
                    if require_clean_exit and return_code != 0:
                        return (
                            True,
                            "Game service exited with code "
                            f"{return_code} during graceful shutdown.",
                        )
                    return True, None
                self._sleep(self._poll_interval_sec)
            return_code = process.poll()
            if return_code is not None:
                if require_clean_exit and return_code != 0:
                    return (
                        True,
                        "Game service exited with code "
                        f"{return_code} during graceful shutdown.",
                    )
                return True, None
        elif request_error is None:
            request_error = "Service rejected the graceful shutdown request."

        if process.poll() is not None:
            return True, forced_shutdown_error or request_error

        if not allow_force_kill:
            if request_error:
                return False, request_error
            return (
                False,
                "Service did not exit before the graceful shutdown deadline; "
                "forced shutdown is disabled for this operation.",
            )

        try:
            forced = self._force_kill(process)
        except Exception as exc:  # noqa: BLE001 - process adapters can vary
            if process.poll() is not None:
                return True, forced_shutdown_error or request_error
            message = f"Forced service shutdown failed: {exc}"
            return False, f"{request_error} {message}" if request_error else message
        if not forced:
            if process.poll() is not None:
                return True, forced_shutdown_error or request_error
            message = "Forced service shutdown failed."
            return False, f"{request_error} {message}" if request_error else message
        if process.poll() is not None:
            return True, forced_shutdown_error
        return False, "Service did not exit after forced shutdown."

    @pyqtSlot()
    def run(self) -> None:
        """Stop Game before Market and emit one terminal result."""
        game_stopped, game_error = self._stop_process(
            self._game_process,
            self._graceful_game_stop,
            graceful_timeout_sec=self._game_graceful_timeout_sec,
            require_clean_exit=True,
            forced_shutdown_error=(
                "Game service stopped without verified graceful cleanup; "
                "persisted state may be incomplete."
            ),
            allow_force_kill=self._allow_force_game_kill,
        )
        market_stopped, market_error = self._stop_process(
            self._market_process,
            self._request_terminate,
            graceful_timeout_sec=self._market_graceful_timeout_sec,
        )
        self.completed.emit(
            ServiceStopResult(
                game_stopped=game_stopped,
                market_stopped=market_stopped,
                game_error=game_error,
                market_error=market_error,
            )
        )


class ServiceMonitor(QObject):
    """Probe Game and Market endpoints in its owning thread."""

    probe_observed = pyqtSignal(object)
    probe_changed = pyqtSignal(object)

    def __init__(
        self,
        interval_ms: int = 5_000,
        parent: QObject | None = None,
        *,
        game_port: int = int(Ports.GAME_TCP),
    ) -> None:
        super().__init__(parent)
        self._interval_ms = max(250, int(interval_ms))
        self._game_port = validate_port(game_port, label="Game service")
        self._shutdown_requested = threading.Event()
        self._startup_timer: QTimer | None = None
        self._timer: QTimer | None = None
        self._last_reachability: tuple[bool, bool] | None = None

    def request_shutdown(self) -> None:
        """Record a cross-thread shutdown intent without touching Qt objects."""
        self._shutdown_requested.set()

    @pyqtSlot()
    def start(self) -> None:
        """Defer monitor activation until the owning event loop is running."""
        if self._startup_timer is None:
            self._startup_timer = QTimer(self)
            self._startup_timer.setSingleShot(True)
            self._startup_timer.timeout.connect(self._begin_monitoring)
        self._startup_timer.start(0)

    @pyqtSlot()
    def _begin_monitoring(self) -> None:
        """Start periodic work or quit when shutdown won the startup race."""
        startup_timer = self._startup_timer
        self._startup_timer = None
        if startup_timer is not None:
            startup_timer.stop()
            startup_timer.deleteLater()
        if self._shutdown_requested.is_set():
            QThread.currentThread().quit()
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(self._interval_ms)
            self._timer.timeout.connect(self.probe_now)
        if not self._timer.isActive():
            self._timer.start()
        self.probe_now()

    @pyqtSlot()
    def probe_now(self) -> None:
        """Probe endpoints, publishing every observation and changed state."""
        game_reachable = server_launcher.is_server_running(
            port=self._game_port
        )
        market_reachable = server_launcher.is_server_running(
            port=int(Ports.MARKET_RPC)
        )
        reachability = (game_reachable, market_reachable)
        probe = ServiceProbe(
            game_reachable=game_reachable,
            market_reachable=market_reachable,
        )
        # Runtime identity can change while reachability remains stable (for
        # example, an externally replaced process binds the same port).  Keep
        # the de-duplicated status signal, but expose every completed probe so
        # the GUI owner can periodically validate its process-bound evidence.
        self.probe_observed.emit(probe)
        if reachability == self._last_reachability:
            return
        self._last_reachability = reachability
        self.probe_changed.emit(probe)

    @pyqtSlot()
    def stop(self) -> None:
        """Record shutdown intent and release the periodic worker timer."""
        self.request_shutdown()
        if self._timer is None:
            return
        self._timer.stop()
        self._timer.deleteLater()
        self._timer = None
