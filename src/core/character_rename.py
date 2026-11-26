"""Official character-name changes on reviewed, offline EveJS installations."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
from uuid import uuid4

from .evejs_compatibility import EveJSSetupRequired, SHARED_GAME_STORE_RELEASES, require_completed_evejs_setup
from .mod_lifecycle_lock import acquire_mod_lifecycle_lease
from .native_runtime import node_environment, node_executable
from .platform import get_hidden_process_flags
from .runtime.data import docker_project_identity
from .runtime.docker_cli import DockerCommandRunner
from .runtime.docker_compose import ComposeTarget, parse_compose_config
from .save_migration_storage import (
    SaveStorage, StorageMount, _mount_string, _native, _pin_runner, _require_local, plain,
)


_REVIEWED_VERSIONS = frozenset({"261115.2", *SHARED_GAME_STORE_RELEASES})
_HELPERS = Path(__file__).resolve().with_name("helpers")
_BACKUP = ".launcher-character-renames"
_STORE = PurePosixPath("/var/lib/evejs/gameStore")
_DATA = _STORE.parent
_RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="
_TIMEOUT = 90
_COMPOSE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REVIEWED_IMAGE_CONTENT = (
    PurePosixPath("/app/server/src/gameStore"),
    PurePosixPath("/app/server/src/services/character/characterNameRuntime.js"),
    PurePosixPath("/app/server/package.json"),
    PurePosixPath("/app/server/node_modules"),
)
_ERROR_MESSAGES = {
    "INVALID_REQUEST": "The character rename request is invalid.",
    "UNSUPPORTED_RELEASE": "Character rename requires a reviewed EveJS runtime.",
    "UNSUPPORTED_LAYOUT": "The selected game store could not be verified for character rename.",
    "OWNER_BUSY": "EveJS still owns the game store. Stop Game and wait for its persistence lease to release.",
    "OWNER_CHANGED": "EveJS persistence ownership changed during character rename.",
    "PENDING_WRITES": "The game store has unfinished writes. Start Game, then stop it cleanly and try again.",
    "TARGET_CHANGED": "The selected account or character changed. Refresh and try again.",
    "INVALID_NAME": "The proposed name does not meet EveJS character-name rules.",
    "RESERVED_NAME": "EveJS reserves that character name.",
    "NAME_UNAVAILABLE": "That character name is already in use.",
}


class CharacterRenameError(RuntimeError):
    def __init__(self, message: str, *, needs_review: bool = False,
                 backup_path: Path | str | None = None) -> None:
        super().__init__(message)
        self.needs_review = needs_review
        self.backup_path = backup_path


@dataclass(frozen=True)
class CharacterRenameRequest:
    evejs_root: str | Path
    settings: Mapping
    username: str
    account_id: int
    character_id: int
    old_name: str
    new_name: str
    compose_target: ComposeTarget | None = None
    target_identity: str | None = None


@dataclass(frozen=True)
class CharacterRenameResult:
    character_id: int
    name: str
    changed: bool
    backup_path: Path | str | None


@dataclass(frozen=True)
class _DockerPlan:
    project: str
    image_id: str
    destination: PurePosixPath
    mount: StorageMount
    authority: tuple


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256 and (
        not any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _validate_request(request: CharacterRenameRequest) -> Path:
    if (
        not _valid_text(request.username) or not _valid_text(request.old_name) or not _valid_text(request.new_name)
        or type(request.account_id) is not int or not 0 < request.account_id < 2**53
        or type(request.character_id) is not int or not 0 < request.character_id < 2**53
    ):
        raise CharacterRenameError(_ERROR_MESSAGES["INVALID_REQUEST"])
    root = plain(Path(request.evejs_root))
    version = plain(root / "config" / "version.json")
    if not version.is_file() or version.stat().st_size > 4096:
        raise CharacterRenameError(_ERROR_MESSAGES["UNSUPPORTED_RELEASE"])
    metadata = json.loads(version.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict) or metadata.get("evejsVersion") not in _REVIEWED_VERSIONS or (
        type(metadata.get("configSchemaVersion")) is not int or metadata["configSchemaVersion"] != 1
    ):
        raise CharacterRenameError(_ERROR_MESSAGES["UNSUPPORTED_RELEASE"])
    try:
        require_completed_evejs_setup(root)
    except EveJSSetupRequired as error:
        raise CharacterRenameError(str(error)) from error
    return root


def _payload(request: CharacterRenameRequest, operation: str) -> bytes:
    return json.dumps({
        "username": request.username, "accountId": request.account_id,
        "characterId": request.character_id, "oldName": request.old_name,
        "name": request.new_name, "operationId": operation,
    }, ensure_ascii=False).encode("utf-8")


def _parse_result(raw: str, code: int, operation: str) -> dict:
    if len(raw.encode("utf-8")) > 64 * 1024:
        raise ValueError("Oversized rename result.")
    (marker,) = [line.removeprefix(_RESULT_PREFIX) for line in raw.splitlines() if line.startswith(_RESULT_PREFIX)]
    result = json.loads(marker)
    if not isinstance(result, dict) or result.get("operationId") != operation:
        raise ValueError("Uncorrelated rename result.")
    if type(result.get("ok")) is not bool or (code == 0) != result["ok"]:
        raise ValueError("Invalid rename result.")
    return result


def _finish(result: dict, request: CharacterRenameRequest, backup: Path | str) -> CharacterRenameResult:
    saved = backup if result.get("backupCreated") is True else None
    if not result["ok"]:
        if result.get("needsReview") is not False:
            raise CharacterRenameError(
                "The rename outcome could not be verified. Keep Game stopped until the game store has been reviewed.",
                needs_review=True, backup_path=saved,
            )
        code = result.get("code")
        raise CharacterRenameError(
            _ERROR_MESSAGES.get(code, "The name could not be changed safely.") if isinstance(code, str)
            else "The name could not be changed safely.",
            backup_path=saved,
        )
    if (
        type(result.get("characterId")) is not int or result["characterId"] != request.character_id
        or not _valid_text(result.get("name")) or type(result.get("changed")) is not bool
        or result["changed"] != (saved is not None)
    ):
        raise CharacterRenameError("The rename result was incomplete. Keep Game stopped and verify the game store.",
                                   needs_review=True, backup_path=backup)
    return CharacterRenameResult(request.character_id, result["name"], result["changed"], saved)


def _require_native_offline(storage: SaveStorage, root: Path) -> None:
    ports = set(storage.ports)
    for filename in ("server.json", "server.local.json"):
        config_file = plain(root / "config" / filename)
        if config_file.is_file():
            if config_file.stat().st_size > 1024 * 1024:
                raise CharacterRenameError("The Native Game endpoint could not be verified.")
            config = json.loads(config_file.read_text(encoding="utf-8"))
            network = config.get("network", {})
            bound_host = network.get("gameServerBindHost")
            if bound_host not in (None, "127.0.0.1", "0.0.0.0", "localhost"):
                raise CharacterRenameError("The Native Game endpoint could not be verified.")
            port = network.get("serverPort")
            if port is not None:
                if type(port) is not int or not 0 < port < 65536:
                    raise CharacterRenameError("The Native Game endpoint could not be verified.")
                ports.add(port)
    for port in ports:
        if os.name == "nt":
            # A loopback connect can time out even when Windows has no listener.
            # An exclusive bind still refuses an occupied or unverifiable port.
            try:
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind(("127.0.0.1", port))
            except OSError as error:
                if error.winerror in (10013, 10048):
                    raise CharacterRenameError("Stop Game and Market before changing characters.") from error
                raise CharacterRenameError("Native service availability could not be verified.") from error
            continue
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                raise CharacterRenameError("Stop Game and Market before changing characters.")
        except ConnectionRefusedError:
            continue
        except OSError as error:
            raise CharacterRenameError("Native service availability could not be verified.") from error


def _native_call(request: CharacterRenameRequest, root: Path, operation: str) -> CharacterRenameResult:
    storage = _native(root, request.settings)
    store = storage.files["game"].parent
    if not storage.files["game"].is_file():
        raise CharacterRenameError(_ERROR_MESSAGES["UNSUPPORTED_LAYOUT"])
    _require_native_offline(storage, root)
    node = node_executable(root)
    environment = {
        key: value for key, value in node_environment(root).items()
        if not key.upper().startswith("EVEJS_") and key.upper() not in {"NODE_OPTIONS", "NODE_PATH"}
    }
    environment.update(
        EVEJS_GAMESTORE_DATA_DIR=str(store / "data"),
        EVEJS_GAMESTORE_OWNER_ROLE="maintenance",
        EVEJS_PERSISTENCE_OWNER_LEASE_MS="120000",
        EVEJS_LOG_LEVEL="0",
    )
    backup = store / _BACKUP / f"{operation}.json"
    try:
        completed = subprocess.run(
            (node, str(_HELPERS / "rename_character.js")),
            cwd=root, env=environment, input=_payload(request, operation),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_TIMEOUT, **get_hidden_process_flags(),
        )
        result = _parse_result(completed.stdout.decode("utf-8"), completed.returncode, operation)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise CharacterRenameError(
            "The rename worker did not return a verified result. Keep Game stopped and verify the game store.",
            needs_review=True, backup_path=backup if backup.is_file() else None,
        ) from error
    return _finish(result, request, backup)


def _compose_projection(raw: str, target: ComposeTarget, expected: str, root: Path) -> tuple:
    document = json.loads(raw)
    config = parse_compose_config(document)
    project = config.project_name
    if (
        not isinstance(project, str) or not _COMPOSE_NAME.fullmatch(project)
        or not config.capabilities.init
        or docker_project_identity(target, project, config=config) != expected
        or config.services["server"].pull_policy != "never"
    ):
        raise ValueError("The selected Compose identity changed.")
    server = document["services"]["server"]
    env = server.get("environment") or {}
    for key, value in (
        ("EVEJS_DATA_ROOT", str(_DATA)), ("EVEJS_GAMESTORE_DATA_DIR", str(_STORE / "data")),
        ("EVEJS_GAMESTORE_SQLITE_PATH", str(_STORE / "gamestore.sqlite")),
    ):
        if env.get(key) not in (None, "", value):
            raise ValueError("The selected Compose project redirects GameStore.")
    matches = []
    for item in server.get("volumes", []):
        destination = PurePosixPath(item["target"])
        if _STORE in destination.parents or any(
            source.is_relative_to(destination) or destination.is_relative_to(source)
            for source in _REVIEWED_IMAGE_CONTENT
        ):
            raise ValueError("The selected Compose project overrides reviewed files or GameStore data.")
        if _STORE.is_relative_to(destination):
            matches.append((destination, item))
        if PurePosixPath("/app/config/version.json").is_relative_to(destination):
            if (destination != PurePosixPath("/app/config") or item["type"] != "bind"
                    or plain(Path(item["source"])) != root / "config"):
                raise ValueError("The selected Compose project uses a different EveJS version source.")
    if not matches:
        raise ValueError("The selected Compose project needs a persistent GameStore mount.")
    # The reviewed bind layout may override the GameStore subtree of the
    # stock data volume. Carry only the most specific authoritative mount.
    destination, item = max(matches, key=lambda entry: len(entry[0].parts))
    if destination not in {_STORE, _DATA} or item.get("read_only") or (item.get("volume") or {}).get("subpath"):
        raise ValueError("The selected Compose GameStore mount is unsupported.")
    source = item["source"]
    kind = item["type"]
    if kind == "volume":
        source = document["volumes"][source]["name"]
        if not isinstance(source, str) or not _COMPOSE_NAME.fullmatch(source):
            raise ValueError("The selected Compose volume has an invalid name.")
    elif kind == "bind":
        source = str(plain(Path(source)))
    else:
        raise ValueError("The selected Compose GameStore mount is unsupported.")
    return project, server["image"], destination, StorageMount(kind, source)


def _volume_identity(raw: str, project: str, name: str) -> tuple:
    (volume,) = json.loads(raw)
    if (
        volume["Name"] != name or volume["Driver"] != "local" or volume.get("Options")
        or (volume.get("Labels") or {}).get("com.docker.compose.project") != project
        or not volume.get("Mountpoint") or not volume.get("CreatedAt")
    ):
        raise ValueError("The named volume is not a local volume owned by the selected project.")
    return volume["Mountpoint"], volume["CreatedAt"]


def _docker_plan(runner: DockerCommandRunner, target: ComposeTarget, expected: str, root: Path) -> _DockerPlan:
    project, image, destination, mount = runner.run_parsed(
        target.compose_args(runner.executable, "config", "--format", "json"), cwd=root,
        parser=lambda raw: _compose_projection(raw, target, expected, root),
    )
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise CharacterRenameError("The selected Docker Game image could not be verified.")
    image_id = runner.run(("image", "inspect", "--format", "{{.Id}}", image), cwd=root).stdout.strip()
    if not _IMAGE_ID.fullmatch(image_id):
        raise CharacterRenameError("The selected Docker Game image could not be verified.")
    if mount.kind == "volume":
        authority = runner.run_parsed(("volume", "inspect", mount.source), cwd=root,
                                      parser=lambda raw: _volume_identity(raw, project, mount.source))
    else:
        store = Path(mount.source).joinpath(*_STORE.relative_to(destination).parts)
        database = plain(store / "gamestore.sqlite")
        stat = database.stat()
        if not database.is_file() or stat.st_nlink != 1:
            raise CharacterRenameError(_ERROR_MESSAGES["UNSUPPORTED_LAYOUT"])
        authority = (stat.st_dev, stat.st_ino)
    return _DockerPlan(project, image_id, destination, mount, authority)


def _docker_host_bind(source: str) -> Path:
    if os.name == "nt" and source.startswith("/"):
        # Docker Desktop reports Windows bind sources from inside its VM.
        match = re.fullmatch(r"/(?:run/desktop/mnt/host|host_mnt|mnt)/([A-Za-z])(?:/(.*))?", source)
        if match:
            return Path(match[1] + ":/" + (match[2] or ""))
    return Path(source)


def _require_docker_offline(runner: DockerCommandRunner, root: Path, plan: _DockerPlan) -> None:
    filters = [f"label=com.docker.compose.project={plan.project}"]
    if plan.mount.kind == "volume":
        filters.append(f"volume={plan.mount.source}")
    for query in filters:
        if runner.run(("ps", "-q", "--filter", query), cwd=root).stdout.strip():
            raise CharacterRenameError("Stop Docker Game, Market, and maintenance containers before renaming.")
    if plan.mount.kind != "bind":
        return
    running = runner.run(("ps", "-q"), cwd=root).stdout.split()
    if not running:
        return
    selected = Path(plan.mount.source)

    def shares_bind(raw: str) -> bool:
        for container in json.loads(raw):
            for mount in container["Mounts"]:
                if mount["Type"] == "bind":
                    source = _docker_host_bind(mount["Source"])
                    if source.is_relative_to(selected) or selected.is_relative_to(source):
                        return True
        return False

    if runner.run_parsed(("inspect", *running), cwd=root, parser=shares_bind):
        raise CharacterRenameError("Another running Docker container is using the selected game store.")


def _docker_call(request: CharacterRenameRequest, root: Path, operation: str) -> CharacterRenameResult:
    target = request.compose_target
    expected = request.target_identity
    if (
        request.settings.get("docker_control_policy") != "managed" or not isinstance(target, ComposeTarget)
        or target.project_directory != root or not isinstance(expected, str) or not expected.startswith("docker:")
    ):
        raise CharacterRenameError("Character rename requires a verified Managed Docker project.")
    for file in (target.compose_file, *target.override_files):
        plain(file)
    runner = DockerCommandRunner()
    runner = _pin_runner(runner, _require_local(runner, root))
    plan = _docker_plan(runner, target, expected, root)
    _require_docker_offline(runner, root, plan)
    if _docker_plan(runner, target, expected, root) != plan:
        raise CharacterRenameError("Docker storage or image changed before character rename.")
    _require_docker_offline(runner, root, plan)
    relative = _STORE.relative_to(plan.destination) / _BACKUP / (operation + ".json")
    if plan.mount.kind == "bind":
        backup: Path | str = Path(plan.mount.source).joinpath(*relative.parts)
    else:
        backup = f"{plan.mount.source}:{relative}"
    args = (
        "run", "--rm", "-i", "--pull", "never", "--network", "none", "--read-only",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--user", "node",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=16m",
        "--mount", _mount_string(plan.mount.kind, plan.mount.source, str(plan.destination)),
        "--mount", _mount_string("bind", str(_HELPERS), "/run/evejs-launcher/helpers", read_only=True),
        "--env", "NODE_OPTIONS=", "--env", "NODE_PATH=",
        "--env", "EVEJS_GAMESTORE_OWNER_ROLE=maintenance",
        "--env", "EVEJS_PERSISTENCE_OWNER_LEASE_MS=120000",
        "--env", "EVEJS_DATA_ROOT=/tmp/evejs-rename",
        "--env", f"EVEJS_GAMESTORE_DATA_DIR={_STORE / 'data'}",
        "--env", "EVEJS_LOG_LEVEL=0",
        "--workdir", "/app", "--entrypoint", "node", plan.image_id,
        "/run/evejs-launcher/helpers/rename_character.js",
    )
    try:
        result = runner.run_with_input_parsed(
            args, cwd=root, input_bytes=_payload(request, operation), timeout=_TIMEOUT,
            parser=lambda raw, code: _parse_result(raw, code, operation),
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise CharacterRenameError(
            "The Docker rename worker did not return a verified result. Keep Game stopped and verify the game store.",
            needs_review=True, backup_path=backup,
        ) from error
    return _finish(result, request, backup)


def rename_character(request: CharacterRenameRequest) -> CharacterRenameResult:
    """Revalidate the selected identity and execute one offline rename."""
    try:
        root = _validate_request(request)
        operation = uuid4().hex
        with acquire_mod_lifecycle_lease(root):
            backend = request.settings.get("runtime_backend", "native")
            if backend == "native":
                return _native_call(request, root, operation)
            if backend == "docker_compose":
                return _docker_call(request, root, operation)
            raise CharacterRenameError("The selected runtime does not support character rename.")
    except CharacterRenameError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError, KeyError) as error:
        raise CharacterRenameError("The selected installation could not be verified for character rename.") from error
