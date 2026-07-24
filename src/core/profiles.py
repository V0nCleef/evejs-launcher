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

    Copies ALL EVE settings from the real client on first creation so the
    EVE client can render its window immediately (proven essential — template
    files alone cause silent crashes for some accounts when other clients
    are already running).

    Args:
        username: Account username (used as profile folder name).
        real_client_path: Path to the real EVE client's tq folder.

    Returns:
        Path to the profile directory.
    """
    profile_dir = PROFILES_ROOT / username
    profile_dir.mkdir(parents=True, exist_ok=True)

    junction = profile_dir / "tq"
    if not junction.exists():
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

    # ── Bootstrap EVE settings from real client (or template fallback) ──
    try:
        _bootstrap_settings(username, real_client_path)
    except Exception:
        pass  # non-fatal — username pre-fill will still run

    return profile_dir


def _bootstrap_settings(username: str, real_client_path: str = "") -> None:
    """Copy EVE settings from the real client to the new profile.

    Copies ALL files (not just template) because the EVE client needs the
    full set of .dat files to initialize when other clients are running.
    Falls back to shipped template files if real client settings don't exist.
    """
    import shutil

    try:
        dst_dir = get_profile_settings_path(username)
    except FileNotFoundError:
        return

    dst_dir.mkdir(parents=True, exist_ok=True)

    # ── Try to copy from the real client's settings first ────────────
    if real_client_path:
        real_key = get_settings_key(real_client_path)
        real_settings = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "CCP" / "EVE" / real_key / "settings"
        )
        if real_settings.exists():
            for src in real_settings.iterdir():
                dst = dst_dir / src.name
                if src.is_file() and not dst.exists():
                    shutil.copy2(src, dst)
                elif src.is_dir() and src.name == "Browser" and not dst.exists():
                    shutil.copytree(src, dst)
            return  # real client copy succeeded — done

    # ── Fallback: template files shipped with the launcher ───────────
    template_dir = Path(__file__).resolve().parent / "template_settings"
    if not template_dir.exists():
        return

    for name in ("prefs.ini", "core_public__.yaml"):
        src = template_dir / name
        dst = dst_dir / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)


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
    the login screen, even for accounts that have never been launched before.

    Also ensures ``newbie=0`` in ``prefs.ini`` and bootstraps a complete
    ``core_public__.yaml`` with ``audio:``, ``device:``, and ``ui:`` sections
    so the EVE client renders the full login screen correctly.
    """
    try:
        settings_dir = get_profile_settings_path(username)
    except FileNotFoundError:
        return  # profile not created yet

    settings_dir.mkdir(parents=True, exist_ok=True)

    # ── prefs.ini: ensure newbie=0 so the client skips the setup wizard ──
    prefs_path = settings_dir / "prefs.ini"
    if prefs_path.exists():
        prefs_text = prefs_path.read_text(encoding="utf-8", errors="replace")
    else:
        prefs_text = ""
    if "newbie=1" in prefs_text:
        prefs_text = prefs_text.replace("newbie=1", "newbie=0")
        prefs_path.write_text(prefs_text, encoding="utf-8")
    elif "newbie=" not in prefs_text:
        prefs_text += "\nnewbie=0\n"
        prefs_path.write_text(prefs_text, encoding="utf-8")

    # ── core_public__.yaml: bootstrap from template, then set username ──
    yaml_path = settings_dir / "core_public__.yaml"
    template_dir = Path(__file__).resolve().parent / "template_settings"
    template_yaml = template_dir / "core_public__.yaml"

    if not yaml_path.exists() and template_yaml.exists():
        # First launch — copy the complete template (audio + device + ui sections)
        import shutil
        shutil.copy2(template_yaml, yaml_path)

    import time
    ts = int(time.time() * 10_000_000)  # EVE uses 100-nanosecond intervals

    # Patch the username under the ``ui:`` section
    if yaml_path.exists():
        text = yaml_path.read_text(encoding="utf-8", errors="replace")
        # Replace placeholder or existing username value
        text = re.sub(
            r"(  username: )\[.*?\]",
            f"\\1[{ts}, {username}]",
            text,
        )
        # Replace placeholder or existing usernames block
        text = re.sub(
            r"(  usernames:\n)(  - .*\n  - .*\n)?",
            f"\\1  - {ts}\n  - [{username}]\n",
            text,
        )
        yaml_path.write_text(text, encoding="utf-8")


def list_profiles() -> list[str]:
    """List all profile names that have junctions."""
    if not PROFILES_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROFILES_ROOT.iterdir()
        if d.is_dir() and (d / "tq").exists()
    ])
