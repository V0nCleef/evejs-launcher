import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.docker_mod_contract import parse_docker_mod_spec
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord, ComposeService, Mount
from src.core.runtime.docker_cli import DockerCommandResult, DockerCommandRunner
from src.core.runtime.docker_mod_helpers import DockerModHelperController, DockerModHelperError, RESULT_PREFIX
from src.core.runtime.docker_mod_images import attach_image_override, prepare_image_override
from src.core.service_status import DockerControlPolicy, ServiceState


def package(tmp_path):
    folder = tmp_path / "mods" / "portable"
    folder.mkdir(parents=True)
    (folder / "helper.cjs").write_text("// stdin helper")
    (folder / "evejs-launcher.mod.json").write_text("{}")
    payload = {"schemaVersion": 1, "dataDirectory": "portable", "serverHelper": {
        "runtime": "node", "path": "helper.cjs", "actions": ["install", "verify"]},
        "hostActions": ["prepare_profile"]}
    return folder, payload


def test_container_contract_rejects_escapes_unknown_actions_and_ambiguous_location(tmp_path):
    folder, payload = package(tmp_path)
    assert parse_docker_mod_spec(payload, folder).data_directory == "portable"
    for invalid in ("../outside", "/absolute", "C:/Windows", "nested/../outside"):
        with pytest.raises(ValueError):
            parse_docker_mod_spec(dict(payload, dataDirectory=invalid), folder)
    with pytest.raises(ValueError):
        parse_docker_mod_spec(dict(payload, hostActions=["install"]), folder)
    with pytest.raises(ValueError):
        parse_docker_mod_spec(dict(payload, hostActions=["arbitrary-command"]), folder)


def test_observer_and_connect_only_do_not_create_image_override(tmp_path):
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    assert attach_image_override(target) == target
    assert not (tmp_path / ".evejs-launcher").exists()
    with pytest.raises(PermissionError):
        prepare_image_override(tmp_path, policy=DockerControlPolicy.CONNECT_ONLY)
    assert not (tmp_path / ".evejs-launcher").exists()


@pytest.mark.parametrize("running", [False, True])
def test_server_helper_uses_correlated_stdin_and_container_paths(tmp_path, running):
    folder, payload = package(tmp_path)
    descriptor = SimpleNamespace(root=tmp_path, folder=folder, id="portable", version="1.0.0",
        manifest_path=folder / "evejs-launcher.mod.json", docker=parse_docker_mod_spec(payload, folder))
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path)
    record = ContainerRecord("server", None, "a" * 12, ServiceState.ONLINE if running else ServiceState.OFFLINE,
        "healthy" if running else None, 0, (), True, "running" if running else "exited")
    config = SimpleNamespace(project_name="fixture", services={"server": ComposeService("server", True, (), None,
        (Mount("volume", "fixture-data", "/var/lib/evejs"),))}, effective_config_digest="a" * 64)
    report = SimpleNamespace(ok=True, config=config, records={"server": record})
    inspector = SimpleNamespace(preflight=lambda _target: report)
    class Runner:
        executable = "docker.exe"
        calls = []
        _bound_without_input = DockerCommandRunner._bound_without_input
        _bound = DockerCommandRunner._bound
        _redact = staticmethod(DockerCommandRunner._redact)
        _output_limit = 65536
        def run_with_input_parsed(self, args, *, cwd, input_bytes, timeout, parser):
            request = json.loads(input_bytes)
            self.calls.append((args, request))
            assert request["runtime"]["evejsRoot"] == "/app"
            assert request["runtime"]["modRoot"] == "/app/mods/portable"
            assert "clientRoot" not in request["runtime"]
            reply = {"protocol": request["protocol"], "requestId": request["requestId"],
                "success": True, "state": "ready", "message": "verified", "restartRequired": [],
                "contributions": [], "environment": {}, "arguments": []}
            return parser(RESULT_PREFIX + json.dumps(reply), 0)
    runner = Runner()
    controller = DockerModHelperController(target, inspector, runner, policy=DockerControlPolicy.MANAGED)
    _, reply = controller.execute(descriptor, "verify")
    assert reply["success"]
    assert ("exec" if running else "run") in runner.calls[0][0]
    assert "NODE_OPTIONS=" in runner.calls[0][0]
    if running:
        with pytest.raises(DockerModHelperError, match="Stop"):
            controller.execute(descriptor, "install")
        assert len(runner.calls) == 1


def test_connect_only_helper_refuses_before_any_target_probe():
    with pytest.raises(PermissionError):
        DockerModHelperController(None, None, None, policy=DockerControlPolicy.CONNECT_ONLY)


def portable_declaration(folder):
    from src.core.mod_api_manifest import ENABLED_UPDATE_ACTIONS
    actions = ["install", "verify", "prepare_disable", "prepare_remove", "prepare_server_start", *sorted(ENABLED_UPDATE_ACTIONS)]
    (folder / "loader.js").write_text("// portable loader")
    return dict(schemaVersion=3, id="portable", displayName="Portable", version="1.1.0", kind="loader",
        supportedBackends=["docker"], activation={"strategy": "loader_rename"}, restart="game_server",
        launcherApi=dict(version=1, minLauncherVersion="1.0.80", helper=dict(runtime="node", path="helper.cjs"),
            capabilities=actions, enabledUpdate=dict(protocol="evejs-mod-enabled-update-v1", compatibility="portable.v1", fromVersions=["1.0.0"])),
        docker=dict(schemaVersion=1, dataDirectory="portable", serverHelper=dict(runtime="node", path="helper.cjs", actions=actions), hostActions=[]))


def test_docker_only_package_admits_declared_start_and_enabled_update(tmp_path):
    from src.core.mod_api_manifest import read_api_manifest
    folder, _ = package(tmp_path)
    declaration = portable_declaration(folder)
    path = folder / "evejs-launcher.mod.json"
    path.write_text(json.dumps(declaration))
    descriptor = read_api_manifest(tmp_path, folder)
    assert descriptor.supported_backends == ("docker",)
    assert descriptor.launcher_api.enabled_update.from_versions == ("1.0.0",)
    assert "prepare_server_start" in descriptor.docker.server_actions


@pytest.mark.parametrize("missing", ["docker", "prepare_server_start", "apply_update", "minimum"])
def test_docker_only_lifecycle_requires_explicit_portable_contract(tmp_path, missing):
    from src.core.mod_api_manifest import read_api_manifest, ModApiManifestError
    folder, _ = package(tmp_path)
    declaration = portable_declaration(folder)
    if missing == "docker":
        del declaration["docker"]
    elif missing == "minimum":
        declaration["launcherApi"]["minLauncherVersion"] = "1.0.79"
    else:
        declaration["docker"]["serverHelper"]["actions"] = [action for action in declaration["launcherApi"]["capabilities"] if action != missing]
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(declaration))
    with pytest.raises(ModApiManifestError):
        read_api_manifest(tmp_path, folder)
