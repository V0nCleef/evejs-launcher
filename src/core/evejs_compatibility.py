"""Read-only EveJS runtime layout and installer capability discovery.

The resolver deliberately reads only bounded release metadata and a few known
source entrypoints. It never imports or executes EveJS code, reads inherited
environment variables, or creates runtime directories.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Literal

from .mod_evejs_compatibility import installed_evejs_version


MetadataStatus = Literal["valid", "missing", "invalid_or_conflicting"]

_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_VERSION_METADATA_PATHS = (Path("package.json"), Path("config/version.json"))


@dataclass(frozen=True)
class RuntimeLayout:
    """Resolved native paths and capabilities for one selected EveJS root.

    ``node_report_directory`` is where this launcher should direct new Node
    reports. ``legacy_node_report_directory`` is an additional location to
    inspect for existing reports when the runtime logs use the data root.
    Paths describe layout only; the resolver does not create them.
    """

    root: Path
    evejs_version: str | None
    metadata_status: MetadataStatus
    log_directory: Path
    node_report_directory: Path
    legacy_node_report_directory: Path | None
    uses_data_root_logs: bool
    certificate_check_only_supported: bool
    config_preflight_supported: bool
    certificate_directory: Path
    game_store_default_directory: Path

    @property
    def node_report_directories(self) -> tuple[Path, ...]:
        """Current report destination followed by any legacy read location."""
        if self.legacy_node_report_directory is None:
            return (self.node_report_directory,)
        return (self.node_report_directory, self.legacy_node_report_directory)


def inspect_evejs_runtime(root, *, data_root_override=None) -> RuntimeLayout:
    """Inspect a native EveJS installation without starting or changing it.

    Relative ``data_root_override`` values are resolved against ``root/server``,
    matching the native child's working directory. ``None`` uses the release's
    default data root. The process environment is intentionally never consulted.
    An override only affects layouts whose logger demonstrably uses data-root
    logs; legacy releases continue to use ``server/logs``.

    An existing directory is required. Invalid, absent, or contradictory version
    metadata is reported in ``metadata_status`` and safely falls back to the
    legacy log layout.
    """
    runtime_root = Path(root).resolve(strict=True)
    if not runtime_root.is_dir():
        raise NotADirectoryError(runtime_root)

    version = installed_evejs_version(runtime_root)
    metadata_status = _metadata_status(runtime_root, version)

    # Capability is based on the known logger integration and data-root resolver,
    # with valid package metadata required to avoid adopting a mixed installation.
    uses_data_root_logs = (
        metadata_status == "valid"
        and _supports_data_root_logs(runtime_root)
    )
    server_root = runtime_root / "server"
    if uses_data_root_logs:
        resolved_data_root = _resolve_data_root(runtime_root, data_root_override)
        log_directory = resolved_data_root / "logs"
        legacy_node_report_directory = server_root / "logs" / "node-reports"
    else:
        log_directory = server_root / "logs"
        legacy_node_report_directory = None

    node_report_directory = log_directory / "node-reports"

    return RuntimeLayout(
        root=runtime_root,
        evejs_version=version,
        metadata_status=metadata_status,
        log_directory=log_directory,
        node_report_directory=node_report_directory,
        legacy_node_report_directory=legacy_node_report_directory,
        uses_data_root_logs=uses_data_root_logs,
        certificate_check_only_supported=_supports_certificate_check_only(runtime_root),
        config_preflight_supported=_supports_config_preflight(runtime_root),
        # The inspected beta still reads the CA here. Its broad dataRoot comment
        # does not establish that certificates move with data-root logs.
        certificate_directory=server_root / "certs",
        # This is the native default only; EVEJS_GAMESTORE_DATA_DIR remains a
        # separate existing contract and is deliberately not inherited here.
        game_store_default_directory=runtime_root / "_local" / "gameStore",
    )


def _metadata_status(root: Path, version: str | None) -> MetadataStatus:
    if version is not None:
        return "valid"
    try:
        has_metadata = any((root / relative).exists() for relative in _VERSION_METADATA_PATHS)
    except OSError:
        has_metadata = True
    return "invalid_or_conflicting" if has_metadata else "missing"


def _read_known_source(root: Path, relative: Path) -> str | None:
    """Read one known file within root with a strict size and containment bound."""
    try:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            return None
        if path.stat().st_size > _MAX_SOURCE_BYTES:
            return None
        return path.read_text(encoding="utf-8-sig")
    except (OSError, RuntimeError, UnicodeError):
        return None


def _supports_data_root_logs(root: Path) -> bool:
    data_root = _read_known_source(root, Path("server/src/config/dataRoot.js"))
    logger = _read_known_source(root, Path("server/src/utils/logger/index.js"))
    if data_root is None or logger is None:
        return False

    # Require both the resolver implementation and the logger's actual use of
    # it. Merely shipping dataRoot.js (or mentioning logs in a comment) is not
    # enough evidence that server.log moved.
    return (
        re.search(r"\bDEFAULT_DATA_ROOT\s*=\s*path\.join\(REPO_ROOT,\s*['\"]_local['\"]\)", data_root)
        is not None
        and re.search(r"\bEVEJS_DATA_ROOT\b", data_root) is not None
        and re.search(r"\bfunction\s+resolveDataRootPath\s*\(", data_root) is not None
        and re.search(r"resolveDataRootPath\s*\(\s*['\"]logs['\"]\s*\)", logger) is not None
        and re.search(r"SERVER_LOG_PATH\s*=\s*path\.join\(LOG_DIR,\s*['\"]server\.log['\"]\)", logger)
        is not None
    )


def _supports_config_preflight(root: Path) -> bool:
    """Recognize the verified 0.12.9 read-only manager contract without loading it."""
    server_package = _read_known_source(root, Path("server/package.json"))
    manager = _read_known_source(root, Path("server/src/config/manager.js"))
    schema = _read_known_source(root, Path("server/src/config/schema/index.js"))
    if server_package is None or manager is None or schema is None:
        return False
    try:
        package = json.loads(server_package)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(package, dict) or package.get("version") != "0.12.9":
        return False

    manager_export = re.search(
        r"\bmodule\.exports\s*=\s*\{(?P<properties>[^}]{0,65536})\}",
        manager,
        flags=re.DOTALL,
    )
    return (
        re.search(r"\bconst\s+EVEJS_VERSION\s*=\s*['\"]0\.12\.9['\"]\s*;", manager)
        is not None
        and manager_export is not None
        and re.search(r"\bEVEJS_VERSION\b", manager_export.group("properties"))
        is not None
    )


def _supports_certificate_check_only(root: Path) -> bool:
    """Check a bounded PowerShell param declaration; never run the installer."""
    script = _read_known_source(
        root,
        Path("tools/ClientSETUP/scripts/Install-EvEJSCerts.ps1"),
    )
    if script is None:
        return False
    declaration = re.match(r"\s*param\s*\((.*?)\)", script, flags=re.IGNORECASE | re.DOTALL)
    if declaration is None:
        return False
    return re.search(
        r"\[\s*switch\s*\]\s*\$CheckOnly\b",
        declaration.group(1),
        flags=re.IGNORECASE,
    ) is not None


def _resolve_data_root(root: Path, override) -> Path:
    """Resolve an explicit data-root value with Node's child-cwd semantics."""
    if override is None:
        return root / "_local"
    try:
        value = str(override).strip()
    except Exception as exc:
        raise TypeError("data_root_override must be path-like or None") from exc
    if not value:
        return root / "_local"
    path = Path(value)
    if not path.is_absolute():
        path = root / "server" / path
    return path.resolve()
