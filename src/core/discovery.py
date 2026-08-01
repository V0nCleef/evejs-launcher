"""EveJS path discovery and validation."""
from pathlib import Path

from .server_selection import discover_server_scripts


def validate_evejs_root(path: str) -> tuple[bool, str]:
    """Validate that a path looks like an EveJS installation root.

    Returns (is_valid, error_message).
    """
    p = Path(path)
    if not p.exists():
        return False, f"Path does not exist: {path}"

    if not p.is_dir():
        return False, f"Path is not a directory: {path}"

    required = [
        ("server/certs/xmpp-ca-cert.pem", "SSL cert (server may not be configured)"),
        ("_local/gameStore/gamestore.sqlite", "Game store database"),
        ("tools/ClientSETUP/scripts/EvEJSConfig.bat", "Client config script"),
    ]

    for rel_path, desc in required:
        if not (p / rel_path).exists():
            return False, f"Missing {desc}: {rel_path}"

    # ── Server start script: accept any StartServer*.bat ─────────────────
    server_bats = discover_server_scripts(p)
    if not server_bats and not (p / "server" / "index.js").exists():
        return False, "Missing server start script (StartServer*.bat) or server/index.js"

    return True, ""


def validate_docker_evejs_root(
    path: str,
    compose_file: str = "",
) -> tuple[bool, str]:
    """Validate only the local files required before Docker preflight.

    A pristine Compose project intentionally has no generated certificates or
    databases yet. Runtime, service, endpoint, and initialization checks belong
    to the read-only Docker preflight worker.
    """
    root = Path(path)
    if not root.is_absolute():
        return False, "Docker project root must be an absolute path."
    if not root.exists():
        return False, f"Docker project root does not exist: {path}"
    if not root.is_dir():
        return False, f"Docker project root is not a directory: {path}"

    compose = Path(compose_file) if compose_file else root / "compose.yaml"
    if not compose.is_absolute():
        return False, "Docker Compose file must be an absolute path."
    if not compose.exists():
        return False, f"Docker Compose file does not exist: {compose}"
    if not compose.is_file():
        return False, f"Docker Compose path is not a file: {compose}"
    return True, ""


def find_client_path(evejs_root: str) -> str | None:
    """Extract EVE client path from EvEJSConfig.bat."""
    cfg = Path(evejs_root) / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    if not cfg.exists():
        return None

    for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("set ") or line.startswith("SET "):
            # Look for EVEJS_CLIENT_PATH=...
            if "EVEJS_CLIENT_PATH=" in line:
                # Extract: set "EVEJS_CLIENT_PATH=G:\..." or set EVEJS_CLIENT_PATH=G:\...
                val = line.split("EVEJS_CLIENT_PATH=", 1)[1].strip()
                val = val.strip('"').strip("'")
                return val if val else None
    return None


def get_play_bat_path(evejs_root: str) -> Path:
    """Get path to Play.bat."""
    return Path(evejs_root) / "tools" / "ClientSETUP" / "scripts" / "Play.bat"


def get_ca_cert_path(evejs_root: str) -> Path:
    """Get path to the CA certificate."""
    return Path(evejs_root) / "server" / "certs" / "xmpp-ca-cert.pem"
