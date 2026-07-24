"""Linux platform implementations for the EveJS Launcher.

Uses Proton/Wine to run the EVE client, symlinks for profile isolation,
and wmctrl/xdotool for window management.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# Subprocess flags
# ═══════════════════════════════════════════════════════════════════════════

def get_client_process_flags() -> dict[str, bool]:
    """Popen kwargs for spawning the EVE client via Proton.

    ``start_new_session=True`` is the POSIX equivalent of
    ``DETACHED_PROCESS`` — the child gets its own process group and
    survives the parent exiting.
    """
    return {"start_new_session": True}


def get_hidden_process_flags() -> dict:
    """No-op on Linux — there are no console windows to hide."""
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# Profile links (symbolic links)
# ═══════════════════════════════════════════════════════════════════════════

def create_directory_link(target: Path, link: Path) -> None:
    """Create a symbolic link (Linux equivalent of a directory junction).

    Uses ``Path.symlink_to()`` — no special privileges needed.
    """
    link.symlink_to(target, target_is_directory=True)


def remove_directory_link(link: Path) -> None:
    """Remove a symbolic link (safe — only the link is removed, not the target)."""
    if link.is_symlink() or link.exists():
        link.unlink()


# ═══════════════════════════════════════════════════════════════════════════
# EVE client paths
# ═══════════════════════════════════════════════════════════════════════════

def get_client_exe_name() -> str:
    """EVE client executable name — same on all platforms (Windows exe)."""
    return "exefile.exe"


def get_market_binary_name() -> str:
    """Market server binary name — no ``.exe`` extension on Linux."""
    return "market-server"


def get_client_exe_path(profile_tq_path: Path) -> Path:
    """Full path to the EVE client executable inside a profile."""
    return profile_tq_path / "bin64" / get_client_exe_name()


# ═══════════════════════════════════════════════════════════════════════════
# EVE settings path (under Proton/Wine prefix)
# ═══════════════════════════════════════════════════════════════════════════

#: Environment variable for a custom Proton prefix.
#: Set automatically by Steam when launching via Proton; can also be set
#: manually for standalone Proton usage.
_ENV_PROTON_PREFIX = "STEAM_COMPAT_DATA_PATH"

#: Default Proton prefix used by the community ``evejs-play.sh`` launcher.
_DEFAULT_PROTON_PREFIX = Path.home() / ".evejs-proton"


def get_eve_settings_path(client_install_path: str) -> Path:
    """Resolve the EVE settings directory under the Proton/Wine prefix.

    On Linux, EVE runs inside Proton (Wine), so its settings live under
    the virtual Windows filesystem inside the Proton prefix:

        ``<prefix>/drive_c/users/steamuser/Local Settings/Application Data/CCP/EVE/<key>/settings/``

    The *client_install_path* must be a **Wine-style path** (e.g.
    ``Z:\\home\\user\\.evejs-proton\\...``) so the settings key derivation
    matches what the EVE client computes internally.
    """
    key = _derive_settings_key(client_install_path)
    prefix = _resolve_proton_prefix()
    user = os.environ.get("USER", "steamuser")
    return (
        prefix
        / "drive_c"
        / "users"
        / user
        / "Local Settings"
        / "Application Data"
        / "CCP"
        / "EVE"
        / key
        / "settings"
    )


def _derive_settings_key(install_path: str) -> str:
    """Replicate EVE's ``settingsKey`` derivation for Wine paths.

    EVE lowercases the path, strips the drive letter colon, and replaces
    directory separators with underscores.  This works the same way on
    Linux/Wine as on native Windows.
    """
    key = install_path.lower()
    key = key.replace(":", "")
    key = key.replace("\\", "_").replace("/", "_")
    key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    key = key.strip("_")
    return f"{key}_127.0.0.1"


def _resolve_proton_prefix() -> Path:
    """Find the EveJS Proton prefix.

    Checks (in order):
    1. ``STEAM_COMPAT_DATA_PATH`` env var (set by Steam Proton)
    2. ``~/.evejs-proton`` (community convention from evejs-play.sh)
    3. ``~/.wine`` (system Wine default)
    """
    env_prefix = os.environ.get(_ENV_PROTON_PREFIX, "")
    if env_prefix and Path(env_prefix).exists():
        return Path(env_prefix)

    if _DEFAULT_PROTON_PREFIX.exists():
        return _DEFAULT_PROTON_PREFIX

    wine_prefix = Path(os.environ.get("WINEPREFIX", Path.home() / ".wine"))
    return wine_prefix


# ═══════════════════════════════════════════════════════════════════════════
# Proton detection
# ═══════════════════════════════════════════════════════════════════════════

def find_proton() -> Path | None:
    """Locate a Proton installation usable for running the EVE client.

    Checks common locations:
    1. ``STEAM_COMPAT_TOOL_PATHS`` (set by community scripts)
    2. ``~/.steam/steam/compatibilitytools.d/`` (GE-Proton, custom builds)
    3. ``~/.local/share/Steam/compatibilitytools.d/`` (Flatpak Steam)
    4. ``~/.var/app/com.valvesoftware.Steam/...`` (Flatpak)

    Returns the directory containing ``proton`` (the runner script), or
    ``None`` if no Proton installation was found.
    """
    # 1. Explicit env override (set by evejs-play.sh style launchers)
    env_path = os.environ.get("STEAM_COMPAT_TOOL_PATHS", "")
    if env_path:
        for d in env_path.split(":"):
            candidate = Path(d.strip())
            if (candidate / "proton").exists():
                return candidate

    # 2. Steam compatibility tools (GE-Proton, etc.)
    candidates = [
        Path.home() / ".steam" / "steam" / "compatibilitytools.d",
        Path.home() / ".local" / "share" / "Steam" / "compatibilitytools.d",
        Path.home() / ".var" / "app" / "com.valvesoftware.Steam"
        / ".local" / "share" / "Steam" / "compatibilitytools.d",
    ]

    for base in candidates:
        if not base.is_dir():
            continue
        # Prefer GE-Proton, then any Proton build
        for child in sorted(base.iterdir(), reverse=True):
            if child.is_dir() and (child / "proton").exists():
                return child

    return None


def get_proton_env(proton_dir: Path, proton_prefix: Path) -> dict[str, str]:
    """Build the environment variables needed to launch via Proton.

    Mirrors the setup in the community ``evejs-play.sh`` (lines 506-517).
    """
    return {
        "STEAM_COMPAT_DATA_PATH": str(proton_prefix),
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(
            Path.home() / ".steam" / "steam"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Window detection (wmctrl / xdotool)
# ═══════════════════════════════════════════════════════════════════════════

def find_eve_window(title: str = "EVE") -> bool:
    """Check whether an EVE client window is currently visible.

    Uses ``wmctrl -l`` to list all windows.  Falls back to ``xdotool``
    if wmctrl is not installed.
    """
    # wmctrl
    if _which("wmctrl"):
        try:
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if title.lower() in line.lower():
                    return True
        except (subprocess.TimeoutExpired, OSError):
            pass
        return False

    # xdotool fallback
    if _which("xdotool"):
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title],
                capture_output=True, text=True, timeout=5,
            )
            return bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError):
            pass

    return False


def find_and_focus_eve_window(title: str = "EVE") -> bool:
    """Find the EVE client window and bring it to the foreground.

    Tries ``wmctrl -a`` first (simplest), then ``xdotool``.
    Returns True if the window was found and focused.
    """
    # wmctrl — ``-a`` activates the first matching window
    if _which("wmctrl"):
        try:
            subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    # xdotool
    if _which("xdotool"):
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                wid = result.stdout.strip().splitlines()[0]
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wid],
                    timeout=5,
                )
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    return False


# ═══════════════════════════════════════════════════════════════════════════
# Proton EVE client launch
# ═══════════════════════════════════════════════════════════════════════════

def launch_eve_client(exe_path: Path, env: dict[str, str], cwd: Path) -> subprocess.Popen:
    """Launch the EVE client through Proton (Linux).

    Auto-detects the Proton installation and prefix.  The *cwd* parameter
    is ignored on Linux — Proton manages the working directory internally.
    """
    proton = find_proton()
    if proton is None:
        raise RuntimeError(
            "Proton not found. Install GE-Proton via ProtonUp-Qt or place it in "
            "~/.steam/steam/compatibilitytools.d/\n"
            "See https://github.com/GloriousEggroll/proton-ge-custom"
        )
    prefix = _resolve_proton_prefix()
    return launch_client_via_proton(exe_path, proton, prefix, env)


def launch_client_via_proton(
    exe_path: Path,
    proton_dir: Path,
    proton_prefix: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Launch the EVE client through Proton.

    Parameters
    ----------
    exe_path:
        Path to ``exefile.exe`` (inside the profile symlink).
    proton_dir:
        Directory containing the ``proton`` runner script
        (e.g. ``~/.steam/steam/compatibilitytools.d/GE-Proton10-32``).
    proton_prefix:
        Proton/Wine prefix directory (e.g. ``~/.evejs-proton``).
    extra_env:
        Additional environment variables merged on top of the defaults
        (proxy settings, CA cert, LaunchDarkly off, etc.).

    Returns
    -------
    subprocess.Popen
        The launched process.  The caller is responsible for monitoring
        and termination.
    """
    proton_bin = proton_dir / "proton"
    if not proton_bin.exists():
        raise FileNotFoundError(f"Proton runner not found: {proton_bin}")

    env = os.environ.copy()
    env.update(get_proton_env(proton_dir, proton_prefix))
    if extra_env:
        env.update(extra_env)

    return subprocess.Popen(
        [str(proton_bin), "run", str(exe_path)],
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ═══════════════════════════════════════════════════════════════════════════
# File helpers
# ═══════════════════════════════════════════════════════════════════════════

def open_text_editor(file_path: Path) -> None:
    """Open *file_path* in the user's preferred text editor.

    Respects ``$EDITOR``; falls back to ``xdg-open`` which delegates to
    the desktop's default handler.
    """
    editor = os.environ.get("EDITOR", "")
    if editor:
        subprocess.Popen([editor, str(file_path)], start_new_session=True)
    else:
        subprocess.Popen(
            ["xdg-open", str(file_path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def get_exe_file_filter() -> str:
    """Qt file-dialog filter string — Linux has no standard extension convention."""
    return "All Files (*)"


# ═══════════════════════════════════════════════════════════════════════════
# Process
# ═══════════════════════════════════════════════════════════════════════════

def hard_exit() -> None:
    """Immediate hard exit — no atexit, no Python cleanup."""
    os._exit(0)


# ═══════════════════════════════════════════════════════════════════════════
# Auto-updater  (bash helper script via nohup)
# ═══════════════════════════════════════════════════════════════════════════

def run_updater(download_url: str, current_exe_path: Path) -> bool:
    """Download + replace the launcher via a self-removing bash helper.

    The helper handles the file-lock retry loop and restart in the
    background — the caller should call :func:`hard_exit` after this
    returns ``True``.
    """
    import tempfile

    from ..updater.github import download_asset

    current_exe_path = Path(current_exe_path)
    temp_dir = Path(tempfile.gettempdir())
    new_exe_path = temp_dir / "evejs_launcher_update"

    # 1. Download the new binary
    ok = download_asset(download_url, new_exe_path)
    if not ok:
        return False

    # 2. Make it executable
    try:
        new_exe_path.chmod(0o755)
    except OSError:
        return False

    # 3. Write the helper script
    helper_path = temp_dir / "evejs_launcher_update_helper.sh"
    helper_script = f"""#!/usr/bin/env bash
set -euo pipefail
NEW_EXE={_sh_quote(str(new_exe_path))}
CURRENT_EXE={_sh_quote(str(current_exe_path))}
sleep 2
mv "$NEW_EXE" "$CURRENT_EXE"
chmod +x "$CURRENT_EXE"
nohup "$CURRENT_EXE" >/dev/null 2>&1 &
disown
rm -f "$0"
"""
    helper_path.write_text(helper_script)
    helper_path.chmod(0o755)

    # 4. Spawn detached
    try:
        subprocess.Popen(
            ["bash", str(helper_path)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except OSError:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _which(cmd: str) -> bool:
    """Return True if *cmd* is on PATH."""
    import shutil
    return shutil.which(cmd) is not None


def _sh_quote(s: str) -> str:
    """Minimal shell quoting for helper scripts."""
    return "'" + s.replace("'", "'\\''") + "'"
