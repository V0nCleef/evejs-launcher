"""Curated catalog of supported utilities in an EveJS installation.

The catalog intentionally exposes reviewed wrapper entrypoints only.  It never
recursively promotes arbitrary files from ``<evejs_root>/tools`` into actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from src.core.runtime.docker_tools import DockerToolAction
from src.core.service_status import DockerControlPolicy, RuntimeBackend


TOOL_CATEGORIES = (
    "Client & Setup",
    "Configuration",
    "Data & Content",
    "Market",
)


class ToolDispatchKind(Enum):
    """Reviewed dispatch boundary selected for one resolved action."""

    NATIVE_WRAPPER = "native_wrapper"
    DOCKER_COMPOSE = "docker_compose"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ToolAction:
    """One launcher-owned action exposed by a tool card."""

    id: str
    label: str
    arguments: tuple[str, ...] = ()
    risk_level: str = "standard"
    confirmation_title: str = ""
    confirmation_body: str = ""


@dataclass(frozen=True)
class ToolDefinition:
    """Immutable metadata for one reviewed external tool wrapper."""

    id: str
    name: str
    category: str
    source_folder: str
    description: str
    relative_entrypoint: Path
    icon: str
    accent_role: str
    prerequisite_label: str
    actions: tuple[ToolAction, ...]


@dataclass(frozen=True)
class ResolvedToolAction:
    """One backend-resolved action with an explicit dispatch capability."""

    action: ToolAction
    dispatch_kind: ToolDispatchKind
    docker_action: DockerToolAction | None = None
    available: bool = True
    unavailable_reason: str = ""

    @property
    def id(self) -> str:
        return self.action.id

    @property
    def label(self) -> str:
        return self.action.label

    @property
    def arguments(self) -> tuple[str, ...]:
        return self.action.arguments

    @property
    def risk_level(self) -> str:
        return self.action.risk_level

    @property
    def confirmation_title(self) -> str:
        return self.action.confirmation_title

    @property
    def confirmation_body(self) -> str:
        return self.action.confirmation_body


@dataclass(frozen=True)
class ResolvedTool:
    """A catalog definition resolved against the configured EveJS root."""

    definition: ToolDefinition
    absolute_entrypoint: Path | None
    available: bool
    unavailable_reason: str = ""
    actions: tuple[ResolvedToolAction, ...] = ()


# ── Catalog definitions ──────────────────────────────────────────────────────


def _launch_action() -> tuple[ToolAction, ...]:
    return (ToolAction(id="launch", label="Launch"),)


_SUPPORTED_TOOLS = (
    ToolDefinition(
        id="client-setup-wizard",
        name="Client Setup Wizard",
        category="Client & Setup",
        source_folder="ClientSETUP",
        description="Prepare client paths, certificates, blue.dll, and start.ini.",
        relative_entrypoint=Path("ClientSETUP/StartClientSetup.bat"),
        icon="CS",
        accent_role="client",
        prerequisite_label="Modifies client",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="blue-dll-patcher",
        name="Blue DLL Patcher",
        category="Client & Setup",
        source_folder="ClientSETUP",
        description="Open the guided patcher for a selected EVE blue.dll file.",
        relative_entrypoint=Path("ClientSETUP/PatchBlueDllGui.bat"),
        icon="BL",
        accent_role="client",
        prerequisite_label="Modifies client",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="client-code-grabber",
        name="Client Code Grabber",
        category="Client & Setup",
        source_folder="ClientCodeGrabber",
        description="GUI for extracting, decompressing, and decompiling client code.",
        relative_entrypoint=Path("ClientCodeGrabber/StartCodeGrabber.bat"),
        icon="CG",
        accent_role="client",
        prerequisite_label="Python required",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="server-config-editor",
        name="Server Config Editor",
        category="Configuration",
        source_folder="ConfigEditor",
        description=(
            "Edit server configuration and local or live database values. "
            "Docker Desktop must be running for containerized installs."
        ),
        relative_entrypoint=Path("ConfigEditor/OpenServerConfig.bat"),
        icon="CF",
        accent_role="configuration",
        prerequisite_label="Docker for containers",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="local-database-creator",
        name="Local Database Creator",
        category="Data & Content",
        source_folder="DatabaseCreator",
        description="Download SDE data and generate local database content.",
        relative_entrypoint=Path("DatabaseCreator/CreateDatabase.bat"),
        icon="DB",
        accent_role="data",
        prerequisite_label="Downloads / writes data",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="reset-local-databases",
        name="Reset Local Databases",
        category="Data & Content",
        source_folder="DatabaseCreator",
        description="Preview or remove generated databases and mutable chat state.",
        relative_entrypoint=Path("DatabaseCreator/ResetLocalDatabases.bat"),
        icon="RS",
        accent_role="danger",
        prerequisite_label="Destructive",
        actions=(
            ToolAction(id="preview", label="Preview", arguments=("/whatif",)),
            ToolAction(
                id="reset",
                label="Reset…",
                risk_level="destructive",
                confirmation_title="Reset local databases?",
                confirmation_body=(
                    "This removes generated local database output and mutable chat "
                    "runtime state. This action cannot be undone."
                ),
            ),
        ),
    ),
    ToolDefinition(
        id="new-eden-store-editor",
        name="New Eden Store Editor",
        category="Data & Content",
        source_folder="NewEdenStoreEditor",
        description="Edit New Eden Store catalog and configuration data.",
        relative_entrypoint=Path("NewEdenStoreEditor/StartStoreEditor.bat"),
        icon="NE",
        accent_role="data",
        prerequisite_label="Python required",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="market-seed-builder",
        name="Market Seed Builder",
        category="Market",
        source_folder="market-seed",
        description="Interactive menu for market builds, smoke tests, and diagnostics.",
        relative_entrypoint=Path("market-seed/BuildMarketSeed.bat"),
        icon="MB",
        accent_role="market",
        prerequisite_label="Interactive console",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="market-seed-builder-gui",
        name="Market Seed Builder GUI",
        category="Market",
        source_folder="market-seed",
        description="Open the graphical market-seed build and diagnostics controls.",
        relative_entrypoint=Path("market-seed/BuildMarketSeedGui.bat"),
        icon="MG",
        accent_role="market",
        prerequisite_label="Writes market data",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="tq-market-snapshot-seeder-v2",
        name="TQ Market Snapshot Seeder v2",
        category="Market",
        source_folder="market-seederv2",
        description="Build and inspect public TQ market snapshot seed data.",
        relative_entrypoint=Path("market-seederv2/BuildMarketSeedV2.bat"),
        icon="TQ",
        accent_role="market",
        prerequisite_label="Writes market data",
        actions=_launch_action(),
    ),
    ToolDefinition(
        id="rust-msvc-market-setup",
        name="Rust & MSVC Market Setup",
        category="Market",
        source_folder="tools",
        description="Install or repair Rust, MSVC Build Tools, and Windows SDK support.",
        relative_entrypoint=Path("InstallRustForMarket.bat"),
        icon="RU",
        accent_role="system",
        prerequisite_label="Administrator / system changes",
        actions=(
            ToolAction(
                id="launch",
                label="Launch",
                risk_level="system",
                confirmation_title="Start system toolchain setup?",
                confirmation_body=(
                    "This utility may request Administrator permission and install or "
                    "repair Rust, Visual Studio C++ Build Tools, and Windows SDK support."
                ),
            ),
        ),
    ),
)


_MANAGED_DOCKER_HOST_WRAPPERS = frozenset(
    {
        "client-setup-wizard",
        "blue-dll-patcher",
        "client-code-grabber",
        "server-config-editor",
    }
)
_CONNECT_ONLY_HOST_WRAPPERS = frozenset(
    {
        "client-setup-wizard",
        "blue-dll-patcher",
        "client-code-grabber",
    }
)
_DOCKER_UNAVAILABLE_REASONS = {
    "reset-local-databases": (
        "Docker volume reset is not supported by the launcher."
    ),
    "new-eden-store-editor": (
        "This host editor is not proven to target the selected Docker runtime."
    ),
    "market-seed-builder-gui": (
        "The host market GUI does not target the selected Docker runtime."
    ),
    "rust-msvc-market-setup": (
        "Docker owns its toolchain; host Rust and MSVC setup is not applicable."
    ),
}
_CONNECT_ONLY_REASON = (
    "Connect-only Docker mode cannot change container, configuration, or data state."
)

_DOCKER_ACTIONS: dict[
    str,
    tuple[tuple[ToolAction, DockerToolAction], ...],
] = {
    "local-database-creator": (
        (
            ToolAction(
                id="initialize",
                label="Initialize…",
                risk_level="consequential",
                confirmation_title="Initialize Docker game data?",
                confirmation_body=(
                    "This runs the selected Compose project's idempotent game-data "
                    "initializer while Server and Market are stopped."
                ),
            ),
            DockerToolAction.INITIALIZE_DATABASE,
        ),
    ),
    "market-seed-builder": (
        (
            ToolAction(id="status", label="Status"),
            DockerToolAction.MARKET_STATUS,
        ),
        (
            ToolAction(
                id="doctor",
                label="Doctor…",
                risk_level="consequential",
                confirmation_title="Validate the Docker market database?",
                confirmation_body=(
                    "This runs the offline market doctor against the selected Compose "
                    "project while Server and Market are stopped."
                ),
            ),
            DockerToolAction.MARKET_DOCTOR,
        ),
        (
            ToolAction(
                id="backup",
                label="Backup…",
                risk_level="consequential",
                confirmation_title="Back up the Docker market database?",
                confirmation_body=(
                    "This creates a retained market backup in the selected Compose "
                    "project while Server and Market are stopped."
                ),
            ),
            DockerToolAction.MARKET_BACKUP,
        ),
        (
            ToolAction(id="backups", label="List Backups"),
            DockerToolAction.MARKET_BACKUPS,
        ),
        (
            ToolAction(id="presets", label="List Presets"),
            DockerToolAction.MARKET_PRESETS,
        ),
        (
            ToolAction(
                id="rebuild-v1-jita",
                label="Rebuild v1 — Jita…",
                risk_level="destructive",
                confirmation_title="Rebuild Docker market with the Jita preset?",
                confirmation_body=(
                    "This replaces seeded liquidity, player orders, events, and history "
                    "using the explicit Jita + New Caldari preset."
                ),
            ),
            DockerToolAction.MARKET_REBUILD_V1_JITA,
        ),
        (
            ToolAction(
                id="rebuild-v1-full-universe",
                label="Rebuild v1 — Full Universe…",
                risk_level="destructive",
                confirmation_title="Rebuild the full Docker market?",
                confirmation_body=(
                    "This replaces seeded liquidity, player orders, events, and history "
                    "using the explicit full-universe preset."
                ),
            ),
            DockerToolAction.MARKET_REBUILD_V1_FULL_UNIVERSE,
        ),
        (
            ToolAction(
                id="restore-latest",
                label="Restore Latest…",
                risk_level="destructive",
                confirmation_title="Restore the latest Docker market backup?",
                confirmation_body=(
                    "This replaces the active market database with the latest retained "
                    "backup while Server and Market are stopped."
                ),
            ),
            DockerToolAction.MARKET_RESTORE_LATEST,
        ),
    ),
    "tq-market-snapshot-seeder-v2": (
        (
            ToolAction(id="snapshot-info", label="Snapshot Info"),
            DockerToolAction.MARKET_SNAPSHOT_INFO,
        ),
        (
            ToolAction(
                id="rebuild-v2",
                label="Rebuild v2…",
                risk_level="destructive",
                confirmation_title="Rebuild the Docker market from the v2 snapshot?",
                confirmation_body=(
                    "This replaces seeded liquidity, player orders, events, and history "
                    "using the reviewed v2 snapshot importer."
                ),
            ),
            DockerToolAction.MARKET_REBUILD_V2,
        ),
    ),
}


# ── Public catalog operations ────────────────────────────────────────────────


def supported_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return the reviewed tool definitions in stable presentation order."""
    return _SUPPORTED_TOOLS


