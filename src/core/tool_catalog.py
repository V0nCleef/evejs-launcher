"""Curated catalog of supported utilities in an EveJS installation.

The catalog intentionally exposes reviewed wrapper entrypoints only.  It never
recursively promotes arbitrary files from ``<evejs_root>/tools`` into actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TOOL_CATEGORIES = (
    "Client & Setup",
    "Configuration",
    "Data & Content",
    "Market",
)


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
class ResolvedTool:
    """A catalog definition resolved against the configured EveJS root."""

    definition: ToolDefinition
    absolute_entrypoint: Path | None
    available: bool
    unavailable_reason: str = ""


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


# ── Public catalog operations ────────────────────────────────────────────────


def supported_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return the reviewed tool definitions in stable presentation order."""
    return _SUPPORTED_TOOLS


def resolve_tools(evejs_root: str | Path | None) -> tuple[ResolvedTool, ...]:
    """Resolve every supported wrapper against *evejs_root* without raising."""
    root_text = str(evejs_root or "").strip()
    if not root_text:
        return _unavailable_tools(None, "Set the EveJS root in Settings")

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
    if not tools_root.is_dir():
        return _unavailable_tools(
            tools_root,
            "Supported tools folder was not found",
        )

    resolved: list[ResolvedTool] = []
    for definition in _SUPPORTED_TOOLS:
        candidate = _resolve_tool_candidate(tools_root, definition)
        if candidate is None:
            resolved.append(
                ResolvedTool(
                    definition=definition,
                    absolute_entrypoint=None,
                    available=False,
                    unavailable_reason="Unsupported tool path",
                )
            )
            continue

        available = candidate.is_file()
        resolved.append(
            ResolvedTool(
                definition=definition,
                absolute_entrypoint=candidate,
                available=available,
                unavailable_reason="" if available else "Not installed",
            )
        )
    return tuple(resolved)


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
            ResolvedTool(
                definition=definition,
                absolute_entrypoint=candidate,
                available=False,
                unavailable_reason=reason,
            )
        )
    return tuple(resolved)
