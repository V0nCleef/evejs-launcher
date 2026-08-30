"""EveJS path discovery and validation."""
import codecs
import locale
import os
import re
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
        ("tools/ClientSETUP/scripts/EvEJSConfig.bat", "Client config script"),
    ]

    for rel_path, desc in required:
        if not (p / rel_path).exists():
            return False, f"Missing {desc}: {rel_path}"

    # ── Server start script: accept any StartServer*.bat ─────────────────
    server_bats = discover_server_scripts(p)
    if not server_bats and not (p / "server" / "index.js").exists():
        return False, "Missing server start script (StartServer*.bat) or server/index.js"

    # Accept either an established runtime database or completed static setup.
    game_store = p / "_local" / "gameStore"
    database = game_store / "gamestore.sqlite"
    manifest = game_store / "manifest.json"
    data_dir = game_store / "data"
    static_data_ready = False
    if manifest.is_file() and data_dir.is_dir():
        try:
            static_data_ready = any(entry.is_file() for entry in data_dir.rglob("*"))
        except OSError:
            static_data_ready = False

    # v0.12.6 creates gamestore.sqlite when the Node server first starts. A
    # freshly completed Native setup is valid before that first runtime start
    # when its generated static-data manifest and files are already present.
    if not database.exists() and not static_data_ready:
        return False, (
            "Missing game store: expected _local/gameStore/gamestore.sqlite "
            "or _local/gameStore/manifest.json with populated "
            "_local/gameStore/data"
        )

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


def resolve_client_tq_path(
    selection: str | Path,
    evejs_root: str | Path = "",
) -> Path | None:
    """Resolve a copied-client selection to its canonical ``tq`` folder.

    Older launcher UI copy asked for ``exefile.exe`` even though profiles,
    overview support, and resource-cache lookup all consume the directory that
    contains ``start.ini`` and ``bin64/exefile.exe``. Accept the common places
    a user may select, but return only that directory contract.
    """
    raw = str(selection).strip().strip('"').strip("'")
    if not raw:
        return None

    root = str(evejs_root).strip()
    if root:
        raw = re.sub(
            r"%EVEJS_REPO_ROOT%",
            lambda _match: root,
            raw,
            flags=re.IGNORECASE,
        )
        if os.sep == "/":
            raw = raw.replace("\\", "/")

    selected = Path(raw).expanduser()
    if selected.is_file():
        if selected.name.casefold() != "exefile.exe":
            return None
        selected = selected.parent

    candidates: list[Path] = []

    def add(candidate: Path) -> None:
        if candidate not in candidates:
            candidates.append(candidate)

    if selected.name.casefold() in {"bin", "bin64"}:
        add(selected.parent)
    add(selected)
    add(selected / "tq")
    add(selected / "EVE" / "tq")
    add(selected / "SharedCache" / "tq")

    for candidate in candidates:
        if (
            candidate.is_dir()
            and (candidate / "start.ini").is_file()
            and (candidate / "bin64" / "exefile.exe").is_file()
        ):
            return candidate
    return None


def find_client_path(evejs_root: str) -> str | None:
    """Extract and canonicalize the copied EVE client path from its batch config."""
    cfg = Path(evejs_root) / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    if not cfg.exists():
        return None

    contents = _read_batch_text(cfg)
    if contents is None:
        return None

    for line in contents.splitlines():
        match = re.match(
            r'^\s*set\s+"?EVEJS_CLIENT_PATH\s*=\s*(.*?)"?\s*$',
            line,
            flags=re.IGNORECASE,
        )
        if match:
            resolved = resolve_client_tq_path(match.group(1), evejs_root)
            return str(resolved) if resolved is not None else None
    return None


def _legacy_batch_encoding() -> str:
    """Return the active Windows ANSI code page for legacy batch files."""
    if os.name == "nt":
        return "mbcs"
    getencoding = getattr(locale, "getencoding", None)
    if callable(getencoding):
        return getencoding()
    return locale.getpreferredencoding(False)


def _read_batch_text(path: Path) -> str | None:
    """Decode modern and Windows-authored batch files without dropping bytes."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if raw.startswith(codecs.BOM_UTF8):
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return raw.decode(_legacy_batch_encoding())
        except (LookupError, UnicodeDecodeError):
            return None


def get_play_bat_path(evejs_root: str) -> Path:
    """Get path to Play.bat."""
    return Path(evejs_root) / "tools" / "ClientSETUP" / "scripts" / "Play.bat"


def get_ca_cert_path(evejs_root: str) -> Path:
    """Get path to the CA certificate."""
    return Path(evejs_root) / "server" / "certs" / "xmpp-ca-cert.pem"