def resolve_tools(
    evejs_root: str | Path | None,
    *,
    backend: RuntimeBackend = RuntimeBackend.NATIVE,
    docker_policy: DockerControlPolicy = DockerControlPolicy.CONNECT_ONLY,
    compose_file: str | Path | None = None,
) -> tuple[ResolvedTool, ...]:
    """Resolve every reviewed action for the selected runtime without raising."""
    root_text = str(evejs_root or "").strip()
    if not root_text:
        return _unavailable_tools(None, "Set the EveJS root in Settings")
    # Python 3.11.0 on Windows can silently truncate a path at NUL during
    # ``Path.resolve`` instead of raising.  Reject it before resolution so a
    # malformed configured root can never produce candidates under a different
    # prefix path.
    if "\x00" in root_text:
        return _unavailable_tools(None, "Configured EveJS root was not found")

    try:
        root = Path(root_text).expanduser().resolve(strict=False)
        tools_root = (root / "tools").resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return _unavailable_tools(
            None,
            "Configured EveJS root was not found",
        )
    if not root.is_dir():
        return _unavailable_tools(
            tools_root,
            "Configured EveJS root was not found",
        )

    wrappers: list[ResolvedTool] = []
    tools_available = tools_root.is_dir()
    for definition in _SUPPORTED_TOOLS:
        candidate = _resolve_tool_candidate(tools_root, definition)
        if candidate is None:
            wrappers.append(
                _resolved_wrapper(
                    definition,
                    None,
                    available=False,
                    reason="Unsupported tool path",
                )
            )
            continue

        available = tools_available and candidate.is_file()
        reason = "" if available else (
            "Not installed"
            if tools_available
            else "Supported tools folder was not found"
        )
        wrappers.append(
            _resolved_wrapper(
                definition,
                candidate,
                available=available,
                reason=reason,
            )
        )

    if backend is RuntimeBackend.NATIVE:
        return tuple(wrappers)
    if backend is not RuntimeBackend.DOCKER_COMPOSE:
        return _unavailable_tools(tools_root, "Unsupported runtime backend")

    compose_available, compose_reason = _docker_compose_availability(
        root,
        compose_file,
    )
    return tuple(
        _resolve_docker_tool(
            wrapper,
            policy=docker_policy,
            compose_available=compose_available,
            compose_reason=compose_reason,
        )
        for wrapper in wrappers
    )


