"""Optional music and LYRA voice support for the launcher."""

from .controller import AudioController
from .events import (
    VoiceAnnouncement,
    VoiceEvent,
    VoiceLine,
    preview_announcement,
    render_announcement,
    service_stop_result_event,
)
from .settings import AudioSettings

__all__ = [
    "AudioController",
    "AudioSettings",
    "VoiceAnnouncement",
    "VoiceEvent",
    "VoiceLine",
    "preview_announcement",
    "render_announcement",
    "service_stop_result_event",
]
