"""Configuration persistence for EveJS Launcher."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import tempfile

from .i18n import normalize_language

log = logging.getLogger(__name__)

APP_NAME = "EveJS-Launcher"
CONFIG_DIR = Path(os.environ.get("APPDATA", "")) / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "evejs_root": "",
    "client_path": "",
    "proxy_url": "http://127.0.0.1:26002",
    "game_port": 26000,
    "auto_start_server": False,
    "auto_start_market": False,
    "runtime_backend": "native",
    "docker_compose_file": "",
    "docker_control_policy": "connect_only",
    "docker_project_name": "",
    "docker_keep_running_on_exit": True,
    "server_mode": "modded",  # legacy fallback when no StartServer*.bat exists
    "server_start_preference": "ask",  # "ask" or a filename relative to the EveJS root
    "stagger_delay_sec": 3,
    "auto_login_enabled": True,
    "theme": "dark",
    "language": "en",
    "hidden_characters": [],  # list of character names hidden from UI
    "hide_test_characters": True,  # auto-hide characters belonging to test/GM accounts
    "never_hide_characters": [],  # characters the user explicitly un-hid — auto-hide skips these
    "animations_enabled": True,  # cross-fade banner, page transitions, card effects
    "hero_rotation_interval_sec": 6,  # legacy preference; retained for config round-trips
    "deep_signal_enabled": True,  # legacy preference; shell is always enabled
    # ── Audio & LYRA ───────────────────────────────────────────────────────
    "audio_master_muted": False,
    # Music-only mute used by the persistent title-bar control.  Master mute
    # remains a separate Settings/runtime safety switch for music and LYRA.
    "audio_music_muted": False,
    "audio_music_enabled": True,
    "audio_music_volume": 50,         # percent, 0-100
    # Retained only to read older configs; custom music is no longer exposed.
    "audio_music_library": [],
    "audio_voice_enabled": True,
    "audio_voice_volume": 100,        # percent, 0-100
    "audio_voice_engine": "",         # blank selects the platform default
    "audio_voice_locale": "",         # blank follows the system locale
    "audio_voice_name": "",           # blank selects the engine default
    "audio_voice_rate": 0.0,           # QTextToSpeech range, -1.0 to 1.0
    "audio_voice_pitch": 0.0,          # QTextToSpeech range, -1.0 to 1.0
    "audio_announce_character_names": True,
    "audio_announce_results": True,
    "audio_ducking_enabled": True,
    "audio_ducking_level": 100,        # music percent while LYRA speaks
    # ── Auto-update ──────────────────────────────────────────────────────
    "update_auto_check": True,           # auto-check for updates on startup
    "update_check_interval_hours": 6,    # hours between background checks
    "update_skip_version": "",           # version string to skip (DEPRECATED - kept for migration)
    "update_skip_versions": [],          # list of version strings the user has skipped
    "update_last_checked": "",           # ISO timestamp of last successful check
}


def _default_config() -> dict:
    """Return a fully independent copy of all default values."""
    return deepcopy(DEFAULT_CONFIG)


def _is_valid_server_start_preference(value: object) -> bool:
    """Return whether *value* is ``ask`` or a root-relative filename."""
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    return value.casefold() == "ask" or ("/" not in value and "\\" not in value)


def _legacy_script_filename(value: object) -> str:
    """Extract a filename from an old absolute or relative script value."""
    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip().replace("\\", "/").rsplit("/", 1)[-1]


def _migrate(stored: dict) -> dict:
    """Migrate known settings while retaining unrelated persisted preferences."""
    migrated = dict(stored)
    preference = migrated.get("server_start_preference")
    if _is_valid_server_start_preference(preference):
        normalized = str(preference).strip()
        preference = "ask" if normalized.casefold() == "ask" else normalized
    else:
        preference = _legacy_script_filename(migrated.get("server_start_script")) or "ask"

    migrated["server_start_preference"] = preference
    migrated["runtime_backend"] = _runtime_backend(migrated.get("runtime_backend"))
    migrated["docker_compose_file"] = _string_setting(migrated.get("docker_compose_file"))
    migrated["docker_control_policy"] = _control_policy(migrated.get("docker_control_policy"))
    migrated["docker_project_name"] = _string_setting(migrated.get("docker_project_name"))
    migrated["docker_keep_running_on_exit"] = _bool_setting(
        migrated.get("docker_keep_running_on_exit"), default=True
    )
    # New configs enable automatic login; malformed persisted values fail
    # closed, while a real saved False remains an explicit opt-out.
    migrated["auto_login_enabled"] = _bool_setting(
        migrated.get("auto_login_enabled"),
        default=(
            DEFAULT_CONFIG["auto_login_enabled"]
            if "auto_login_enabled" not in migrated
            else False
        ),
    )
    migrated["deep_signal_enabled"] = _bool_setting(
        migrated.get("deep_signal_enabled"), default=True
    )
    migrated["language"] = normalize_language(migrated.get("language"))
    _migrate_audio_settings(migrated)
    for legacy_key in (
        "server_start_script",
        "server_start_scripts",
        "server_script_prompted",
    ):
        migrated.pop(legacy_key, None)
    _normalize_settings(migrated)
    return migrated


def _runtime_backend(value: object) -> str:
    return value if isinstance(value, str) and value in {"native", "docker_compose"} else "native"


def _control_policy(value: object) -> str:
    return value if isinstance(value, str) and value in {"connect_only", "managed"} else "connect_only"


_INTEGER_SETTING_RANGES = {
    "game_port": (1, 65535),
    "stagger_delay_sec": (0, 30),
    # Zero predates the Settings control and disables the repeating timer.
    "update_check_interval_hours": (0, 72),
    "hero_rotation_interval_sec": (0, 2_147_483),
}


def _integer_setting(value: object, *, default: int, minimum: int, maximum: int) -> int:
    """Normalize integer preferences without truncating fractions or Booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return default
    try:
        numeric = int(value)
    except (ValueError, OverflowError):
        return default
    return numeric if minimum <= numeric <= maximum else default


