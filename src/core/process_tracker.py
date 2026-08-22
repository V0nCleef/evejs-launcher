"""Process tracker — monitors launched EVE client processes."""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import subprocess

from .platform import has_visible_window_for_pid

CLIENT_LAUNCH_GRACE_SECONDS = 20.0
CLIENT_WINDOW_CLOSE_GRACE_SECONDS = 2.0


@dataclass
class LaunchedClient:
    """Represents a running EVE client."""
    pid: int
    username: str
    character_name: str
    started_at: datetime = field(default_factory=datetime.now)
    process: Optional[subprocess.Popen] = None
    has_seen_window: bool = False
    window_missing_since: datetime | None = None

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.started_at).total_seconds()

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def uptime_str(self) -> str:
        secs = int(self.uptime_seconds)
        h, m = divmod(secs, 3600)
        m, s = divmod(m, 60)
        if h > 0:
            return f"{h}h {m}m"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"


class ProcessTracker:
    """Tracks all launched EVE clients."""

    def __init__(
        self,
        *,
        window_probe: Callable[[int], bool] | None = None,
        window_close_grace_seconds: float = CLIENT_WINDOW_CLOSE_GRACE_SECONDS,
    ):
        self._clients: list[LaunchedClient] = []
        self._window_probe = window_probe or has_visible_window_for_pid
        self._window_close_grace_seconds = max(
            0.0,
            float(window_close_grace_seconds),
        )

    def add(self, username: str, character_name: str, proc: subprocess.Popen) -> LaunchedClient:
        """Register a newly launched client."""
        client = LaunchedClient(
            pid=proc.pid,
            username=username,
            character_name=character_name,
            process=proc,
        )
        self._clients.append(client)
        return client

    def remove(self, pid: int) -> None:
        """Remove a client by PID."""
        self._clients = [c for c in self._clients if c.pid != pid]

    def kill(self, pid: int) -> bool:
        """Kill a specific client by PID."""
        for client in self._clients:
            if client.pid == pid:
                try:
                    client.process.terminate()
                    return True
                except Exception:
                    pass
        return False

    def kill_all(self) -> int:
        """Kill all running clients. Returns number killed."""
        killed = 0
        for client in self._clients:
            try:
                if client.is_running:
                    client.process.terminate()
                    killed += 1
            except Exception:
                pass
        self._clients.clear()
        return killed

    def prune_dead(self) -> int:
        """Remove exited or post-window cleanup processes.

        This is the tracker's sole deletion boundary. Read APIs deliberately
        filter dead processes without consuming them so the UI cannot miss the
        removal event when an unrelated status poll wins a timer race.
        """
        before = len(self._clients)
        self._clients = [c for c in self._clients if self._retain_client(c)]
        return before - len(self._clients)

    def _retain_client(self, client: LaunchedClient) -> bool:
        if not client.is_running:
            return False
        try:
            window_visible = bool(self._window_probe(client.pid))
        except Exception:
            # A failed observation must never make a live client launchable.
            return True
        if window_visible:
            client.has_seen_window = True
            client.window_missing_since = None
            return True
        if not client.has_seen_window:
            # EVE legitimately has no top-level window during startup.
            return True
        now = datetime.now()
        if client.window_missing_since is None:
            client.window_missing_since = now
        missing_seconds = (now - client.window_missing_since).total_seconds()
        return missing_seconds < self._window_close_grace_seconds

    @property
    def running(self) -> list[LaunchedClient]:
        """Return list of currently running clients."""
        return [client for client in self._clients if client.is_running]

    @property
    def running_count(self) -> int:
        return len(self.running)

    def is_account_running(self, username: str) -> bool:
        """Check if any client from this account is currently running."""
        for c in self.running:
            if c.username == username:
                return True
        return False

    def account_launch_grace_remaining(
        self,
        username: str,
        window_delay_sec: float = CLIENT_LAUNCH_GRACE_SECONDS,
    ) -> float | None:
        """Return seconds left in one tracked account's launch grace period.

        A process PID is attributable to its exact launcher request; a global
        EVE window is not.  Keep this state process- and time-based until a
        per-process HWND mapping exists.
        """
        delay = max(0.0, float(window_delay_sec))
        for c in self.running:
            if c.username == username:
                elapsed = (datetime.now() - c.started_at).total_seconds()
                return max(0.0, delay - elapsed)
        return None

    def is_account_launching(
        self,
        username: str,
        window_delay_sec: float = CLIENT_LAUNCH_GRACE_SECONDS,
    ) -> bool:
        """Return whether an attributed live process is still in launch grace."""
        remaining = self.account_launch_grace_remaining(
            username,
            window_delay_sec,
        )
        return remaining is not None and remaining > 0.0

    def get_running_character(self, username: str) -> str | None:
        """Get the character name running on this account, if any."""
        for c in self.running:
            if c.username == username:
                return c.character_name
        return None
