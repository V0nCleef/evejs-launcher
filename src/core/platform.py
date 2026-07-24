"""Platform abstraction layer — auto-detects OS and exposes unified API.

All platform-specific code lives in ``platform_win.py`` / ``platform_linux.py``.
Callers import from here and never check ``sys.platform`` themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_PLATFORM = sys.platform

if _PLATFORM == "win32":
    from .platform_win import (  # noqa: F401 — re-exported below
        create_directory_link,
        find_and_focus_eve_window,
        find_eve_window,
        get_client_exe_name,
        get_client_exe_path,
        get_client_process_flags,
        get_eve_settings_path,
        get_exe_file_filter,
        get_hidden_process_flags,
        get_market_binary_name,
        hard_exit,
        launch_eve_client,
        open_text_editor,
        remove_directory_link,
        run_updater,
    )
elif _PLATFORM == "linux":
    from .platform_linux import (  # noqa: F401
        create_directory_link,
        find_and_focus_eve_window,
        find_eve_window,
        get_client_exe_name,
        get_client_exe_path,
        get_client_process_flags,
        get_eve_settings_path,
        get_exe_file_filter,
        get_hidden_process_flags,
        get_market_binary_name,
        hard_exit,
        launch_eve_client,
        open_text_editor,
        remove_directory_link,
        run_updater,
    )
else:
    raise RuntimeError(f"Unsupported platform: {_PLATFORM}")


# Re-export everything so callers do ``from .platform import create_directory_link``.
__all__ = [
    "create_directory_link",
    "find_and_focus_eve_window",
    "find_eve_window",
    "get_client_exe_name",
    "get_client_exe_path",
    "get_client_process_flags",
    "get_eve_settings_path",
    "get_exe_file_filter",
    "get_hidden_process_flags",
    "get_market_binary_name",
    "hard_exit",
    "launch_eve_client",
    "open_text_editor",
    "remove_directory_link",
    "run_updater",
    "is_linux",
    "is_windows",
]

is_windows: bool = _PLATFORM == "win32"
is_linux: bool = _PLATFORM == "linux"
