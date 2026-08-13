"""Managed Docker Compose lifecycle policy tests (fakes only)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.runtime.docker_cli import DockerCommandResult
from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord
from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
from src.core.service_status import DockerControlPolicy, ServiceState
from src.core.runtime.docker_controller import DockerLifecycleAction, ManagedComposeController


class FakeInspector:
    def __init__(self, records: dict[str, ContainerRecord]) -> None:
        self.records = records
        self.preflights = 0
        self.statuses = 0

    def preflight(self, _target: ComposeTarget):
        self.preflights += 1
        return type("Report", (), {"ok": True, "diagnostics": (), "records": self.records})()

    def status(self, _target: ComposeTarget):
        self.statuses += 1
        return self.records


class FakeRunner:
    executable = "docker"

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    def run(self, args: tuple[str, ...], *, cwd: Path, timeout: float = 10.0) -> DockerCommandResult:
        self.calls.append((args, cwd, timeout))
        return DockerCommandResult(args, 0, "", "", False, False)


class SequenceInspector(FakeInspector):
    """Return an explicit status sequence so readiness cannot pass early."""

    def __init__(
        self,
        preflight_records: dict[str, ContainerRecord],
        statuses: list[dict[str, ContainerRecord]],
    ) -> None:
        super().__init__(preflight_records)
        self._status_sequence = iter(statuses)

    def status(self, _target: ComposeTarget):
        self.statuses += 1
        return next(self._status_sequence)


def record(service: str, state: ServiceState, *, raw: str = "exited", health: str | None = None) -> ContainerRecord:
    return ContainerRecord(service, None, None, state, health, None, (), raw_state=raw)


@pytest.mark.parametrize(("action", "tail"), [
    (DockerLifecycleAction.START_MARKET, ("up", "-d", "market")),
    (DockerLifecycleAction.START_GAME, ("up", "-d", "server")),
    (DockerLifecycleAction.START_STACK, ("up", "-d", "server")),
    (DockerLifecycleAction.STOP_GAME, ("stop", "server")),
    (DockerLifecycleAction.STOP_MARKET, ("stop", "market")),
    (DockerLifecycleAction.STOP_ALL, ("stop", "server", "market")),
    (DockerLifecycleAction.RESTART_GAME, ("restart", "server")),
    (
        DockerLifecycleAction.RECREATE_GAME,
        ("up", "-d", "--no-deps", "--force-recreate", "server"),
    ),
])
def test_managed_exact_argv_matrix_and_explicit_target(action: DockerLifecycleAction, tail: tuple[str, ...], tmp_path: Path) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    runner = FakeRunner()
    desired_state = ServiceState.OFFLINE if action in {
        DockerLifecycleAction.STOP_GAME, DockerLifecycleAction.STOP_MARKET,
        DockerLifecycleAction.STOP_ALL,
    } else ServiceState.ONLINE
    inspector = FakeInspector({
        "server": record("server", desired_state, raw="exited" if desired_state is ServiceState.OFFLINE else "running"),
        "market": record("market", desired_state, raw="exited" if desired_state is ServiceState.OFFLINE else "running"),
    })
    controller = ManagedComposeController(target, inspector, runner, policy=DockerControlPolicy.MANAGED, sleep_fn=lambda _x: None)

    result = controller.execute(action)

    assert result.succeeded
    assert inspector.preflights == 1
    assert runner.calls[0][0] == (*target.compose_args("docker", *tail),)
    assert runner.calls[0][1] == tmp_path.resolve()
    assert "-f" in runner.calls[0][0] and "--project-directory" in runner.calls[0][0] and "-p" in runner.calls[0][0]
    assert not set(runner.calls[0][0]).intersection({"down", "rm", "kill", "start", "run", "exec", "build", "pull"})


def test_policy_and_arbitrary_actions_reject_before_mutation(tmp_path: Path) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    runner = FakeRunner()
    inspector = FakeInspector({})
    with pytest.raises(PermissionError):
        ManagedComposeController(target, inspector, runner, policy=DockerControlPolicy.CONNECT_ONLY)
    controller = ManagedComposeController(target, inspector, runner, policy=DockerControlPolicy.MANAGED)
    with pytest.raises(ValueError):
        controller.execute("up database")  # type: ignore[arg-type]
    assert runner.calls == []


def test_expected_effective_target_drift_rejects_before_lifecycle_mutation(
    tmp_path: Path,
) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("name: actual\nservices: {}\n", encoding="utf-8")
    target = ComposeTarget(compose, tmp_path, "fixture")
    runner = FakeRunner()
    inspector = FakeInspector({
        "server": record("server", ServiceState.OFFLINE),
        "market": record("market", ServiceState.OFFLINE),
    })

    def preflight(_selected: ComposeTarget):
        inspector.preflights += 1
        return type(
            "Report",
            (),
            {
                "ok": True,
                "diagnostics": (),
                "records": inspector.records,
                "config": type("Config", (), {"project_name": "actual"})(),
            },
        )()

    inspector.preflight = preflight  # type: ignore[method-assign]
    controller = ManagedComposeController(
        target,
        inspector,
        runner,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity="docker:stale-target",
    )

    result = controller.execute(DockerLifecycleAction.STOP_ALL)

    assert not result.succeeded
    assert result.target_identity == docker_project_identity(target, "actual")
    assert "changed" in (result.error or "").lower()
    assert runner.calls == []


@pytest.mark.parametrize("raw,state,health", [
    ("running", ServiceState.ONLINE, "healthy"), ("starting", ServiceState.STARTING, None),
    ("restarting", ServiceState.STARTING, None), ("stopping", ServiceState.STOPPING, None),
    ("running", ServiceState.FAILED, "unhealthy"), ("", ServiceState.UNKNOWN, None),
])
def test_stop_market_fails_closed_when_server_is_not_safely_stopped(raw: str, state: ServiceState, health: str | None, tmp_path: Path) -> None:
    runner = FakeRunner()
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        FakeInspector({"server": record("server", state, raw=raw, health=health), "market": record("market", ServiceState.ONLINE, raw="running", health="healthy")}),
        runner, policy=DockerControlPolicy.MANAGED,
    )

    result = controller.execute(DockerLifecycleAction.STOP_MARKET)

    assert not result.succeeded
    assert "Server" in (result.error or "")
    assert runner.calls == []


def test_failed_preflight_does_not_mutate(tmp_path: Path) -> None:
    class UnsafeInspector(FakeInspector):
        def preflight(self, _target: ComposeTarget):
            return type("Report", (), {"ok": False, "diagnostics": ("unsafe",), "records": None})()
    runner = FakeRunner()
    controller = ManagedComposeController(ComposeTarget(tmp_path / "compose.yaml", tmp_path), UnsafeInspector({}), runner, policy=DockerControlPolicy.MANAGED)
    assert not controller.execute(DockerLifecycleAction.START_GAME).succeeded
    assert runner.calls == []


@pytest.mark.parametrize(("action", "timeout"), [
    (DockerLifecycleAction.START_MARKET, 180.0),
    (DockerLifecycleAction.START_GAME, 180.0),
    (DockerLifecycleAction.START_STACK, 180.0),
    (DockerLifecycleAction.STOP_GAME, 75.0),
    (DockerLifecycleAction.STOP_MARKET, 60.0),
    (DockerLifecycleAction.STOP_ALL, 75.0),
    (DockerLifecycleAction.RESTART_GAME, 75.0),
    (DockerLifecycleAction.RECREATE_GAME, 180.0),
])
def test_each_lifecycle_action_uses_its_explicit_command_timeout(action, timeout, tmp_path: Path) -> None:
    desired = ServiceState.OFFLINE if action in {
        DockerLifecycleAction.STOP_GAME, DockerLifecycleAction.STOP_MARKET,
        DockerLifecycleAction.STOP_ALL,
    } else ServiceState.ONLINE
    records = {
        "server": record("server", desired, raw="exited" if desired is ServiceState.OFFLINE else "running"),
        "market": record("market", desired, raw="exited" if desired is ServiceState.OFFLINE else "running"),
    }
    runner = FakeRunner()
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), FakeInspector(records), runner,
        policy=DockerControlPolicy.MANAGED,
    )

    assert controller.execute(action).succeeded
    assert runner.calls[0][2] == timeout


def test_start_market_polls_starting_until_online(tmp_path: Path) -> None:
    starting = {"market": record("market", ServiceState.STARTING, raw="running", health="starting")}
    online = {"market": record("market", ServiceState.ONLINE, raw="running", health="healthy")}
    inspector = SequenceInspector(starting, [starting, online])
    sleeps: list[float] = []
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), inspector, FakeRunner(),
        policy=DockerControlPolicy.MANAGED,
        clock_fn=lambda: 0.0,
        sleep_fn=sleeps.append,
    )

    result = controller.execute(DockerLifecycleAction.START_MARKET)

    assert result.succeeded
    assert result.records is online
    assert inspector.statuses == 2
    assert sleeps == [0.25]


def test_no_healthcheck_start_requires_semantic_endpoint_readiness(
    tmp_path: Path,
) -> None:
    endpoints = RuntimeEndpoints(
        game=Endpoint("server", "127.0.0.1", 26000, 26000, "tcp"),
        image=Endpoint("server", "127.0.0.1", 26001, 26001, "tcp"),
        proxy=Endpoint("server", "127.0.0.1", 26002, 26002, "tcp"),
        assets=Endpoint("server", "127.0.0.1", 443, 26003, "tcp"),
        xmpp=Endpoint("server", "127.0.0.1", 5222, 5222, "tcp"),
        market=Endpoint("market", "127.0.0.1", 40110, 40110, "tcp"),
    )
    service = type("Service", (), {"has_healthcheck": False})()
    config = type(
        "Config",
        (),
        {
            "services": {"server": service, "market": service},
            "endpoints": endpoints,
        },
    )()
    records = {
        "server": record(
            "server", ServiceState.STARTING, raw="running", health=None,
        ),
        "market": record(
            "market", ServiceState.STARTING, raw="running", health=None,
        ),
    }

    class NoHealthInspector(FakeInspector):
        def preflight(self, _target: ComposeTarget):
            self.preflights += 1
            return type(
                "Report",
                (),
                {
                    "ok": True,
                    "diagnostics": (),
                    "records": self.records,
                    "config": config,
                },
            )()

    tcp_probes: list[Endpoint] = []
    http_probes: list[Endpoint] = []
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        NoHealthInspector(records),
        FakeRunner(),
        policy=DockerControlPolicy.MANAGED,
        endpoint_probe=lambda endpoint: tcp_probes.append(endpoint) or True,
        http_probe=lambda endpoint: http_probes.append(endpoint) or True,
    )

    result = controller.execute(DockerLifecycleAction.START_STACK)

    assert result.succeeded
    assert result.records is records
    assert tcp_probes == [endpoints.game]
    assert http_probes == [endpoints.proxy, endpoints.market]


@pytest.mark.parametrize(
    "action",
    [DockerLifecycleAction.START_GAME, DockerLifecycleAction.START_STACK],
)
def test_game_and_stack_start_wait_for_both_game_and_market(
    action: DockerLifecycleAction,
    tmp_path: Path,
) -> None:
    game_online_market_starting = {
        "server": record("server", ServiceState.ONLINE, raw="running", health="healthy"),
        "market": record("market", ServiceState.STARTING, raw="running", health="starting"),
    }
    game_starting_market_online = {
        "server": record("server", ServiceState.STARTING, raw="running", health="starting"),
        "market": record("market", ServiceState.ONLINE, raw="running", health="healthy"),
    }
    both_online = {
        "server": record("server", ServiceState.ONLINE, raw="running", health="healthy"),
        "market": record("market", ServiceState.ONLINE, raw="running", health="healthy"),
    }
    inspector = SequenceInspector(
        game_online_market_starting,
        [game_online_market_starting, game_starting_market_online, both_online],
    )
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), inspector, FakeRunner(),
        policy=DockerControlPolicy.MANAGED,
        clock_fn=lambda: 0.0,
        sleep_fn=lambda _seconds: None,
    )

    result = controller.execute(action)

    assert result.succeeded
    assert result.records is both_online
    assert inspector.statuses == 3


@pytest.mark.parametrize("raw_state", ["created", "dead", "exited"])
def test_stop_market_accepts_only_explicit_stopped_server_states(
    raw_state: str,
    tmp_path: Path,
) -> None:
    preflight = {
        "server": record("server", ServiceState.OFFLINE, raw=raw_state),
        "market": record("market", ServiceState.ONLINE, raw="running", health="healthy"),
    }
    stopped = {
        "server": record("server", ServiceState.OFFLINE, raw=raw_state),
        "market": record("market", ServiceState.OFFLINE, raw="exited"),
    }
    runner = FakeRunner()
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        SequenceInspector(preflight, [stopped]),
        runner,
        policy=DockerControlPolicy.MANAGED,
    )

    result = controller.execute(DockerLifecycleAction.STOP_MARKET)

    assert result.succeeded
    assert runner.calls[0][0][-2:] == ("stop", "market")


def test_stop_market_accepts_an_absent_server_record(tmp_path: Path) -> None:
    preflight = {
        "market": record("market", ServiceState.ONLINE, raw="running", health="healthy"),
    }
    stopped = {"market": record("market", ServiceState.OFFLINE, raw="exited")}
    runner = FakeRunner()
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        SequenceInspector(preflight, [stopped]),
        runner,
        policy=DockerControlPolicy.MANAGED,
    )

    assert controller.execute(DockerLifecycleAction.STOP_MARKET).succeeded
    assert len(runner.calls) == 1


def test_preflight_diagnostic_is_redacted_and_bounded(tmp_path: Path) -> None:
    class FailedInspector(FakeInspector):
        def preflight(self, _target: ComposeTarget):
            diagnostic = "password=sentinel " + ("x" * 2_000)
            return type("Report", (), {"ok": False, "diagnostics": (diagnostic,), "records": None})()

    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), FailedInspector({}), FakeRunner(),
        policy=DockerControlPolicy.MANAGED,
    )

    result = controller.execute(DockerLifecycleAction.START_GAME)

    assert not result.succeeded
    assert "sentinel" not in (result.error or "")
    assert "[REDACTED]" in (result.error or "")
    assert len(result.error or "") <= 512


def test_command_exception_diagnostic_is_redacted_and_bounded(tmp_path: Path) -> None:
    class ExplodingRunner(FakeRunner):
        def run(self, *args, **kwargs):
            raise OSError("token=sentinel " + ("x" * 2_000))

    records = {
        "server": record("server", ServiceState.OFFLINE, raw="exited"),
        "market": record("market", ServiceState.OFFLINE, raw="exited"),
    }
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), FakeInspector(records),
        ExplodingRunner(), policy=DockerControlPolicy.MANAGED,
    )

    result = controller.execute(DockerLifecycleAction.START_GAME)

    assert not result.succeeded
    assert "sentinel" not in (result.error or "")
    assert "[REDACTED]" in (result.error or "")
    assert len(result.error or "") <= 512


def test_inspection_exception_diagnostic_is_redacted_and_bounded(tmp_path: Path) -> None:
    class ExplodingInspector(FakeInspector):
        def status(self, _target: ComposeTarget):
            raise ValueError("Authorization: Bearer sentinel " + ("x" * 2_000))

    records = {
        "server": record("server", ServiceState.OFFLINE, raw="exited"),
        "market": record("market", ServiceState.OFFLINE, raw="exited"),
    }
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), ExplodingInspector(records),
        FakeRunner(), policy=DockerControlPolicy.MANAGED,
    )

    result = controller.execute(DockerLifecycleAction.START_GAME)

    assert not result.succeeded
    assert "sentinel" not in (result.error or "")
    assert "[REDACTED]" in (result.error or "")
    assert len(result.error or "") <= 512


def test_readiness_timeout_returns_latest_records_without_an_extra_mutation(tmp_path: Path) -> None:
    starting = {
        "server": record("server", ServiceState.STARTING, raw="running", health="starting"),
        "market": record("market", ServiceState.STARTING, raw="running", health="starting"),
    }
    inspector = FakeInspector(starting)
    runner = FakeRunner()
    clock = iter((0.0, 0.0, 2.0)).__next__
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), inspector, runner,
        policy=DockerControlPolicy.MANAGED,
        readiness_timeout_sec=1.0,
        clock_fn=clock,
        sleep_fn=lambda _seconds: None,
    )

    result = controller.execute(DockerLifecycleAction.START_STACK)

    assert not result.succeeded
    assert result.records is starting
    assert "timeout" in (result.error or "").casefold()
    assert len(result.error or "") <= 512
    assert len(runner.calls) == 1
