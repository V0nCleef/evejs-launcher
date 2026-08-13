"""Normalized runtime view of persisted audio configuration."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


def _bool(mapping: Mapping[str, object], key: str, default: bool) -> bool:
    value = mapping.get(key)
    return value if isinstance(value, bool) else default


def _percent(mapping: Mapping[str, object], key: str, default: int) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    numeric = float(value)
    if not math.isfinite(numeric):
        return default
    return max(0, min(100, int(round(numeric))))


def _axis(mapping: Mapping[str, object], key: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    numeric = float(value)
    if not math.isfinite(numeric):
        return 0.0
    return max(-1.0, min(1.0, numeric))


def _text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class AudioSettings:
    """Validated settings consumed by the optional Qt audio backends."""

    master_muted: bool = False
    music_muted: bool = False
    music_enabled: bool = True
    music_volume: int = 50
    voice_enabled: bool = True
    voice_volume: int = 100
    voice_engine: str = ""
    voice_locale: str = ""
    voice_name: str = ""
    voice_rate: float = 0.0
    voice_pitch: float = 0.0
    announce_character_names: bool = True
    announce_results: bool = True
    ducking_enabled: bool = True
    ducking_level: int = 100

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "AudioSettings":
        """Build a safe runtime snapshot even from an unnormalized mapping."""
        return cls(
            master_muted=_bool(mapping, "audio_master_muted", False),
            music_muted=_bool(mapping, "audio_music_muted", False),
            music_enabled=_bool(mapping, "audio_music_enabled", True),
            music_volume=_percent(mapping, "audio_music_volume", 50),
            voice_enabled=_bool(mapping, "audio_voice_enabled", True),
            voice_volume=_percent(mapping, "audio_voice_volume", 100),
            voice_engine=_text(mapping, "audio_voice_engine"),
            voice_locale=_text(mapping, "audio_voice_locale"),
            voice_name=_text(mapping, "audio_voice_name"),
            voice_rate=_axis(mapping, "audio_voice_rate"),
            voice_pitch=_axis(mapping, "audio_voice_pitch"),
            announce_character_names=_bool(
                mapping, "audio_announce_character_names", True
            ),
            announce_results=_bool(mapping, "audio_announce_results", True),
            ducking_enabled=_bool(mapping, "audio_ducking_enabled", True),
            ducking_level=_percent(mapping, "audio_ducking_level", 100),
        )
