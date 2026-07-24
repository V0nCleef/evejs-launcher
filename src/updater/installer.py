"""Download + replace flow for self-updating the launcher.

Downloads the new ``.exe`` to a temp location, writes a helper script to
``%TEMP%``, spawns it detached, and returns control so the caller can exit.
The helper script handles the file-lock retry loop and restart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from src.updater.github import download_asset

# Minimum size for a valid PyInstaller .exe (4 KiB — anything smaller is
# definitely corrupt / a GitHub error page).
_MIN_EXE_SIZE: int = 4096

# The Python DLL that MUST be bundled inside the CArchive.  We verify its
# presence with a static scan — no process spawning.
_REQUIRED_DLL: bytes = b"python311.dll"


def _verify_exe_integrity(exe_path: Path) -> None:
    """Raise :class:`ValueError` if *exe_path* looks invalid or corrupt.

    Performs **static** checks only (no process spawning):
    1. File exists and is above minimum size.
    2. Starts with the Windows PE magic ``MZ``.
    3. Contains at least two references to ``python311.dll`` — one in the
       bootloader import table and one in the CArchive metadata.  A single
       missing reference means the exe cannot load Python.
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

    # Scan for python311.dll.  A healthy PyInstaller exe has 3+ references:
    #   • bootloader import table (LoadLibrary target)
    #   • CArchive TOC entry (bundled file metadata)
    #   • CArchive string table
    # We require at least 2 to guard against false positives from a single
    # stray string match.
    count = 0
    with open(exe_path, "rb") as fh:
        chunk_size = 1 << 20  # 1 MiB
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            count += chunk.count(_REQUIRED_DLL)
            if count >= 2:
                return  # early exit — we have enough evidence

    raise ValueError(
        f"Downloaded .exe contains only {count} reference(s) to "
        f"{_REQUIRED_DLL.decode()} (expected ≥2).  "
        "The bootloader may be corrupted.  Aborting update."
    )

# The helper VBScript is shipped at the repo root; we read it at runtime
# and write a copy to %TEMP% so it can outlive the launcher process.
# When frozen by PyInstaller, bundled data is extracted to sys._MEIPASS.
if getattr(sys, "frozen", False):
    _HELPER_SOURCE = Path(sys._MEIPASS) / "update_helper.vbs"
else:
    _HELPER_SOURCE = Path(__file__).resolve().parent.parent.parent / "update_helper.vbs"


def download_and_install(
    download_url: str,
    current_exe_path: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bool:
    """Download the new ``.exe`` and hand off to the update helper.

    1. Downloads the asset to a temp ``.exe`` file.
    2. Copies (or symlinks) the ``update_helper.py`` script into ``%TEMP%``.
    3. Spawns the helper via ``subprocess.Popen`` with ``DETACHED_PROCESS``
       so it survives the launcher exiting.
    4. Returns *True* — the caller should then call ``sys.exit(0)`` or
       equivalent to let the helper take over.

    Parameters
    ----------
    download_url:
        Direct URL of the ``.exe`` asset.
    current_exe_path:
        Path to the currently-running launcher ``.exe`` (typically
        ``sys.executable`` or the PyInstaller-frozen executable).
    progress_callback:
        Forwarded to :func:`github.download_asset`.

    Returns
    -------
    bool
        *True* when the download succeeds and the helper has been launched.
        *False* when the download itself fails (caller should report the
        error to the user rather than exiting).
    """
    current_exe_path = Path(current_exe_path)

    # 1. Download to a temp location.
    temp_dir = Path(tempfile.gettempdir())
    new_exe_path = temp_dir / "evejs_launcher_update.exe"

    ok = download_asset(download_url, new_exe_path, progress_callback)
    if not ok:
        return False

    # Pre-flight: verify the downloaded .exe references the correct Python DLL.
    # A corrupted bootloader (e.g. UPX-garbled DLL name) would brick the
    # launcher after replacement with no recovery path.
    try:
        _verify_exe_integrity(new_exe_path)
    except ValueError as exc:
        # Clean up the bad download so it doesn't pollute %TEMP%.
        try:
            new_exe_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    # 2. Write (or overwrite) the helper VBScript into %TEMP%.
    helper_dest = temp_dir / "evejs_launcher_update_helper.vbs"
    try:
        helper_source = _HELPER_SOURCE.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        fallback = Path.cwd() / "update_helper.vbs"
        if fallback.exists():
            helper_source = fallback.read_text(encoding="utf-8")
        else:
            return False

    helper_dest.write_text(helper_source, encoding="utf-8")

    # 3. Spawn the helper VBScript via wscript.exe — completely silent,
    # no console window, no output.  The script handles the wait, replace,
    # and optional restart entirely in the background.
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS

    try:
        subprocess.Popen(
            [
                "wscript.exe",
                str(helper_dest),
                str(current_exe_path),
                str(new_exe_path),
                "--restart",
            ],
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return False

    return True
