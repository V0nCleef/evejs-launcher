"""Pure runtime status model shared by every launcher presentation surface."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol


class ServiceState(Enum):
    """Lifecycle state for a reachable launcher service."""

    OFFLINE = "offline"
    STARTING = "starting"
    ONLINE = "online"
    STOPPING = "stopping"
    FAILED = "failed"


class ProcessLike(Protocol):
    """Minimal subprocess interface needed by the pure status derivation."""

    pid: int

    def poll(self) -> int | None: ...


@dataclass(frozen=True)
class RuntimeSnapshot:
    """One authoritative observation fanned out to Home, nav, and footer."""

    game: ServiceState
    market: ServiceState
    running_clients: int
    game_pid: int | None = None
    market_pid: int | None = None
    game_owned: bool = False
    market_owned: bool = False
    game_error: str | None = None
    market_error: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def derive_service_state(
    *,
    reachable: bool,
    process: ProcessLike | None,
    intent: ServiceState | None = None,
    last_error: str | None = None,
) -> tuple[ServiceState, int | None, str | None]:
    """Derive state, owned PID, and diagnostic from one service observation.

    Reachability remains authoritative for services started outside the launcher.
    An owned live process that is not reachable is still starting.  If that
    process exits before readiness, the failed state is retained for diagnosis.
    """

    return_code: int | None = None
    alive = False
    pid: int | None = None
    if process is not None:
        return_code = process.poll()
        alive = return_code is None
        if alive:
            pid = int(process.pid)

    if intent is ServiceState.STOPPING:
        return ServiceState.STOPPING, pid, last_error
    if reachable:
        return ServiceState.ONLINE, pid, None
    if last_error:
        return ServiceState.FAILED, pid, last_error
    if alive:
        return ServiceState.STARTING, pid, last_error
    if process is not None and intent is ServiceState.STARTING:
        diagnostic = last_error or f"Process exited before readiness (code {return_code})"
        return ServiceState.FAILED, None, diagnostic
    return ServiceState.OFFLINE, None, None
