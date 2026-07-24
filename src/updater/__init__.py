"""Update system for EveJS Launcher V2.

Public API:
    UpdateChecker  — background thread that polls GitHub for new releases
    UpdateDialog   — dark-themed modal that shows changelog + action buttons
    check_for_updates() — convenience wrapper that runs UpdateChecker
    get_current_version() — read the VERSION file
"""

from __future__ import annotations

from src.updater.checker import UpdateChecker, check_for_updates, get_current_version
from src.updater.dialog import UpdateDialog

__all__ = [
    "UpdateChecker",
    "UpdateDialog",
    "check_for_updates",
    "get_current_version",
]
