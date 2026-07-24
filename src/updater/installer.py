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

# The helper script is shipped at the repo root; we read it at runtime
# and write a copy to %TEMP% so it can outlive the launcher process.
_HELPER_SOURCE = Path(__file__).resolve().parent.parent.parent / "update_helper.py"


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

    # 2. Write (or overwrite) the helper script into %TEMP%.
    helper_dest = temp_dir / "evejs_launcher_update_helper.py"
    try:
        helper_source = _HELPER_SOURCE.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        # If the helper isn't found at the expected path (e.g. during
        # development), look relative to the current working directory.
        fallback = Path.cwd() / "update_helper.py"
        if fallback.exists():
            helper_source = fallback.read_text(encoding="utf-8")
        else:
            return False

    helper_dest.write_text(helper_source, encoding="utf-8")

    # 3. Spawn the helper detached.
    flags = 0
    if sys.platform == "win32":
        flags = (
            subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )

    try:
        subprocess.Popen(
            [
                sys.executable,
                str(helper_dest),
                "--old-exe",
                str(current_exe_path),
                "--new-exe",
                str(new_exe_path),
                "--restart",
            ],
            creationflags=flags,  # type: ignore[arg-type]
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        return False

    return True
