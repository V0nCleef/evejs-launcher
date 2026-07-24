"""Windows platform implementations for the EveJS Launcher.

Extracted from the original codebase — these are the native Windows
paths that were previously inline in launcher.py, profiles.py, etc.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# Subprocess flags
# ═══════════════════════════════════════════════════════════════════════════

def get_client_process_flags() -> dict[str, int]:
    """Popen kwargs for spawning the EVE client (detached, no console)."""
    return {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS,
    }


def launch_eve_client(exe_path: Path, env: dict[str, str], cwd: Path) -> subprocess.Popen:
    """Launch the EVE client executable directly (native Windows)."""
    return subprocess.Popen(
        [str(exe_path)],
        env=env,
        cwd=str(cwd),
        **get_client_process_flags(),
    )


def get_hidden_process_flags() -> dict[str, int]:
    """Popen kwargs for background server processes (no console window)."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


# ═══════════════════════════════════════════════════════════════════════════
# Profile links (directory junctions)
# ═══════════════════════════════════════════════════════════════════════════

def create_directory_link(target: Path, link: Path) -> None:
    """Create a directory junction (``mklink /J`` — no admin required)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create junction at {link}: {result.stderr.strip()}"
        )


def remove_directory_link(link: Path) -> None:
    """Remove a directory junction (``rmdir`` — safe, doesn't delete target)."""
    if link.exists():
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=True)


# ═══════════════════════════════════════════════════════════════════════════
# EVE client paths
# ═══════════════════════════════════════════════════════════════════════════

def get_client_exe_name() -> str:
    return "exefile.exe"


def get_market_binary_name() -> str:
    return "market-server.exe"


def get_client_exe_path(profile_tq_path: Path) -> Path:
    """Full path to the EVE client executable inside a profile."""
    return profile_tq_path / "bin64" / get_client_exe_name()


# ═══════════════════════════════════════════════════════════════════════════
# EVE settings path (under %LOCALAPPDATA%/CCP/EVE/<key>/settings/)
# ═══════════════════════════════════════════════════════════════════════════

def get_eve_settings_path(client_install_path: str) -> Path:
    """Resolve the EVE settings directory for a given install path.

    EVE derives a ``settingsKey`` from the executable path, so a unique
    junction path yields a unique settings folder — this is how per-account
    profile isolation works.
    """
    key = _derive_settings_key(client_install_path)
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    return Path(local_appdata) / "CCP" / "EVE" / key / "settings"


def _derive_settings_key(install_path: str) -> str:
    """Mirror EVE's ``PrepareClientSettings.ps1`` key derivation."""
    key = install_path.lower()
    key = key.replace(":", "")
    key = key.replace("\\", "_").replace("/", "_")
    key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    key = key.strip("_")
    return f"{key}_127.0.0.1"


# ═══════════════════════════════════════════════════════════════════════════
# Window detection (pygetwindow)
# ═══════════════════════════════════════════════════════════════════════════

def find_eve_window(title: str = "EVE") -> bool:
    """Check whether an EVE client window is currently visible."""
    try:
        import pygetwindow as gw

        for win in gw.getAllWindows():
            if win.title == title and win.width > 200 and win.height > 200:
                return True
    except ImportError:
        pass
    return False


def find_and_focus_eve_window(title: str = "EVE") -> bool:
    """Find the EVE client window and bring it to the foreground.

    Returns True if a matching window was found and focused.
    """
    try:
        import pygetwindow as gw
    except ImportError:
        return False

    for win in gw.getAllWindows():
        if title.lower() in win.title.lower() and win.width > 200:
            try:
                if win.isMinimized:
                    win.restore()
                win.activate()
                return True
            except Exception:
                pass
    return False


# ═══════════════════════════════════════════════════════════════════════════
# File helpers
# ═══════════════════════════════════════════════════════════════════════════

def open_text_editor(file_path: Path) -> None:
    """Open *file_path* in Notepad."""
    subprocess.Popen(["notepad.exe", str(file_path)])


def get_exe_file_filter() -> str:
    """Qt file-dialog filter string for executables."""
    return "Executables (*.exe);;All Files (*)"


