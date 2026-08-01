"""Configuration persistence for EveJS Launcher."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile

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
    "theme": "dark",
    "hidden_characters": [],  # list of character names hidden from UI
    "hide_test_characters": True,  # auto-hide characters belonging to test/GM accounts
    "never_hide_characters": [],  # characters the user explicitly un-hid — auto-hide skips these
    "animations_enabled": True,  # cross-fade banner, page transitions, card effects
    "hero_rotation_interval_sec": 6,  # seconds between hero banner cross-fades
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
    """Normalize legacy server-selector keys into one preference value."""
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
    for legacy_key in (
        "server_start_script",
        "server_start_scripts",
        "server_script_prompted",
    ):
        migrated.pop(legacy_key, None)
    return migrated


def _runtime_backend(value: object) -> str:
    return value if value in {"native", "docker_compose"} else "native"


def _control_policy(value: object) -> str:
    return value if value in {"connect_only", "managed"} else "connect_only"


def _string_setting(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _bool_setting(value: object, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def load() -> dict:
    """Load config, merging with defaults."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
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
