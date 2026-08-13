"""Fixed LYRA voice-line catalog and typed launcher announcements.

Runtime context such as character names, group names, and launch counts is
never interpolated into speech or captions. This keeps the prerecorded voice
catalog private, deterministic, and available offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class VoiceEvent(Enum):
    """Launcher events that may select one fixed LYRA recording."""

    SERVER_STACK_LAUNCHING = "server_stack_launching"
    SERVER_STACK_ONLINE = "server_stack_online"
    SERVER_STACK_FAILED = "server_stack_failed"
    SERVER_STACK_STOPPING = "server_stack_stopping"
    SERVER_STACK_OFFLINE = "server_stack_offline"
    GAME_SERVER_LAUNCHING = "game_server_launching"
    GAME_SERVER_ONLINE = "game_server_online"
    GAME_SERVER_LAUNCH_FAILED = "game_server_launch_failed"
    GAME_SERVER_STOPPING = "game_server_stopping"
    GAME_SERVER_OFFLINE = "game_server_offline"
    MARKET_SERVER_LAUNCHING = "market_server_launching"
    MARKET_SERVER_ONLINE = "market_server_online"
    MARKET_SERVER_LAUNCH_FAILED = "market_server_launch_failed"
    MARKET_SERVER_STOPPING = "market_server_stopping"
    MARKET_SERVER_OFFLINE = "market_server_offline"
    SERVICE_STOP_FAILED = "service_stop_failed"
    CHARACTER_LAUNCHING = "character_launching"
    CHARACTER_LAUNCH_FAILED = "character_launch_failed"
    GROUP_LAUNCHING = "group_launching"
    LAUNCH_SEQUENCE_COMPLETE = "launch_sequence_complete"
    CLIENTS_TERMINATING = "clients_terminating"
    CLIENTS_TERMINATED = "clients_terminated"


class VoiceLine(Enum):
    """Stable identifiers used as both manifest keys and WAV basenames."""

    PREVIEW = "preview"
    SERVER_STACK_LAUNCHING = "server_stack_launching"
    SERVER_STACK_ONLINE = "server_stack_online"
    SERVER_STACK_FAILED = "server_stack_failed"
    SERVER_STACK_STOPPING = "server_stack_stopping"
    SERVER_STACK_OFFLINE = "server_stack_offline"
    GAME_SERVER_LAUNCHING = "game_server_launching"
    GAME_SERVER_ONLINE = "game_server_online"
    GAME_SERVER_LAUNCH_FAILED = "game_server_launch_failed"
    GAME_SERVER_STOPPING = "game_server_stopping"
    GAME_SERVER_OFFLINE = "game_server_offline"
    MARKET_SERVER_LAUNCHING = "market_server_launching"
    MARKET_SERVER_ONLINE = "market_server_online"
    MARKET_SERVER_LAUNCH_FAILED = "market_server_launch_failed"
    MARKET_SERVER_STOPPING = "market_server_stopping"
    MARKET_SERVER_OFFLINE = "market_server_offline"
    SERVICE_STOP_FAILED = "service_stop_failed"
    CHARACTER_LAUNCHING = "character_launching"
    CHARACTER_LAUNCH_FAILED = "character_launch_failed"
    GROUP_LAUNCHING = "group_launching"
    LAUNCH_SEQUENCE_COMPLETE = "launch_sequence_complete"
    LAUNCH_SEQUENCE_PARTIAL = "launch_sequence_partial"
    LAUNCH_SEQUENCE_CANCELLED = "launch_sequence_cancelled"
    CLIENTS_TERMINATING = "clients_terminating"
    CLIENTS_TERMINATED = "clients_terminated"

    @property
    def filename(self) -> str:
        return f"{self.value}.wav"

    @property
    def text(self) -> str:
        return VOICE_LINE_TEXT[self]


# Approved fixed catalog. A sentence and its recording must be reviewed and
# replaced together whenever this mapping changes.
VOICE_LINE_TEXT: dict[VoiceLine, str] = {
    VoiceLine.PREVIEW: "LYRA online. Shipboard systems ready.",
    VoiceLine.SERVER_STACK_LAUNCHING: "Launching server stack.",
    VoiceLine.SERVER_STACK_ONLINE: "Server stack online.",
    VoiceLine.SERVER_STACK_FAILED: "Server stack launch failed.",
    VoiceLine.SERVER_STACK_STOPPING: "Stopping server stack.",
    VoiceLine.SERVER_STACK_OFFLINE: "Server stack offline.",
    VoiceLine.GAME_SERVER_LAUNCHING: "Launching game server.",
    VoiceLine.GAME_SERVER_ONLINE: "Game server online.",
    VoiceLine.GAME_SERVER_LAUNCH_FAILED: "Game server launch failed.",
    VoiceLine.GAME_SERVER_STOPPING: "Stopping game server.",
    VoiceLine.GAME_SERVER_OFFLINE: "Game server offline.",
    VoiceLine.MARKET_SERVER_LAUNCHING: "Launching market server.",
    VoiceLine.MARKET_SERVER_ONLINE: "Market server online.",
    VoiceLine.MARKET_SERVER_LAUNCH_FAILED: "Market server launch failed.",
    VoiceLine.MARKET_SERVER_STOPPING: "Stopping market server.",
    VoiceLine.MARKET_SERVER_OFFLINE: "Market server offline.",
    VoiceLine.SERVICE_STOP_FAILED: "Service shutdown failed.",
    VoiceLine.CHARACTER_LAUNCHING: "Launching selected character.",
    VoiceLine.CHARACTER_LAUNCH_FAILED: "Character launch failed.",
    VoiceLine.GROUP_LAUNCHING: "Launching character group.",
    VoiceLine.LAUNCH_SEQUENCE_COMPLETE: "Launch sequence complete.",
    VoiceLine.LAUNCH_SEQUENCE_PARTIAL: "Launch sequence complete, with errors.",
    VoiceLine.LAUNCH_SEQUENCE_CANCELLED: "Launch sequence cancelled.",
    VoiceLine.CLIENTS_TERMINATING: "Terminating all clients.",
    VoiceLine.CLIENTS_TERMINATED: "All clients terminated.",
}


@dataclass(frozen=True)
class VoiceAnnouncement:
    """One fixed caption/recording selected for a typed launcher event."""

    event: VoiceEvent
    line: VoiceLine
    is_result: bool = False

    @property
    def text(self) -> str:
        return self.line.text


_RESULT_EVENTS = {
    VoiceEvent.SERVER_STACK_ONLINE,
    VoiceEvent.SERVER_STACK_FAILED,
    VoiceEvent.SERVER_STACK_OFFLINE,
    VoiceEvent.GAME_SERVER_ONLINE,
    VoiceEvent.GAME_SERVER_LAUNCH_FAILED,
    VoiceEvent.GAME_SERVER_OFFLINE,
    VoiceEvent.MARKET_SERVER_ONLINE,
    VoiceEvent.MARKET_SERVER_LAUNCH_FAILED,
    VoiceEvent.MARKET_SERVER_OFFLINE,
    VoiceEvent.SERVICE_STOP_FAILED,
    VoiceEvent.CHARACTER_LAUNCH_FAILED,
    VoiceEvent.LAUNCH_SEQUENCE_COMPLETE,
    VoiceEvent.CLIENTS_TERMINATED,
}


_SERVICE_START_RESULTS: dict[VoiceEvent, tuple[VoiceEvent, VoiceEvent]] = {
    VoiceEvent.SERVER_STACK_LAUNCHING: (
        VoiceEvent.SERVER_STACK_ONLINE,
        VoiceEvent.SERVER_STACK_FAILED,
    ),
    VoiceEvent.GAME_SERVER_LAUNCHING: (
        VoiceEvent.GAME_SERVER_ONLINE,
        VoiceEvent.GAME_SERVER_LAUNCH_FAILED,
    ),
    VoiceEvent.MARKET_SERVER_LAUNCHING: (
        VoiceEvent.MARKET_SERVER_ONLINE,
        VoiceEvent.MARKET_SERVER_LAUNCH_FAILED,
    ),
}


_SERVICE_STOP_RESULTS: dict[VoiceEvent, VoiceEvent] = {
    VoiceEvent.SERVER_STACK_STOPPING: VoiceEvent.SERVER_STACK_OFFLINE,
    VoiceEvent.GAME_SERVER_STOPPING: VoiceEvent.GAME_SERVER_OFFLINE,
    VoiceEvent.MARKET_SERVER_STOPPING: VoiceEvent.MARKET_SERVER_OFFLINE,
}


def service_start_result_event(
    launching_event: VoiceEvent,
    *,
    succeeded: bool,
) -> VoiceEvent:
    """Return the matching completion event for an accepted service start.

    The accepted launching event is an attribution token, not inferred from
    the services a worker happened to touch. This keeps automatic starts and
    internal maintenance restarts silent while preserving exact Game, Market,
    and full-stack result wording for explicit launcher actions.
    """
    try:
        online_event, failed_event = _SERVICE_START_RESULTS[launching_event]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported service launching event: {launching_event!r}"
        ) from exc
    return online_event if succeeded else failed_event


def service_stop_result_event(
    stopping_event: VoiceEvent,
    *,
    succeeded: bool,
) -> VoiceEvent:
    """Return the bounded completion event for an accepted service stop.

    Callers must pass the exact event attributed when a real stop worker was
    accepted. This prevents internal maintenance stops and no-op button paths
    from being described as user-requested shutdowns.
    """
    try:
        completed_event = _SERVICE_STOP_RESULTS[stopping_event]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported service stopping event: {stopping_event!r}"
        ) from exc
    return completed_event if succeeded else VoiceEvent.SERVICE_STOP_FAILED


def preview_announcement() -> VoiceAnnouncement:
    """Return the one fixed Settings preview line."""
    return VoiceAnnouncement(
        event=VoiceEvent.SERVER_STACK_ONLINE,
        line=VoiceLine.PREVIEW,
    )


def render_announcement(
    event: VoiceEvent,
    *,
    character_name: object = "",
    group_name: object = "",
    launched_count: object = 0,
    failed_count: object = 0,
    cancelled: object = False,
    announce_character_names: bool = True,
    announce_results: bool = True,
) -> VoiceAnnouncement | None:
    """Render one fixed, pre-recordable announcement from a typed event.

    Runtime names and counts are intentionally ignored.  LYRA's spoken catalog
    is finite so the launcher can bundle natural pre-generated lines without a
    heavyweight neural model or leaking private labels into an audio cache.
    The legacy keyword arguments remain accepted for API/config migration.
    """
    del character_name, group_name, launched_count, announce_character_names
    is_result = event in _RESULT_EVENTS
    if is_result and not announce_results:
        return None

    if event is VoiceEvent.SERVER_STACK_LAUNCHING:
        line = VoiceLine.SERVER_STACK_LAUNCHING
    elif event is VoiceEvent.SERVER_STACK_ONLINE:
        line = VoiceLine.SERVER_STACK_ONLINE
    elif event is VoiceEvent.SERVER_STACK_FAILED:
        line = VoiceLine.SERVER_STACK_FAILED
    elif event is VoiceEvent.SERVER_STACK_STOPPING:
        line = VoiceLine.SERVER_STACK_STOPPING
    elif event is VoiceEvent.SERVER_STACK_OFFLINE:
        line = VoiceLine.SERVER_STACK_OFFLINE
    elif event is VoiceEvent.GAME_SERVER_LAUNCHING:
        line = VoiceLine.GAME_SERVER_LAUNCHING
    elif event is VoiceEvent.GAME_SERVER_ONLINE:
        line = VoiceLine.GAME_SERVER_ONLINE
    elif event is VoiceEvent.GAME_SERVER_LAUNCH_FAILED:
        line = VoiceLine.GAME_SERVER_LAUNCH_FAILED
    elif event is VoiceEvent.GAME_SERVER_STOPPING:
        line = VoiceLine.GAME_SERVER_STOPPING
    elif event is VoiceEvent.GAME_SERVER_OFFLINE:
        line = VoiceLine.GAME_SERVER_OFFLINE
    elif event is VoiceEvent.MARKET_SERVER_LAUNCHING:
        line = VoiceLine.MARKET_SERVER_LAUNCHING
    elif event is VoiceEvent.MARKET_SERVER_ONLINE:
        line = VoiceLine.MARKET_SERVER_ONLINE
    elif event is VoiceEvent.MARKET_SERVER_LAUNCH_FAILED:
        line = VoiceLine.MARKET_SERVER_LAUNCH_FAILED
    elif event is VoiceEvent.MARKET_SERVER_STOPPING:
        line = VoiceLine.MARKET_SERVER_STOPPING
    elif event is VoiceEvent.MARKET_SERVER_OFFLINE:
        line = VoiceLine.MARKET_SERVER_OFFLINE
    elif event is VoiceEvent.SERVICE_STOP_FAILED:
        line = VoiceLine.SERVICE_STOP_FAILED
    elif event is VoiceEvent.CHARACTER_LAUNCHING:
        line = VoiceLine.CHARACTER_LAUNCHING
    elif event is VoiceEvent.CHARACTER_LAUNCH_FAILED:
        line = VoiceLine.CHARACTER_LAUNCH_FAILED
    elif event is VoiceEvent.GROUP_LAUNCHING:
        line = VoiceLine.GROUP_LAUNCHING
    elif event is VoiceEvent.LAUNCH_SEQUENCE_COMPLETE:
        if cancelled is True:
            line = VoiceLine.LAUNCH_SEQUENCE_CANCELLED
        else:
            try:
                failures = max(0, int(failed_count))
            except (TypeError, ValueError, OverflowError):
                failures = 0
            line = (
                VoiceLine.LAUNCH_SEQUENCE_PARTIAL
                if failures
                else VoiceLine.LAUNCH_SEQUENCE_COMPLETE
            )
    elif event is VoiceEvent.CLIENTS_TERMINATING:
        line = VoiceLine.CLIENTS_TERMINATING
    elif event is VoiceEvent.CLIENTS_TERMINATED:
        line = VoiceLine.CLIENTS_TERMINATED
    else:  # pragma: no cover - Enum exhaustiveness guard
        raise ValueError(f"Unsupported voice event: {event!r}")

    return VoiceAnnouncement(event=event, line=line, is_result=is_result)