# ═══════════════════════════════════════════════════════════════════════════
# Process
# ═══════════════════════════════════════════════════════════════════════════

def hard_exit() -> None:
    """Immediate hard exit — no atexit, no Python cleanup.

    Necessary after spawning the updater helper to avoid transient
    ``Failed to load Python DLL`` dialogs during PyInstaller shutdown.
    """
    os._exit(0)


# ═══════════════════════════════════════════════════════════════════════════
# Auto-updater  (VBScript helper via wscript.exe)
# ═══════════════════════════════════════════════════════════════════════════

def run_updater(download_url: str, current_exe_path: Path) -> bool:
    """Download + replace the launcher via a silent VBScript helper.

    The helper handles the file-lock retry loop and restart in the
    background — the caller should call :func:`hard_exit` after this
    returns ``True``.
    """
    import tempfile

    from .platform_win import _verify_exe_integrity
    from ..updater.github import download_asset

    current_exe_path = Path(current_exe_path)
    temp_dir = Path(tempfile.gettempdir())
    new_exe_path = temp_dir / "evejs_launcher_update.exe"

    # 1. Download
    ok = download_asset(download_url, new_exe_path)
    if not ok:
        return False

    # 2. Verify integrity
    try:
        _verify_exe_integrity(new_exe_path)
    except ValueError:
        try:
            new_exe_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    # 3. Locate the VBS helper
    helper_source = _find_vbs_helper()
    if helper_source is None:
        return False

    helper_dest = temp_dir / "evejs_launcher_update_helper.vbs"
    helper_dest.write_text(helper_source, encoding="utf-8")

    # 4. Spawn detached
    try:
        subprocess.Popen(
            [
                "wscript.exe",
                str(helper_dest),
                str(current_exe_path),
                str(new_exe_path),
                "--restart",
            ],
            creationflags=subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except OSError:
        return False


# ── Updater helpers ──────────────────────────────────────────────────────

# Minimum size for a valid PyInstaller .exe (4 KiB — anything smaller is
# definitely corrupt / a GitHub error page).
_MIN_EXE_SIZE: int = 4096
# The Python DLL that MUST be bundled inside the CArchive.
_REQUIRED_DLL: bytes = b"python311.dll"


def _verify_exe_integrity(exe_path: Path) -> None:
    """Raise ValueError if *exe_path* looks invalid or corrupt.

    Static checks only (no process spawning):
    1. File exists and is above minimum size.
    2. Starts with the Windows PE magic ``MZ``.
    3. Contains at least two references to ``python311.dll``.
    """
    if not exe_path.is_file():
        raise ValueError(f"Downloaded file missing: {exe_path}")

    file_size = exe_path.stat().st_size
    if file_size < _MIN_EXE_SIZE:
        raise ValueError(
            f"Downloaded file too small ({file_size} bytes) — likely not an executable."
        )

    try:
        with open(exe_path, "rb") as fh:
            header = fh.read(2)
        if header[:2] != b"MZ":
            raise ValueError(
                "Downloaded file is not a valid Windows executable (missing MZ header)."
            )
    except OSError as exc:
        raise ValueError(f"Could not read downloaded file: {exc}") from exc

    count = 0
    with open(exe_path, "rb") as fh:
        chunk_size = 1 << 20  # 1 MiB
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            count += chunk.count(_REQUIRED_DLL)
            if count >= 2:
                return

    raise ValueError(
        f"Downloaded .exe contains only {count} reference(s) to "
        f"{_REQUIRED_DLL.decode()} (expected ≥2).  "
        "The bootloader may be corrupted.  Aborting update."
    )


def _find_vbs_helper() -> str | None:
    """Locate ``update_helper.vbs`` — frozen or source."""
    # Frozen by PyInstaller → bundled data extracted to sys._MEIPASS
    if getattr(sys, "frozen", False):
        candidate = Path(sys._MEIPASS) / "update_helper.vbs"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")

    # Source run — look relative to this file
    candidate = Path(__file__).resolve().parent.parent.parent / "update_helper.vbs"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")

    # Last resort: CWD
    candidate = Path.cwd() / "update_helper.vbs"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")

    return None
