"""Account and character discovery from EveJS SQLite database."""
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ── Solar system name cache ────────────────────────────────────────────────
_SOLAR_SYSTEM_NAMES: dict[str, dict[int, str]] | None = {}
_SOLAR_SYSTEM_NAMES_LOCK = threading.Lock()


def clear_solar_system_name_cache() -> None:
    """Discard location names cached for the previously configured EveJS root."""
    global _SOLAR_SYSTEM_NAMES
    with _SOLAR_SYSTEM_NAMES_LOCK:
        _SOLAR_SYSTEM_NAMES = {}


def _load_solar_system_names(evejs_root: str) -> dict[int, str]:
    """Build a solarSystemID → name lookup from EveJS static data."""
    global _SOLAR_SYSTEM_NAMES
    cache_key = str(Path(evejs_root).resolve())
    with _SOLAR_SYSTEM_NAMES_LOCK:
        if _SOLAR_SYSTEM_NAMES is None:
            _SOLAR_SYSTEM_NAMES = {}
        cached = _SOLAR_SYSTEM_NAMES.get(cache_key)
        if cached is not None:
            return cached
        try:
            path = (
                Path(evejs_root)
                / "_local"
                / "gameStore"
                / "data"
                / "solarSystems"
                / "data.json"
            )
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                names = {
                    system["solarSystemID"]: system["solarSystemName"]
                    for system in data.get("solarSystems", [])
                }
            else:
                names = {}
        except Exception:
            names = {}
        _SOLAR_SYSTEM_NAMES[cache_key] = names
        return names


def _resolve_location(solar_system_id: int, evejs_root: str) -> str:
    """Resolve a solarSystemID to its name; falls back to the ID as string."""
    if not solar_system_id:
        return "—"
    names = _load_solar_system_names(evejs_root)
    return names.get(solar_system_id, f"System {solar_system_id}")


def _fmt_isk(balance: int) -> str:
    """Format an ISK balance in compact form."""
    if balance >= 1_000_000_000:
        return f"{balance / 1_000_000_000:.1f}B"
    if balance >= 1_000_000:
        return f"{balance / 1_000_000:.1f}M"
    if balance >= 1_000:
        return f"{balance / 1_000:.1f}k"
    return str(balance)


def _fmt_sp(sp: int) -> str:
    """Format skill points in compact form."""
    if sp >= 1_000_000:
        return f"{sp / 1_000_000:.1f}M"
    if sp >= 1_000:
        return f"{sp / 1_000:.0f}k"
    return str(sp)


@dataclass
class Character:
    """A character belonging to an account."""
    char_id: int
    name: str
    portrait_path: str | None = None  # local file path to portrait, resolved later
    # Stats extracted from the EveJS character JSON blob.
    isk: int = 0
    skill_points: int = 0
    ship_name: str = "—"
    ship_type_id: int = 0
    location: str = "—"       # resolved solar system name (or ID as fallback)
    security_status: float = 0.0


@dataclass
class Account:
    """An EveJS account with its characters."""
    username: str
    account_id: int
    role: str
    banned: bool
    characters: list[Character] = field(default_factory=list)
    hidden: bool = False  # user has hidden this account in the launcher UI


def load_accounts(evejs_root: str) -> list[Account]:
    """Load all accounts and characters from EveJS gamestore.sqlite.

    Args:
        evejs_root: Path to the EveJS installation root.

    Returns:
        List of Account objects, sorted by username.
    """
    game_store_path = Path(evejs_root) / "_local" / "gameStore"
    return _load_accounts_from_database(
        game_store_path / "gamestore.sqlite",
        lambda solar_system_id: _resolve_location(solar_system_id, evejs_root),
    )


def load_accounts_from_game_store(game_store_path: Path) -> list[Account]:
    """Load accounts from an exact, already-verified gameStore directory."""
    game_store_path = Path(game_store_path)
    names = _load_game_store_solar_system_names(game_store_path)
    return _load_accounts_from_database(
        game_store_path / "gamestore.sqlite",
        lambda solar_system_id: names.get(
            solar_system_id,
            "—" if not solar_system_id else f"System {solar_system_id}",
        ),
    )


def _load_game_store_solar_system_names(game_store_path: Path) -> dict[int, str]:
    """Read location names from one authoritative gameStore without global cache."""
    path = game_store_path / "data" / "solarSystems" / "data.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            system["solarSystemID"]: system["solarSystemName"]
            for system in data.get("solarSystems", [])
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def _load_accounts_from_database(
    db_path: Path,
    resolve_location: Callable[[int], str],
) -> list[Account]:
    """Map one exact read-only SQLite database into launcher domain models."""
    if not db_path.exists():
        return []

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()

    # Load accounts
    accounts: dict[str, Account] = {}
    try:
        for username, json_blob in cur.execute("SELECT key, json FROM accounts"):
            data = json.loads(json_blob)
            accounts[username] = Account(
                username=username,
                account_id=data.get("id", 0),
                role=str(data.get("role", "0")),
                banned=data.get("banned", False),
            )
    except sqlite3.OperationalError:
        con.close()
        return []

    # Load characters and attach to accounts
    for char_id, json_blob in cur.execute("SELECT key, json FROM characters"):
        data = json.loads(json_blob)
        account_id = data.get("accountId")
        if account_id is not None:
            for acc in accounts.values():
                if acc.account_id == account_id:
                    acc.characters.append(Character(
                        char_id=int(char_id),
                        name=data.get("characterName", "Unknown"),
                        isk=data.get("balance", 0),
                        skill_points=data.get("skillPoints", 0),
                        ship_name=data.get("shipName", "—"),
                        ship_type_id=data.get("shipTypeID", 0),
                        security_status=data.get("securityStatus", 0.0),
                        location=resolve_location(data.get("solarSystemID", 0)),
                    ))
                    break

    con.close()

    # Sort by username
    result = sorted(accounts.values(), key=lambda a: a.username.lower())

    # Sort characters within each account by name
    for acc in result:
        acc.characters.sort(key=lambda c: c.name.lower())

    return result


def get_character_detail(evejs_root: str, char_id: int) -> dict | None:
    """Load the full character JSON blob from the database.

    Returns a dict with all character fields, or None if not found.
    """
    db_path = Path(evejs_root) / "_local" / "gameStore" / "gamestore.sqlite"
    return _get_character_detail_from_database(db_path, char_id)


def get_character_detail_from_game_store(
    game_store_path: Path,
    char_id: int,
) -> dict | None:
    """Load one character from an exact, already-verified gameStore directory."""
    return _get_character_detail_from_database(
        Path(game_store_path) / "gamestore.sqlite",
        char_id,
    )


def _get_character_detail_from_database(
    db_path: Path,
    char_id: int,
) -> dict | None:
    """Load one character JSON object from an exact read-only SQLite file."""
    if not db_path.exists():
        return None

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    try:
        for (blob,) in cur.execute("SELECT json FROM characters WHERE key = ?", (str(char_id),)):
            return json.loads(blob)
    finally:
        con.close()
    return None


def get_account_characters(evejs_root: str, username: str) -> list[Character]:
    """Get all characters for a specific account."""
    accounts = load_accounts(evejs_root)
    for acc in accounts:
        if acc.username == username:
            return acc.characters
    return []