def filter_tools(
    tools: Iterable[ResolvedTool],
    query: str = "",
    category: str | None = None,
) -> tuple[ResolvedTool, ...]:
    """Filter resolved tools by presented metadata and semantic category."""
    query_key = query.strip().casefold()
    category_key = (category or "").strip().casefold()
    if category_key in {"", "all", "all categories"}:
        category_key = ""

    matches: list[ResolvedTool] = []
    for tool in tools:
        definition = tool.definition
        if category_key and definition.category.casefold() != category_key:
            continue
        searchable = " ".join(
            (
                definition.name,
                definition.description,
                definition.category,
                definition.source_folder,
            )
        ).casefold()
        if query_key and query_key not in searchable:
            continue
        matches.append(tool)
    return tuple(matches)


# ── Resolution helpers ───────────────────────────────────────────────────────


def _resolved_wrapper(
    definition: ToolDefinition,
    candidate: Path | None,
    *,
    available: bool,
    reason: str,
) -> ResolvedTool:
    dispatch_kind = (
        ToolDispatchKind.NATIVE_WRAPPER
        if available
        else ToolDispatchKind.UNAVAILABLE
    )
    actions = tuple(
        ResolvedToolAction(
            action,
            dispatch_kind,
            available=available,
            unavailable_reason="" if available else reason,
        )
        for action in definition.actions
    )
    return ResolvedTool(
        definition,
        candidate,
        available,
        "" if available else reason,
        actions,
    )


