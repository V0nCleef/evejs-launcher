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
    "grey": "#8F9EAD",
    "green": "#4FE07F",
}

# ── Semantic aliases for widget styling ──────────────────────────────────────
COLORS["bg"] = COLORS["void_black"]
COLORS["panel"] = COLORS["deep_space"]
COLORS["card"] = COLORS["carbon"]
COLORS["hover"] = COLORS["steel"]

# Deep Signal semantic roles.  The original palette and aliases above are kept
# intact because existing pages still consume them directly.  New UI code
# should prefer these intent-based names so a later palette pass does not
# require changing individual widgets.
SEMANTIC_COLORS: dict[str, str] = {
    "background": COLORS["void_black"],
    "background_raised": "#07111D",
    "surface": COLORS["deep_space"],
    "surface_elevated": COLORS["carbon"],
    "surface_hover": COLORS["steel"],
    "border": "#263747",
    "border_bright": "#34586A",
    "accent": COLORS["teal"],
    "accent_dim": COLORS["teal_dim"],
    "accent_soft": "#164A57",
    "warning": COLORS["gold"],
    "danger": COLORS["red"],
    "success": COLORS["green"],
    "text_primary": COLORS["white"],
    "text_secondary": "#A7B6C5",
    "text_muted": COLORS["grey"],
}

# Status widgets accept lifecycle names from both service and client domains.
# Keeping the mapping here gives custom-painted controls and QSS roles one
# authoritative colour contract without coupling them to runtime modules.
STATUS_COLORS: dict[str, str] = {
    "idle": SEMANTIC_COLORS["text_muted"],
    "offline": SEMANTIC_COLORS["text_muted"],
    "ready": SEMANTIC_COLORS["accent"],
    "starting": SEMANTIC_COLORS["warning"],
    "launching": SEMANTIC_COLORS["warning"],
    "stopping": SEMANTIC_COLORS["warning"],
    "online": SEMANTIC_COLORS["success"],
    "running": SEMANTIC_COLORS["success"],
    "degraded": SEMANTIC_COLORS["warning"],
    "unknown": SEMANTIC_COLORS["warning"],
    "failed": SEMANTIC_COLORS["danger"],
    "error": SEMANTIC_COLORS["danger"],
}

# ── Shared visual scales ─────────────────────────────────────────────────────
SPACING: dict[str, int] = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADII: dict[str, int] = {"control": 4, "card": 6, "panel": 8}
CONTROL_HEIGHTS: dict[str, int] = {"compact": 36, "action": 44}

# Additive Deep Signal scales.  The legacy dictionaries above deliberately
# retain their exact values for backwards compatibility and contract tests.
DEEP_SIGNAL_RADII: dict[str, int] = {
    "glass": 12,
    "hero": 16,
    "pill": 999,
}
MOTION_DURATIONS_MS: dict[str, int] = {
    "instant": 0,
    "fast": 140,
    "page": 180,
    "state": 260,
    "ambient": 1_800,
}
MOTION_TIMER_INTERVAL_MS: int = 50

# ── Application metadata ─────────────────────────────────────────────────────
APP_NAME: str = "EveJS-Launcher"
APP_TITLE: str = "EVEJS LAUNCHER V1"

# ── Version ───────────────────────────────────────────────────────────────────
VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION"
APP_VERSION = VERSION_PATH.read_text().strip() if VERSION_PATH.exists() else "0.0.0"

# ── GitHub / auto-update ──────────────────────────────────────────────────────
GITHUB_REPO = "V0nCleef/evejs-launcher"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ── Network ports ────────────────────────────────────────────────────────────
class Ports(IntEnum):
    GAME_TCP = 26000
    GAME_MARKET_PROXY = 26001
    CLIENT_HTTP_PROXY = 26002
    MARKET_HTTP = 40110
    MARKET_RPC = 40111


# ── Runtime status for accounts / clients ────────────────────────────────────
class Status(Enum):
    READY = "ready"
    LAUNCHING = "launching"
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
    TOOLS = 3
    SETTINGS = 4
