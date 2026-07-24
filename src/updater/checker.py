"""Background update checker running on a :class:`PyQt6.QtCore.QThread`.

The checker queries the GitHub Releases API and compares the latest tag
against the version baked into the launcher.  It fires one of three
signals depending on the outcome.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from src.updater.github import get_latest_release


def get_current_version() -> str:
    """Return the version string from the VERSION file at the repo root.

    This is callable from any thread — it does a plain file read with no
    Qt involvement.
    """
    version_path = Path(__file__).resolve().parent.parent.parent / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return "0.0.0"


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a ``MAJOR.MINOR.PATCH`` string into a 3-tuple of ints.

    Strips a leading ``v`` or ``V`` prefix automatically.
    Handles extra segments gracefully (e.g. ``1.2.3-beta`` → ``(1, 2, 3)``).
    """
    clean = version.lstrip("vV")
    parts = clean.split(".")
    try:
        return (
            int(parts[0]) if len(parts) > 0 else 0,
            int(parts[1]) if len(parts) > 1 else 0,
            int(parts[2]) if len(parts) > 2 else 0,
        )
    except (ValueError, IndexError):
        return (0, 0, 0)


def _load_skipped_versions() -> set[str]:
    """Read the set of versions the user has chosen to skip."""
    from src import config
    cfg = config.load()
    skipped = cfg.get("update_skip_versions", [])
    if isinstance(skipped, list):
        return set(skipped)
    return set()


def _save_skipped_versions(skipped: set[str]) -> None:
    """Persist the set of skipped versions to the launcher config."""
    from src import config
    cfg = config.load()
    cfg["update_skip_versions"] = sorted(skipped)
    config.save(cfg)


class UpdateChecker(QThread):
    """Worker thread that queries GitHub for the latest release.

    Signals
    -------
    update_available(version, changelog, download_url, published_at)
        Emitted when a *newer* version is found on GitHub.
    up_to_date()
        Emitted when the installed version matches (or exceeds) the remote.
    check_failed(error_message)
        Emitted when the network request fails for any reason.
    """

    update_available = pyqtSignal(str, str, str, str)
    up_to_date = pyqtSignal()
    check_failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._current_version: str = get_current_version()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_version(self) -> str:
        """The version string read from the local VERSION file."""
        return self._current_version

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Convenience wrapper — starts the thread if not already running."""
        if not self.isRunning():
            self.start()

    def run(self) -> None:
        """Perform the remote check and emit exactly one signal."""
        release = get_latest_release()
        if release is None:
            self.check_failed.emit(
                "Could not reach GitHub to check for updates.  "
                "Please verify your internet connection and try again."
            )
            return

        tag: str = release.get("tag_name", "")
        if not tag:
            self.check_failed.emit("Received an empty tag from GitHub.")
            return

        remote_version = _parse_semver(tag)
        local_version = _parse_semver(self._current_version)

        if remote_version <= local_version:
            self.up_to_date.emit()
            return

        # Honour the user's skip list.
        skipped = _load_skipped_versions()
        if tag in skipped:
            self.up_to_date.emit()
            return

        # Try to locate the first .exe asset.
        download_url = ""
        for asset in release.get("assets", []):
            if asset.get("name", "").lower().endswith(".exe"):
                download_url = asset.get("browser_download_url", "")
                break

        self.update_available.emit(
            tag,
            release.get("body", ""),
            download_url,
            release.get("published_at", ""),
        )

    # ------------------------------------------------------------------
    # Skip management (call from main thread)
    # ------------------------------------------------------------------

    @staticmethod
    def skip_version(version_str: str) -> None:
        """Mark *version_str* as skipped so the user is not re-notified."""
        skipped = _load_skipped_versions()
        skipped.add(version_str)
        _save_skipped_versions(skipped)

    @staticmethod
    def is_version_skipped(version_str: str) -> bool:
        """Return *True* if *version_str* was previously skipped."""
        return version_str in _load_skipped_versions()


# ------------------------------------------------------------------
# Convenience helpers
# ------------------------------------------------------------------


def check_for_updates(parent=None) -> UpdateChecker:  # type: ignore[no-untyped-def]
    """Create and start an :class:`UpdateChecker` thread.

    Returns the thread instance so the caller can connect signals.

    Usage::

        checker = check_for_updates(self)
        checker.update_available.connect(self._on_update_available)
        checker.up_to_date.connect(self._on_up_to_date)
        checker.check_failed.connect(self._on_check_failed)
    """
    checker = UpdateChecker(parent)
    checker.start()
    return checker
