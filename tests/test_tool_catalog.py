"""Tests for the curated EveJS external-tool catalog."""
from __future__ import annotations

from pathlib import Path, PurePath

from src.core.tool_catalog import (
    TOOL_CATEGORIES,
    ToolDispatchKind,
    filter_tools,
    resolve_tools,
    supported_tool_definitions,
)
from src.core.runtime.docker_tools import DockerToolAction
from src.core.service_status import DockerControlPolicy, RuntimeBackend


EXPECTED_TOOL_IDS = (
    "client-setup-wizard",
    "blue-dll-patcher",
    "client-code-grabber",
    "server-config-editor",
    "local-database-creator",
    "reset-local-databases",
    "new-eden-store-editor",
    "market-seed-builder",
    "market-seed-builder-gui",
    "tq-market-snapshot-seeder-v2",
    "rust-msvc-market-setup",
)

EXPECTED_ENTRYPOINTS = {
    "client-setup-wizard": Path("ClientSETUP/StartClientSetup.bat"),
    "blue-dll-patcher": Path("ClientSETUP/PatchBlueDllGui.bat"),
    "client-code-grabber": Path("ClientCodeGrabber/StartCodeGrabber.bat"),
    "server-config-editor": Path("ConfigEditor/OpenServerConfig.bat"),
    "local-database-creator": Path("DatabaseCreator/CreateDatabase.bat"),
    "reset-local-databases": Path("DatabaseCreator/ResetLocalDatabases.bat"),
    "new-eden-store-editor": Path("NewEdenStoreEditor/StartStoreEditor.bat"),
    "market-seed-builder": Path("market-seed/BuildMarketSeed.bat"),
    "market-seed-builder-gui": Path("market-seed/BuildMarketSeedGui.bat"),
    "tq-market-snapshot-seeder-v2": Path(
        "market-seederv2/BuildMarketSeedV2.bat"
    ),
    "rust-msvc-market-setup": Path("InstallRustForMarket.bat"),
}


def _install_wrappers(root: Path, tool_ids: tuple[str, ...] = EXPECTED_TOOL_IDS) -> None:
    for tool_id in tool_ids:
        wrapper = root / "tools" / EXPECTED_ENTRYPOINTS[tool_id]
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")


def test_supported_catalog_has_the_verified_tools_in_stable_order() -> None:
    definitions = supported_tool_definitions()

    assert tuple(tool.id for tool in definitions) == EXPECTED_TOOL_IDS
    assert len({tool.id for tool in definitions}) == len(definitions)
    assert TOOL_CATEGORIES == (
        "Client & Setup",
        "Configuration",
        "Data & Content",
        "Market",
    )
    assert tuple(dict.fromkeys(tool.category for tool in definitions)) == TOOL_CATEGORIES

    for definition in definitions:
        entrypoint = PurePath(definition.relative_entrypoint)
        assert definition.name
        assert definition.description
        assert definition.source_folder
        assert definition.category in TOOL_CATEGORIES
        assert not entrypoint.is_absolute()
        assert entrypoint.suffix.casefold() == ".bat"
        assert ".." not in entrypoint.parts
        assert definition.actions
        assert "G:" not in str(entrypoint)


def test_catalog_exposes_only_curated_wrappers(tmp_path: Path) -> None:
    _install_wrappers(tmp_path)
    internal = tmp_path / "tools" / "ServerStack" / "EnsureMarketServer.ps1"
    internal.parent.mkdir(parents=True)
    internal.write_text("# internal\n", encoding="utf-8")
    nested = tmp_path / "tools" / "ClientSETUP" / "scripts" / "InstallCerts.bat"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("@echo off\n", encoding="utf-8")
    unknown = tmp_path / "tools" / "RunAnything.bat"
    unknown.write_text("@echo off\n", encoding="utf-8")

    resolved = resolve_tools(tmp_path)

    assert tuple(tool.definition.id for tool in resolved) == EXPECTED_TOOL_IDS
    resolved_paths = {tool.absolute_entrypoint for tool in resolved}
    assert internal not in resolved_paths
    assert nested not in resolved_paths
    assert unknown not in resolved_paths


def test_resolution_uses_the_supplied_root_and_reports_missing_wrapper(tmp_path: Path) -> None:
    _install_wrappers(tmp_path, tuple(tool for tool in EXPECTED_TOOL_IDS if tool != "blue-dll-patcher"))

    resolved = resolve_tools(tmp_path)
    by_id = {tool.definition.id: tool for tool in resolved}

    assert by_id["client-setup-wizard"].absolute_entrypoint == (
        tmp_path / "tools" / EXPECTED_ENTRYPOINTS["client-setup-wizard"]
    )
    assert by_id["client-setup-wizard"].available is True
    assert by_id["client-setup-wizard"].unavailable_reason == ""
    assert by_id["blue-dll-patcher"].available is False
    assert by_id["blue-dll-patcher"].unavailable_reason == "Not installed"
    assert all("G:" not in str(tool.absolute_entrypoint) for tool in resolved)


