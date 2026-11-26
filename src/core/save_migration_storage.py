"""Resolve and transfer verified save storage for local Native and Docker worlds."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import tomllib

from .host import IS_LINUX, external_process_environment
from .evejs_compatibility import SHARED_GAME_STORE_RELEASES
from .mod_evejs_compatibility import installed_evejs_version
from .platform import get_hidden_process_flags
from .runtime.docker_cli import DockerCommandRunner
from .runtime.docker_compose import parse_compose_config
from .runtime.docker_setup import DockerSetupDraft, build_compose_target

_GAME = "/var/lib/evejs/gameStore"
_MARKET = "/var/lib/evejs/market"
_SKINS = "/var/lib/evejs/skinr/designs"
_HELPER = Path(__file__).with_name("helpers") / "save-migration.js"
_SHARED_HELPER = _HELPER.with_name("shared_game_store.js")
_VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TIMEOUT = 1800
_GAME_DEFINITIONS = ("config/version.json", "server/src/gameStore/index.js", "server/src/gameStore/sqliteStore.js")
_MARKET_DEFINITIONS = ("config/version.json", "externalservices/market-server/src/state.rs",
                       "externalservices/market-server/crates/market-common/src/lib.rs")
_STOCK_SERVER_BINDS = {
    "/app/server/src/_secondary/image/generated/Alliance": False,
    "/app/server/certs": True,
    "/app/server/src/_secondary/express/certs": True,
}
_IMAGE_DEFINITIONS = """
const fs = require('node:fs'), crypto = require('node:crypto');
const files = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = {};
for (const file of files) {
  if (fs.statSync('/app/' + file).size > 4*1024*1024) throw Error('Oversized save format definition');
  const data = fs.readFileSync('/app/' + file);
  result[file] = crypto.createHash('sha256').update(data.toString('utf8').replace(/\\r\\n/g,'\\n')).digest('hex');
}
process.stdout.write(JSON.stringify(result));
"""
_SHARED_VALIDATION = """
try {
  const { root, game, market, fresh } = JSON.parse(fs.readFileSync(0, "utf8"));
  reviewTransfer(root, game, market, fresh);
  process.stdout.write("verified");
} catch (error) {
  process.stdout.write(error.sharedStoreCode ? error.message : "The reviewed shared save could not be verified.");
  process.exitCode = 1;
}
"""


class SaveMigrationError(RuntimeError):
    pass


def plain(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise SaveMigrationError("Save storage needs an absolute local path.")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise SaveMigrationError("Save storage cannot use symbolic links or junctions.")
    return path


def _family(key, file):
    return {key + suffix: file.with_name(file.name + suffix)
            for suffix in ("", "-wal", "-shm", "-journal")}


def _paths(store, market, skins):
    return {**_family("game", store / "gamestore.sqlite"),
            **_family("market", market), "images": store / "images", "skins": skins}


def _parse_helper(output, code):
    result = json.loads(output)
    if code == 0 and result.get("ok") is True and isinstance(result.get("files"), dict):
        return result["files"], None
    message = result.get("message", "The save storage helper could not finish.")
    return None, str(message)[:300]


def _mount_string(kind, source, target, *, read_only=False):
    columns = [f"type={kind}", f"source={source}", f"target={target}"]
    if kind == "volume":
        columns.append("volume-nocopy")
    if read_only:
        columns.append("readonly")
    with io.StringIO(newline="") as stream:
        csv.writer(stream, lineterminator="").writerow(columns)
        return stream.getvalue()


@dataclass(frozen=True)
class StorageMount:
    kind: str
    source: str


def _under(left, right):
    return left == right or left in right.parents or right in left.parents


@dataclass(frozen=True)
class SaveStorage:
    root: Path
    files: dict
    node: str
    ports: tuple[int, ...] = ()
    runner: DockerCommandRunner | None = None
    project: str = ""
    mounts: tuple[StorageMount, ...] = ()

    @property
    def bind_paths(self):
        if self.runner:
            return tuple(Path(m.source) for m in self.mounts if m.kind == "bind")
        return tuple(set(self.files.values()))

    @property
    def volume_names(self):
        return frozenset(m.source for m in self.mounts if m.kind == "volume")

    def shares_save_storage(self, other):
        return (bool(self.volume_names & other.volume_names)
                or any(_under(path, peer) for path in self.bind_paths for peer in other.bind_paths))

    def require_offline(self):
        if not self.runner:
            for port in self.ports:
                with socket.socket() as listener:
                    listener.settimeout(0.4)
                    if listener.connect_ex(("127.0.0.1", port)) == 0:
                        raise SaveMigrationError(
                            f"Stop Game and Market for both installations before transferring (port {port} is in use).")
            return
        runner = self.runner
        filters = [f"label=com.docker.compose.project={self.project}"]
        filters.extend(f"volume={name}" for name in sorted(self.volume_names))
        for query in filters:
            if runner.run(("ps", "-q", "--filter", query), cwd=self.root).stdout.strip():
                raise SaveMigrationError("Stop the Docker project's Game, Market, and maintenance containers.")
        binds = self.bind_paths
        if binds:
            running = runner.run(("ps", "-q"), cwd=self.root).stdout.split()
            if running:
                matches = runner.run_parsed(("inspect", *running), cwd=self.root, parser=lambda raw: any(
                    mount.get("Type") == "bind"
                    and any(_under(Path(mount["Source"]), bind) for bind in binds)
                    for container in json.loads(raw) for mount in container.get("Mounts", ())))
                if matches:
                    raise SaveMigrationError("Another running container is using the selected save storage.")

    def action(self, operation, bundle=None, *, expected=None, incoming=None, keep=()):
        request = {"operation": operation, "files": {key: str(file) for key, file in self.files.items()}}
        if bundle is not None:
            plain(bundle)
            request["bundle"] = "/bundle" if self.runner else str(bundle)
        if expected is not None:
            request.update(expected=expected, incoming=incoming, keep=keep)
        if not self.runner:
            # Windows process creation can reject a long working directory.
            # The helper and every path it receives are already absolute.
            completed = subprocess.run(
                [self.node, str(_HELPER)], input=json.dumps(request).encode(),
                capture_output=True, timeout=_TIMEOUT, env=external_process_environment(), **get_hidden_process_flags(),
            )
            try:
                result, message = _parse_helper(completed.stdout, completed.returncode)
            except (ValueError, TypeError) as error:
                raise SaveMigrationError("The Native save storage helper could not finish.") from error
        else:
            if IS_LINUX:
                request["hostOwner"] = [os.getuid(), os.getgid()]
            request["docker"] = True
            args = ["run", "--rm", "-i", "--pull", "never", "--network", "none",
                    "--read-only", "--security-opt", "no-new-privileges", "--user", "0:0",
                    "--cap-drop", "ALL", "--cap-add", "DAC_OVERRIDE", "--cap-add", "CHOWN",
                    "--cap-add", "FOWNER", "--entrypoint", "node"]
            for index, mount in enumerate(self.mounts):
                args += ["--mount", _mount_string(mount.kind, mount.source, f"/live{index}",
                                                   read_only=operation != "replace")]
            if bundle is not None:
                args += ["--mount", _mount_string("bind", str(bundle), "/bundle",
                                                   read_only=operation != "capture")]
            args += [self.node, "-e", _HELPER.read_text(encoding="utf-8")]
            result, message = self.runner.run_with_input_parsed(
                tuple(args), cwd=self.root, input_bytes=json.dumps(request).encode(),
                parser=_parse_helper, timeout=_TIMEOUT,
            )
        if message is not None:
            raise SaveMigrationError(message)
        if not isinstance(result, dict) or set(result) != set(self.files):
            raise SaveMigrationError("The save storage verification returned an incomplete result.")
        return result

    def verify_shared_bundle(self, bundle, *, game=True, market=False, fresh=False):
        """Verify detached database files without mounting the installation's live storage."""
        plain(bundle)
        path = PurePosixPath("/bundle") if self.runner else bundle
        payload = json.dumps({
            "root": "/app" if self.runner else str(self.root),
            "game": str(path / "game") if game else None,
            "market": str(path / "market") if market else None,
            "fresh": fresh,
        }).encode()
        program = _SHARED_HELPER.read_text(encoding="utf-8") + _SHARED_VALIDATION
        try:
            if not self.runner:
                env = {key: value for key, value in external_process_environment().items()
                       if key.upper() not in {"NODE_OPTIONS", "NODE_PATH"}}
                result = subprocess.run(
                    [self.node, "-e", program], input=payload, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=60, env=env, **get_hidden_process_flags(),
                )
                code, output = result.returncode, result.stdout.decode("utf-8")
            else:
                args = (
                    "run", "--rm", "-i", "--pull", "never", "--network", "none", "--read-only",
                    "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--entrypoint", "node",
                    "--env", "NODE_OPTIONS=", "--env", "NODE_PATH=",
                    "--mount", _mount_string("bind", str(bundle), "/bundle", read_only=True),
                    self.node, "-e", program,
                )
                code, output = self.runner.run_with_input_parsed(
                    args, cwd=self.root, input_bytes=payload,
                    parser=lambda raw, status: (status, raw), timeout=60,
                )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            raise SaveMigrationError("The reviewed shared save could not be verified.") from error
        if code or output != "verified":
            raise SaveMigrationError(output[:300] or "The reviewed shared save could not be verified.")


