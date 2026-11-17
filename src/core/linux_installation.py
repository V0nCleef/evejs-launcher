"""Read the Linux Setup interchange files without executing shell configuration.

This is an interoperability reader. The third-party installer and payload are
not bundled; installation, repair and upgrades remain owned by Linux Setup.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex


class LinuxSetupError(ValueError):
    """An existing prepared installation cannot safely be used."""


def read_json(path: Path, *, limit: int = 1024 * 1024) -> dict:
    try:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("oversized metadata")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("metadata must be an object")
        return value
    except (OSError, ValueError, UnicodeError) as exc:
        raise LinuxSetupError(f"Invalid Linux Setup metadata: {path.name}") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assignment_value(raw: str) -> str:
    # Bash printf %q uses either backslash escapes or ANSI-C quoting. No shell
    # is involved, including for values which happen to contain shell syntax.
    if raw.startswith("$'"):
        if not raw.endswith("'"):
            raise LinuxSetupError("Unterminated Linux Setup value.")
        body = raw[2:-1]
        result = []
        index = 0
        escapes = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
                   "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"'}
        while index < len(body):
            char = body[index]
            index += 1
            if char == "'":
                raise LinuxSetupError("Unsupported Linux Setup quoting.")
            if char != "\\":
                result.append(char.encode("utf-8"))
                continue
            if index == len(body):
                raise LinuxSetupError("Incomplete Linux Setup escape.")
            char = body[index]
            index += 1
            if char in escapes:
                result.append(escapes[char].encode("utf-8"))
                continue
            lengths = {"x": (1, 2), "u": (4, 4), "U": (8, 8)}
            if char in lengths:
                low, high = lengths[char]
                match = re.match(r"[0-9a-fA-F]{%d,%d}" % (low, high), body[index:])
                if match is None:
                    raise LinuxSetupError("Invalid Linux Setup character escape.")
                number = int(match[0], 16)
                result.append(bytes([number]) if char == "x" else chr(number).encode("utf-8"))
                index += len(match[0])
            elif char in "01234567":
                match = re.match(r"[0-7]{0,2}", body[index:])[0]
                number = int(char + match, 8)
                if number > 255:
                    raise LinuxSetupError("Invalid Linux Setup byte escape.")
                result.append(bytes([number]))
                index += len(match)
            else:
                raise LinuxSetupError("Unsupported Linux Setup escape.")
        try:
            value = b"".join(result).decode("utf-8")
        except UnicodeError as exc:
            raise LinuxSetupError("Linux Setup paths must be valid UTF-8.") from exc
    else:
        # Refuse active shell operators/expansion outside single quotes rather
        # than interpreting them as part of a selected executable or path.
        quote = None
        escaped = False
        for char in raw:
            if escaped:
                escaped = False
            elif char == "\\" and quote != "'":
                escaped = True
            elif quote:
                if char == quote:
                    quote = None
                elif quote == '"' and char in "$`":
                    raise LinuxSetupError("Shell expansion is not supported in Linux Setup configuration.")
            elif char in "'\"":
                quote = char
            elif char.isspace() or char in "$`;|&<>()":
                raise LinuxSetupError("Only literal Linux Setup assignments are supported.")
        try:
            parts = shlex.split(raw, comments=False, posix=True)
        except ValueError as exc:
            raise LinuxSetupError("Invalid Linux Setup quoting.") from exc
        if len(parts) > 1:
            raise LinuxSetupError("Expected one Linux Setup value.")
        value = parts[0] if parts else ""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise LinuxSetupError("Control characters in Linux Setup paths are unsupported.")
    return value


def read_setup_config(path: Path) -> dict[str, str]:
    try:
        with path.open("rb") as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise LinuxSetupError("Linux Setup configuration is too large.")
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LinuxSetupError("Select an existing installation prepared by EveJS Linux Setup (.evejs-linux.conf).") from exc
    values = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if match is None or match[1] in values:
            raise LinuxSetupError("Linux Setup configuration has invalid or duplicate assignments.")
        values[match[1]] = _assignment_value(match[2])
    return values


@dataclass(frozen=True)
class LinuxInstallation:
    root: Path
    client: Path
    cache: Path
    proton: Path
    prefix: Path
    steam: Path
    node: Path
    version: str
    build: int
    cdn_port: int
    platform_mode: str

    @classmethod
    def load(cls, root: str | Path) -> "LinuxInstallation":
        selected = Path(root).expanduser().resolve(strict=True)
        values = read_setup_config(selected / ".evejs-linux.conf")

        def absolute(key: str) -> Path:
            value = values.get(key, "")
            if not value or not Path(value).is_absolute():
                raise LinuxSetupError(f"Linux Setup is missing an absolute {key} path.")
            return Path(value).resolve(strict=True)

        if absolute("EVEJS_REPO") != selected:
            raise LinuxSetupError("Linux Setup configuration belongs to a different installation.")
        journal = selected / '_local/launcher-linux/cdn-profile/manifest.json'
        if journal.exists() and read_json(journal, limit=16384).get('phase') == 'prepared':
            raise LinuxSetupError('A CDN profile transaction was interrupted. Open Linux maintenance and recover the CDN profile before starting EveJS.')
        metadata = read_json(selected / "config/version.json")
        version = str(metadata.get("evejsVersion") or metadata.get("version") or "")
        if not version or version != values.get("EVEJS_VERSION"):
            raise LinuxSetupError("Linux Setup version differs from the installed release; prepare runtime compatibility again.")
        try:
            build = int(values["SUPPORTED_BUILD"])
            cdn = int(values.get("EVEJS_CDN_PORT", "443"))
        except (KeyError, ValueError) as exc:
            raise LinuxSetupError("Linux Setup has an invalid client build or CDN port.") from exc
        if build != 3396210 or cdn not in (443, 8443):
            raise LinuxSetupError("This Linux preview supports client build 3396210 and prepared CDN profiles 443/8443.")
        mode = values.get("EVEJS_PLATFORM_MODE", "standard-linux")
        if mode not in ("standard-linux", "steamos-portable") or (mode == "steamos-portable" and cdn != 8443):
            raise LinuxSetupError("Linux Setup has an inconsistent platform/CDN profile.")
        installation = cls(selected, absolute("TQ_PATH"), absolute("CACHE_ROOT"), absolute("PROTON_BIN"),
                           absolute("PROTON_PREFIX"), absolute("STEAM_ROOT"), absolute("NODE_BIN"),
                           version, build, cdn, mode)
        required = (installation.client / "bin64/exefile.exe", installation.client / "start.ini",
                    installation.cache / "index_tranquility.txt", selected / "server/index.js")
        if any(not path.is_file() for path in required) or not (installation.cache / "ResFiles").is_dir():
            raise LinuxSetupError("Linux Setup client, resource cache or server files are incomplete.")
        for executable in (installation.proton, installation.node):
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise LinuxSetupError(f"Linux runtime executable is unavailable: {executable.name}")
        if not installation.prefix.is_dir() or not installation.steam.is_dir():
            raise LinuxSetupError("Linux Setup prefix or Steam directory is unavailable.")
        return installation

    def server_environment(self) -> dict[str, str]:
        from .host import external_process_environment
        env = external_process_environment()
        env.pop("NODE_OPTIONS", None)
        env.pop("NODE_PATH", None)
        env.pop("EVEJS_DATA_ROOT", None)
        env.update(EVEJS_PROXY_LOCAL_INTERCEPT="1", EVEJS_PROXY_LOOPBACK_CDN_LISTEN_PORT=str(self.cdn_port),
                   EVEJS_XMPP_SERVER_BIND_HOST="127.0.0.1", EVEJS_XMPP_CONNECT_HOST="127.0.0.1")
        env["PATH"] = str(self.node.parent) + os.pathsep + env.get("PATH", "")
        return env

    def client_endpoints(self) -> tuple[int, str]:
        from urllib.parse import urlsplit
        config = read_json(self.root / "config/server.json")
        try:
            port = config.get("network", {}).get("serverPort", 26000)
            proxy = config.get("gateway", {}).get("microservicesPublicUrl", "http://127.0.0.1:26002/")
            parsed = urlsplit(proxy)
            if (type(port) is not int or not 1 <= port <= 65535 or parsed.scheme != "http"
                    or parsed.hostname != "127.0.0.1" or parsed.port != 26002
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in ("", "/")):
                raise ValueError("unsupported endpoints")
        except (AttributeError, TypeError, ValueError) as exc:
            raise LinuxSetupError("Linux stage 1 requires the prepared loopback proxy on port 26002 and a valid Game port.") from exc
        return port, proxy.rstrip("/")

    def market_source_identity(self) -> tuple[Path, str, Path]:
        market = self.root / "externalservices/market-server"
        inputs = {market / name for name in ("Cargo.toml", "Cargo.lock", "build.rs") if (market / name).is_file()}
        for folder in ("src", "crates"):
            inputs.update(path for path in (market / folder).rglob("*")
                          if path.is_file() and (path.suffix == ".rs" or path.name in ("Cargo.toml", "build.rs")))
        if not inputs:
            raise LinuxSetupError("Market build inputs are missing.")
        records = [f"{path.relative_to(market).as_posix()} {file_sha256(path).upper()}"
                   for path in sorted(inputs, key=lambda p: p.as_posix())]
        digest = hashlib.sha256("\n".join(records).encode()).hexdigest().upper()
        identity = hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
        cache = self.root.parent / ".evejs-linux-runtime" / identity
        return market, digest, cache

    def verified_market_binary(self) -> Path:
        from .linux_mod_market import verified_mod_market
        supplied = verified_mod_market(self)
        if supplied is not None:
            return supplied
        from .linux_market import verified_source_build
        market, digest, cache = self.market_source_identity()
        native = verified_source_build(self, digest, cache)
        if native is not None:
            return native
        directory = cache / "market-builds" / digest.lower()
        try:
            metadata = read_json(directory / "build.json")
        except LinuxSetupError as exc:
            raise LinuxSetupError("No Linux Market runtime matches these sources. Prepare the stock runtime with Linux Setup, or use Build Market from source in Linux maintenance for native source mods.") from exc
        binary = directory / "market-server"
        if (metadata.get("schema") != "evejs-linux-market-runtime/1"
                or metadata.get("repo") != str(self.root) or metadata.get("evejsVersion") != self.version
                or metadata.get("mode") not in ("stock", "stock-loader-lu-disabled") or metadata.get("marketProfile") != f"stock-v{self.version}-market"
                or str(metadata.get("sourceDigest", "")).upper() != digest
                or metadata.get("binary") != str(binary) or binary.is_symlink()
                or not binary.is_file() or not os.access(binary, os.X_OK)
                or file_sha256(binary).upper() != str(metadata.get("binarySha256", "")).upper()):
            raise LinuxSetupError("No verified stock Linux Market runtime matches this installation. Prepare runtime compatibility in Linux Setup.")
        return binary

    def require_supported_mods(self) -> None:
        from .mod_manifest import scan_mods
        from .mod_host import mod_unavailable_reason
        for mod in scan_mods(self.root):
            if mod.active:
                reason = mod_unavailable_reason(mod)
                if not mod.valid or reason:
                    raise LinuxSetupError(f'{mod.name}: {reason or mod.error or "Invalid mod declaration."}')


def imported_settings(root: str | Path) -> dict:
    installation = LinuxInstallation.load(root)
    port, proxy = installation.client_endpoints()
    return {"evejs_root": str(installation.root), "client_path": str(installation.client),
            "runtime_backend": "native",
            "game_port": port, "proxy_url": proxy}