def _resolve_docker_tool(
    wrapper: ResolvedTool,
    *,
    policy: DockerControlPolicy,
    compose_available: bool,
    compose_reason: str,
) -> ResolvedTool:
    tool_id = wrapper.definition.id
    host_wrappers = (
        _MANAGED_DOCKER_HOST_WRAPPERS
        if policy is DockerControlPolicy.MANAGED
        else _CONNECT_ONLY_HOST_WRAPPERS
    )
    if tool_id in host_wrappers:
        return wrapper

    semantic_actions = _DOCKER_ACTIONS.get(tool_id)
    if policy is not DockerControlPolicy.MANAGED:
        actions = tuple(
            ResolvedToolAction(
                action,
                ToolDispatchKind.UNAVAILABLE,
                available=False,
                unavailable_reason=_CONNECT_ONLY_REASON,
            )
            for action in (
                tuple(item[0] for item in semantic_actions)
                if semantic_actions is not None
                else wrapper.definition.actions
            )
        )
        return ResolvedTool(
            wrapper.definition,
            wrapper.absolute_entrypoint,
            False,
            _CONNECT_ONLY_REASON,
            actions,
        )

    if semantic_actions is not None:
        actions = tuple(
            ResolvedToolAction(
                action,
                (
                    ToolDispatchKind.DOCKER_COMPOSE
                    if compose_available
                    else ToolDispatchKind.UNAVAILABLE
                ),
                docker_action if compose_available else None,
                available=compose_available,
                unavailable_reason="" if compose_available else compose_reason,
            )
            for action, docker_action in semantic_actions
        )
        return ResolvedTool(
            wrapper.definition,
            wrapper.absolute_entrypoint,
            compose_available,
            "" if compose_available else compose_reason,
            actions,
        )

    reason = _DOCKER_UNAVAILABLE_REASONS.get(
        tool_id,
        "This tool has no reviewed operation for Managed Docker.",
    )
    actions = tuple(
        ResolvedToolAction(
            action,
            ToolDispatchKind.UNAVAILABLE,
            available=False,
            unavailable_reason=reason,
        )
        for action in wrapper.definition.actions
    )
    return ResolvedTool(
        wrapper.definition,
        wrapper.absolute_entrypoint,
        False,
        reason,
        actions,
    )


