"""Application-wide constants for EveJS Launcher V2."""
from __future__ import annotations

from enum import Enum, IntEnum
from pathlib import Path

# ── Colour palette (EVE-inspired dark theme) ────────────────────────────────
COLORS: dict[str, str] = {
    "void_black": "#05080D",
    "deep_space": "#0B1017",
    "carbon": "#131A23",
    "steel": "#1E2A38",
    "teal": "#00C8E0",
    "teal_dim": "#0099B8",
    "gold": "#FFB800",
    "red": "#E04F4F",
    "white": "#F0F4F8",
    "grey": "#7A8A99",
    "green": "#4FE07F",
}

# ── Semantic aliases for widget styling ──────────────────────────────────────
COLORS["bg"] = COLORS["void_black"]
COLORS["panel"] = COLORS["deep_space"]
COLORS["card"] = COLORS["carbon"]
COLORS["hover"] = COLORS["steel"]

# ── Application metadata ─────────────────────────────────────────────────────
APP_NAME: str = "EveJS-Launcher-V2"
APP_TITLE: str = "EVEJS MULTIBOX LAUNCHER V2"

# ── Version ───────────────────────────────────────────────────────────────────
VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION"
APP_VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else "0.0.0"

# ── GitHub / auto-update ──────────────────────────────────────────────────────
GITHUB_REPO = "V0nCleef/evejs-launcher"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ── Network ports ────────────────────────────────────────────────────────────
class Ports(IntEnum):
    GAME = 26000
    MARKET = 26001
    PROXY = 26002


# ── Runtime status for accounts / clients ────────────────────────────────────
class Status(Enum):
    READY = "ready"
    RUNNING = "running"
    BANNED = "banned"
    NO_PROFILE = "no_profile"
    SAME_ACCOUNT_ONLINE = "same_account_online"
    ERROR = "error"


# ── Navigation pages (index into QStackedWidget) ─────────────────────────────
class Page(IntEnum):
    HOME = 0
    CHARACTERS = 1
    MODS = 2
    SETTINGS = 3
