"""Download + replace flow for self-updating the launcher.

Downloads the new .exe to a temp location, writes a helper script,
spawns it detached, and returns control so the caller can exit.
The helper script handles the file-lock retry loop and restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.updater.github import download_asset


def download_and_install(
    download_url: str,
    current_exe_path: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bool:
    """Download the new .exe and hand off to the update helper.

    Delegates to :func:`src.core.platform.run_updater` which is the
    platform-specific implementation (VBScript/wscript on Windows,
    bash/nohup on Linux).

    Returns
    -------
    bool
        *True* when the download succeeds and the helper has been launched.
        *False* when the download itself fails (caller should report the
        error to the user rather than exiting).
    """
    from src.core.platform import run_updater
    return run_updater(download_url, current_exe_path)
