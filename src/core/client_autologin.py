"""Build-gated automatic login support for the copied EveJS EVE client.

The supported clients already implement command-line login and character
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


LOCAL_DUMMY_PASSWORD = "evejs-local"
NO_CONSOLE_ARGUMENT = "/noconsole"

_REQUIRED_ENTRY_SHA256_BY_BUILD = {
    3396210: {
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
    },
    3724492: {
        "utillib/__init__.pyj": (
            "1BF557FF2A379D4159F039C2400189F205A1BB777DDC359D5D2E08DD063B4C67"
        ),
        "carbon/client/script/remote/connectionService.pyj": (
            "5B210125506AC256B39E0D32A258006ED858BDD5464703219ED1841A0A043E6D"
        ),
        "eve/client/script/ui/login/loginII.pyj": (
            "A7ABB6C688863DE4C14BCB44AACF75327F42A09C39FCEA08425504141FF3AD37"
        ),
        "eve/client/script/ui/login/charSelection/characterSelection.pyj": (
            "D0EB70EAEA5CF1B6D36AFC6F526AA2EB0C35060C10FC589ED6E007C0FD16A010"
        ),
    },
}


@dataclass(frozen=True)
class AutoLoginCapability:
    """Static compatibility result for one EveJS root and copied client."""

    supported: bool
    reason: str
    build: int | None = None


@dataclass(frozen=True)
class AutoLoginLaunch:
    """Validated-at-launch intent to sign in and optionally enter a character."""

    username: str
    character_id: int | None = None


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
    if not isinstance(payload, dict):
        return False
    development = payload.get("development")
    return (
        isinstance(development, dict)
        and development.get("devSkipPasswordValidation") is True
    )


def _client_entries_match(
    code_archive: Path, required_entry_hashes: dict[str, str]
) -> bool:
    try:
        with zipfile.ZipFile(code_archive, "r") as archive:
            for entry, expected in required_entry_hashes.items():
                actual = hashlib.sha256(archive.read(entry)).hexdigest().upper()
                if actual != expected:
                    return False
    except (OSError, KeyError, zipfile.BadZipFile):
        return False
    return True


def supports_client_no_console(executable: Path) -> bool:
    """Return whether the copied client exposes its native no-console mode."""
    try:
        return b"/noconsole" in executable.read_bytes()
    except OSError:
        return False


def inspect_auto_login_capability(
    evejs_root: str | Path,
    client_path: str | Path,
) -> AutoLoginCapability:
    """Return whether the configured Native setup supports safe local login."""
    root = Path(evejs_root)
    if not root.is_dir():
        return _unsupported("Select a valid EveJS root.")

    client_capability = inspect_client_auto_login_capability(client_path)
    if not client_capability.supported:
        return client_capability
    if not _password_bypass_enabled(root):
        return _unsupported(
            "EveJS local password bypass is disabled or could not be verified.",
            build=client_capability.build,
        )
    return client_capability


def inspect_client_auto_login_capability(
    client_path: str | Path,
) -> AutoLoginCapability:
    """Check only the copied-client files needed for automatic login."""
    client = Path(client_path)
    if not client.is_dir():
        return _unsupported("Select the copied EVE client tq folder.")

    build = _read_client_build(client / "start.ini")
    if build is None:
        return _unsupported("The copied client build could not be identified.")
    required_entry_hashes = _REQUIRED_ENTRY_SHA256_BY_BUILD.get(build)
    if required_entry_hashes is None:
        return _unsupported(
            f"EVE build {build} is not supported for automatic login.",
            build=build,
        )
    if not _client_entries_match(client / "code.ccp", required_entry_hashes):
        return _unsupported(
            f"The build {build} login modules are missing or modified.",
            build=build,
        )
    if not supports_client_no_console(client / "bin64" / "exefile.exe"):
        return _unsupported(
            "The copied client does not expose the required no-console mode.",
            build=build,
        )
    return AutoLoginCapability(
        True,
        f"Supported — copied EVE build {build}; no client patch required.",
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


def build_auto_login_arguments(intent: AutoLoginLaunch) -> tuple[str, ...]:
    """Build native account login, optionally selecting an existing character."""
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
    if character_id is not None and (
        isinstance(character_id, bool)
        or not isinstance(character_id, int)
        or character_id <= 0
    ):
        raise ValueError("A positive character ID is required for automatic login.")
    arguments = (
        NO_CONSOLE_ARGUMENT,
        f"/login:{username}:{LOCAL_DUMMY_PASSWORD}",
    )
    if character_id is not None:
        arguments += (f"/autoSelectCharacter:{character_id}",)
    return arguments


def require_auto_login_arguments(
    intent: AutoLoginLaunch,
    *,
    evejs_root: str | Path,
    client_path: str | Path,
    game_host: str,
    server_password_bypass_verified: bool = False,
) -> tuple[str, ...]:
    """Fail closed unless copied-client and server gates have been verified.

    Docker callers may attest the server setting from the selected running
    container. Native callers leave the argument false and use the local
    server configuration check below.
    """
    if not is_loopback_host(game_host):
        raise AutoLoginUnavailableError(
            "Automatic login is restricted to a local EveJS game endpoint."
        )
    if server_password_bypass_verified is True:
        if not Path(evejs_root).is_dir():
            raise AutoLoginUnavailableError("Select a valid EveJS root.")
        capability = inspect_client_auto_login_capability(client_path)
    else:
        capability = inspect_auto_login_capability(evejs_root, client_path)
    if not capability.supported:
        raise AutoLoginUnavailableError(capability.reason)
    return build_auto_login_arguments(intent)
