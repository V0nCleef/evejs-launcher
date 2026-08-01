"""Closed managed-Docker Tool Deck command and preflight contracts."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_cli import (
    DockerCommandError,
    DockerCommandResult,
)
from src.core.runtime.docker_compose import (
    ComposeCapabilities,
    ComposeTarget,
    ContainerRecord,
    PreflightReport,
)
from src.core.runtime.docker_tools import (
    DockerToolAction,
    ManagedDockerToolController,
    docker_tool_spec,
)
from src.core.service_status import DockerControlPolicy, ServiceState


EXPECTED_TAILS = {
    DockerToolAction.INITIALIZE_DATABASE: (
        "run", "--rm", "--no-deps", "init",
    ),
    DockerToolAction.MARKET_STATUS: (
        "run", "--rm", "--no-deps", "market-tools", "status",
    ),
    DockerToolAction.MARKET_DOCTOR: (
        "run", "--rm", "--no-deps", "market-tools", "doctor",
    ),
    DockerToolAction.MARKET_BACKUP: (
        "run", "--rm", "--no-deps", "market-tools", "backup",
    ),
    DockerToolAction.MARKET_BACKUPS: (
        "run", "--rm", "--no-deps", "market-tools", "backups",
    ),
    DockerToolAction.MARKET_PRESETS: (
        "run", "--rm", "--no-deps", "market-tools", "presets",
    ),
    DockerToolAction.MARKET_REBUILD_V1_JITA: (
        "run", "--rm", "--no-deps", "market-tools",
        "rebuild", "v1", "--preset", "jita_new_caldari",
    ),
    DockerToolAction.MARKET_REBUILD_V1_FULL_UNIVERSE: (
        "run", "--rm", "--no-deps", "market-tools",
        "rebuild", "v1", "--preset", "full_universe",
    ),
    DockerToolAction.MARKET_RESTORE_LATEST: (
        "run", "--rm", "--no-deps", "market-tools",
        "restore", "latest",
    ),
    DockerToolAction.MARKET_SNAPSHOT_INFO: (
        "run", "--rm", "--no-deps", "market-tools", "snapshot-info",
    ),
    DockerToolAction.MARKET_REBUILD_V2: (
        "run", "--rm", "--no-deps", "market-tools", "rebuild", "v2",
    ),
}

OFFLINE_ACTIONS = {
    DockerToolAction.INITIALIZE_DATABASE,
    DockerToolAction.MARKET_DOCTOR,
    DockerToolAction.MARKET_BACKUP,
    DockerToolAction.MARKET_REBUILD_V1_JITA,
    DockerToolAction.MARKET_REBUILD_V1_FULL_UNIVERSE,
    DockerToolAction.MARKET_RESTORE_LATEST,
    DockerToolAction.MARKET_REBUILD_V2,
}

INFORMATION_ACTIONS = set(DockerToolAction) - OFFLINE_ACTIONS


class FakeRunner:
    executable = "docker-fixture"

    def __init__(self, *, fail: bool = False, output: str = "") -> None:
        self.fail = fail
        self.output = output
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    def run(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        timeout: float = 10.0,
    ) -> DockerCommandResult:
        self.calls.append((args, cwd, timeout))
        result = DockerCommandResult(
            (self.executable, *args),
            1 if self.fail else 0,
            self.output,
            self.output,
            False,
            False,
        )
        if self.fail:
            raise DockerCommandError("compose", result)
        return result


class FakeInspector:
    def __init__(
        self,
        records: dict[str, ContainerRecord] | None = None,
        *,
        ok: bool = True,
        init: bool = True,
        market_tools: bool = True,
        effective_project_name: str | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.records = records if records is not None else _stopped_records()
        self.ok = ok
        self.init = init
        self.market_tools = market_tools
        self.effective_project_name = effective_project_name
        self.events = events
        self.preflights = 0

    def preflight(self, _target: ComposeTarget) -> PreflightReport:
        self.preflights += 1
        if self.events is not None:
            self.events.append("preflight")
        config = SimpleNamespace(
            project_name=self.effective_project_name,
            capabilities=ComposeCapabilities(
                init=self.init,
                market_tools=self.market_tools,
            )
        )
        return PreflightReport(
            self.ok,
            ("private fixture preflight failure",) if not self.ok else (),
            config if self.ok else None,
            self.records if self.ok else None,
        )


def _record(
    service: str,
    *,
    raw_state: str,
    exists: bool = True,
) -> ContainerRecord:
    state = (
        ServiceState.ONLINE
        if raw_state == "running"
        else ServiceState.OFFLINE
    )
    return ContainerRecord(
        service,
        None,
        None,
        state,
        "healthy" if raw_state == "running" else None,
        None,
        (),
        exists=exists,
        raw_state=raw_state,
    )


def _stopped_records() -> dict[str, ContainerRecord]:
    return {
        "server": _record("server", raw_state="exited"),
        "market": _record("market", raw_state="exited"),
    }


def _controller(
    tmp_path: Path,
    inspector: FakeInspector,
    runner: FakeRunner,
    *,
    target: ComposeTarget | None = None,
) -> ManagedDockerToolController:
    return ManagedDockerToolController(
        target or ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        inspector,
        runner,
        policy=DockerControlPolicy.MANAGED,
    )


@pytest.mark.parametrize("action", list(DockerToolAction))
def test_every_docker_tool_action_maps_to_exact_compose_argv(
    action: DockerToolAction,
    tmp_path: Path,
) -> None:
    primary = tmp_path / "compose.yaml"
    first_override = tmp_path / "compose.fixture-a.yaml"
    second_override = tmp_path / "compose.fixture-b.yaml"
    target = ComposeTarget(
        primary,
        tmp_path,
        "fixture-project",
        (first_override, second_override),
    )
    runner = FakeRunner(output="private payload must not escape")
    inspector = FakeInspector()

    result = _controller(
        tmp_path,
        inspector,
        runner,
        target=target,
    ).execute(action)

    assert result.succeeded
    assert result.action is action
    assert "private payload" not in result.message
    assert len(runner.calls) == 1
    argv, cwd, timeout = runner.calls[0]
    assert argv == target.compose_args(
        runner.executable,
        *EXPECTED_TAILS[action],
    )
    assert cwd == tmp_path.resolve()
    assert timeout == docker_tool_spec(action).timeout
    assert argv.count("-f") == 3
    assert argv.index(str(first_override.resolve())) < argv.index(
        str(second_override.resolve())
    )
    assert not set(argv).intersection(
        {"down", "kill", "exec", "build", "pull", "start", "stop"}
    )


def test_tool_result_carries_authoritative_preflight_target_identity(
    tmp_path: Path,
) -> None:
    target = ComposeTarget(
        tmp_path / "compose.yaml",
        tmp_path,
        "fixture-selected-project",
    )
    inspector = FakeInspector(effective_project_name="fixture-effective-project")

    result = _controller(
        tmp_path,
        inspector,
        FakeRunner(),
        target=target,
    ).execute(DockerToolAction.MARKET_STATUS)

    assert result.succeeded
    assert result.target_identity == docker_project_identity(
        target,
        "fixture-effective-project",
    )


def test_preflight_target_change_runs_no_tool_command(tmp_path: Path) -> None:
    target = ComposeTarget(
        tmp_path / "compose.yaml",
        tmp_path,
        "fixture-selected-project",
    )
    expected_identity = docker_project_identity(target, "fixture-effective-a")
    runner = FakeRunner()
    controller = ManagedDockerToolController(
        target,
        FakeInspector(effective_project_name="fixture-effective-b"),
        runner,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity=expected_identity,
    )

    result = controller.execute(DockerToolAction.MARKET_STATUS)

    assert not result.succeeded
    assert result.target_identity == docker_project_identity(
        target,
        "fixture-effective-b",
    )
    assert "changed" in result.error.casefold()
    assert runner.calls == []


def test_docker_tool_command_specs_are_exhaustive_and_unique() -> None:
    specs = {action: docker_tool_spec(action) for action in DockerToolAction}

    assert set(specs) == set(DockerToolAction)
    assert {action: spec.command for action, spec in specs.items()} == EXPECTED_TAILS
    assert len({spec.command for spec in specs.values()}) == len(specs)
    assert {
        action for action, spec in specs.items() if spec.requires_services_stopped
    } == OFFLINE_ACTIONS


def test_tool_controller_accepts_only_closed_semantic_actions(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    inspector = FakeInspector()
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)

    with pytest.raises(PermissionError):
        ManagedDockerToolController(
            target,
            inspector,
            runner,
            policy=DockerControlPolicy.CONNECT_ONLY,
        )
    controller = _controller(tmp_path, inspector, runner, target=target)
    with pytest.raises(ValueError):
        controller.execute("run --rm arbitrary")  # type: ignore[arg-type]

    assert inspector.preflights == 0
    assert runner.calls == []


@pytest.mark.parametrize("action", OFFLINE_ACTIONS)
@pytest.mark.parametrize("running_service", ["server", "market"])
def test_running_service_blocks_every_offline_required_action(
    action: DockerToolAction,
    running_service: str,
    tmp_path: Path,
) -> None:
    records = _stopped_records()
    records[running_service] = _record(running_service, raw_state="running")
    runner = FakeRunner()
    inspector = FakeInspector(records)

    result = _controller(tmp_path, inspector, runner).execute(action)

    assert not result.succeeded
    assert running_service.title() in result.error
    assert inspector.preflights == 1
    assert runner.calls == []


@pytest.mark.parametrize("raw_state", ["created", "exited", "dead"])
def test_offline_preflight_accepts_only_explicit_safe_stopped_records(
    raw_state: str,
    tmp_path: Path,
) -> None:
    records = {
        "server": _record("server", raw_state=raw_state),
        "market": _record("market", raw_state=raw_state),
    }
    runner = FakeRunner()

    result = _controller(
        tmp_path,
        FakeInspector(records),
        runner,
    ).execute(DockerToolAction.MARKET_DOCTOR)

    assert result.succeeded
    assert len(runner.calls) == 1


@pytest.mark.parametrize("action", INFORMATION_ACTIONS)
def test_information_actions_are_not_blocked_by_online_services(
    action: DockerToolAction,
    tmp_path: Path,
) -> None:
    records = {
        "server": _record("server", raw_state="running"),
        "market": _record("market", raw_state="running"),
    }
    runner = FakeRunner()

    result = _controller(
        tmp_path,
        FakeInspector(records),
        runner,
    ).execute(action)

    assert result.succeeded
    assert len(runner.calls) == 1


def test_preflight_occurs_before_tool_operation(tmp_path: Path) -> None:
    events: list[str] = []

    class OrderedRunner(FakeRunner):
        def run(self, *args, **kwargs):
            events.append("run")
            return super().run(*args, **kwargs)

    runner = OrderedRunner()
    inspector = FakeInspector(events=events)

    result = _controller(tmp_path, inspector, runner).execute(
        DockerToolAction.MARKET_STATUS
    )

    assert result.succeeded
    assert events == ["preflight", "run"]


def test_failed_tool_preflight_runs_no_tool_command(tmp_path: Path) -> None:
    runner = FakeRunner()
    inspector = FakeInspector(ok=False)

    result = _controller(tmp_path, inspector, runner).execute(
        DockerToolAction.MARKET_STATUS
    )

    assert not result.succeeded
    assert "private fixture" not in result.error
    assert inspector.preflights == 1
    assert runner.calls == []


@pytest.mark.parametrize(
    ("action", "init", "market_tools", "expected"),
    [
        (DockerToolAction.INITIALIZE_DATABASE, False, True, "Init"),
        (DockerToolAction.MARKET_STATUS, True, False, "Market-tools"),
    ],
)
def test_tool_action_requires_its_effective_compose_service(
    action: DockerToolAction,
    init: bool,
    market_tools: bool,
    expected: str,
    tmp_path: Path,
) -> None:
    runner = FakeRunner()

    result = _controller(
        tmp_path,
        FakeInspector(init=init, market_tools=market_tools),
        runner,
    ).execute(action)

    assert not result.succeeded
    assert expected in result.error
    assert runner.calls == []


def test_tool_command_failure_returns_stable_private_safe_result(
    tmp_path: Path,
) -> None:
    runner = FakeRunner(
        fail=True,
        output="password=fixture-secret C:/Private/fixture-compose.yaml",
    )

    result = _controller(
        tmp_path,
        FakeInspector(),
        runner,
    ).execute(DockerToolAction.MARKET_STATUS)

    assert not result.succeeded
    assert "fixture-secret" not in result.error
    assert "Private" not in result.error
    assert len(result.error) <= 512
