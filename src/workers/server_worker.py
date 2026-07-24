"""QObject-based server controller for EveJS game and market servers.

Designed to live in a dedicated QThread. All subprocess creation, port
probing, and process termination happen in this object's thread context.

Signals:
    server_started:  Game server subprocess successfully spawned.
    server_stopped:  Game server subprocess terminated.
    market_started:  Market server subprocess successfully spawned.
    market_stopped:  Market server subprocess terminated.
    status_changed(bool, bool): server_online, market_online after each probe.
"""
import subprocess
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..core import server_launcher


class ServerController(QObject):
    """Control EveJS game and market server processes.

    Intended usage:
        thread = QThread()
        controller = ServerController()
        controller.moveToThread(thread)
        thread.start()
    """

    server_started = pyqtSignal()
    server_stopped = pyqtSignal()
    market_started = pyqtSignal()
    market_stopped = pyqtSignal()
    status_changed = pyqtSignal(bool, bool)  # server_online, market_online

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server_proc: Optional[subprocess.Popen] = None
        self._market_proc: Optional[subprocess.Popen] = None
        self._probe_timer: Optional[QTimer] = None
        self._last_status = (False, False)

    # ── Public slots (called via signal/slot or invokeMethod) ───────────

    def start_server(self, evejs_root: str, mode: str = "modded") -> None:
        """Start the EveJS game server.

        Args:
            evejs_root: Path to EveJS installation root.
            mode:       "vanilla" or "modded".
        """
        if self._server_proc is not None and self._server_proc.poll() is None:
            return  # already running

        try:
            self._server_proc = server_launcher.start_game_server(
                evejs_root, mode=mode
            )
            self.server_started.emit()
        except Exception as exc:  # pragma: no cover - defensive
            # Could emit an error signal here if desired
            print(f"Failed to start game server: {exc}")
            self._server_proc = None

    def stop_server(self) -> None:
        """Terminate the game server process."""
        if self._server_proc is None:
            return

        try:
            if self._server_proc.poll() is None:
                self._server_proc.terminate()
                try:
                    self._server_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._server_proc.kill()
        finally:
            self._server_proc = None
            self.server_stopped.emit()

    def start_market(self, evejs_root: str) -> None:
        """Start the EveJS market server.

        Args:
            evejs_root: Path to EveJS installation root.
        """
        if self._market_proc is not None and self._market_proc.poll() is None:
            return  # already running

        try:
            self._market_proc = server_launcher.start_market_server(evejs_root)
            self.market_started.emit()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"Failed to start market server: {exc}")
            self._market_proc = None

    def stop_market(self) -> None:
        """Terminate the market server process."""
        if self._market_proc is None:
            return

        try:
            if self._market_proc.poll() is None:
                self._market_proc.terminate()
                try:
                    self._market_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._market_proc.kill()
        finally:
            self._market_proc = None
            self.market_stopped.emit()

    # ── Status probing ───────────────────────────────────────────────────

    def probe_status(self) -> None:
        """Start a 2-second interval timer to probe server and market ports.

        Stops any existing probe timer. Emits status_changed only when the
        online/offline state actually changes.
        """
        if self._probe_timer is not None:
            self._probe_timer.stop()
            self._probe_timer.deleteLater()

        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(2000)  # 2 seconds
        self._probe_timer.timeout.connect(self._do_probe)
        self._probe_timer.start()

    def stop_probing(self) -> None:
        """Stop the status probe timer."""
        if self._probe_timer is not None:
            self._probe_timer.stop()
            self._probe_timer.deleteLater()
            self._probe_timer = None

    # ── Internal helpers ─────────────────────────────────────────────────

    def _do_probe(self) -> None:
        """Check ports and emit status_changed if state flipped."""
        server_online = server_launcher.is_server_running(port=26000)
        market_online = server_launcher.is_server_running(port=26001)

        current = (server_online, market_online)
        if current != self._last_status:
            self._last_status = current
            self.status_changed.emit(server_online, market_online)

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """Stop probing and terminate all managed processes."""
        self.stop_probing()
        self.stop_server()
        self.stop_market()
