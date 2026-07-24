#!/usr/bin/env python3
"""Standalone updater helper script for EveJS Launcher V2.

This script is spawned **detached** by the launcher's update flow.  It waits
for the old launcher to exit, replaces the old ``.exe`` with the newly
downloaded one (retrying if the file is locked), and optionally restarts.

Usage::

    python update_helper.py --old-exe PATH --new-exe PATH [--restart]

Logs are written to ``%APPDATA%/EveJS-Launcher-V2/logs/updater.log``.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path


def _setup_logging() -> logging.Logger:
    """Configure the updater logger to write to the app's log directory."""
    log_dir = Path(os.environ.get("APPDATA", "")) / "EveJS-Launcher-V2" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "updater.log"

    logger = logging.getLogger("updater_helper")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicating handlers on re-import (though this script runs
    # standalone, defensive code costs nothing).
    if not logger.handlers:
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # Also log to stderr so any caller can see output.
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(formatter)
        logger.addHandler(sh)

    return logger


log = _setup_logging()


def _try_replace(old_exe: Path, new_exe: Path) -> bool:
    """Attempt to move *new_exe* over *old_exe*.

    Returns *True* on success.  On Windows the old executable may still be
    locked briefly after the process exits, so the caller should retry.
    """
    try:
        # Remove the old executable first.
        if old_exe.exists():
            old_exe.unlink()
        # shutil.move across drives may fail; os.replace is atomic on the
        # same filesystem.
        os.replace(new_exe, old_exe)
        log.info("Replaced %s with %s", old_exe, new_exe.name)
        return True
    except PermissionError:
        log.debug("PermissionError — file still locked: %s", old_exe)
        return False
    except OSError as exc:
        log.warning("OSError during replace: %s", exc)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="EveJS Launcher V2 — update helper"
    )
    parser.add_argument(
        "--old-exe",
        required=True,
        type=Path,
        help="Path to the currently-installed launcher .exe",
    )
    parser.add_argument(
        "--new-exe",
        required=True,
        type=Path,
        help="Path to the downloaded update .exe",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        default=False,
        help="Launch the new .exe after the replacement succeeds",
    )
    args = parser.parse_args()

    old_exe: Path = args.old_exe.resolve()
    new_exe: Path = args.new_exe.resolve()

    log.info("Update helper started.")
    log.info("  Old exe : %s", old_exe)
    log.info("  New exe : %s", new_exe)
    log.info("  Restart : %s", args.restart)

    if not new_exe.exists():
        log.error("New .exe not found at %s — aborting.", new_exe)
        _show_failure_dialog()
        return 1

    # ── Wait for the old launcher to exit ─────────────────────────────────
    log.info("Waiting 2 seconds for the old launcher to exit…")
    time.sleep(2)

    # ── Retry loop (up to 10 attempts, 500 ms apart) ─────────────────────
    max_attempts = 10
    retry_delay = 0.5

    for attempt in range(1, max_attempts + 1):
        if _try_replace(old_exe, new_exe):
            break
        log.info(
            "Retry %d/%d — waiting %.1f s…",
            attempt,
            max_attempts,
            retry_delay,
        )
        time.sleep(retry_delay)
    else:
        # All retries exhausted.
        log.error(
            "Failed to replace %s after %d attempts.",
            old_exe,
            max_attempts,
        )
        _show_failure_dialog()
        return 2

    # ── Restart the new launcher ──────────────────────────────────────────
    if args.restart:
        log.info("Launching %s", old_exe)
        try:
            subprocess.Popen(
                [str(old_exe)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            log.error("Failed to launch new .exe: %s", exc)
            _show_failure_dialog()
            return 3

    log.info("Update helper finished successfully.")
    return 0


def _show_failure_dialog() -> None:
    """Attempt to show a message box with manual-update instructions.

    Tries :mod:`tkinter` first (bundled with Python on Windows), then falls
    back to printing to stderr.
    """
    message = (
        "EveJS Launcher could not complete the update automatically.\n\n"
        "Please download the latest version manually from:\n"
        "https://github.com/V0nCleef/evejs-launcher/releases\n\n"
        "A copy of the new .exe was saved to your Temp folder."
    )
    try:
        import tkinter.messagebox  # type: ignore[import-untyped]

        tkinter.messagebox.showerror(
            "Update Failed — EveJS Launcher V2",
            message,
        )
    except Exception:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
