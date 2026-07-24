"""Process tracker — monitors launched EVE client processes."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import subprocess


@dataclass
class LaunchedClient:
    """Represents a running EVE client."""
    pid: int
    username: str
    character_name: str
    started_at: datetime = field(default_factory=datetime.now)
    process: Optional[subprocess.Popen] = None

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

    def __init__(self):
        self._clients: list[LaunchedClient] = []

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
        """Remove processes that have exited. Returns number removed."""
        before = len(self._clients)
        self._clients = [c for c in self._clients if c.is_running]
        return before - len(self._clients)

    @property
    def running(self) -> list[LaunchedClient]:
        """Return list of currently running clients."""
        self.prune_dead()
        return list(self._clients)

    @property
    def running_count(self) -> int:
        return len(self.running)

    def is_account_running(self, username: str) -> bool:
        """Check if any client from this account is currently running."""
        for c in self.running:
            if c.username == username:
                return True
        return False

    def get_running_character(self, username: str) -> str | None:
        """Get the character name running on this account, if any."""
        for c in self.running:
            if c.username == username:
                return c.character_name
        return None
