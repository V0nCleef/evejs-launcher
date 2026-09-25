"""Read-only Docker configuration checks for client automatic login."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from src.core.launcher import ClientLaunchContext
from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_autologin import (
    DockerAutoLoginContextError,
    DockerAutoLoginProbe,
    assert_docker_auto_login_context_current,
    inspect_docker_auto_login_capability,
)
from src.core.runtime.docker_compose import (
    ComposeInspector,
    ComposeTarget,
    ContainerRecord,
    PreflightReport,
    parse_compose_config,
)
from src.core.service_status import ServiceState


_FULL_ID = "a" * 64
_RUNTIME_ID = "b" * 64


def _fixture(tmp_path: Path):
    compose = tmp_path / "compose.yaml"
    compose.write_text("name: fixture\n", encoding="utf-8")
    target = ComposeTarget(compose, tmp_path, "fixture")
    config = parse_compose_config(
        {
            "name": "fixture",
            "services": {
                "market": {"ports": ["127.0.0.1:40110:40110"]},
                "server": {
                    "ports": [
                        "127.0.0.1:26000:26000",
                        "127.0.0.1:26001:26001",
                        "127.0.0.1:26002:26002",
                        "127.0.0.1:26003:26003",
                        "127.0.0.1:5222:5222",
                    ]
                },
            },
        }
    )
    record = ContainerRecord(
        "server",
        "fixture-server-1",
        _FULL_ID[:12],
        ServiceState.ONLINE,
        "healthy",
        0,
        (),
        raw_state="running",
    )
    report = PreflightReport(
        True,
        (),
        config,
        MappingProxyType({"server": record}),
    )
    target_identity = docker_project_identity(
        target,
        config.project_name,
        config=config,
    )
    context = ClientLaunchContext.from_docker(
        config.endpoints,
        target_identity=target_identity,
        settings_identity="fixture-settings",
        monitor_generation=7,
    )
    probe = DockerAutoLoginProbe(target, _RUNTIME_ID)
    return target, config, report, context, probe


class _Inspector:
    def __init__(self, report: PreflightReport, *, enabled: bool = True) -> None:
        self.report = report
        self.enabled = enabled
        self.preflight_calls = 0
        self.runtime_identities = [_RUNTIME_ID, _RUNTIME_ID]
        self.exec_container_ids: list[str] = []
        self.probe_error: Exception | None = None

    def preflight(self, _target: ComposeTarget) -> PreflightReport:
        self.preflight_calls += 1
        return self.report

    def container_runtime_id_and_identity(
        self,
        _target: ComposeTarget,
        _record: ContainerRecord,
    ) -> tuple[str, str]:
        runtime_identity = self.runtime_identities.pop(0)
        return _FULL_ID, runtime_identity

    def server_password_bypass_enabled(
        self,
        _target: ComposeTarget,
        _record: ContainerRecord,
        container_id: str,
    ) -> bool:
        self.exec_container_ids.append(container_id)
        if self.probe_error is not None:
            raise self.probe_error
        return self.enabled


def test_enabled_docker_bypass_is_verified_in_pinned_running_container(
    tmp_path: Path,
) -> None:
    _target, _config, report, context, probe = _fixture(tmp_path)
    inspector = _Inspector(report)

    check = inspect_docker_auto_login_capability(
        probe,
        context,
        inspector=inspector,
    )

    assert check.enabled is True
    assert check.verified is True
    assert inspector.exec_container_ids == [_FULL_ID]
    assert inspector.preflight_calls == 2


def test_verified_disabled_bypass_falls_back_without_auto_login(
    tmp_path: Path,
) -> None:
    _target, _config, report, context, probe = _fixture(tmp_path)
    inspector = _Inspector(report, enabled=False)

    check = inspect_docker_auto_login_capability(
        probe,
        context,
        inspector=inspector,
    )

    assert check.enabled is False
    assert check.verified is True
    assert "disabled" in check.reason.casefold()
    assert inspector.preflight_calls == 2


def test_config_probe_failure_after_identity_validation_falls_back_manually(
    tmp_path: Path,
) -> None:
    _target, _config, report, context, probe = _fixture(tmp_path)
    inspector = _Inspector(report)
    inspector.probe_error = RuntimeError("private command output")

    check = inspect_docker_auto_login_capability(
        probe,
        context,
        inspector=inspector,
    )

    assert check.enabled is False
    assert check.verified is False
    assert "could not be verified" in check.reason.casefold()
    assert "private command output" not in check.reason
    assert inspector.preflight_calls == 2


def test_stale_project_identity_aborts_before_running_probe(
    tmp_path: Path,
) -> None:
    _target, _config, report, context, probe = _fixture(tmp_path)
    inspector = _Inspector(report)
    stale_context = replace(context, target_identity="stale-target")

    with pytest.raises(DockerAutoLoginContextError, match="project changed"):
        inspect_docker_auto_login_capability(
            probe,
            stale_context,
            inspector=inspector,
        )

    assert inspector.exec_container_ids == []


def test_changed_container_or_endpoint_mapping_aborts_launch_context(
    tmp_path: Path,
) -> None:
    _target, _config, report, context, probe = _fixture(tmp_path)
    changed = _Inspector(report)
    changed.runtime_identities = [_RUNTIME_ID, "c" * 64]

    with pytest.raises(DockerAutoLoginContextError, match="changed after"):
        inspect_docker_auto_login_capability(probe, context, inspector=changed)

    bad_endpoint = replace(context, game_port=26001)
    unchanged = _Inspector(report)
    with pytest.raises(DockerAutoLoginContextError, match="endpoint mapping"):
        inspect_docker_auto_login_capability(
            probe,
            bad_endpoint,
            inspector=unchanged,
        )
    assert unchanged.exec_container_ids == []


def test_pre_spawn_check_revalidates_target_and_container_identity(
    tmp_path: Path,
) -> None:
    _target, _config, report, context, probe = _fixture(tmp_path)
    inspector = _Inspector(report)

    assert_docker_auto_login_context_current(
        probe,
        context,
        inspector=inspector,
    )

    assert inspector.preflight_calls == 1
    assert inspector.exec_container_ids == []


def test_compose_inspector_exec_probe_is_exact_and_secret_free(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class Runner:
        executable = "docker"

        def run_parsed(self, args, *, cwd, parser, timeout=10.0):
            captured["args"] = args
            captured["cwd"] = cwd
            return parser("true\n")

    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    inspector = ComposeInspector(Runner())

    record = ContainerRecord(
        "server",
        "fixture-server",
        _FULL_ID[:12],
        ServiceState.ONLINE,
        "healthy",
        0,
        (),
        raw_state="running",
    )
    assert inspector.server_password_bypass_enabled(target, record, _FULL_ID) is True
    assert captured["args"] == (
        "exec",
        "--env",
        "NODE_OPTIONS=",
        _FULL_ID,
        "node",
        "-e",
        "const c=require('/app/server/src/config');"
        "process.stdout.write(c.devSkipPasswordValidation===true?'true\\n':'false\\n')",
    )
    assert "NODE_OPTIONS=" in captured["args"]

    with pytest.raises(ValueError, match="server identity"):
        inspector.server_password_bypass_enabled(target, record, "unsafe;id")
