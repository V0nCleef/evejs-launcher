"""Managed Docker Compose lifecycle policy and attestation tests."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.core.runtime.docker_cli import DockerCommandResult, DockerCommandRunner
from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_compose import (
    ComposeTarget,
    ContainerRecord,
    docker_container_runtime_identity,
)
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

    def __init__(self, *, inspect_node_options: str = "") -> None:
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []
        self.inspect_node_options = inspect_node_options

    def run(self, args: tuple[str, ...], *, cwd: Path, timeout: float = 10.0) -> DockerCommandResult:
        self.calls.append((args, cwd, timeout))
        if args and args[0] == "inspect":
            environment = json.dumps(
                [f"NODE_OPTIONS={self.inspect_node_options}"],
                separators=(",", ":"),
            )
            stdout = (
                ("a" * 64)
                + "\t2026-08-22T10:20:30.123456789Z\ttrue\t"
                + environment
                + "\n"
            )
            return DockerCommandResult(args, 0, stdout, "", False, False)
        return DockerCommandResult(args, 0, "", "", False, False)

    def run_parsed(self, args, *, cwd, parser, timeout=10.0):
        result = self.run(args, cwd=cwd, timeout=timeout)
        if result.truncated:
            raise ValueError("structured fixture output was truncated")
        return parser(result.stdout)


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
    return ContainerRecord(
        service,
        None,
        "a" * 12,
        state,
        health,
        None,
        (),
        raw_state=raw,
    )



@pytest.mark.parametrize("failure", ["world_lease", "clock_rollback"])
def test_managed_recovers_only_a_fresh_game_lease_exit(tmp_path, failure):
    _prove_game_lease_recovery(tmp_path, failure=failure, expected_restarts=1)


@pytest.mark.parametrize("failure", [
    "different_error", "older_attempt", "old_log_marker", "active_owner", "oom", "market_offline",
    "changed_container", "changed_project", "verification_only",
])
def test_managed_does_not_retry_other_failures(tmp_path, failure):
    _prove_game_lease_recovery(tmp_path, failure=failure, expected_restarts=0)


def test_managed_game_lease_recovery_is_bounded_to_one_start(tmp_path):
    _prove_game_lease_recovery(tmp_path, failure="repeat_exit", expected_restarts=1)


def _prove_game_lease_recovery(tmp_path, *, failure, expected_restarts):
    (tmp_path / "package.json").write_text('{"version":"261126.2"}', encoding="utf-8")
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    target = ComposeTarget(compose, tmp_path, "lease-fixture")
    exited_game = replace(record("server", ServiceState.OFFLINE), exit_code=1)
    ready_market = record("market", ServiceState.ONLINE, raw="running", health="healthy")
    ready_game = record("server", ServiceState.ONLINE, raw="running", health="healthy")
    old_started = "2026-08-22T10:20:30.123456789Z"
    current_started = (
        "2026-08-22T10:19:30.123456789Z" if failure == "clock_rollback"
        else "2026-08-22T10:24:30.123456789Z"
    )
    failed = {
        "server": exited_game,
        "market": record("market", ServiceState.OFFLINE) if failure == "market_offline" else ready_market,
    }
    ready = {"server": ready_game, "market": ready_market}

    class Inspector:
        def preflight(self, _target):
            records = {"server": exited_game, "market": ready_market}
            if failure == "changed_container" and runner.inspect_count > 1:
                records["server"] = replace(exited_game, short_id="b" * 12)
            project = "changed" if failure == "changed_project" and runner.inspect_count > 1 else "lease-fixture"
            return SimpleNamespace(ok=True, records=records, config=SimpleNamespace(project_name=project))

        def status(self, _target):
            return ready if runner.restarts and failure != "repeat_exit" else failed

    class Runner(FakeRunner):
        inspect_count = 0
        restarts = 0

        def run(self, args, *, cwd, timeout=10.0):
            if args[0] == "inspect":
                self.inspect_count += 1
                started = old_started if self.inspect_count == 1 or failure == "older_attempt" else current_started
                cid = args[-1][0] * 64
                oom = "true" if failure == "oom" else "false"
                return DockerCommandResult(args, 0, f"{cid}\t{started}\texited\t1\t{oom}\n", "", False, False)
            if args[0] == "logs":
                assert args == ("logs", "--tail", "80", "--timestamps", "a" * 64)
                if self.inspect_count == 1 and failure != "old_log_marker":
                    return DockerCommandResult(args, 0, "", "", False, False)
                code = {
                    "different_error": "SQLITE_CORRUPT",
                    "active_owner": "PERSISTENCE_OWNER_ACTIVE",
                }.get(failure, "PERSISTENCE_OWNER_LEASE_LOST")
                stamp = "2026-08-22T10:18:00.123456789Z"
                stderr = f"{stamp} Error: startup failure\n{stamp}   code: '{code}',\n{stamp}   ownerRole: 'world',\n"
                return DockerCommandResult(args, 0, "", stderr, False, False)
            if args[0] == "start":
                assert args == ("start", "a" * 64)
                self.restarts += 1
            return super().run(args, cwd=cwd, timeout=timeout)

    runner = Runner()
    result = ManagedComposeController(
        target, Inspector(), runner, policy=DockerControlPolicy.MANAGED,
        verification_only=failure == "verification_only",
    ).execute(DockerLifecycleAction.START_STACK)

    assert runner.restarts == expected_restarts
    assert result.succeeded == (failure in {"world_lease", "clock_rollback"})
    if failure == "repeat_exit":
        assert "could not restart" in result.error
    if failure == "verification_only":
        assert not runner.calls

def test_pending_2611253_setup_blocks_start_without_blocking_stop(tmp_path):
    (tmp_path / "package.json").write_text('{"version":"261125.3"}', encoding="utf-8")
    setup = tmp_path / "_local/setup-state.json"
    setup.parent.mkdir()
    setup.write_text('{"schema":1,"mode":"docker","pendingVersion":"261125.3"}', encoding="utf-8")
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    target = ComposeTarget(compose, tmp_path, "setup-qa")
    inspector = FakeInspector({
        role: record(role, ServiceState.OFFLINE) for role in ("server", "market")
    })
    runner = FakeRunner()
    controller = ManagedComposeController(target, inspector, runner, policy=DockerControlPolicy.MANAGED)

    started = controller.execute(DockerLifecycleAction.START_STACK)
    assert not started.succeeded and started.diagnostic_code == "evejs_setup_required"
    assert "Resume setup" in started.error
    assert inspector.preflights == 0 and not runner.calls

    stopped = controller.execute(DockerLifecycleAction.STOP_ALL)
    assert stopped.succeeded
    assert runner.calls[-1][0][-3:] == ("stop", "server", "market")


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


@pytest.mark.parametrize("market_exists", [False, True])
def test_recreate_game_starts_offline_market_before_recreating_game(tmp_path, market_exists):
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    offline = {"server": record("server", ServiceState.OFFLINE)}
    if market_exists:
        offline["market"] = record("market", ServiceState.OFFLINE)
    warming = {**offline, "market": record("market", ServiceState.STARTING, raw="running", health="starting")}
    market_ready = {**offline, "market": record("market", ServiceState.ONLINE, raw="running", health="healthy")}
    ready = {**market_ready, "server": record("server", ServiceState.ONLINE, raw="running", health="healthy")}

    class Inspector(SequenceInspector):
        def preflight(self, target):
            report = super().preflight(target)
            report.config = SimpleNamespace(project_name="fixture")
            return report

    inspector = Inspector(offline, [warming, market_ready, ready, ready])
    runner = FakeRunner()
    start_market = target.compose_args("docker", "up", "-d", "market")
    recreate_game = target.compose_args("docker", "up", "-d", "--no-deps", "--force-recreate", "server")

    def while_market_warms(_seconds):
        assert [argv for argv, _, _ in runner.calls] == [start_market]

    controller = ManagedComposeController(
        target, inspector, runner, policy=DockerControlPolicy.MANAGED, sleep_fn=while_market_warms,
    )
    result = controller.execute(DockerLifecycleAction.RECREATE_GAME)

    assert result.succeeded and result.records == ready
    assert [argv for argv, _, _ in runner.calls[:2]] == [start_market, recreate_game]
    assert inspector.preflights == 2


def test_recreate_game_refuses_to_start_game_after_market_exits(tmp_path):
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    offline = {"server": record("server", ServiceState.OFFLINE)}
    failed = {**offline, "market": replace(record("market", ServiceState.FAILED), exit_code=17)}
    runner = FakeRunner()
    controller = ManagedComposeController(
        target, SequenceInspector(offline, [failed]), runner, policy=DockerControlPolicy.MANAGED,
    )

    result = controller.execute(DockerLifecycleAction.RECREATE_GAME)

    assert not result.succeeded and result.action is DockerLifecycleAction.RECREATE_GAME
    assert result.diagnostic_code == "container_exited" and result.records == failed
    assert [argv for argv, _, _ in runner.calls] == [target.compose_args("docker", "up", "-d", "market")]


def test_recreate_verification_does_not_start_market(tmp_path):
    runner = FakeRunner()
    inspector = FakeInspector({"server": record("server", ServiceState.ONLINE, raw="running")})
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), inspector, runner,
        policy=DockerControlPolicy.MANAGED, verification_only=True,
        readiness_timeout_sec=1.0, clock_fn=iter((0.0, 2.0)).__next__,
    )

    result = controller.execute(DockerLifecycleAction.RECREATE_GAME)

    assert not result.succeeded and result.diagnostic_code == "readiness_timeout"
    assert not runner.calls


def test_success_returns_privacy_safe_effective_node_options_digest(
    tmp_path: Path,
) -> None:
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    records = {
        "server": record("server", ServiceState.ONLINE, raw="running"),
        "market": record("market", ServiceState.ONLINE, raw="running"),
    }
    digest = hashlib.sha256(b"--require /app/mods/alpha/loader.js").hexdigest()

    class Inspector(FakeInspector):
        def preflight(self, _target: ComposeTarget):
            self.preflights += 1
            config = type(
                "Config",
                (),
                {"project_name": "fixture", "server_node_options_sha256": digest},
            )()
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

    runner = FakeRunner(
        inspect_node_options="--require /app/mods/alpha/loader.js"
    )
    controller = ManagedComposeController(
        target,
        Inspector(records),
        runner,
        policy=DockerControlPolicy.MANAGED,
        sleep_fn=lambda _seconds: None,
    )

    result = controller.execute(DockerLifecycleAction.RECREATE_GAME)

    assert result.succeeded
    assert result.server_node_options_sha256 == digest
    assert result.game_runtime_identity == docker_container_runtime_identity(
        "a" * 64,
        "2026-08-22T10:20:30.123456789Z",
        expected_short_id="a" * 12,
    )
    assert runner.calls[-1][0][0] == "inspect"


def test_recreate_parses_raw_secret_bearing_inspect_only_inside_real_runner(
    tmp_path: Path,
) -> None:
    """Redaction must not alter the value hashed for runtime attestation."""

    private_node_options = (
        "--require /app/mods/alpha/loader.js password=CANARY_INSPECT_SECRET"
    )
    raw_environment = json.dumps(
        [
            f"NODE_OPTIONS={private_node_options}",
            "DATABASE_PASSWORD=CANARY_DATABASE_SECRET",
        ],
        separators=(",", ":"),
    )

    def execute(
        argv: tuple[str, ...],
        _cwd: Path,
        _timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        if len(argv) > 1 and argv[1] == "inspect":
            stdout = (
                ("a" * 64)
                + "\t2026-08-22T10:20:30.123456789Z\ttrue\t"
                + raw_environment
                + "\n"
            )
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    records = {
        "server": record("server", ServiceState.ONLINE, raw="running"),
        "market": record("market", ServiceState.ONLINE, raw="running"),
    }
    runner = DockerCommandRunner(executable="docker", execute=execute)
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        FakeInspector(records),
        runner,
        policy=DockerControlPolicy.MANAGED,
        sleep_fn=lambda _seconds: None,
    )

    result = controller.execute(DockerLifecycleAction.RECREATE_GAME)

    assert result.succeeded
    assert result.server_node_options_sha256 == hashlib.sha256(
        private_node_options.encode("utf-8")
    ).hexdigest()
    assert result.game_runtime_identity == docker_container_runtime_identity(
        "a" * 64,
        "2026-08-22T10:20:30.123456789Z",
        expected_short_id="a" * 12,
    )
    assert "CANARY_INSPECT_SECRET" not in repr(result)
    assert "CANARY_DATABASE_SECRET" not in repr(result)


def test_recreate_fails_closed_when_actual_container_environment_is_unreadable(
    tmp_path: Path,
) -> None:
    records = {
        "server": record("server", ServiceState.ONLINE, raw="running"),
        "market": record("market", ServiceState.ONLINE, raw="running"),
    }

    class BadInspectRunner(FakeRunner):
        def run(self, args, *, cwd, timeout=10.0):
            if args and args[0] == "inspect":
                return DockerCommandResult(args, 0, "malformed\n", "", False, False)
            return super().run(args, cwd=cwd, timeout=timeout)

    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path),
        FakeInspector(records),
        BadInspectRunner(),
        policy=DockerControlPolicy.MANAGED,
        sleep_fn=lambda _seconds: None,
    )

    result = controller.execute(DockerLifecycleAction.RECREATE_GAME)

    assert not result.succeeded
    assert "environment could not be verified" in (result.error or "")


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
        FakeRunner(), policy=DockerControlPolicy.MANAGED, readiness_timeout_sec=.01,
        sleep_fn=lambda _seconds: None,
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


def test_transient_monitor_error_does_not_abort_a_successful_start(tmp_path):
    online = {name: record(name, ServiceState.ONLINE, raw="running", health="healthy") for name in ("server", "market")}
    class TransientInspector(FakeInspector):
        def status(self, target):
            self.statuses += 1
            if self.statuses == 1:
                raise OSError("temporary monitor outage")
            return online
    runner = FakeRunner()
    controller = ManagedComposeController(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path), TransientInspector(online), runner,
        policy=DockerControlPolicy.MANAGED, readiness_timeout_sec=1,
        sleep_fn=lambda _seconds: None,
    )
    assert controller.execute(DockerLifecycleAction.START_STACK).succeeded
    assert len(runner.calls) == 1


def test_late_runtime_verification_performs_no_lifecycle_command(tmp_path):
    online = {name:record(name,ServiceState.ONLINE,raw="running",health="healthy") for name in ("server","market")}
    runner = FakeRunner()
    result = ManagedComposeController(ComposeTarget(tmp_path/"compose.yaml",tmp_path),FakeInspector(online),runner,
        policy=DockerControlPolicy.MANAGED,verification_only=True).execute(DockerLifecycleAction.RECREATE_STACK)
    assert result.succeeded and result.game_runtime_identity
    assert runner.calls and all(args[0] == "inspect" for args,_,_ in runner.calls)


def test_confirmed_fatal_exit_is_distinct_from_an_inspection_timeout(tmp_path):
    from dataclasses import replace
    failed=replace(record("server",ServiceState.OFFLINE,raw="exited"),exit_code=1)
    records={"server":failed,"market":record("market",ServiceState.ONLINE,raw="running",health="healthy")}
    runner=FakeRunner()
    result=ManagedComposeController(ComposeTarget(tmp_path/"compose.yaml",tmp_path),FakeInspector(records),runner,
        policy=DockerControlPolicy.MANAGED,sleep_fn=lambda _:pytest.fail("Fatal exit must not wait out readiness")).execute(DockerLifecycleAction.START_STACK)
    assert not result.succeeded and result.diagnostic_code=="container_exited"
    assert len(runner.calls)==1 and "stop" not in runner.calls[0][0]


def test_ready_service_with_wrong_image_is_left_running_and_unverified(tmp_path):
    from types import SimpleNamespace
    online = {name:record(name,ServiceState.ONLINE,raw="running",health="healthy") for name in ("server","market")}
    class Inspector(FakeInspector):
        def preflight(self,target):
            return SimpleNamespace(ok=True,records=online,config=SimpleNamespace(project_name="qa",services={
                "server":SimpleNamespace(image="sha256:"+"b"*64,has_healthcheck=True)}))
    class Runner(FakeRunner):
        def run_parsed(self,args,**kwargs):
            self.calls.append((args,kwargs["cwd"],kwargs["timeout"]))
            return "sha256:"+"e"*64
    runner = Runner()
    result = ManagedComposeController(ComposeTarget(tmp_path/"compose.yaml",tmp_path),Inspector(online),runner,
        policy=DockerControlPolicy.MANAGED).execute(DockerLifecycleAction.START_GAME)
    assert not result.succeeded and result.diagnostic_code == "image_unverified"
    assert all("stop" not in args for args,_,_ in runner.calls)


@pytest.mark.parametrize("change", ["server_replaced", "market_replaced", "market_stopped", "inspection"])
def test_image_attestation_requires_a_fresh_unchanged_healthy_sample(tmp_path, change):
    from dataclasses import replace
    from types import SimpleNamespace
    online = {name: record(name, ServiceState.ONLINE, raw="running", health="healthy") for name in ("server", "market")}
    image = "sha256:" + "b" * 64
    class Inspector(FakeInspector):
        def preflight(self, target):
            return SimpleNamespace(ok=True, records=online, config=SimpleNamespace(project_name="qa", services={
                name: SimpleNamespace(image=image, has_healthcheck=True) for name in online}))
        def status(self, target):
            self.statuses += 1
            if self.statuses == 1:
                return online
            if change == "inspection":
                raise OSError("Monitor unavailable")
            fresh = dict(online)
            if change == "market_stopped":
                fresh["market"] = replace(fresh["market"], state=ServiceState.OFFLINE, raw_state="exited")
            else:
                name = change.split("_")[0]
                fresh[name] = replace(fresh[name], short_id="replacement")
            return fresh
    class Runner(FakeRunner):
        def run_parsed(self, args, **kwargs):
            self.calls.append((args, kwargs["cwd"], kwargs["timeout"]))
            return image
    runner = Runner()
    result = ManagedComposeController(ComposeTarget(tmp_path/"compose.yaml", tmp_path), Inspector(online), runner,
        policy=DockerControlPolicy.MANAGED).execute(DockerLifecycleAction.START_GAME)
    assert not result.succeeded and result.diagnostic_code == "runtime_changed"
    assert not result.immutable_images  # Prior image observations were invalidated.
    assert all("stop" not in args for args, _, _ in runner.calls)


def test_diagnostics_correlate_profile_stages_stop_reason_and_result(tmp_path, caplog):
    records = {name: record(name, ServiceState.OFFLINE, raw="exited") for name in ("server", "market")}
    with caplog.at_level("INFO", logger="src.core.runtime.docker_controller"):
        result = ManagedComposeController(ComposeTarget(tmp_path/"compose.yaml", tmp_path), FakeInspector(records),
            FakeRunner(), policy=DockerControlPolicy.MANAGED, installation_profile_id="world-ironman").execute(DockerLifecycleAction.STOP_ALL)
    assert result.succeeded
    entries = [entry.getMessage() for entry in caplog.records if "Docker lifecycle operation=" in entry.getMessage()]
    import re
    assert len(set(re.search(r"operation=([a-f0-9]{32})", line)[1] for line in entries)) == 1
    assert all("installation=world-ironman" in line and "elapsed=" in line for line in entries)
    assert any("reason=explicit_stop" in line for line in entries)
    assert any("stage=finished" in line and "reason=verified" in line for line in entries)
    assert str(tmp_path) not in "\n".join(entries)


def test_command_failure_retains_useful_bounded_redacted_stderr(tmp_path):
    from src.core.runtime.docker_cli import DockerCommandError
    class FailedRunner(FakeRunner):
        def run(self, args, **kwargs):
            raise DockerCommandError("compose", DockerCommandResult(tuple(args), 1, "", "port is already allocated password=sentinel " + "x"*2000, False, False))
    records = {name: record(name, ServiceState.OFFLINE, raw="exited") for name in ("server", "market")}
    result = ManagedComposeController(ComposeTarget(tmp_path/"compose.yaml", tmp_path), FakeInspector(records), FailedRunner(),
        policy=DockerControlPolicy.MANAGED).execute(DockerLifecycleAction.START_STACK)
    assert not result.succeeded and "port is already allocated" in result.error
    assert "sentinel" not in result.error and len(result.error) <= 512
