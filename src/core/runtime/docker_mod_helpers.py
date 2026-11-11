"""Closed, target-bound stdin protocol for declared container-side mod actions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_cli import redact_docker_diagnostic
from src.core.service_status import DockerControlPolicy

RESULT_PREFIX = "EVEJS_MOD_HELPER_RESULT="
MUTATIONS = frozenset({"install", "prepare_disable", "prepare_remove", "recover", "prepare_update", "apply_update", "commit_update", "rollback_update", "prepare_server_start"})


class DockerModHelperError(RuntimeError):
    pass


def installation_identity(target, config):
    """Persistent storage owner; image and preload edits are operation identities."""
    stores = [(role, mount.type, mount.source, mount.target)
              for role, service in sorted(config.services.items()) if role in {"server", "market"}
              for mount in service.mounts
              if mount.target == "/var/lib/evejs" or mount.target.startswith("/var/lib/evejs/")]
    if not stores:
        raise DockerModHelperError("Container mod state requires the selected persistent /var/lib/evejs data mount.")
    actual_storage = [item for item in getattr(config, "resource_keys", ()) if item[0] in {"volume", "data bind"}]
    return "installation:" + hashlib.sha256(json.dumps((str(target.project_directory.resolve()),
        config.project_name or target.project_name, sorted(stores), actual_storage), separators=(",", ":")).encode()).hexdigest()


class DockerModHelperController:
    def __init__(self, target, inspector, runner, *, policy, expected_identity=None):
        if policy is not DockerControlPolicy.MANAGED:
            raise PermissionError("Connect-only mode cannot execute mod lifecycle helpers.")
        self.target, self.inspector, self.runner = target, inspector, runner
        self.expected_identity = expected_identity

    def execute(self, descriptor, action, *, settings=None, host_capabilities=None, update=None):
        spec = descriptor.docker
        if spec is None or action not in spec.server_actions:
            raise DockerModHelperError("The mod has not declared this container action.")
        root = Path(descriptor.root).resolve(strict=True)
        if root != self.target.project_directory.resolve(strict=True):
            raise DockerModHelperError("The helper belongs to a different installation.")
        relative = spec.helper_path.relative_to(descriptor.folder).as_posix()
        package = descriptor.folder.resolve(strict=True)
        package.relative_to(root)
        digest = hashlib.sha256(descriptor.manifest_path.read_bytes()).hexdigest()
        try:
            folder = package.relative_to(root / "mods")
        except ValueError:
            folder = None
        container_package = ("/app/mods/" + folder.as_posix() if folder is not None and len(folder.parts) == 1
                             else "/app/.evejs-launcher/mod-provider/" + digest)
        if folder is None and update is None:
            raise DockerModHelperError("Only retained update providers may execute outside the mods directory.")
        helper_digest = hashlib.sha256(spec.helper_path.read_bytes()).hexdigest()
        report = self.inspector.preflight(self.target)
        if not report.ok or report.config is None or report.records is None:
            raise DockerModHelperError("Docker mod preflight failed; inspect the selected project.")
        identity = docker_project_identity(self.target, report.config.project_name, config=report.config)
        storage_identity = installation_identity(self.target, report.config)
        if self.expected_identity is not None and identity != self.expected_identity:
            raise DockerModHelperError("The Docker target changed before mod preparation.")
        if action in MUTATIONS and any(record.exists and record.raw_state not in {"created", "exited", "dead"}
                for role, record in report.records.items() if not (action == "prepare_server_start" and role == "market")):
            raise DockerModHelperError("Stop the selected project's services and maintenance writers before this mod action.")
        final = self.inspector.preflight(self.target)
        if (not final.ok or final.config is None or final.records != report.records
                or final.config.effective_config_digest != report.config.effective_config_digest
                or hashlib.sha256(spec.helper_path.read_bytes()).hexdigest() != helper_digest):
            raise DockerModHelperError("Docker authority or helper content changed before execution.")
        request_id = uuid.uuid4().hex
        request = {
            "protocol": "evejs_launcher_mod_v1", "requestId": request_id, "action": action,
            "mod": {"id": descriptor.id, "version": descriptor.version,
                    "root": container_package, "helperSha256": helper_digest},
            "runtime": {"backend": "docker", "platform": "linux", "evejsRoot": "/app",
                        "dataRoot": "/var/lib/evejs", "modRoot": container_package,
                        "modDataRoot": "/var/lib/evejs/mods/" + spec.data_directory,
                        "targetIdentity": identity, "installationIdentity": storage_identity, "role": "server"},
            "settings": settings or {"global": {}, "profile": {}},
            "hostCapabilities": host_capabilities or {},
        }
        if action == "prepare_server_start":
            market = report.records.get("market")
            service = report.config.services.get("market")
            if market is None or market.raw_state != "running" or not market.short_id or service is None or not service.image:
                raise DockerModHelperError("Start the selected Market before preparing Game mods.")
            def image_id(text):
                import re
                value = text.strip()
                if not re.fullmatch(r"sha256:[0-9a-f]{64}",value):
                    raise DockerModHelperError("The selected Market image identity is unavailable.")
                return value
            expected_image = (service.image if service.image.startswith("sha256:") else self.runner.run_parsed(
                ("image","inspect","--format","{{.Id}}",service.image),cwd=root,parser=image_id,timeout=15))
            actual_image = self.runner.run_parsed(("inspect","--type","container","--format","{{.Image}}",market.short_id),
                cwd=root,parser=image_id,timeout=15)
            if actual_image != expected_image:
                raise DockerModHelperError("The running Market does not use its declared image. Apply Mods before starting Game.")
            request["runtime"]["market"] = {"imageId":actual_image,"expectedImageId":expected_image}
            current = self.inspector.preflight(self.target)
            if (not current.ok or current.config is None or current.records != report.records
                    or current.config.effective_config_digest != report.config.effective_config_digest):
                raise DockerModHelperError("The selected Market or Game changed during image verification. Retry preparation.")
        if update is not None:
            from src.core.mod_api_runtime import validate_enabled_update_context
            from src.core.mod_settings import ModSettingsContext
            validate_enabled_update_context(descriptor, ModSettingsContext(root, package), update, "docker")
            old_folder = Path(update["fromModRoot"]).relative_to(root / "mods")
            request["update"] = dict(update, fromModRoot="/app/mods/" + old_folder.as_posix())
        payload = json.dumps(request, separators=(",", ":"), allow_nan=False).encode()
        if len(payload) > 16 * 1024:
            raise DockerModHelperError("Docker helper request exceeds the safe bound.")
        record = report.records.get("server")
        path = container_package + "/" + relative
        if record is not None and record.exists and record.raw_state == "running":
            command = ("exec", "-T", "-e", "NODE_OPTIONS=", "-e", "EVEJS_MOD_DATA_ROOT=/var/lib/evejs/mods", "server", "node", path, "--stdin")
        else:
            mounts = ("--volume", f"{(root / 'mods').as_posix()}:/app/mods:ro")
            if container_package.startswith("/app/.evejs-launcher/mod-provider/"):
                mounts += ("--volume", f"{package.as_posix()}:{container_package}:ro")
            command = ("run", "--pull", "never", "--rm", "--no-deps", "-T", *mounts,
                       "--entrypoint", "node", "-e", "NODE_OPTIONS=", "-e", "EVEJS_MOD_DATA_ROOT=/var/lib/evejs/mods",
                       "server", path, "--stdin")
        def parse_reply(stdout, returncode):
            return self._parse_reply(stdout, returncode, request_id, request["protocol"], payload)
        value = self.runner.run_with_input_parsed(self.target.compose_args(self.runner.executable, *command),
                    cwd=self.target.project_directory, input_bytes=payload, timeout=120.0, parser=parse_reply)
        return request_id, value

    def _parse_reply(self, stdout, returncode, request_id, protocol, payload):
        lines = [line[len(RESULT_PREFIX):] for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
        if len(lines) != 1 or len(lines[0].encode()) > 64 * 1024:
            raise DockerModHelperError("Docker mod helper did not return one correlated result.")
        try:
            from src.core.mod_api_manifest import _object
            value = json.loads(lines[0], object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            raise DockerModHelperError("Docker mod helper returned invalid JSON.") from None
        if (not isinstance(value, dict) or value.get("protocol") != protocol
                or value.get("requestId") != request_id or type(value.get("success")) is not bool
                or value.get("state") not in {"ready", "failed", "blocked", "unsupported", "recovery-required"}
                or value.get("contributions", []) or value.get("environment", {}) or value.get("arguments", [])):
            raise DockerModHelperError("Docker mod helper returned an unsupported result contract.")
        allowed = {"protocol", "requestId", "success", "state", "message", "restartRequired", "contributions", "environment", "arguments", "clientScriptDelivery"}
        restarts = value.get("restartRequired")
        if (set(value) - allowed or not isinstance(value.get("message"), str)
                or not isinstance(restarts, list) or len(restarts) > 3
                or any(type(v) is not str or v not in {"game_server", "market_server", "client", "launcher", "none"} for v in restarts)
                or len(set(restarts)) != len(restarts)
                or (value["success"] != (value["state"] == "ready"))
                or value.get("clientScriptDelivery") not in {None, "none", "login-handshake", "archive"}):
            raise DockerModHelperError("Docker mod helper returned an invalid lifecycle result.")
        if returncode != 0 and value["success"]:
            raise DockerModHelperError("Docker helper exit status contradicts its success result.")
        # Scrub only human-readable output after validating the envelope.
        value["message"] = self.runner._bound_without_input(value["message"], payload)[0][:512]
        return value
