"""Phase 3 Docker runtime data-source contract tests."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import MappingProxyType

import pytest

from src.core.runtime.data import (
    DataSourceError,
    DockerExportDataSource,
    SqliteGameStoreDataSource,
    inspect_docker_data_source,
    docker_project_identity,
    docker_settings_identity,
    select_docker_data_source,
)
from src.core.runtime.docker_cli import DockerCommandError, DockerCommandResult
from src.core.runtime.docker_compose import (
    ComposeTarget,
    ContainerRecord,
    PreflightReport,
    parse_compose_config,
)
from src.core.service_status import DockerControlPolicy, ServiceState


_FIXTURE_CHARACTER_ID = 900000001
_EXPORT_TIMEOUT = 7.5


class FakeRunner:
    """Record exact Docker argv and return injected bounded results."""

    executable = "docker.exe"

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    def run(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        timeout: float = 10.0,
    ) -> DockerCommandResult:
        self.calls.append((args, cwd, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, DockerCommandResult):
            return outcome
        return DockerCommandResult(
            (self.executable, *args),
            0,
            str(outcome),
            "",
            False,
            False,
        )


def _target(tmp_path: Path, project_name: str = "fixture-world") -> ComposeTarget:
    return ComposeTarget(
        tmp_path / "compose.yaml",
        tmp_path,
        project_name,
    )


def _record(raw_state: str) -> ContainerRecord:
    state = ServiceState.ONLINE if raw_state == "running" else ServiceState.OFFLINE
    return ContainerRecord(
        "server",
        "fixture-world-server-1",
        "0123456789ab",
        state,
        "healthy" if raw_state == "running" else None,
        0 if raw_state == "exited" else None,
        (),
        True,
        raw_state,
    )


def _export_payload() -> dict[str, object]:
    return {
        "generatedAt": "2026-01-01T00:00:00.000Z",
        "paths": {
            "databasePath": "/private/container/path",
            "staticDataRoot": "/private/container/static",
        },
        "players": [
            {
                "characterId": str(_FIXTURE_CHARACTER_ID),
                "characterName": "Fixture Pilot",
                "accountId": 501,
                "accountName": "fixture-account",
                "banned": False,
                "isGM": True,
                "shipName": "Fixture Frigate",
                "solarSystemName": "Fixture System",
                "balance": 1234.5,
                "skillPoints": 9876,
                "securityStatus": -1.25,
                "privateToken": "synthetic-private-value",
            }
        ],
        "selectedCharacterId": str(_FIXTURE_CHARACTER_ID),
        "selectedPlayer": {
            "characterId": str(_FIXTURE_CHARACTER_ID),
            "accountName": "fixture-account",
            "character": {
                "characterName": "Fixture Pilot",
                "balance": 1234.5,
                "shipName": "Fixture Frigate",
            },
            "account": {
                "password": "synthetic-private-value",
            },
        },
        "privateToken": "synthetic-private-value",
    }


def _config_payload(mounts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "name": "fixture-world",
        "services": {
            "server": {
                "healthcheck": {"test": ["CMD", "fixture-health"]},
                "volumes": mounts,
                "ports": [
                    {"host_ip": "127.0.0.1", "published": 32600, "target": 26000},
                    {"host_ip": "127.0.0.1", "published": 32601, "target": 26001},
                    {"host_ip": "127.0.0.1", "published": 32602, "target": 26002},
                    {"host_ip": "127.0.0.1", "published": 32603, "target": 26003},
                    {"host_ip": "127.0.0.1", "published": 35222, "target": 5222},
                ],
            },
            "market": {
                "ports": [
                    {"host_ip": "127.0.0.1", "published": 44110, "target": 40110}
                ]
            },
        },
    }


def _source(
    tmp_path: Path,
    raw_state: str,
    runner: FakeRunner,
    *,
    output_limit: int = 1024 * 1024,
    control_policy: DockerControlPolicy = DockerControlPolicy.CONNECT_ONLY,
) -> DockerExportDataSource:
    target = _target(tmp_path)
    return DockerExportDataSource(
        target,
        _record(raw_state),
        runner,
        docker_project_identity(target, "fixture-world"),
        control_policy=control_policy,
        timeout=_EXPORT_TIMEOUT,
        output_limit=output_limit,
    )


def _command_error(
    *,
    returncode: int | None,
    timed_out: bool = False,
    private_stderr: str = "",
) -> DockerCommandError:
    result = DockerCommandResult(
        ("docker.exe", "compose"),
        returncode,
        "",
        private_stderr,
        False,
        timed_out,
    )
    return DockerCommandError("compose", result)


def test_running_container_read_uses_exact_allowlisted_exec(tmp_path: Path) -> None:
    runner = FakeRunner([json.dumps(_export_payload())])
    source = _source(tmp_path, "running", runner)

    accounts = source.load_accounts()

    expected_tail = (
        "exec",
        "-T",
        "-e",
        "EVEJS_CONFIG_CLI_DOCKER_PROXY=1",
        "server",
        "node",
        "/app/tools/ConfigEditor/config-manager-cli.js",
        "database-export",
    )
    assert runner.calls == [
        (
            _target(tmp_path).compose_args(runner.executable, *expected_tail),
            tmp_path.resolve(),
            _EXPORT_TIMEOUT,
        )
    ]
    assert len(accounts) == 1
    account = accounts[0]
    assert (account.username, account.account_id, account.role, account.banned) == (
        "fixture-account",
        501,
        "gm",
        False,
    )
    assert len(account.characters) == 1
    character = account.characters[0]
    assert (
        character.char_id,
        character.name,
        character.ship_name,
        character.location,
    ) == (
        _FIXTURE_CHARACTER_ID,
        "Fixture Pilot",
        "Fixture Frigate",
        "Fixture System",
    )


@pytest.mark.parametrize("raw_state", ["created", "exited"])
def test_stopped_container_read_uses_exact_allowlisted_run(
    tmp_path: Path,
    raw_state: str,
) -> None:
    runner = FakeRunner([json.dumps(_export_payload())])
    source = _source(
        tmp_path,
        raw_state,
        runner,
        control_policy=DockerControlPolicy.MANAGED,
    )

    source.load_accounts()

    expected_tail = (
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "-e",
        "EVEJS_CONFIG_CLI_DOCKER_PROXY=1",
        "server",
        "node",
        "/app/tools/ConfigEditor/config-manager-cli.js",
        "database-export",
    )
    assert runner.calls[0][0] == _target(tmp_path).compose_args(
        runner.executable,
        *expected_tail,
    )


@pytest.mark.parametrize("raw_state", ["created", "exited", "dead"])
def test_connect_only_stopped_volume_read_fails_without_container_mutation(
    tmp_path: Path,
    raw_state: str,
) -> None:
    runner = FakeRunner([json.dumps(_export_payload())])
    source = _source(tmp_path, raw_state, runner)

    with pytest.raises(DataSourceError) as error:
        source.load_accounts()

    assert error.value.code == "connect_only_stopped"
    assert runner.calls == []


def test_character_detail_uses_requested_selected_player(tmp_path: Path) -> None:
    payload = _export_payload()
    runner = FakeRunner([json.dumps(payload)])
    source = _source(tmp_path, "running", runner)

    detail = source.get_character_detail(_FIXTURE_CHARACTER_ID)

    assert detail == payload["selectedPlayer"]["character"]  # type: ignore[index]
    assert runner.calls[0][0][-2:] == (
        "database-export",
        str(_FIXTURE_CHARACTER_ID),
    )


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        (FileNotFoundError("private host path"), "docker_cli_unavailable"),
        (_command_error(returncode=127), "export_unsupported"),
        (_command_error(returncode=None, timed_out=True), "export_timeout"),
    ],
)
def test_cli_failures_are_bounded_and_private_safe(
    tmp_path: Path,
    outcome: object,
    expected_code: str,
) -> None:
    runner = FakeRunner([outcome])
    source = _source(tmp_path, "running", runner)

    with pytest.raises(DataSourceError) as error:
        source.load_accounts()

    assert error.value.code == expected_code
    message = str(error.value)
    assert len(message) <= 160
    assert "private host path" not in message
    assert "synthetic-private-value" not in message


def test_malformed_private_export_never_leaks_payload(tmp_path: Path) -> None:
    private_value = "synthetic-private-value"
    malformed = {
        "players": {"password": private_value},
        "paths": {"databasePath": "/private/container/path"},
    }
    runner = FakeRunner([json.dumps(malformed)])
    source = _source(tmp_path, "running", runner)

    with pytest.raises(DataSourceError) as error:
        source.load_accounts()

    assert error.value.code == "malformed_export"
    assert private_value not in str(error.value)
    assert "/private/container/path" not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [
        DockerCommandResult(
            ("docker.exe", "compose"),
            0,
            "{}",
            "",
            True,
            False,
        ),
        "x" * 65,
    ],
)
def test_export_output_is_bounded(
    tmp_path: Path,
    result: object,
) -> None:
    runner = FakeRunner([result])
    source = _source(tmp_path, "running", runner, output_limit=64)

    with pytest.raises(DataSourceError) as error:
        source.load_accounts()

    assert error.value.code == "export_too_large"


def test_volume_layout_never_falls_back_to_stale_host_sqlite(tmp_path: Path) -> None:
    stale = tmp_path / "_local" / "gameStore" / "gamestore.sqlite"
    stale.parent.mkdir(parents=True)
    stale.touch()
    target = _target(tmp_path)
    config = parse_compose_config(
        _config_payload(
            [
                {
                    "type": "volume",
                    "source": "fixture-data",
                    "target": "/var/lib/evejs",
                }
            ]
        )
    )
    runner = FakeRunner([json.dumps(_export_payload())])

    selection = select_docker_data_source(
        target,
        config,
        MappingProxyType({"server": _record("running")}),
        runner,
        control_policy=DockerControlPolicy.CONNECT_ONLY,
    )

    assert isinstance(selection.data_source, DockerExportDataSource)
    selection.data_source.load_accounts()
    assert runner.calls[0][0][-1] == "database-export"


def test_verified_game_store_bind_uses_exact_sqlite_source(tmp_path: Path) -> None:
    game_store = tmp_path / "authoritative-game-store"
    game_store.mkdir()
    (game_store / "gamestore.sqlite").touch()
    target = _target(tmp_path)
    config = parse_compose_config(
        _config_payload(
            [
                {
                    "type": "volume",
                    "source": "fixture-data",
                    "target": "/var/lib/evejs",
                },
                {
                    "type": "bind",
                    "source": str(game_store),
                    "target": "/var/lib/evejs/gameStore",
                },
            ]
        )
    )
    runner = FakeRunner([])

    selection = select_docker_data_source(
        target,
        config,
        MappingProxyType({"server": _record("running")}),
        runner,
        control_policy=DockerControlPolicy.CONNECT_ONLY,
    )

    assert isinstance(selection.data_source, SqliteGameStoreDataSource)
    assert selection.data_source.game_store_path == game_store.resolve()
    assert runner.calls == []


def test_verified_bind_reader_uses_that_game_store_for_accounts_and_detail(
    tmp_path: Path,
) -> None:
    game_store = tmp_path / "authoritative-game-store"
    data_path = game_store / "data" / "solarSystems" / "data.json"
    data_path.parent.mkdir(parents=True)
    data_path.write_text(
        json.dumps(
            {
                "solarSystems": [
                    {
                        "solarSystemID": 30000001,
                        "solarSystemName": "Fixture System",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    database = game_store / "gamestore.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE accounts (key TEXT, json TEXT)")
    connection.execute("CREATE TABLE characters (key TEXT, json TEXT)")
    connection.execute(
        "INSERT INTO accounts VALUES (?, ?)",
        (
            "fixture-account",
            json.dumps({"id": 501, "role": "gm", "banned": False}),
        ),
    )
    character_payload = {
        "accountId": 501,
        "characterName": "Fixture Pilot",
        "solarSystemID": 30000001,
        "balance": 100,
    }
    connection.execute(
        "INSERT INTO characters VALUES (?, ?)",
        (str(_FIXTURE_CHARACTER_ID), json.dumps(character_payload)),
    )
    connection.commit()
    connection.close()
    source = SqliteGameStoreDataSource(game_store)

    accounts = source.load_accounts()
    detail = source.get_character_detail(_FIXTURE_CHARACTER_ID)

    assert accounts[0].characters[0].location == "Fixture System"
    assert detail == character_payload


def test_connect_only_export_has_no_lifecycle_or_write_command(tmp_path: Path) -> None:
    runner = FakeRunner([json.dumps(_export_payload())])
    source = _source(tmp_path, "running", runner)

    source.load_accounts()

    argv = runner.calls[0][0]
    assert source.control_policy is DockerControlPolicy.CONNECT_ONLY
    assert "database-export" in argv
    assert not {"up", "start", "stop", "restart", "down", "rm"}.intersection(argv)


def test_docker_project_identity_is_private_safe_and_project_specific(
    tmp_path: Path,
) -> None:
    first = docker_project_identity(_target(tmp_path, "fixture-world-a"), "effective-a")
    second = docker_project_identity(_target(tmp_path, "fixture-world-b"), "effective-b")

    assert first != second
    assert str(tmp_path) not in first
    assert "effective-a" not in first
    assert len(first) <= 80


def test_docker_project_identity_changes_with_ordered_override_content(
    tmp_path: Path,
) -> None:
    base = tmp_path / "compose.yaml"
    override = tmp_path / "launcher.override.yaml"
    base.write_text("services: {}\n", encoding="utf-8")
    override.write_text("services:\n  server: {}\n", encoding="utf-8")
    target = ComposeTarget(base, tmp_path, override_files=(override,))
    first = docker_project_identity(target, "fixture")

    override.write_text("services:\n  server:\n    environment: {}\n", encoding="utf-8")
    second = docker_project_identity(target, "fixture")

    assert first != second
    assert str(tmp_path) not in first


def test_docker_project_identity_changes_with_effective_mount_source(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    first_config = parse_compose_config(
        _config_payload(
            [
                {
                    "type": "volume",
                    "source": "fixture-data-a",
                    "target": "/var/lib/evejs",
                }
            ]
        )
    )
    second_config = parse_compose_config(
        _config_payload(
            [
                {
                    "type": "volume",
                    "source": "fixture-data-b",
                    "target": "/var/lib/evejs",
                }
            ]
        )
    )

    first = docker_project_identity(
        target,
        first_config.project_name,
        config=first_config,
    )
    second = docker_project_identity(
        target,
        second_config.project_name,
        config=second_config,
    )

    assert first != second
    assert "fixture-data-a" not in first


def test_docker_project_identity_changes_with_private_effective_environment(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    first_payload = _config_payload([])
    second_payload = _config_payload([])
    first_payload["services"]["server"]["environment"] = {
        "PRIVATE_RUNTIME_VALUE": "fixture-secret-a"
    }
    second_payload["services"]["server"]["environment"] = {
        "PRIVATE_RUNTIME_VALUE": "fixture-secret-b"
    }
    first_config = parse_compose_config(first_payload)
    second_config = parse_compose_config(second_payload)

    first = docker_project_identity(
        target,
        first_config.project_name,
        config=first_config,
    )
    second = docker_project_identity(
        target,
        second_config.project_name,
        config=second_config,
    )

    assert first != second
    assert "fixture-secret-a" not in first
    assert "fixture-secret-b" not in second


def test_docker_settings_identity_is_private_safe_and_selection_specific(
    tmp_path: Path,
) -> None:
    first = docker_settings_identity(
        str(tmp_path),
        str(tmp_path / "compose-a.yaml"),
        "fixture-project-a",
    )
    second = docker_settings_identity(
        str(tmp_path),
        str(tmp_path / "compose-b.yaml"),
        "fixture-project-b",
    )

    assert first != second
    assert str(tmp_path) not in first
    assert "fixture-project-a" not in first
    assert len(first) <= 96


def test_inspected_selection_reuses_one_injected_runner(tmp_path: Path) -> None:
    target = _target(tmp_path)
    config = parse_compose_config(
        _config_payload(
            [
                {
                    "type": "volume",
                    "source": "fixture-data",
                    "target": "/var/lib/evejs",
                }
            ]
        )
    )
    runner = FakeRunner([])

    class Inspector:
        def preflight(self, observed_target: ComposeTarget) -> PreflightReport:
            assert observed_target == target
            return PreflightReport(
                True,
                ("fixture-ok",),
                config,
                MappingProxyType({"server": _record("running")}),
            )

    selection = inspect_docker_data_source(
        target,
        control_policy=DockerControlPolicy.CONNECT_ONLY,
        runner_factory=lambda: runner,
        inspector_factory=lambda selected_runner: Inspector()
        if selected_runner is runner
        else pytest.fail("Inspector received a different runner"),
    )

    assert isinstance(selection.data_source, DockerExportDataSource)
    assert selection.endpoints == config.endpoints


def test_failed_inspection_does_not_expose_private_diagnostics(tmp_path: Path) -> None:
    private_value = "synthetic-private-value"

    class Inspector:
        def preflight(self, observed_target: ComposeTarget) -> PreflightReport:
            return PreflightReport(
                False,
                (f"password={private_value} at /private/host/path",),
            )

    with pytest.raises(DataSourceError) as error:
        inspect_docker_data_source(
            _target(tmp_path),
            control_policy=DockerControlPolicy.CONNECT_ONLY,
            runner_factory=lambda: FakeRunner([]),
            inspector_factory=lambda runner: Inspector(),
        )

    assert error.value.code == "docker_preflight_failed"
    assert private_value not in str(error.value)
    assert "/private/host/path" not in str(error.value)
