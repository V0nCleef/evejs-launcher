"""Configuration persistence for EveJS Launcher."""
import json
import os
from pathlib import Path

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
    "server_mode": "modded",  # "vanilla" or "modded"
    "stagger_delay_sec": 3,
    "autologin_delay_sec": 2,
    "autologin_window_title": "EVE",
    "theme": "dark",
    "hidden_accounts": [],  # list of usernames hidden from UI
    "hide_test_accounts": True,  # auto-hide EveJS default test/GM accounts
    "animations_enabled": True,  # cross-fade banner, page transitions, card effects
    "hero_rotation_interval_sec": 6,  # seconds between hero banner cross-fades
    # ── Auto-update ──────────────────────────────────────────────────────
    "update_auto_check": True,           # auto-check for updates on startup
    "update_check_interval_hours": 6,    # hours between background checks
    "update_skip_version": "",           # version string to skip (DEPRECATED - kept for migration)
    "update_skip_versions": [],          # list of version strings the user has skipped
    "update_last_checked": "",           # ISO timestamp of last successful check
}


def load() -> dict:
    """Load config, merging with defaults."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **stored}
    return DEFAULT_CONFIG.copy()


def save(cfg: dict) -> None:
    """Save config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_setting(key: str):
    """Get a single setting value."""
    return load().get(key)


def set_setting(key: str, value) -> None:
    """Update a single setting."""
    cfg = load()
    cfg[key] = value
    save(cfg)
