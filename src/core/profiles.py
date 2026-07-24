"""Profile management via directory junctions.

Each account gets a junction pointing to the real EVE client install.
This gives each account a unique path → unique settings folder on first launch.
"""
import os
import re
import subprocess
from pathlib import Path

from ..config import CONFIG_DIR

PROFILES_ROOT = CONFIG_DIR / "Profiles"


def get_settings_key(client_path: str) -> str:
    """Replicate settingsKey derivation from PrepareClientSettings.ps1.

    The settings folder is keyed by the executable's install path.
    Each junction has a unique path → unique settings.
    """
    key = client_path.lower()
    key = key.replace(":", "")
    key = key.replace("\\", "_").replace("/", "_")
    # Keep only alphanumeric, dots, underscores, hyphens
    key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    key = key.strip("_")
    return f"{key}_127.0.0.1"


def create_profile(username: str, real_client_path: str) -> Path:
    """Create a junction profile for the given account.

    Args:
        username: Account username (used as profile folder name).
        real_client_path: Path to the real EVE client's tq folder.

    Returns:
        Path to the profile directory.
    """
    profile_dir = PROFILES_ROOT / username
    profile_dir.mkdir(parents=True, exist_ok=True)

    junction = profile_dir / "tq"
    if junction.exists():
        return profile_dir

    # mklink /J creates a directory junction (no admin required on Win10+)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(Path(real_client_path))],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create junction for {username}: {result.stderr.strip()}"
        )

    return profile_dir


def delete_profile(username: str) -> None:
    """Remove the profile junction. Settings in LOCALAPPDATA are NOT deleted."""
    profile_dir = PROFILES_ROOT / username
    junction = profile_dir / "tq"

    if junction.exists():
        # rmdir on a junction removes the junction link, NOT the target
        subprocess.run(["cmd", "/c", "rmdir", str(junction)], check=True)

    # Remove empty profile dir (only if empty after junction removal)
    if profile_dir.exists():
        try:
            profile_dir.rmdir()
        except OSError:
            pass  # directory not empty, leave it


def profile_exists(username: str) -> bool:
    """Check if a profile junction exists for this account."""
    junction = PROFILES_ROOT / username / "tq"
    return junction.exists()


def get_profile_client_path(username: str) -> Path | None:
    """Get the junction path used to launch the client for this profile."""
    junction = PROFILES_ROOT / username / "tq"
    return junction if junction.exists() else None


def get_profile_settings_path(username: str) -> Path:
    """Get the expected EVE settings folder for this profile."""
    junction = PROFILES_ROOT / username / "tq"
    if not junction.exists():
        raise FileNotFoundError(f"Profile junction does not exist: {username}")
    key = get_settings_key(str(junction))
    return Path(os.environ.get("LOCALAPPDATA", "")) / "CCP" / "EVE" / key / "settings"


def prefill_username(username: str) -> None:
    """Write the username to the EVE client settings so it's pre-filled on
    the login screen, even for accounts that have never been launched before."""
    try:
        settings_dir = get_profile_settings_path(username)
    except FileNotFoundError:
        return  # profile not created yet

    settings_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = settings_dir / "core_public__.yaml"

    # Read existing YAML or start fresh
    if yaml_path.exists():
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
    else:
        text = "generic: {}\n"

    # The EVE client stores the last username like:
    #   username: [timestamp, Voncleef]
    #   usernames: [timestamp, [Voncleef]]
    import time
    ts = int(time.time() * 10_000_000)  # EVE uses 100-nanosecond intervals

    if "username:" in text:
        text = re.sub(r"username: \[.*?\]", f"username: [{ts}, {username}]", text)
    else:
        text += f"\nusername: [{ts}, {username}]"

    yaml_path.write_text(text, encoding="utf-8")


def list_profiles() -> list[str]:
    """List all profile names that have junctions."""
    if not PROFILES_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROFILES_ROOT.iterdir()
        if d.is_dir() and (d / "tq").exists()
    ])