def bundle_inventory(bundle, files):
    """Inventory a launcher-owned snapshot without depending on a host Node."""
    import hashlib
    from stat import S_ISDIR, S_ISREG

    def tree(file, digest, relative=""):
        info = file.lstat()
        if file.is_symlink() or file.is_junction():
            raise SaveMigrationError("A save contains a symbolic link or junction.")
        if S_ISDIR(info.st_mode):
            record = [relative, "directory"]
        elif S_ISREG(info.st_mode) and info.st_nlink == 1:
            record = [relative, "file", info.st_size]
        else:
            raise SaveMigrationError("A save contains a special or hard-linked file.")
        digest.update(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode())
        if S_ISDIR(info.st_mode):
            for child in sorted(file.iterdir(), key=lambda item: item.name.encode("utf-16-be")):
                tree(child, digest, relative + "/" + child.name)
        else:
            with file.open("rb") as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(data)

    result = {}
    for name in files:
        file = bundle / name
        result[name] = None
        if file.exists() or file.is_symlink():
            digest = hashlib.sha256()
            tree(file, digest)
            result[name] = digest.hexdigest()
    return result


def _native(root, settings):
    store = root / "_local" / "gameStore"
    if not plain(store / "data").is_dir():
        raise SaveMigrationError("Set up the Native installation before transferring a save.")
    market_root = root / "externalservices" / "market-server"
    market_config = plain(market_root / "config" / "market-server.local.toml")
    market = market_root / "data" / "generated" / "market.sqlite"
    ports = {int(settings.get("game_port", 26000)), 26000, 40110, 40111}
    if market_config.is_file():
        with market_config.open("rb") as stream:
            settings_file = tomllib.load(stream)
        market = market_root / settings_file.get("storage", {}).get("database_path", str(market))
        ports.update((settings_file.get("network", {}).get("port", 40110),
                      settings_file.get("rpc", {}).get("port", 40111)))
    plain(market)
    if not market.is_relative_to(market_root):
        raise SaveMigrationError("The Native market database must be inside its own installation.")
    if any(type(port) is not int or not 1 <= port < 65536 for port in ports):
        raise SaveMigrationError("One of the Native service ports is invalid.")
    node = shutil.which("node")
    if IS_LINUX:
        from .linux_installation import LinuxInstallation
        if (root / ".evejs-linux.conf").is_file() or not node:
            node = str(LinuxInstallation.load(root).node)
    if not node:
        raise SaveMigrationError("Cannot find the Node executable for the Native installation.")
    paths = _paths(store, market, root / "_local" / "skinr" / "designs")
    return SaveStorage(root, {key: plain(path) for key, path in paths.items()}, node, tuple(sorted(ports)))