def test_missing_root_returns_truthful_unavailable_states(tmp_path: Path) -> None:
    resolved = resolve_tools(tmp_path / "missing")

    assert len(resolved) == len(EXPECTED_TOOL_IDS)
    assert all(tool.available is False for tool in resolved)
    assert {tool.unavailable_reason for tool in resolved} == {
        "Configured EveJS root was not found"
    }


def test_blank_root_returns_truthful_unavailable_states() -> None:
    resolved = resolve_tools("")

    assert len(resolved) == len(EXPECTED_TOOL_IDS)
    assert all(tool.available is False for tool in resolved)
    assert all(tool.absolute_entrypoint is None for tool in resolved)
    assert {tool.unavailable_reason for tool in resolved} == {
        "Set the EveJS root in Settings"
    }


def test_invalid_root_syntax_returns_unavailable_states_without_raising() -> None:
    resolved = resolve_tools("invalid\x00root")

    assert len(resolved) == len(EXPECTED_TOOL_IDS)
    assert all(tool.available is False for tool in resolved)
    assert all(tool.absolute_entrypoint is None for tool in resolved)
    assert {tool.unavailable_reason for tool in resolved} == {
        "Configured EveJS root was not found"
    }


def test_candidate_resolution_failure_marks_only_that_tool_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_wrappers(tmp_path)
    path_type = type(tmp_path)
    original_resolve = path_type.resolve

    def resolve_with_one_failure(self: Path, strict: bool = False) -> Path:
        if self.name == "StartClientSetup.bat":
            raise OSError("reparse point could not be resolved")
        return original_resolve(self, strict=strict)

    monkeypatch.setattr(path_type, "resolve", resolve_with_one_failure)

    resolved = resolve_tools(tmp_path)
    by_id = {tool.definition.id: tool for tool in resolved}

    failed = by_id["client-setup-wizard"]
    assert failed.available is False
    assert failed.absolute_entrypoint is None
    assert failed.unavailable_reason == "Unsupported tool path"
    assert all(
        tool.available
        for tool_id, tool in by_id.items()
        if tool_id != "client-setup-wizard"
    )


def test_missing_tools_folder_returns_truthful_unavailable_states(tmp_path: Path) -> None:
    resolved = resolve_tools(tmp_path)

    assert len(resolved) == len(EXPECTED_TOOL_IDS)
    assert all(tool.available is False for tool in resolved)
    assert {tool.unavailable_reason for tool in resolved} == {
        "Supported tools folder was not found"
    }


def test_reset_and_system_setup_actions_encode_guardrails() -> None:
    definitions = {tool.id: tool for tool in supported_tool_definitions()}

    reset_actions = definitions["reset-local-databases"].actions
    assert tuple(action.id for action in reset_actions) == ("preview", "reset")
    assert reset_actions[0].arguments == ("/whatif",)
    assert reset_actions[0].confirmation_title == ""
    assert reset_actions[1].arguments == ()
    assert reset_actions[1].risk_level == "destructive"
    assert reset_actions[1].confirmation_title
    assert reset_actions[1].confirmation_body

    system_action = definitions["rust-msvc-market-setup"].actions[0]
    assert system_action.risk_level == "system"
    assert system_action.confirmation_title
    assert system_action.confirmation_body


def test_filtering_is_case_insensitive_and_matches_all_presented_metadata(
    tmp_path: Path,
) -> None:
    _install_wrappers(tmp_path)
    tools = resolve_tools(tmp_path)

    assert tuple(tool.definition.id for tool in filter_tools(tools, "wizard", None)) == (
        "client-setup-wizard",
    )
    assert tuple(tool.definition.id for tool in filter_tools(tools, "MARKET", None)) == (
        "market-seed-builder",
        "market-seed-builder-gui",
        "tq-market-snapshot-seeder-v2",
        "rust-msvc-market-setup",
    )
    assert tuple(tool.definition.id for tool in filter_tools(tools, "clientsetup", None)) == (
        "client-setup-wizard",
        "blue-dll-patcher",
    )
    assert tuple(tool.definition.id for tool in filter_tools(tools, "decompiling", None)) == (
        "client-code-grabber",
    )
    assert tuple(
        tool.definition.id
        for tool in filter_tools(tools, "", "data & CONTENT")
    ) == (
        "local-database-creator",
        "reset-local-databases",
        "new-eden-store-editor",
    )
    assert filter_tools(tools, "does-not-exist", None) == ()


