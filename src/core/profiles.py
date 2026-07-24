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

    Also bootstraps the EVE settings directory with template files
    (``core_user__.dat``, ``core_char__.dat``, ``prefs.ini``) so the
    EVE client can render its login window on first launch.  Without
    these bootstrap files the DirectX window never materialises.

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

    # ── Bootstrap EVE settings with template files ──────────────────
    try:
        _bootstrap_settings(username)
    except Exception:
        pass  # non-fatal — username pre-fill will still run

    return profile_dir


def _bootstrap_settings(username: str) -> None:
    """Copy template EVE settings files so the login window renders on first launch."""
    import shutil

    try:
        settings_dir = get_profile_settings_path(username)
    except FileNotFoundError:
        return

    settings_dir.mkdir(parents=True, exist_ok=True)

    # Template files shipped with the launcher
    template_dir = Path(__file__).resolve().parent / "template_settings"
    if not template_dir.exists():
        return

    for name in ("core_user__.dat", "core_char__.dat", "prefs.ini"):
        src = template_dir / name
        dst = settings_dir / name
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

    Also ensures ``newbie=0`` in ``prefs.ini`` so the EVE client shows the
    normal login screen instead of the first-run setup wizard (EULA, graphics
    config) which can fail silently under the EveJS proxy.
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

    # ── core_public__.yaml: pre-fill username under the ui: section ─────
    # The EVE client stores username under the ``ui:`` key, NOT at the top
    # level.  Writing it at the top level gets silently dropped when the
    # client rewrites the file on startup.
    yaml_path = settings_dir / "core_public__.yaml"

    if yaml_path.exists():
        lines = yaml_path.read_text(encoding="utf-8", errors="replace").splitlines(True)
    else:
        lines = ["generic: {}\n", "ui:\n"]

    import time
    ts = int(time.time() * 10_000_000)  # EVE uses 100-nanosecond intervals

    # Find or create the ``ui:`` section and insert/update ``username:``
    in_ui = False
    ui_indent = "  "
    found_username = False
    found_usernames = False
    result: list[str] = []
    username_line = f"{ui_indent}username: [{ts}, {username}]\n"
    usernames_line = f"{ui_indent}usernames:\n"

    for line in lines:
        stripped = line.lstrip()
        # Track when we enter/leave the ui: section
        if stripped.startswith("ui:") or stripped.startswith('"ui":'):
            in_ui = True
            result.append(line)
            continue
        if in_ui and not line.startswith((" ", "\t")) and stripped:
            # Left the ui: section (non-indented, non-empty line)
            # Insert username/usernames if not yet found
            if not found_username:
                result.append(username_line)
            if not found_usernames:
                # Write usernames block
                result.append(usernames_line)
                result.append(f"{ui_indent}- {ts}\n")
                result.append(f"{ui_indent}- [{username}]\n")
            in_ui = False

        if in_ui and stripped.startswith("username:"):
            result.append(username_line)
            found_username = True
            continue
        if in_ui and stripped.startswith("usernames:"):
            result.append(usernames_line)
            found_usernames = True
            continue
        # Skip old usernames list entries (indented list items under usernames:)
        if found_usernames and in_ui and line.startswith(f"{ui_indent}-"):
            continue
        # Reset usernames flag when we hit another key
        if found_usernames and in_ui and stripped and not line.startswith(f"{ui_indent}-"):
            found_usernames = False

        result.append(line)

    # If we ended inside ui: section, append username/usernames
    if in_ui and not found_username:
        result.append(username_line)
    if in_ui and not found_usernames:
        result.append(usernames_line)
        result.append(f"{ui_indent}- {ts}\n")
        result.append(f"{ui_indent}- [{username}]\n")

    # If no ui: section exists at all, add one
    if not any("ui:" in l for l in result):
        result.append("\nui:\n")
        result.append(username_line)
        result.append(usernames_line)
        result.append(f"{ui_indent}- {ts}\n")
        result.append(f"{ui_indent}- [{username}]\n")

    yaml_path.write_text("".join(result), encoding="utf-8")


def list_profiles() -> list[str]:
    """List all profile names that have junctions."""
    if not PROFILES_ROOT.exists():
        return []
    return sorted([
        d.name for d in PROFILES_ROOT.iterdir()
        if d.is_dir() and (d / "tq").exists()
    ])
