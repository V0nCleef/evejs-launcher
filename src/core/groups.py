"""Account groups for one-click multi-launch."""
import json
from pathlib import Path

from ..config import CONFIG_DIR

GROUPS_FILE = CONFIG_DIR / "groups.json"


def load_groups() -> dict[str, list[str]]:
    """Load groups from disk. Returns {group_name: [username, ...]}."""
    if GROUPS_FILE.exists():
        try:
            return json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_groups(groups: dict[str, list[str]]) -> None:
    """Save groups to disk."""
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(json.dumps(groups, indent=2), encoding="utf-8")


def create_group(name: str, usernames: list[str]) -> None:
    """Create or overwrite a group."""
    groups = load_groups()
    groups[name] = usernames
    save_groups(groups)


def delete_group(name: str) -> None:
    """Delete a group by name."""
    groups = load_groups()
    groups.pop(name, None)
    save_groups(groups)


def add_to_group(name: str, username: str) -> None:
    """Add an account to a group."""
    groups = load_groups()
    if name in groups and username not in groups[name]:
        groups[name].append(username)
        save_groups(groups)


def remove_from_group(name: str, username: str) -> None:
    """Remove an account from a group."""
    groups = load_groups()
    if name in groups and username in groups[name]:
        groups[name].remove(username)
        save_groups(groups)


def get_group_members(name: str) -> list[str]:
    """Get the list of usernames in a group."""
    return load_groups().get(name, [])