def _require_local(runner, root):
    endpoint = os.environ.get("DOCKER_HOST", "")
    if not endpoint or os.environ.get("DOCKER_CONTEXT"):
        endpoint = runner.run(("context", "inspect", "--format",
                               '{{(index .Endpoints "docker").Host}}'), cwd=root).stdout.strip()
    if not endpoint.startswith(("npipe://", "unix://")):
        raise SaveMigrationError("Save Migration needs a local Docker context.")
    system = runner.run(("version", "--format", "{{.Server.Os}}"), cwd=root).stdout.strip()
    if system != "linux":
        raise SaveMigrationError("Save Migration needs Docker's Linux container engine.")
    return endpoint


def _pin_runner(runner, endpoint):
    # The default Docker context is mutable outside this process. Every later
    # command must use the local endpoint that was checked at the start.
    def execute(argv, cwd, timeout):
        return runner._execute((argv[0], "--host", endpoint, *argv[1:]), cwd, timeout)
    def execute_input(argv, cwd, timeout, payload):
        return runner._execute_input((argv[0], "--host", endpoint, *argv[1:]), cwd, timeout, payload)
    return DockerCommandRunner(runner.executable, execute=execute, execute_input=execute_input)


def _verify_image_definitions(runner, root, image, files):
    expected = {}
    for relative in files:
        definition = plain(root / relative)
        if not definition.is_file() or definition.stat().st_size > 4 * 1024 * 1024:
            raise SaveMigrationError("The installation is missing its save-format definitions.")
        data = definition.read_bytes().replace(b"\r\n", b"\n")
        expected[relative] = hashlib.sha256(data).hexdigest()
    def projection(raw, code):
        result = json.loads(raw)
        return code == 0 and result == expected
    try:
        matches = runner.run_with_input_parsed((
            "run", "--rm", "-i", "--pull", "never", "--network", "none", "--read-only",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--entrypoint", "node",
            image, "-e", _IMAGE_DEFINITIONS,
        ), cwd=root, input_bytes=json.dumps(files).encode(), parser=projection, timeout=60)
    except (ValueError, RuntimeError, OSError) as error:
        raise SaveMigrationError("The Docker image save format could not be verified against this installation.") from error
    if matches is not True:
        raise SaveMigrationError("The Docker image save format differs from this installation. Set up matching images first.")


