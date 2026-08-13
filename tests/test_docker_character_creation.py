"""Managed Docker character creation command and safety contracts."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_character_creation import (
    DockerCharacterCreationRequest,
    ManagedDockerCharacterCreationController,
)
from src.core.runtime.docker_cli import (
    DockerCommandError,
    DockerCommandResult,
    DockerCommandRunner,
)
from src.core.runtime.docker_compose import (
    ComposeCapabilities,
    ComposeConfig,
    ComposeService,
    ComposeTarget,
    ContainerRecord,
    Mount,
    PreflightReport,
)
from src.core.service_status import DockerControlPolicy, ServiceState


RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="


class FakeInspector:
    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        self.reports: list[PreflightReport] = []
        self.calls: list[ComposeTarget] = []

    def preflight(self, target: ComposeTarget) -> PreflightReport:
        self.calls.append(target)
        if self.reports:
            return self.reports.pop(0)
        return self.report


class FakeRunner:
    executable = "docker-fixture"

    def __init__(
        self,
        stdout: str = "",
        *,
        truncated: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.stdout = stdout
        self.truncated = truncated
        self.error = error
        self.calls: list[tuple[tuple[str, ...], Path, bytes, float]] = []

    def run_with_input(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
        input_bytes: bytes,
        timeout: float,
    ) -> DockerCommandResult:
        self.calls.append((args, cwd, input_bytes, timeout))
        if self.error is not None:
            raise self.error
        return DockerCommandResult(
            (self.executable, *args),
            0,
            self.stdout,
            "",
            self.truncated,
            False,
        )


def _service(name: str, *mounts: Mount) -> ComposeService:
    return ComposeService(
        name,
        False,
        (),
        None,
        mounts,
        image="evejs-local",
        pull_policy="never",
    )


def _record(
    service: str,
    raw_state: str | None,
    *,
    exists: bool = True,
) -> ContainerRecord:
    state = ServiceState.ONLINE if raw_state == "running" else ServiceState.OFFLINE
    return ContainerRecord(
        service,
        None,
        None,
        state,
        None,
        None,
        (),
        exists=exists,
        raw_state=raw_state,
    )


def _success_output() -> str:
    return RESULT_PREFIX + json.dumps(
        {
            "ok": True,
            "accountId": 101,
            "characterId": 202,
            "rookieShipVerified": True,
            "backupCreated": True,
            "cleanupConfirmed": True,
            "restartSafe": True,
        },
        separators=(",", ":"),
    )


def _fixture(
    tmp_path: Path,
) -> tuple[
    ComposeTarget,
    Path,
    Path,
    ComposeConfig,
    dict[str, ContainerRecord],
]:
    project = tmp_path / "project"
    project.mkdir()
    compose_file = project / "compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    helper_directory = tmp_path / "helpers"
    helper_directory.mkdir()
    (helper_directory / "docker_create_character.js").write_text(
        "// fixture\n", encoding="utf-8"
    )
    (helper_directory / "game_store_maintenance.js").write_text(
        "// fixture\n", encoding="utf-8"
    )
    (helper_directory / "terminal_result.js").write_text(
        "// fixture\n", encoding="utf-8"
    )
    backup_directory = tmp_path / "backup-operation"
    target = ComposeTarget(compose_file, project, "selected-project")
    services = {
        "server": _service(
            "server",
            Mount("volume", "evejs-data", "/var/lib/evejs"),
        ),
        "market": _service("market"),
        "init": _service("init"),
    }
    config = ComposeConfig(
        "effective-project",
        services,
        object(),  # type: ignore[arg-type] - endpoints are irrelevant here
        ComposeCapabilities(init=True, market_tools=False),
        "a" * 64,
    )
    records = {
        service: _record(service, "exited")
        for service in ("server", "market", "init")
    }
    return target, helper_directory, backup_directory, config, records


def _controller(
    target: ComposeTarget,
    helper_directory: Path,
    backup_directory: Path,
    config: ComposeConfig,
    records: dict[str, ContainerRecord],
    runner: FakeRunner,
    *,
    expected_identity: str | None = None,
) -> tuple[ManagedDockerCharacterCreationController, FakeInspector]:
    inspector = FakeInspector(PreflightReport(True, (), config, records))
    controller = ManagedDockerCharacterCreationController(
        target,
        inspector,
        runner,
        policy=DockerControlPolicy.MANAGED,
        expected_target_identity=(
            expected_identity
            or docker_project_identity(
                target,
                config.project_name,
                config=config,
            )
        ),
        helper_directory=helper_directory,
        backup_directory=backup_directory,
    )
    return controller, inspector


def _with_server_mounts(
    config: ComposeConfig,
    *mounts: Mount,
) -> ComposeConfig:
    services = dict(config.services)
    services["server"] = replace(services["server"], mounts=mounts)
    return replace(config, services=services)


def test_exact_one_off_command_and_canonical_private_stdin(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    request = DockerCharacterCreationRequest(
        "  captain_01  ", "\ufeff Capsule\ufeff  Pilot \ufeff", True
    )

    result = controller.execute(request)

    identity = docker_project_identity(
        target,
        config.project_name,
        config=config,
    )
    assert result.succeeded
    assert result.account_id == 101
    assert result.character_id == 202
    assert result.backup_created is True
    assert result.cleanup_confirmed is True
    assert result.restart_safe is True
    assert result.target_identity == identity
    assert inspector.calls == [target, target]
    assert len(runner.calls) == 1
    argv, cwd, input_bytes, timeout = runner.calls[0]
    assert argv == target.compose_args(
        runner.executable,
        "run",
        "--pull",
        "never",
        "--rm",
        "--no-deps",
        "-T",
        "--user",
        "node",
        "--volume",
        "/app/server/logs",
        "--volume",
        f"{helpers.resolve()}:/run/evejs-launcher/helpers:ro",
        "--volume",
        f"{backup.resolve()}:/run/evejs-launcher/backup:rw",
        "--env",
        "NODE_OPTIONS=",
        "--env",
        "EVEJS_LOG_LEVEL=0",
        "--env",
        "EVEJS_GAMESTORE_OWNER_ROLE=maintenance",
        "--env",
        "EVEJS_PERSISTENCE_OWNER_LEASE_MS=900000",
        "--env",
        "EVEJS_GAMESTORE_SQLITE_PATH=/var/lib/evejs/gameStore/gamestore.sqlite",
        "--env",
        "EVEJS_GAMESTORE_DATA_DIR=/var/lib/evejs/gameStore/data",
        "--entrypoint",
        "node",
        "--workdir",
        "/app",
        "server",
        "/run/evejs-launcher/helpers/docker_create_character.js",
    )
    assert cwd == target.project_directory
    assert timeout == 300.0
    assert input_bytes == (
        b'{"characterName":"Capsule Pilot","isGM":true,'
        b'"password":"evejs-local","username":"captain_01"}'
    )
    assert "captain_01" not in " ".join(argv)
    assert "Capsule Pilot" not in " ".join(argv)
    assert "captain_01" not in repr(result)
    assert "Capsule Pilot" not in repr(result)
    assert "/app/server/logs" in argv
    assert "EVEJS_LOG_LEVEL=0" in argv
    assert "--pull" in argv and "never" in argv


def test_changed_second_effective_preflight_blocks_before_backup_or_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    original_compose_bytes = target.compose_file.read_bytes()
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    changed_services = dict(config.services)
    changed_services["server"] = replace(
        changed_services["server"],
        image="evejs-changed",
        mounts=(Mount("volume", "changed-store", "/var/lib/evejs"),),
    )
    changed_config = replace(
        config,
        services=changed_services,
        effective_config_digest="b" * 64,
    )
    inspector.reports = [
        PreflightReport(True, (), config, records),
        PreflightReport(True, (), changed_config, records),
    ]
    monkeypatch.setattr(
        controller,
        "_prepare_backup_directory",
        lambda: pytest.fail("Changed effective authority reached backup preparation"),
    )

    result = controller.execute(
        DockerCharacterCreationRequest("fixture-account", "Fixture Pilot", False)
    )

    assert result.succeeded is False
    assert result.backup_created is False
    assert result.restart_safe is True
    assert "authority changed" in result.error.casefold()
    assert inspector.calls == [target, target]
    assert runner.calls == []
    assert not backup.exists()
    assert target.compose_file.read_bytes() == original_compose_bytes


def test_unchanged_second_effective_preflight_runs_one_helper(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("fixture-account", "Fixture Pilot", False)
    )

    assert result.succeeded is True
    assert inspector.calls == [target, target]
    assert len(runner.calls) == 1
    assert backup.is_dir()


def test_reviewed_contract_requires_never_pull_policy(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    services = dict(config.services)
    services["server"] = replace(services["server"], pull_policy="missing")
    config = replace(config, services=services)
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert runner.calls == []
    assert not backup.exists()


@pytest.mark.parametrize(
    "topology",
    ["exact", "live-ancestor", "backup-ancestor", "helper-overlap"],
)
def test_bind_game_store_rejects_helper_or_backup_path_overlap(
    topology: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, helpers, default_backup, config, records = _fixture(tmp_path)
    if topology == "exact":
        backup = tmp_path / "shared-game-store"
        backup.mkdir()
        live_store = backup
    elif topology == "live-ancestor":
        live_store = tmp_path / "live-game-store"
        live_store.mkdir()
        backup = live_store / "launcher-backups"
    elif topology == "backup-ancestor":
        backup = tmp_path / "launcher-backups"
        backup.mkdir()
        live_store = backup / "live-game-store"
        live_store.mkdir()
    else:
        live_store = helpers
        backup = default_backup

    config = _with_server_mounts(
        config,
        Mount("bind", str(live_store), "/var/lib/evejs/gameStore"),
    )
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    monkeypatch.setattr(
        controller,
        "_prepare_backup_directory",
        lambda: pytest.fail("Overlapping topology reached backup preparation"),
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert inspector.calls == [target]
    assert runner.calls == []
    if topology == "live-ancestor":
        assert not backup.exists()


def test_non_overlapping_parent_bind_resolves_game_store_suffix(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    live_root = tmp_path / "live-bind"
    (live_root / "gameStore").mkdir(parents=True)
    config = _with_server_mounts(
        config,
        Mount("bind", str(live_root), "/var/lib/evejs"),
    )
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert result.succeeded
    assert len(runner.calls) == 1
    assert backup.is_dir()


def test_named_volume_preserves_reviewed_character_creation_path(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    config = _with_server_mounts(
        config,
        Mount("volume", "evejs-game-data", "/var/lib/evejs/gameStore"),
    )
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert result.succeeded
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "nested_target",
    [
        "/var/lib/evejs/gameStore/data",
        "/var/lib/evejs/gameStore/data/accounts",
    ],
    ids=["data", "table"],
)
def test_reviewed_contract_rejects_nested_game_store_mounts(
    nested_target: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    config = _with_server_mounts(
        config,
        Mount("volume", "evejs-game-data", "/var/lib/evejs/gameStore"),
        Mount("volume", "unexpected-overlay", nested_target),
    )
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    monkeypatch.setattr(
        controller,
        "_prepare_backup_directory",
        lambda: pytest.fail("Nested mount reached backup preparation"),
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert inspector.calls == [target]
    assert runner.calls == []
    assert not backup.exists()


def test_controller_requires_managed_policy_before_other_setup() -> None:
    with pytest.raises(PermissionError):
        ManagedDockerCharacterCreationController(
            object(),  # type: ignore[arg-type]
            object(),
            object(),
            policy=DockerControlPolicy.CONNECT_ONLY,
            expected_target_identity="not-a-target",
            helper_directory=Path("relative-helper"),
            backup_directory=Path("relative-backup"),
        )


def test_effective_target_change_blocks_before_command(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(_success_output())
    expected = docker_project_identity(target, "previous-effective-project")
    controller, inspector = _controller(
        target,
        helpers,
        backup,
        config,
        records,
        runner,
        expected_identity=expected,
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "changed" in result.error.casefold()
    assert result.target_identity == docker_project_identity(
        target,
        config.project_name,
        config=config,
    )
    assert result.backup_created is False
    assert result.rollback_confirmed is True
    assert result.restart_safe is True
    assert inspector.calls == [target]
    assert runner.calls == []
    assert not backup.exists()


@pytest.mark.parametrize("raw_state", ["created", "exited", "dead"])
def test_all_three_services_accept_only_explicit_safe_stopped_states(
    raw_state: str,
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    records = {
        service: _record(service, raw_state)
        for service in ("server", "market", "init")
    }
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert result.succeeded
    assert len(runner.calls) == 1


@pytest.mark.parametrize("service", ["server", "market", "init"])
@pytest.mark.parametrize(
    "raw_state",
    ["running", "restarting", "starting", "stopping", "paused", None],
)
def test_any_unsafe_raw_service_state_blocks_the_helper(
    service: str,
    raw_state: str | None,
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    records[service] = _record(service, raw_state)
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert service.title() in result.error
    assert result.backup_created is False
    assert result.rollback_confirmed is True
    assert result.restart_safe is True
    assert runner.calls == []
    assert not backup.exists()


def test_absent_records_are_safely_stopped(tmp_path: Path) -> None:
    target, helpers, backup, config, _records = _fixture(tmp_path)
    records = {
        service: ContainerRecord.absent(service)
        for service in ("server", "market", "init")
    }
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert result.succeeded


@pytest.mark.parametrize("unsupported", ["missing_init", "tmpfs", "no_mount"])
def test_reviewed_v0125_structural_gate_runs_no_helper(
    unsupported: str,
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    services = dict(config.services)
    capabilities = config.capabilities
    if unsupported == "missing_init":
        services.pop("init")
        capabilities = ComposeCapabilities(init=False, market_tools=False)
    elif unsupported == "tmpfs":
        services["server"] = _service(
            "server", Mount("tmpfs", "ephemeral", "/var/lib/evejs")
        )
    else:
        services["server"] = _service("server")
    config = replace(config, services=services, capabilities=capabilities)
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "v0.12.5" in result.error
    assert inspector.calls == [target]
    assert runner.calls == []


def test_additional_effective_service_blocks_character_mutation(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    services = dict(config.services)
    services["extension"] = _service(
        "extension",
        Mount("volume", "evejs-data", "/var/lib/evejs"),
    )
    records["extension"] = _record("extension", "exited")
    config = replace(config, services=services)
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "additional services" in result.error.casefold()
    assert runner.calls == []
    assert not backup.exists()


def test_additional_observed_service_record_blocks_character_mutation(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    records["orphaned-tool"] = _record("orphaned-tool", "running")
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "additional service records" in result.error.casefold()
    assert runner.calls == []
    assert not backup.exists()


@pytest.mark.parametrize(
    "missing",
    [
        "docker_create_character.js",
        "game_store_maintenance.js",
        "terminal_result.js",
    ],
)
def test_missing_fixed_helper_contract_blocks_execution(
    missing: str,
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    (helpers / missing).unlink()
    runner = FakeRunner(_success_output())
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert runner.calls == []
    assert not backup.exists()


def test_failed_preflight_never_exposes_diagnostics_or_runs_helper(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    inspector.report = PreflightReport(
        False,
        ("captain_01 Capsule Pilot private/project/path",),
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "captain_01" not in result.error
    assert "Capsule Pilot" not in result.error
    assert "private/project" not in result.error
    assert result.backup_created is False
    assert result.rollback_confirmed is True
    assert result.restart_safe is True
    assert runner.calls == []
    assert not backup.exists()


def test_failure_marker_exposes_only_rollback_and_restart_safety(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    output = RESULT_PREFIX + json.dumps(
        {
            "ok": False,
            "error": "captain_01 Capsule Pilot secret detail",
            "backupCreated": True,
            "rollbackSucceeded": True,
            "restartSafe": True,
        }
    )
    runner = FakeRunner(output)
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert result.backup_created is True
    assert result.rollback_confirmed is True
    assert result.restart_safe is True
    assert "restored" in result.error.casefold()
    assert "captain_01" not in repr(result)
    assert "Capsule Pilot" not in repr(result)
    assert "secret detail" not in repr(result)


def test_cli_exit_zero_semantic_failure_marker_remains_private(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    calls: list[tuple[tuple[str, ...], Path, float, bytes]] = []
    marker = RESULT_PREFIX + json.dumps(
        {
            "ok": False,
            "error": "local-account Local Pilot INTERNAL_HELPER_DETAIL",
            "backupCreated": True,
            "rollbackSucceeded": True,
            "restartSafe": True,
        },
        separators=(",", ":"),
    )

    def execute_input(
        argv: tuple[str, ...],
        cwd: Path,
        timeout: float,
        input_bytes: bytes,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, cwd, timeout, input_bytes))
        # Helper semantic failures deliberately exit zero so the bounded marker
        # reaches the controller instead of DockerCommandError dropping stdout.
        return subprocess.CompletedProcess(argv, 0, marker, "")

    runner = DockerCommandRunner(
        executable="docker-fixture",
        execute_input=execute_input,
    )
    controller, _inspector = _controller(
        target,
        helpers,
        backup,
        config,
        records,
        runner,  # type: ignore[arg-type] - exercise the production runner seam
    )

    result = controller.execute(
        DockerCharacterCreationRequest("local-account", "Local Pilot")
    )

    assert len(calls) == 1
    assert calls[0][0][0] == "docker-fixture"
    assert not result.succeeded
    assert result.backup_created is True
    assert result.rollback_confirmed is True
    assert result.restart_safe is True
    assert "restored" in result.error.casefold()
    assert "local-account" not in repr(result)
    assert "Local Pilot" not in repr(result)
    assert "INTERNAL_HELPER_DETAIL" not in repr(result)


def test_verified_commit_without_cleanup_is_success_but_not_restart_safe(
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    output = RESULT_PREFIX + json.dumps(
        {
            "ok": True,
            "accountId": 101,
            "characterId": 202,
            "rookieShipVerified": True,
            "backupCreated": True,
            "cleanupConfirmed": False,
            "restartSafe": False,
        },
        separators=(",", ":"),
    )
    controller, _inspector = _controller(
        target,
        helpers,
        backup,
        config,
        records,
        FakeRunner(output),
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert result.succeeded
    assert result.account_id == 101
    assert result.character_id == 202
    assert result.backup_created is True
    assert result.cleanup_confirmed is False
    assert result.restart_safe is False
    assert "cleanup" in result.message.casefold()
    assert not result.error


@pytest.mark.parametrize(
    ("cleanup_confirmed", "restart_safe"),
    [(True, False), (False, True), (None, False)],
)
def test_success_marker_requires_coherent_cleanup_contract(
    cleanup_confirmed: bool | None,
    restart_safe: bool,
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    marker: dict[str, object] = {
        "ok": True,
        "accountId": 101,
        "characterId": 202,
        "rookieShipVerified": True,
        "backupCreated": True,
        "restartSafe": restart_safe,
    }
    if cleanup_confirmed is not None:
        marker["cleanupConfirmed"] = cleanup_confirmed
    controller, _inspector = _controller(
        target,
        helpers,
        backup,
        config,
        records,
        FakeRunner(RESULT_PREFIX + json.dumps(marker, separators=(",", ":"))),
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert result.cleanup_confirmed is None
    assert result.restart_safe is None
    assert "unverifiable" in result.error.casefold()


@pytest.mark.parametrize(
    "stdout,truncated",
    [
        ("ordinary output without a marker", False),
        (RESULT_PREFIX + "not-json", False),
        (_success_output() + "\n" + _success_output(), False),
        (_success_output(), True),
    ],
)
def test_only_one_complete_bounded_result_marker_is_accepted(
    stdout: str,
    truncated: bool,
    tmp_path: Path,
) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(stdout, truncated=truncated)
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "captain_01" not in result.error
    assert runner.calls


def test_success_requires_verified_backup_and_rookie_ship(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    output = RESULT_PREFIX + json.dumps(
        {
            "ok": True,
            "accountId": 101,
            "characterId": 202,
            "rookieShipVerified": True,
            "backupCreated": False,
        }
    )
    runner = FakeRunner(output)
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert result.account_id is None
    assert result.character_id is None


def test_invalid_request_is_private_safe_and_runs_no_docker(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    private_name = "x" * 200

    result = controller.execute(
        DockerCharacterCreationRequest("bad account", private_name)
    )

    assert not result.succeeded
    assert "bad account" not in result.error
    assert private_name not in result.error
    assert inspector.calls == []
    assert runner.calls == []


def test_input_runner_failure_never_exposes_private_values(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    command_result = DockerCommandResult(
        ("docker-fixture", "compose"),
        1,
        "",
        "",
        False,
        False,
    )
    runner = FakeRunner(
        error=DockerCommandError(
            "captain_01 Capsule Pilot",
            command_result,
        )
    )
    controller, _inspector = _controller(
        target, helpers, backup, config, records, runner
    )

    result = controller.execute(
        DockerCharacterCreationRequest("captain_01", "Capsule Pilot")
    )

    assert not result.succeeded
    assert "captain_01" not in result.error
    assert "Capsule Pilot" not in result.error


def test_each_execute_uses_a_fresh_compose_preflight(tmp_path: Path) -> None:
    target, helpers, backup, config, records = _fixture(tmp_path)
    runner = FakeRunner(_success_output())
    controller, inspector = _controller(
        target, helpers, backup, config, records, runner
    )
    request = DockerCharacterCreationRequest("captain_01", "Capsule Pilot")

    first = controller.execute(request)
    records["server"] = _record("server", "running")
    second = controller.execute(request)

    assert first.succeeded
    assert not second.succeeded
    assert inspector.calls == [target, target, target]
    assert len(runner.calls) == 1