def _normalize_settings(migrated: dict) -> None:
    """Make supported field types safe for consumers without discarding a profile.

    Unknown keys remain untouched. Lists in the current schema contain names,
    versions or paths; keep valid entries rather than losing an entire list to
    one malformed member. Audio's more specific normalization runs first.
    """
    for key, default in DEFAULT_CONFIG.items():
        value = migrated.get(key, default)
        if isinstance(default, bool):
            migrated[key] = _bool_setting(value, default=default)
        elif isinstance(default, str):
            migrated[key] = value if isinstance(value, str) else default
        elif isinstance(default, list):
            migrated[key] = (
                [item for item in value if isinstance(item, str)]
                if isinstance(value, list)
                else []
            )

    for key, (minimum, maximum) in _INTEGER_SETTING_RANGES.items():
        migrated[key] = _integer_setting(
            migrated.get(key), default=DEFAULT_CONFIG[key],
            minimum=minimum, maximum=maximum,
        )
    if migrated["server_mode"] not in {"vanilla", "modded"}:
        migrated["server_mode"] = DEFAULT_CONFIG["server_mode"]


def _string_setting(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _path_list_setting(value: object) -> list[str]:
    """Normalize the retired personal-library field for silent compatibility.

    A single string is accepted for compatibility with the earliest local
    playlist prototype. Existence is deliberately not part of migration so an
    older configuration can round-trip without losing data, even though the
    launcher no longer exposes custom-music controls.
    """
    candidates = [value] if isinstance(value, str) else value
    if not isinstance(candidates, (list, tuple)):
        return []

    paths: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        path = candidate.strip()
        identity = path.casefold()
        if not path or identity in seen:
            continue
        seen.add(identity)
        paths.append(path)
    return paths


def _bool_setting(value: object, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _percent_setting(value: object, *, default: int) -> int:
    """Return a finite integer percentage clamped to 0-100."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    return int(round(max(0, min(100, value))))


def _speech_axis_setting(value: object, *, default: float = 0.0) -> float:
    """Return a finite QTextToSpeech rate/pitch value clamped to -1..1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    return float(max(-1.0, min(1.0, value)))


_AUDIO_SETTING_ALIASES = {
    "master_muted": "audio_master_muted",
    "music_muted": "audio_music_muted",
    "music_enabled": "audio_music_enabled",
    "music_volume": "audio_music_volume",
    "music_library": "audio_music_library",
    "voice_enabled": "audio_voice_enabled",
    "voice_volume": "audio_voice_volume",
    "tts_engine": "audio_voice_engine",
    "tts_locale": "audio_voice_locale",
    "tts_voice": "audio_voice_name",
    "tts_rate": "audio_voice_rate",
    "tts_pitch": "audio_voice_pitch",
    "voice_announce_names": "audio_announce_character_names",
    "voice_announce_results": "audio_announce_results",
    "voice_duck_music": "audio_ducking_enabled",
    "voice_ducking_percent": "audio_ducking_level",
}


def _migrate_audio_settings(migrated: dict) -> None:
    """Migrate early prototype names and normalize every audio setting in-place."""
    has_explicit_music_mute = (
        "audio_music_muted" in migrated or "music_muted" in migrated
    )
    for legacy_key, current_key in _AUDIO_SETTING_ALIASES.items():
        if current_key not in migrated and legacy_key in migrated:
            migrated[current_key] = migrated[legacy_key]
        migrated.pop(legacy_key, None)

    # Interface cues were retired as a product feature.  Drop both the
    # prototype names and their later persisted names instead of carrying
    # invisible, inactive settings forward indefinitely.
    for retired_key in (
        "ui_sounds_enabled",
        "ui_sounds_volume",
        "audio_ui_sounds_enabled",
        "audio_ui_sounds_volume",
    ):
        migrated.pop(retired_key, None)

    legacy_master_muted = _bool_setting(
        migrated.get("audio_master_muted"), default=False
    )
    # The former title-bar control persisted a master mute even though users
    # understood it as the soundtrack button. Transfer that state once when
    # no dedicated music choice exists, then retire the persisted master mute
    # so it cannot silently suppress LYRA after this upgrade.
    if not has_explicit_music_mute:
        migrated["audio_music_muted"] = legacy_master_muted
    migrated["audio_master_muted"] = False
    migrated["audio_music_muted"] = _bool_setting(
        migrated.get("audio_music_muted"), default=False
    )
    migrated["audio_music_enabled"] = _bool_setting(
        migrated.get("audio_music_enabled"), default=True
    )
    migrated["audio_music_volume"] = _percent_setting(
        migrated.get("audio_music_volume"), default=50
    )
    migrated["audio_music_library"] = _path_list_setting(
        migrated.get("audio_music_library")
    )
    migrated["audio_voice_enabled"] = _bool_setting(
        migrated.get("audio_voice_enabled"), default=True
    )
    migrated["audio_voice_volume"] = _percent_setting(
        migrated.get("audio_voice_volume"), default=100
    )
    for key in ("audio_voice_engine", "audio_voice_locale", "audio_voice_name"):
        migrated[key] = _string_setting(migrated.get(key))
    migrated["audio_voice_rate"] = _speech_axis_setting(
        migrated.get("audio_voice_rate")
    )
    migrated["audio_voice_pitch"] = _speech_axis_setting(
        migrated.get("audio_voice_pitch")
    )
    migrated["audio_announce_character_names"] = _bool_setting(
        migrated.get("audio_announce_character_names"), default=True
    )
    migrated["audio_announce_results"] = _bool_setting(
        migrated.get("audio_announce_results"), default=True
    )
    migrated["audio_ducking_enabled"] = _bool_setting(
        migrated.get("audio_ducking_enabled"), default=True
    )
    migrated["audio_ducking_level"] = _percent_setting(
        migrated.get("audio_ducking_level"), default=100
    )


def load() -> dict:
    """Load config, merging with defaults."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            # ``utf-8-sig`` accepts ordinary UTF-8 and the BOM emitted by
            # Windows PowerShell's JSON tooling. A valid launcher config must
            # not be quarantined merely because it carries that marker.
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("configuration root must be a JSON object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.{stamp}.broken")
            try:
                os.replace(CONFIG_FILE, backup)
                log.warning("Invalid configuration moved to %s: %s", backup, exc)
            except OSError:
                log.exception("Invalid configuration could not be backed up: %s", CONFIG_FILE)
            return _default_config()

        stored = _migrate(raw)
        repaired = [
            key for key in DEFAULT_CONFIG
            if key in raw and (
                type(raw[key]) is not type(stored[key]) or raw[key] != stored[key]
            )
        ]
        if repaired:
            # Field names are useful diagnostics; paths, accounts and values
            # are private. Leave the original file intact until an explicit save.
            log.warning("Normalized persisted configuration fields: %s", ", ".join(repaired))
        cfg = _default_config()
        cfg.update(stored)
        return cfg
    return _default_config()


def save(cfg: dict) -> None:
    """Atomically save config using a temporary file beside the destination."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_DIR,
            prefix=f".{CONFIG_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(cfg, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, CONFIG_FILE)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def get_setting(key: str):
    """Get a single setting value."""
    return load().get(key)


def set_setting(key: str, value) -> None:
    """Update a single setting."""
    cfg = load()
    cfg[key] = value
    save(cfg)
