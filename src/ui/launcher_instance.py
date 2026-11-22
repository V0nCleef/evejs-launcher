"""Keep a single interactive launcher for each launcher settings directory."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time
from collections.abc import Callable

from PyQt6.QtCore import QLockFile, QStandardPaths, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


_ACK = b"restore"
_STARTUP_TIMEOUT = 5.0


class LauncherInstance:
    def __init__(self, config_dir: Path, *, activate_existing: bool = True) -> None:
        config_key = os.path.normcase(str(config_dir.resolve()))
        name = "evejs-launcher-" + hashlib.sha256(os.fsencode(config_key)).hexdigest()[:32]
        if os.name == "nt":
            lock_dir = config_dir
            self._address = name
        else:
            # Keep the Unix socket name short, and keep the lock writable when
            # the config directory itself is read-only.
            runtime_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
            if not runtime_dir:
                raise RuntimeError("The launcher runtime directory is unavailable.")
            lock_dir = Path(runtime_dir)
            self._address = str(lock_dir / name)
        lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(lock_dir / (name + ".lock")))
        self._lock.setStaleLockTime(0)
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._receive_activation)
        self._can_forward = activate_existing
        self._restore: Callable[[], None] | None = None
        self._restore_pending = False
        self.primary = False

    def __enter__(self) -> LauncherInstance:
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while not self._lock.tryLock():
            if self._lock.error() != QLockFile.LockError.LockFailedError:
                raise RuntimeError("Could not lock the launcher instance.")
            if not self._can_forward or self._forward_activation(deadline):
                return self
            if time.monotonic() >= deadline:
                raise RuntimeError("The running launcher did not respond to the restore request.")
            time.sleep(0.05)

        try:
            # Only the lock owner can clean up the previous process's socket.
            QLocalServer.removeServer(self._address)
            if not self._server.listen(self._address):
                raise RuntimeError(f"Could not listen for launcher activation: {self._server.errorString()}")
        except BaseException:
            self._lock.unlock()
            raise
        self.primary = True
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self.primary:
            self._server.close()
            self._lock.unlock()

    def set_restore(self, restore: Callable[[], None] | None) -> None:
        self._restore = restore
        if self._restore_pending and restore is not None:
            QTimer.singleShot(0, self._restore_if_ready)

    def _forward_activation(self, deadline: float) -> bool:
        # The new process, when started from the desktop, can grant its
        # foreground permission to the existing process on Windows.
        if os.name == "nt":
            import ctypes

            locked, pid, _, _ = self._lock.getLockInfo()
            if locked and pid > 0:
                ctypes.windll.user32.AllowSetForegroundWindow(pid)

        client = QLocalSocket()
        client.connectToServer(self._address)
        if not client.waitForConnected(100):
            return False
        while client.bytesAvailable() < len(_ACK):
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0 or not client.waitForReadyRead(remaining_ms):
                return False
        return bytes(client.read(len(_ACK))) == _ACK

    def _receive_activation(self) -> None:
        while self._server.hasPendingConnections():
            client = self._server.nextPendingConnection()
            client.disconnected.connect(client.deleteLater)
            client.write(_ACK)
            client.disconnectFromServer()
        self._restore_pending = True
        self._restore_if_ready()

    def _restore_if_ready(self) -> None:
        if self._restore_pending and self._restore is not None:
            self._restore_pending = False
            self._restore()