def test_native_capabilities_preserve_every_existing_wrapper_action(
    tmp_path: Path,
) -> None:
    _install_wrappers(tmp_path)

    resolved = resolve_tools(
        tmp_path,
        backend=RuntimeBackend.NATIVE,
        docker_policy=DockerControlPolicy.CONNECT_ONLY,
    )

    assert all(tool.available for tool in resolved)
    for tool in resolved:
        assert tuple(action.action for action in tool.actions) == tool.definition.actions
        assert all(
            action.dispatch_kind is ToolDispatchKind.NATIVE_WRAPPER
            for action in tool.actions
        )


def test_managed_docker_maps_container_operations_and_disables_unsupported_items(
    tmp_path: Path,
) -> None:
    _install_wrappers(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    by_id = {
        tool.definition.id: tool
        for tool in resolve_tools(
            tmp_path,
            backend=RuntimeBackend.DOCKER_COMPOSE,
            docker_policy=DockerControlPolicy.MANAGED,
            compose_file=compose,
        )
    }

    for tool_id in (
        "client-setup-wizard",
        "blue-dll-patcher",
        "client-code-grabber",
        "server-config-editor",
    ):
        assert by_id[tool_id].actions[0].dispatch_kind is ToolDispatchKind.NATIVE_WRAPPER
        assert by_id[tool_id].actions[0].available

    database = by_id["local-database-creator"].actions
    assert tuple(action.docker_action for action in database) == (
        DockerToolAction.INITIALIZE_DATABASE,
    )
    market_actions = tuple(
        action.docker_action for action in by_id["market-seed-builder"].actions
    )
    assert market_actions == (
        DockerToolAction.MARKET_STATUS,
        DockerToolAction.MARKET_DOCTOR,
        DockerToolAction.MARKET_BACKUP,
        DockerToolAction.MARKET_BACKUPS,
        DockerToolAction.MARKET_PRESETS,
        DockerToolAction.MARKET_REBUILD_V1_JITA,
        DockerToolAction.MARKET_REBUILD_V1_FULL_UNIVERSE,
        DockerToolAction.MARKET_RESTORE_LATEST,
    )
    snapshot_actions = {
        action.docker_action
        for action in by_id["tq-market-snapshot-seeder-v2"].actions
    }
    assert snapshot_actions == {
        DockerToolAction.MARKET_SNAPSHOT_INFO,
        DockerToolAction.MARKET_REBUILD_V2,
    }
    mapped_actions = [
        action.docker_action
        for tool in by_id.values()
        for action in tool.actions
        if action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE
    ]
    assert len(mapped_actions) == len(DockerToolAction)
    assert set(mapped_actions) == set(DockerToolAction)

    for tool_id in (
        "reset-local-databases",
        "new-eden-store-editor",
        "market-seed-builder-gui",
        "rust-msvc-market-setup",
    ):
        tool = by_id[tool_id]
        assert not tool.available
        assert tool.unavailable_reason
        assert all(
            action.dispatch_kind is ToolDispatchKind.UNAVAILABLE
            for action in tool.actions
        )


def test_connect_only_docker_preserves_host_client_tools_but_maps_no_container_action(
    tmp_path: Path,
) -> None:
    _install_wrappers(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    tools = resolve_tools(
        tmp_path,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_policy=DockerControlPolicy.CONNECT_ONLY,
        compose_file=compose,
    )
    by_id = {tool.definition.id: tool for tool in tools}

    for tool_id in (
        "client-setup-wizard",
        "blue-dll-patcher",
        "client-code-grabber",
    ):
        assert by_id[tool_id].available
        assert all(
            action.dispatch_kind is ToolDispatchKind.NATIVE_WRAPPER
            for action in by_id[tool_id].actions
        )
    for tool_id in set(EXPECTED_TOOL_IDS) - {
        "client-setup-wizard",
        "blue-dll-patcher",
        "client-code-grabber",
    }:
        assert not by_id[tool_id].available
        assert by_id[tool_id].unavailable_reason

    assert all(
        action.dispatch_kind is not ToolDispatchKind.DOCKER_COMPOSE
        and action.docker_action is None
        for tool in tools
        for action in tool.actions
    )


def test_every_backend_tool_action_is_mapped_or_explicitly_unavailable(
    tmp_path: Path,
) -> None:
    _install_wrappers(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    for backend, policy in (
        (RuntimeBackend.NATIVE, DockerControlPolicy.CONNECT_ONLY),
        (RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.MANAGED),
        (RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.CONNECT_ONLY),
    ):
        tools = resolve_tools(
            tmp_path,
            backend=backend,
            docker_policy=policy,
            compose_file=compose,
        )
        assert len(tools) == len(EXPECTED_TOOL_IDS)
        for tool in tools:
            assert tool.actions
            for action in tool.actions:
                assert action.dispatch_kind in ToolDispatchKind
                if action.dispatch_kind is ToolDispatchKind.UNAVAILABLE:
                    assert action.unavailable_reason
                else:
                    assert action.available