def _compose_layout(document, *, shared=False, root=None):
    services = document["services"]
    server = services["server"]
    market = services["market"]
    project = document["name"]
    if not _VOLUME.fullmatch(project):
        raise SaveMigrationError("Could not verify the Compose project name.")
    for service in (server, market):
        environment = service.get("environment") or {}
        if environment.get("EVEJS_DATA_ROOT") not in (None, "", "/var/lib/evejs"):
            raise SaveMigrationError("This Compose project overrides the EveJS save layout.")
    data = (server.get("environment") or {}).get("EVEJS_GAMESTORE_DATA_DIR")
    if data not in (None, "", _GAME + "/data"):
        raise SaveMigrationError("This Compose project overrides the GameStore location.")
    if shared:
        for role in (server, market):
            env = role.get("environment") or {}
            if env.get("EVEJS_GAMESTORE_SQLITE_PATH") not in (None, "", _GAME + "/gamestore.sqlite"):
                raise SaveMigrationError("This Compose project overrides the shared GameStore database.")
            protected = (PurePosixPath("/app/server"), PurePosixPath("/app/externalservices/market-server/src"))
            for mount in role.get("volumes", []):
                target = mount["target"]
                destination = PurePosixPath(target)
                if destination.anchor != "/" or ".." in destination.parts or str(destination) != target:
                    raise SaveMigrationError("This Compose project has a non-canonical mount target.")
                if not any(_under(destination, member) for member in protected):
                    continue
                if (
                    role is server and root is not None and target in _STOCK_SERVER_BINDS
                    and mount["type"] == "bind"
                    and mount.get("read_only", False) is _STOCK_SERVER_BINDS[target]
                    and (mount.get("bind") or {}).get("propagation") in (None, "rprivate")
                    and plain(Path(mount["source"])) == root.joinpath(*destination.parts[2:])
                ):
                    continue
                raise SaveMigrationError("This Compose project overrides the reviewed save-format code.")
    used = []
    folders = []
    for service, location in ((server, _GAME), (market, _MARKET), (server, _SKINS)):
        folder = PurePosixPath(location)
        mounts = service.get("volumes", [])
        matches = [(PurePosixPath(item["target"]), item) for item in mounts
                   if folder.is_relative_to(PurePosixPath(item["target"]))]
        if not matches and location == _SKINS:
            folders.append(None)  # A GameStore-only bind has no persistent designs directory.
            continue
        if not matches or any(folder in PurePosixPath(item["target"]).parents for item in mounts):
            raise SaveMigrationError("The Compose save storage needs a writable directory mount without nested mounts.")
        parent, mount = max(matches, key=lambda record: len(record[0].parts))
        if location == _SKINS and parent == folder:
            raise SaveMigrationError("The ship design directory needs a writable parent mount.")
        if mount.get("read_only") or mount.get("volume", {}).get("subpath"):
            raise SaveMigrationError("The Compose save mount must be writable without a volume subpath.")
        kind = mount["type"]
        source = mount["source"]
        if kind == "volume":
            source = document["volumes"][source]["name"]
            if not _VOLUME.fullmatch(source):
                raise SaveMigrationError("Invalid Compose volume name.")
        elif kind == "bind":
            source = str(plain(Path(source)))
        else:
            raise SaveMigrationError("Save Migration needs a Compose bind mount or named volume.")
        descriptor = StorageMount(kind, source)
        if descriptor not in used:
            used.append(descriptor)
        folders.append(PurePosixPath(f"/live{used.index(descriptor)}") / folder.relative_to(parent))
    game, orders, skins = folders
    paths = _paths(game, orders / "market.sqlite", skins)
    if skins is None:
        del paths["skins"]
    return project, (server["image"], market["image"]), tuple(used), paths