def _docker_compose_availability(
    root: Path,
    compose_file: str | Path | None,
) -> tuple[bool, str]:
    compose_text = str(compose_file or "").strip()
    try:
        compose = (
            Path(compose_text).expanduser()
            if compose_text
            else root / "compose.yaml"
        )
        if not compose.is_absolute():
            return False, "Selected Compose file must be absolute"
        compose = compose.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False, "Selected Compose file was not found"
    if not compose.is_file():
        return False, "Selected Compose file was not found"
    return True, ""


def _resolve_tool_candidate(
    tools_root: Path,
    definition: ToolDefinition,
) -> Path | None:
    """Resolve one catalog path safely and keep it contained under tools."""
    try:
        candidate = (tools_root / definition.relative_entrypoint).resolve(strict=False)
        candidate.relative_to(tools_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate


def _unavailable_tools(
    tools_root: Path | None,
    reason: str,
) -> tuple[ResolvedTool, ...]:
    resolved: list[ResolvedTool] = []
    for definition in _SUPPORTED_TOOLS:
        candidate = (
            _resolve_tool_candidate(tools_root, definition)
            if tools_root is not None
            else None
        )
        resolved.append(
            _resolved_wrapper(
                definition,
                candidate,
                available=False,
                reason=reason,
            )
        )
    return tuple(resolved)
