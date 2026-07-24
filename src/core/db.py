"""Account and character discovery from EveJS SQLite database."""
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


# ── Solar system name cache ────────────────────────────────────────────────
_SOLAR_SYSTEM_NAMES: dict[int, str] | None = None


def _load_solar_system_names(evejs_root: str) -> dict[int, str]:
    """Build a solarSystemID → name lookup from EveJS static data."""
    global _SOLAR_SYSTEM_NAMES
    if _SOLAR_SYSTEM_NAMES is not None:
        return _SOLAR_SYSTEM_NAMES
    try:
        path = Path(evejs_root) / "_local" / "gameStore" / "data" / "solarSystems" / "data.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            _SOLAR_SYSTEM_NAMES = {
                s["solarSystemID"]: s["solarSystemName"]
                for s in data.get("solarSystems", [])
            }
        else:
            _SOLAR_SYSTEM_NAMES = {}
    except Exception:
        _SOLAR_SYSTEM_NAMES = {}
    return _SOLAR_SYSTEM_NAMES


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
    db_path = Path(evejs_root) / "_local" / "gameStore" / "gamestore.sqlite"
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
                        location=_resolve_location(
                            data.get("solarSystemID", 0), evejs_root,
                        ),
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
    import json, sqlite3
    db_path = Path(evejs_root) / "_local" / "gameStore" / "gamestore.sqlite"
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