def _docker(root, settings, *, target=None, expected_digest=None):
    runner = DockerCommandRunner()
    runner = _pin_runner(runner, _require_local(runner, root))
    target = target or build_compose_target(DockerSetupDraft(
        str(root), settings.get("docker_compose_file", ""), settings.get("docker_project_name", ""),
        settings.get("docker_control_policy", "connect_only"),
        settings.get("docker_keep_running_on_exit", True), settings.get("client_path", ""),
    ))
    shared = installed_evejs_version(root) in SHARED_GAME_STORE_RELEASES
    def projection(raw):
        try:
            document = json.loads(raw)
            if expected_digest is not None and parse_compose_config(document).effective_config_digest != expected_digest:
                raise SaveMigrationError("The selected Compose configuration changed during character maintenance.")
            return _compose_layout(document, shared=shared, root=root), None
        except (KeyError, TypeError, ValueError, OSError, SaveMigrationError) as error:
            return None, str(error) if isinstance(error, SaveMigrationError) else "Could not resolve the Compose save storage."
    layout, message = runner.run_parsed(target.compose_args(runner.executable, "config", "--format", "json"),
                                       cwd=root, parser=projection)
    if message:
        raise SaveMigrationError(message)
    project, images, mounts, files = layout
    image_ids = []
    for image in images:
        image_id = runner.run(("image", "inspect", "--format", "{{.Id}}", image), cwd=root).stdout.strip()
        if not _ID.fullmatch(image_id):
            raise SaveMigrationError("Set up the Docker installation before transferring a save.")
        image_ids.append(image_id)
    _verify_image_definitions(runner, root, image_ids[0], _GAME_DEFINITIONS)
    _verify_image_definitions(runner, root, image_ids[1], _MARKET_DEFINITIONS)
    for mount in mounts:
        if mount.kind == "volume":
            metadata = runner.run_parsed(("volume", "inspect", mount.source), cwd=root, parser=lambda raw: (
                (lambda volume: (volume.get("Driver"), (volume.get("Labels") or {}).get("com.docker.compose.project"),
                                 bool(volume.get("Options"))))(json.loads(raw)[0])
            ))
            if metadata != ("local", project, False):
                raise SaveMigrationError("Save Migration needs a local volume owned by the selected Compose project.")
    return SaveStorage(root, files, image_ids[0], runner=runner, project=project, mounts=mounts)


def resolve_storage(root, settings):
    if settings.get("runtime_backend") == "docker_compose":
        return _docker(root, settings)
    if settings.get("runtime_backend", "native") == "native":
        return _native(root, settings)
    raise SaveMigrationError("The installation runtime configuration is invalid.")
