"""Build-gated automatic login support for the copied EveJS EVE client.

The supported client already implements command-line login and character
selection.  This module verifies the exact known implementation before the
launcher opts into those switches; it never modifies client files and never
handles a real password.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import zipfile


SUPPORTED_BUILD = 3396210
LOCAL_DUMMY_PASSWORD = "evejs-local"
NO_CONSOLE_ARGUMENT = "/noconsole"

_REQUIRED_ENTRY_SHA256 = {
    "utillib/__init__.pyj": (
        "F0F2E5BA970EACE41EF405E114BC6A1CC2EFD7AA804AF7E383CB1341BB23C594"
    ),
    "carbon/client/script/remote/connectionService.pyj": (
        "B148ABAEBA26A8B60256781CEFDB0D6890272301AE94AF2C5425051C5D22457E"
    ),
    "eve/client/script/ui/login/loginII.pyj": (
        "4B7DB99D94EBA7BEE86C1C7D967F998EF96BF806163A31D084772C8AA7F7382B"
    ),
    "eve/client/script/ui/login/charSelection/characterSelection.pyj": (
        "C64564EF6E55317E4B68A7F4673A889A98C83427DC51C0DCEEBF310FBD32E206"
    ),
}


@dataclass(frozen=True)
class AutoLoginCapability:
    """Static compatibility result for one EveJS root and copied client."""

    supported: bool
    reason: str
    build: int | None = None


@dataclass(frozen=True)
class AutoLoginLaunch:
    """Validated-at-launch intent to sign in and enter one character."""

    username: str
    character_id: int


class AutoLoginUnavailableError(RuntimeError):
    """Raised when opt-in login is requested for an unsupported setup."""


def _unsupported(reason: str, *, build: int | None = None) -> AutoLoginCapability:
    return AutoLoginCapability(False, reason, build)


def _read_client_build(start_ini: Path) -> int | None:
    try:
        text = start_ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"(?im)^\s*build\s*=\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else None


def _password_bypass_enabled(evejs_root: Path) -> bool:
    config_path = evejs_root / "config" / "server.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    development = payload.get("development")
    return (
        isinstance(development, dict)
        and development.get("devSkipPasswordValidation") is True
    )


def _client_entries_match(code_archive: Path) -> bool:
    try:
        with zipfile.ZipFile(code_archive, "r") as archive:
            for entry, expected in _REQUIRED_ENTRY_SHA256.items():
                actual = hashlib.sha256(archive.read(entry)).hexdigest().upper()
                if actual != expected:
                    return False
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return True


def _client_supports_no_console(executable: Path) -> bool:
    """Return whether the copied client exposes its native no-console mode."""
    try:
        return b"/noconsole" in executable.read_bytes()
    except OSError:
        return False


def inspect_auto_login_capability(
    evejs_root: str | Path,
    client_path: str | Path,
) -> AutoLoginCapability:
    """Return whether the configured copied client supports safe local login."""
    root = Path(evejs_root)
    client = Path(client_path)
    if not root.is_dir():
        return _unsupported("Select a valid EveJS root.")
    if not client.is_dir():
        return _unsupported("Select the copied EVE client tq folder.")

    build = _read_client_build(client / "start.ini")
    if build is None:
        return _unsupported("The copied client build could not be identified.")
    if build != SUPPORTED_BUILD:
        return _unsupported(
            f"EVE build {build} is not supported for automatic login.",
            build=build,
        )
    if not _client_entries_match(client / "code.ccp"):
        return _unsupported(
            "The build 3396210 login modules are missing or modified.",
            build=build,
        )
    if not _client_supports_no_console(client / "bin64" / "exefile.exe"):
        return _unsupported(
            "The copied client does not expose the required no-console mode.",
            build=build,
        )
    if not _password_bypass_enabled(root):
        return _unsupported(
            "EveJS local password bypass is disabled or could not be verified.",
            build=build,
        )
    return AutoLoginCapability(
        True,
        "Supported — copied EVE build 3396210; no client patch required.",
        build,
    )


def is_loopback_host(host: str) -> bool:
    """Return whether *host* identifies the local loopback interface."""
    if not isinstance(host, str) or not host:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_auto_login_arguments(intent: AutoLoginLaunch) -> tuple[str, str, str]:
    """Build the three client arguments allowed for automatic login."""
    username = intent.username
    if (
        not isinstance(username, str)
        or not username
        or username != username.strip()
        or ":" in username
        or any(character in username for character in "\r\n\x00")
    ):
        raise ValueError("The account username is not safe for automatic login.")
    character_id = intent.character_id
    if (
        isinstance(character_id, bool)
        or not isinstance(character_id, int)
        or character_id <= 0
    ):
        raise ValueError("A positive character ID is required for automatic login.")
    return (
        NO_CONSOLE_ARGUMENT,
        f"/login:{username}:{LOCAL_DUMMY_PASSWORD}",
        f"/autoSelectCharacter:{character_id}",
    )


def require_auto_login_arguments(
    intent: AutoLoginLaunch,
    *,
    evejs_root: str | Path,
    client_path: str | Path,
    game_host: str,
) -> tuple[str, str, str]:
    """Fail closed unless the exact local copied-client setup is supported."""
    if not is_loopback_host(game_host):
        raise AutoLoginUnavailableError(
            "Automatic login is restricted to a local EveJS game endpoint."
        )
    capability = inspect_auto_login_capability(evejs_root, client_path)
    if not capability.supported:
        raise AutoLoginUnavailableError(capability.reason)
    return build_auto_login_arguments(intent)
