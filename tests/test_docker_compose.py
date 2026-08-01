"""Read-only Compose discovery, parsing, and preflight tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.runtime.docker_cli import DockerCommandError, DockerCommandResult
from src.core.runtime.docker_compose import (
    ComposeInspector,
    ComposeTarget,
    ComposeValidationError,
    PreflightFailureKind,
    parse_compose_config,
    parse_ps_output,
    resolve_mount,
)
from src.core.service_status import ServiceState


FIXTURES = Path(__file__).parent / "fixtures" / "docker"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_target_uses_absolute_paths_optional_validated_project_name_and_explicit_prefix() -> None:
    target = ComposeTarget(Path("C:/Fixture Space/EveJS/compose.yaml"), Path("C:/Fixture Space/EveJS"), "fixture_evejs-1")

    assert target.compose_file.is_absolute()
    assert target.project_directory.is_absolute()
    assert target.base_argv("docker") == (
        "docker", "compose", "-f", str(target.compose_file), "--project-directory", str(target.project_directory), "-p", "fixture_evejs-1",
    )
    with pytest.raises(ValueError):
        ComposeTarget(Path("C:/Fixture/compose.yaml"), Path("C:/Fixture"), "bad name!")


def test_target_rejects_relative_inputs_before_normalization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        ComposeTarget(Path("compose.yaml"), Path("."))


@pytest.mark.parametrize("name", ["compose-ps-array.json", "compose-ps-object.json", "compose-ps-ndjson.jsonl"])
def test_ps_parser_accepts_phase_zero_array_object_and_ndjson_shapes(name: str) -> None:
    records = parse_ps_output(fixture(name))

    assert records
    assert all(record.service in {"init", "market", "server"} for record in records)


def test_ps_parser_accepts_reasonable_object_collection_and_maps_states() -> None:
    wrapped = json.dumps({"containers": [
        {"Service": "market", "Name": "project-market-1", "ID": "abcdef012345", "State": "running", "Health": "healthy", "ExitCode": 0, "Publishers": []},
        {"Service": "server", "Name": "project-server-1", "ID": "012345abcdef", "State": "running", "Health": "unhealthy", "ExitCode": 0, "Publishers": []},
        {"Service": "init", "Name": "project-init-1", "ID": "fedcba987654", "State": "exited", "Health": "", "ExitCode": 0, "Publishers": []},
        {"Service": "other", "Name": "project-other-1", "ID": "987654fedcba", "State": "exited", "Health": "", "ExitCode": 3, "Publishers": []},
        {"Service": "sigterm", "Name": "project-sigterm-1", "ID": "444444444444", "State": "exited", "Health": "", "ExitCode": 143, "Publishers": []},
        {"Service": "created", "Name": "project-created-1", "ID": "111111111111", "State": "created", "Health": "", "ExitCode": 0, "Publishers": []},
        {"Service": "restarting", "Name": "project-restarting-1", "ID": "222222222222", "State": "restarting", "Health": "", "ExitCode": 0, "Publishers": []},
        {"Service": "stopping", "Name": "project-stopping-1", "ID": "333333333333", "State": "stopping", "Health": "", "ExitCode": 0, "Publishers": []},
        {"Service": "nohealth", "Name": "project-nohealth-1", "ID": "555555555555", "State": "running", "Health": "", "ExitCode": 0, "Publishers": []},
    ]})
    states = {record.service: record.state for record in parse_ps_output(wrapped)}

    assert states["market"] is ServiceState.ONLINE
    assert states["server"] is ServiceState.FAILED
    assert states["init"] is ServiceState.OFFLINE
    assert states["other"] is ServiceState.OFFLINE
    assert states["sigterm"] is ServiceState.OFFLINE
    sigterm = next(record for record in parse_ps_output(wrapped) if record.service == "sigterm")
    assert sigterm.exit_code == 143
    assert states["created"] is ServiceState.OFFLINE
    assert states["restarting"] is ServiceState.STARTING
    assert states["stopping"] is ServiceState.STOPPING
    assert states["nohealth"] is ServiceState.STARTING


def test_config_parser_resolves_remapped_loopback_endpoints_and_nested_mount() -> None:
    config = {
        "name": "fixture-evejs",
        "services": {
            "init": {"volumes": [{"type": "volume", "source": "data", "target": "/var/lib/evejs"}]},
            "market": {"ports": ["127.0.0.1:40110:40110"], "healthcheck": {"test": ["CMD", "true"]}, "depends_on": {"init": {"condition": "service_completed_successfully"}}},
            "server": {
                "ports": ["127.0.0.1:32600:26000", "127.0.0.1:32601:26001", "127.0.0.1:32602:26002", "127.0.0.1:34443:26003", "127.0.0.1:35222:5222"],
                "healthcheck": {"test": ["CMD", "true"]}, "depends_on": {"market": {"condition": "service_healthy"}},
                "stop_grace_period": "45s",
                "volumes": [{"type": "volume", "source": "data", "target": "/var/lib/evejs"}, {"type": "bind", "source": "C:/Fixture Space/EveJS/_local/gameStore", "target": "/var/lib/evejs/gameStore"}],
            },
            "market-tools": {},
        },
    }
    parsed = parse_compose_config(config)

    assert parsed.project_name == "fixture-evejs"
    assert parsed.endpoints.game.port == 32600
    assert parsed.endpoints.image.port == 32601
    assert parsed.endpoints.proxy.port == 32602
    assert parsed.endpoints.assets.port == 34443
    assert parsed.endpoints.xmpp.port == 35222
    assert parsed.endpoints.market.port == 40110
    assert parsed.endpoints.game.service == "server"
    assert parsed.endpoints.market.service == "market"
    assert parsed.endpoints.game.protocol == "tcp"
    assert parsed.capabilities.init and parsed.capabilities.market_tools
    assert parsed.services["server"].has_healthcheck
    assert resolve_mount(parsed.services["server"].mounts, "/var/lib/evejs/gameStore/private.db").source == "C:/Fixture Space/EveJS/_local/gameStore"


def test_config_parser_treats_explicitly_disabled_healthcheck_as_absent() -> None:
    config = {
        "services": {
            "market": {
                "ports": ["127.0.0.1:40110:40110"],
                "healthcheck": {"test": ["CMD", "true"]},
            },
            "server": {
                "ports": [
                    "127.0.0.1:32600:26000",
                    "127.0.0.1:32601:26001",
                    "127.0.0.1:32602:26002",
                    "127.0.0.1:34443:26003",
                    "127.0.0.1:35222:5222",
                ],
                "healthcheck": {
                    "test": ["CMD", "true"],
                    "disable": True,
                },
            },
        },
    }

    parsed = parse_compose_config(config)

    assert parsed.services["server"].has_healthcheck is False
    assert parsed.services["market"].has_healthcheck is True


def test_config_parser_rejects_nonloopback_and_duplicate_targets() -> None:
    invalid_port = {"services": {"server": {"ports": ["0.0.0.0:32600:26000"]}, "market": {"ports": ["127.0.0.1:40110:40110"]}}}
    with pytest.raises(ComposeValidationError, match="loopback"):
        parse_compose_config(invalid_port)

    with pytest.raises(ComposeValidationError, match="Duplicate"):
        resolve_mount((
            {"type": "bind", "source": "one", "target": "/var/lib/evejs/gameStore"},
            {"type": "volume", "source": "two", "target": "/var/lib/evejs/gameStore"},
        ), "/var/lib/evejs/gameStore/db.sqlite")


def test_config_parser_requires_each_endpoint_from_its_semantic_service() -> None:
    config = {"services": {
        "market": {"ports": ["127.0.0.1:40110:40110", "127.0.0.1:32600:26000"]},
        "server": {"ports": ["127.0.0.1:32601:26001", "127.0.0.1:32602:26002", "127.0.0.1:34443:26003", "127.0.0.1:35222:5222"]},
    }}

    with pytest.raises(ComposeValidationError, match="server.*26000"):
        parse_compose_config(config)


def test_config_parser_rejects_duplicate_semantic_target_and_host_bind_collision() -> None:
    base = {"market": {"ports": ["127.0.0.1:40110:40110"]}, "server": {"ports": [
        "127.0.0.1:32600:26000", "127.0.0.1:32601:26001", "127.0.0.1:32602:26002", "127.0.0.1:34443:26003", "127.0.0.1:35222:5222",
    ]}}
    duplicate_target = {"services": {**base, "server": {"ports": [*base["server"]["ports"], "127.0.0.1:42600:26000"]}}}
    with pytest.raises(ComposeValidationError, match="Duplicate.*server.*26000"):
        parse_compose_config(duplicate_target)

    collision = {"services": {**base, "market": {"ports": ["127.0.0.1:32600:40110"]}}}
    with pytest.raises(ComposeValidationError, match="host bind"):
        parse_compose_config(collision)


def test_success_models_do_not_expose_mutable_service_or_record_mappings(tmp_path: Path) -> None:
    config = parse_compose_config({"services": {
        "market": {"ports": ["127.0.0.1:40110:40110"]},
        "server": {"ports": ["127.0.0.1:32600:26000", "127.0.0.1:32601:26001", "127.0.0.1:32602:26002", "127.0.0.1:34443:26003", "127.0.0.1:35222:5222"]},
    }})
    with pytest.raises(TypeError):
        config.services["other"] = config.services["server"]  # type: ignore[index]


def test_preflight_classifies_stopped_daemon_with_actionable_diagnostic(
    tmp_path: Path,
) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            calls.append(args)
            result = DockerCommandResult(args, 1, "", "", False, False)
            raise DockerCommandError(args[0], result)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.ok is False
    assert report.failure_kind is PreflightFailureKind.DAEMON_UNAVAILABLE
    assert report.diagnostics == (
        "Docker Desktop engine is unavailable. Start Docker Desktop and wait for the engine to finish starting.",
    )
    assert calls == [("version", "--format", "{{.Server.Os}}|{{.Server.Version}}")]


def test_preflight_classifies_windows_container_engine_mode(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            return DockerCommandResult(args, 0, "windows|29.5.2", "", False, False)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.WRONG_ENGINE_MODE
    assert report.diagnostics == (
        "Docker is using Windows containers. Switch Docker Desktop to Linux containers.",
    )


def test_preflight_classifies_missing_compose_plugin(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            if args[0] == "version":
                return DockerCommandResult(args, 0, "linux|29.5.2", "", False, False)
            result = DockerCommandResult(args, 1, "", "", False, False)
            raise DockerCommandError(args[0], result)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.COMPOSE_UNAVAILABLE
    assert report.diagnostics == (
        "Docker Compose plugin is unavailable. Install or enable Docker Compose in Docker Desktop.",
    )


def test_preflight_classifies_invalid_compose_configuration(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            stdout = {
                ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}"): "linux|29.5.2",
                ("compose", "version", "--format", "{{.Version}}"): "5.1.3",
            }.get(args)
            if stdout is not None:
                return DockerCommandResult(args, 0, stdout, "", False, False)
            result = DockerCommandResult(args, 15, "", "", False, False)
            raise DockerCommandError(args[0], result)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.COMPOSE_CONFIG_INVALID
    assert report.diagnostics == (
        "Compose configuration is invalid. Check the selected Compose file and its local paths.",
    )


def test_preflight_classifies_missing_compose_file_without_running_cli(
    tmp_path: Path,
) -> None:
    class Runner:
        executable = "docker"

        def run(self, *_args, **_kwargs):
            pytest.fail("a missing Compose file must fail before CLI execution")

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(tmp_path / "missing-compose.yaml", tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.COMPOSE_CONFIG_INVALID
    assert report.diagnostics == (
        "Compose configuration is invalid. Check the selected Compose file and its local paths.",
    )


def test_preflight_classifies_malformed_effective_config_json(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            stdout = {
                ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}"): "linux|29.5.2",
                ("compose", "version", "--format", "{{.Version}}"): "5.1.3",
            }.get(args)
            if stdout is None and args[-4:] == (
                "--profile", "tools", "config", "--services",
            ):
                stdout = "init\nmarket\nmarket-tools\nserver\n"
            elif stdout is None and args[-2:] == ("config", "--services"):
                stdout = "init\nmarket\nserver\n"
            elif stdout is None and args[-3:] == ("config", "--format", "json"):
                stdout = "{malformed"
            assert stdout is not None
            return DockerCommandResult(args, 0, stdout, "", False, False)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.COMPOSE_CONFIG_INVALID
    assert report.diagnostics == (
        "Compose configuration is invalid. Check the selected Compose file and its local paths.",
    )


def test_preflight_classifies_missing_required_service_set(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            stdout = {
                ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}"): "linux|29.5.2",
                ("compose", "version", "--format", "{{.Version}}"): "5.1.3",
            }.get(args, "market\n")
            return DockerCommandResult(args, 0, stdout, "", False, False)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.SERVICE_SET_INVALID
    assert report.diagnostics == (
        "Compose configuration must define the required EveJS server and market services.",
    )


def test_preflight_classifies_service_status_inspection_failure(tmp_path: Path) -> None:
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = json.dumps({
        "name": "fixture",
        "services": {
            "init": {},
            "market": {"ports": ["127.0.0.1:40110:40110"]},
            "server": {"ports": [
                "127.0.0.1:32600:26000",
                "127.0.0.1:32601:26001",
                "127.0.0.1:32602:26002",
                "127.0.0.1:34443:26003",
                "127.0.0.1:35222:5222",
            ]},
        },
    })

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            stdout = {
                ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}"): "linux|29.5.2",
                ("compose", "version", "--format", "{{.Version}}"): "5.1.3",
            }.get(args)
            if stdout is None and args[-4:] == (
                "--profile", "tools", "config", "--services",
            ):
                stdout = "init\nmarket\nmarket-tools\nserver\n"
            elif stdout is None and args[-2:] == ("config", "--services"):
                stdout = "init\nmarket\nserver\n"
            elif stdout is None and args[-3:] == ("config", "--format", "json"):
                stdout = config
            elif stdout is None and args[-4:] == (
                "ps", "--all", "--format", "json",
            ):
                result = DockerCommandResult(args, 1, "", "", False, False)
                raise DockerCommandError(args[0], result)
            assert stdout is not None
            return DockerCommandResult(args, 0, stdout, "", False, False)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.failure_kind is PreflightFailureKind.INSPECT_FAILED
    assert report.diagnostics == (
        "Docker Compose service status could not be inspected. Check Docker Desktop and the selected project.",
    )


def test_read_only_preflight_command_order_and_allowlist(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("name: fixture\n", encoding="utf-8")
    config = json.dumps({"name": "fixture", "services": {"init": {}, "market": {"ports": ["127.0.0.1:40110:40110"]}, "server": {"ports": ["127.0.0.1:32600:26000", "127.0.0.1:32601:26001", "127.0.0.1:32602:26002", "127.0.0.1:34443:26003", "127.0.0.1:35222:5222"]}}})

    class Runner:
        executable = "docker"
        def run(self, args: tuple[str, ...], *, cwd: Path, timeout: float = 10.0) -> DockerCommandResult:
            calls.append(args)
            stdout = {
                ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}"): "linux|29.5.2",
                ("compose", "version", "--format", "{{.Version}}"): "5.1.3",
            }.get(args)
            if stdout is None and args[-4:] == (
                "--profile", "tools", "config", "--services",
            ):
                stdout = "init\nmarket\nmarket-tools\nserver\n"
            elif stdout is None and args[-2:] == ("config", "--services"):
                stdout = "init\nmarket\nserver\n"
            elif stdout is None and args[-3:] == ("config", "--format", "json"):
                stdout = config
            elif stdout is None and args[-4:] == ("ps", "--all", "--format", "json"):
                stdout = "[]"
            assert stdout is not None
            return DockerCommandResult(args, 0, stdout, "", False, False)

    report = ComposeInspector(Runner()).preflight(ComposeTarget(compose_file, tmp_path))

    assert report.ok
    assert [args[0] for args in calls] == [
        "version", "compose", "compose", "compose", "compose", "compose",
    ]
    assert all(not set(args).intersection({"up", "stop", "restart", "run", "exec", "logs", "down", "build", "pull", "create"}) for args in calls)
    compose_calls = calls[2:]
    assert all("-f" in args and "--project-directory" in args for args in compose_calls)
    assert compose_calls[1][-4:] == (
        "--profile", "tools", "config", "--services",
    )
    assert report.config is not None
    assert report.config.capabilities.market_tools
    assert report.records is not None
    with pytest.raises(TypeError):
        report.records["other"] = report.records["server"]  # type: ignore[index]


def test_preflight_discovers_profile_gated_market_tools_with_tools_profile(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    compose_file = tmp_path / "compose.yaml"
    compose_file.write_text("name: fixture\n", encoding="utf-8")
    config = json.dumps({
        "name": "fixture",
        "services": {
            "init": {},
            "market": {"ports": ["127.0.0.1:40110:40110"]},
            "server": {"ports": [
                "127.0.0.1:32600:26000",
                "127.0.0.1:32601:26001",
                "127.0.0.1:32602:26002",
                "127.0.0.1:34443:26003",
                "127.0.0.1:35222:5222",
            ]},
        },
    })

    class Runner:
        executable = "docker"

        def run(
            self,
            args: tuple[str, ...],
            *,
            cwd: Path,
            timeout: float = 10.0,
        ) -> DockerCommandResult:
            calls.append(args)
            stdout = {
                ("version", "--format", "{{.Server.Os}}|{{.Server.Version}}"): (
                    "linux|29.5.2"
                ),
                ("compose", "version", "--format", "{{.Version}}"): "5.1.3",
            }.get(args)
            if stdout is None and args[-4:] == (
                "--profile", "tools", "config", "--services",
            ):
                stdout = "init\nmarket\nmarket-tools\nserver\n"
            elif stdout is None and args[-2:] == ("config", "--services"):
                stdout = "init\nmarket\nserver\n"
            elif stdout is None and args[-3:] == ("config", "--format", "json"):
                stdout = config
            elif stdout is None and args[-4:] == (
                "ps", "--all", "--format", "json",
            ):
                stdout = "[]"
            assert stdout is not None
            return DockerCommandResult(args, 0, stdout, "", False, False)

    report = ComposeInspector(Runner()).preflight(
        ComposeTarget(compose_file, tmp_path)
    )

    assert report.ok
    assert report.config is not None
    assert report.config.capabilities.market_tools
    assert any(
        args[-4:] == ("--profile", "tools", "config", "--services")
        for args in calls
    )


@pytest.mark.parametrize("args", [
    ("system", "prune"),
    ("compose", "rm"), ("compose", "kill"), ("compose", "start"),
    ("compose", "pause"), ("compose", "cp"), ("compose", "create"),
    ("compose", "up"), ("compose", "logs"),
    ("compose", "config", "--format", "yaml"), ("compose", "ps", "--format", "json"),
])
def test_read_only_allowlist_rejects_every_unrecognized_shape_before_runner(args: tuple[str, ...], tmp_path: Path) -> None:
    class Runner:
        executable = "docker"
        def run(self, *args: object, **kwargs: object) -> DockerCommandResult:
            raise AssertionError("unrecognized command reached runner")

    inspector = ComposeInspector(Runner())
    with pytest.raises(RuntimeError, match="allowlist"):
        inspector._run(args, ComposeTarget(tmp_path / "compose.yaml", tmp_path))
