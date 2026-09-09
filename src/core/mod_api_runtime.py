"""Versioned, package-neutral helpers for the public launcher mod interface.

Discovery reads declarations only. Explicit lifecycle actions run a contained
helper, validate its correlated reply, and expose declarative key edits to the
same ownership engine used by settings forms. A helper's binary receipt is its
own responsibility; accepting a reply never claims its proposed edits were
already committed by the host. Executable mods are not an OS security sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import base64
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import time
from types import MappingProxyType
from typing import Callable, Iterable, Mapping
import uuid

from ..constants import APP_VERSION
from . import platform as platform_api
from .mod_api_manifest import ModApiDescriptor, NOTIFICATION_ACTIONS, read_api_manifest
from .mod_config_documents import canonical_key
from .mod_contributions import (
    ConfigurationReview, ContributionConflict, ContributionOwner, ContributionPlan,
    ContributionStore, FileChange, FileTarget, KeyEdit, RemovalReviewRequired, read_target,
)
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_manifest import Mod, scan_mods
from .mod_settings import ModSettingsContext, ModSettingsSession, profile_identity
from .mod_settings_schema import SettingsFile, parse_settings_schema


PROTOCOL = "evejs_launcher_mod_v1"
MAX_REPLY_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_HELPER_BYTES = 32 * 1024 * 1024
DEFAULT_HELPER_TIMEOUT = 120.0
_ACTION_TIMEOUTS = {"install": 3600.0, "recover": 3600.0, "prepare_disable": 600.0, "prepare_remove": 600.0}
_ACTIONS = {"prepare_profile", "install", "verify", "prepare_disable", "prepare_remove", "recover"} | NOTIFICATION_ACTIONS
_RESTARTS = {"none", "client", "game_server", "launcher"}
_BASES = {"mod", "evejs", "profile", "profile_settings", "client"}
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_ARGUMENT = re.compile(r"(?:/|--?)([A-Za-z][A-Za-z0-9_-]{0,63})(?:[:=](.*))?\Z")
_RESERVED_ENVIRONMENT = {
    "COMPUTERNAME", "HOSTNAME", "USERNAME", "USERDOMAIN", "USERDOMAIN_ROAMINGPROFILE",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PATH", "PATHEXT",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "PSMODULEPATH",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_OPTIONS",
    "PYTHONPATH", "PYTHONHOME", "__COMPAT_LAYER", "EVE_CLIENT_SENTRY_DSN",
    "LD_OFFLINE", "LAUNCHDARKLY_OFFLINE", "LAUNCHDARKLY_SEND_EVENTS", "LD_SEND_EVENTS",
}
_RESERVED_ARGUMENTS = {
    "port", "server", "serverhost", "serverport", "proxy", "proxyurl", "login",
    "username", "password", "account", "character", "characterid", "autoselectcharacter",
    "settings", "settingspath", "resfiles", "remotefilecachefolder", "noconsole",
}
_RESERVED_PATHS = {"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ModApiRuntimeError(RuntimeError):
    """The selected helper cannot safely complete the requested operation."""


@dataclass(frozen=True)
class ModHelperReceipt:
    target: FileTarget
    state: str
    schema_version: int
    digest: str


@dataclass(frozen=True)
class ModHelperResult:
    descriptor: ModApiDescriptor
    context: ModSettingsContext
    action: str
    request_id: str
    success: bool
    state: str
    message: str
    restart_required: tuple[str, ...]
    contributions: tuple[KeyEdit, ...]
    contribution_files: tuple[SettingsFile, ...]
    environment: Mapping[str, str]
    arguments: tuple[str, ...]
    receipt: ModHelperReceipt | None
    request_path: Path

    def require_ready(self) -> "ModHelperResult":
        if not self.success or self.state != "ready":
            raise ModApiRuntimeError(f"{self.descriptor.display_name}: {self.message or self.state}")
        return self


@dataclass(frozen=True)
class PreparedClientMods:
    environment: Mapping[str, str]
    arguments: tuple[str, ...]
    results: tuple[ModHelperResult, ...]
    notifications: tuple[tuple[ModApiDescriptor, ModSettingsContext], ...] = ()


def _plain(value: object, label: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value) or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ModApiRuntimeError(f"{label} must be bounded plain text.")
    return value


def _relative(value: object) -> str:
    path = _plain(value, "Contribution/receipt path", 240)
    if "\\" in path or PurePosixPath(path).is_absolute() or any(
        part in {"", ".", ".."} or part.endswith((".", " ")) or any(char in ':*?<>|"' for char in part)
        or part.split(".", 1)[0].casefold() in _RESERVED_PATHS for part in path.split("/")
    ):
        raise ModApiRuntimeError("Contribution/receipt paths must stay inside their declared base.")
    return path


def _json(content: bytes) -> dict:
    if len(content) > MAX_REPLY_BYTES:
        raise ModApiRuntimeError("The helper JSON document exceeds the size limit.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ModApiRuntimeError("Duplicate helper JSON key.")
            result[key] = value
        return result
    try:
        result = json.loads(content, object_pairs_hook=unique, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ModApiRuntimeError("The helper JSON document is invalid.") from exc


def _fingerprint(path: Path) -> str:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or getattr(before, "st_file_attributes", 0) & 0x400 or before.st_size > MAX_HELPER_BYTES:
        raise ModApiRuntimeError("The helper is not a bounded regular package file.")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            if size > MAX_HELPER_BYTES:
                raise ModApiRuntimeError("The helper grew beyond its captured size limit.")
            digest.update(chunk)
    after = path.lstat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity(before) != identity(opened) or identity(before) != identity(after):
        raise ModApiRuntimeError("The helper changed while its request was being captured.")
    return digest.hexdigest()


def helper_coordination_root(descriptor: ModApiDescriptor, context: ModSettingsContext, action: str) -> Path:
    if action == "prepare_profile" or action in NOTIFICATION_ACTIONS or descriptor.kind == "client-package":
        if context.client_root is None:
            raise ModApiRuntimeError("This helper requires the selected physical EVE client.")
        return context.client_root
    return context.evejs_root


def _captured_context(descriptor: ModApiDescriptor, context: ModSettingsContext, action: str, backend: str) -> ModSettingsContext:
    if action not in _ACTIONS:
        raise ModApiRuntimeError("Unsupported public mod helper action.")
    if descriptor.root != context.evejs_root or descriptor.folder != context.mod_folder:
        raise ModApiRuntimeError("The mod does not match the captured installation context.")
    if read_api_manifest(context.evejs_root, context.mod_folder) != descriptor:
        raise ModApiRuntimeError("The mod declaration changed. Refresh Mods before retrying.")
    api = descriptor.launcher_api
    if api is None or api.helper is None or action not in api.capabilities:
        raise ModApiRuntimeError("The mod does not declare this helper capability.")
    if backend not in descriptor.supported_backends:
        raise ModApiRuntimeError("The mod does not support the selected backend.")
    if tuple(map(int, api.min_launcher_version.split("."))) > tuple(map(int, APP_VERSION.split("."))):
        raise ModApiRuntimeError(f"This mod requires launcher {api.min_launcher_version} or later.")
    if action == "prepare_profile" or action in NOTIFICATION_ACTIONS:
        if not context.profile_id or context.profile_root is None or context.profile_settings_root is None:
            raise ModApiRuntimeError("Profile preparation requires a complete captured profile context.")
        return context
    # Installation and shared binary restoration are global operations, even
    # if the caller happens to have a character selected in the user interface.
    return replace(context, profile_id="", profile_root=None, profile_settings_root=None,
                   profile_storage_root=None, profile_settings_storage_root=None)


def _settings_values(descriptor: ModApiDescriptor, context: ModSettingsContext) -> dict:
    values = {"global": {}, "profile": {}}
    if descriptor.settings is None:
        return values
    schema = parse_settings_schema(descriptor.settings)
    for scope in values:
        if any(field.scope == scope for field in schema.fields) and (scope == "global" or context.profile_id):
            session = ModSettingsSession.open(context, descriptor.settings, scope=scope, manifest_path=descriptor.manifest_path)
            values[scope] = dict(session.values)
    return values


def _command(descriptor: ModApiDescriptor, request: Path, result: Path) -> tuple[list[str], dict[str, str]]:
    helper = descriptor.launcher_api.helper
    environment = dict(os.environ)
    if helper.runtime == "powershell":
        windows = environment.get("SystemRoot") or environment.get("WINDIR")
        if not windows:
            raise ModApiRuntimeError("Windows PowerShell is unavailable.")
        executable = Path(windows) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if not executable.is_file() or not (executable.parent / "Modules").is_dir():
            raise ModApiRuntimeError("Windows PowerShell is unavailable.")
        environment["PSModulePath"] = str(executable.parent / "Modules")
        command = [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper.path), "-RequestPath", str(request), "-ResultPath", str(result)]
    else:
        if helper.runtime == "node":
            node = shutil.which("node")
            if not node:
                raise ModApiRuntimeError("The mod helper requires Node.js on PATH.")
            command = [str(Path(node).resolve(strict=True)), str(helper.path)]
            environment.pop("NODE_OPTIONS", None)
        else:
            command = [str(helper.path)]
        command += ["--request", str(request), "--result", str(result)]
    return command, environment


def run_helper_process(command: list[str], *, cwd: Path, environment: dict[str, str], timeout: float, output_directory: Path) -> subprocess.CompletedProcess[str]:
    """Contain the whole helper tree before it runs; bound time and output."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ModApiRuntimeError("A helper timeout must be between zero and one hour.")
    with (output_directory / "stdout.log").open("xb") as stdout, (output_directory / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen(command, shell=False, cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, env=environment, **platform_api.get_suspended_hidden_process_flags())
        job = None
        try:
            handle = getattr(process, "_handle", None)
            if not isinstance(handle, int) or handle <= 0:
                raise ModApiRuntimeError("The helper process handle is unavailable.")
            job = platform_api.create_kill_on_close_job(handle)
            if job is None or not platform_api.resume_process(handle):
                raise ModApiRuntimeError("The helper process tree could not be contained and started.")
            deadline = time.monotonic() + timeout
            while True:
                if sum(os.fstat(stream.fileno()).st_size for stream in (stdout, stderr)) > MAX_OUTPUT_BYTES:
                    raise ModApiRuntimeError("The mod helper exceeded its diagnostic output limit.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModApiRuntimeError("The mod helper timed out; its process tree was stopped.")
                try:
                    returncode = process.wait(timeout=min(remaining, 0.2))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if job is not None:
                try:
                    if process.poll() is None:
                        platform_api.terminate_job(job)
                finally:
                    platform_api.close_job(job)
            elif process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise ModApiRuntimeError("Helper cleanup could not confirm process exit.") from exc
    outputs = []
    total = 0
    for name in ("stdout.log", "stderr.log"):
        with (output_directory / name).open("rb") as stream:
            value = stream.read(MAX_OUTPUT_BYTES + 1)
        total += len(value)
        if total > MAX_OUTPUT_BYTES:
            raise ModApiRuntimeError("The mod helper exceeded its diagnostic output limit.")
        outputs.append(value.decode("utf-8", errors="replace"))
    return subprocess.CompletedProcess(command, returncode, *outputs)


def _request_folder(root: Path, request_id: str) -> Path:
    folder = root / "_local" / "launcher-mods" / "helpers" / request_id
    # Reuse no-follow target validation before and after creating each parent.
    for path in reversed((folder, *tuple(folder.parents)[:3])):
        FileTarget.capture(path / "request.json", root, "json")
        path.mkdir(exist_ok=True)
    FileTarget.capture(folder / "request.json", root, "json")
    return folder


def _environment(values: object) -> Mapping[str, str]:
    if not isinstance(values, dict) or len(values) > 64:
        raise ModApiRuntimeError("Helper environment must be a bounded string mapping.")
    result, folded = {}, set()
    for key, value in values.items():
        if not isinstance(key, str) or not _ENVIRONMENT_NAME.fullmatch(key) or key.upper() in folded:
            raise ModApiRuntimeError("Helper environment names must be distinct valid names.")
        upper = key.upper()
        if upper in _RESERVED_ENVIRONMENT or upper.startswith(("EVEJS_", "EO_")):
            raise ModApiRuntimeError(f"The launcher owns environment variable {key}.")
        folded.add(upper)
        result[key] = _plain(value, "Environment value", 32767, empty=True)
    return MappingProxyType(result)


def _arguments(values: object) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) > 64:
        raise ModApiRuntimeError("Helper arguments must be a bounded list of switches.")
    result, names = [], set()
    for value in values:
        argument = _plain(value, "Client argument", 4096)
        match = _ARGUMENT.fullmatch(argument)
        if match is None:
            raise ModApiRuntimeError("Mod arguments must use one complete named switch per list item.")
        name = match[1].casefold()
        if name in _RESERVED_ARGUMENTS:
            raise ModApiRuntimeError(f"The launcher owns client switch {match[1]}.")
        if name in names:
            raise ModApiRuntimeError("A helper returned duplicate client switches.")
        names.add(name)
        result.append(argument)
    if sum(map(len, result)) > 16000:
        raise ModApiRuntimeError("Helper client arguments exceed the size limit.")
    return tuple(result)


def _contributions(values: object, action: str, context: ModSettingsContext) -> tuple[tuple[KeyEdit, ...], tuple[SettingsFile, ...]]:
    if not isinstance(values, list) or len(values) > 512:
        raise ModApiRuntimeError("Helper contributions must be a bounded list.")
    edits, files = [], []
    for index, row in enumerate(values):
        if isinstance(row, dict) and set(row) == {"base", "path", "format", "source"} and row["format"] == "file":
            if action not in {"install", "prepare_profile"} or row["base"] == "profile_settings":
                raise ModApiRuntimeError("Whole-file overlays require installation or private profile preparation.")
            source = _relative(row["source"])
            staged = FileTarget.capture(context.mod_folder / source, context.mod_folder, "file")
            content = read_target(staged)
            if content is None:
                raise ModApiRuntimeError("The staged overlay file is missing.")
            row = {"base": row["base"], "path": row["path"], "format": "file",
                   "key": ["$content"], "value": base64.b64encode(content).decode("ascii")}
            staged_file = True
        else:
            staged_file = False
        if not isinstance(row, dict) or set(row) != {"base", "path", "format", "key", "value"}:
            raise ModApiRuntimeError("A contribution needs base, path, format, key and value.")
        base, path, format = row["base"], _relative(row["path"]), row["format"]
        if not isinstance(base, str) or base not in _BASES or not isinstance(format, str) or format not in {"json", "ini", "yaml", "file", "text"} or (format == "file" and not staged_file):
            raise ModApiRuntimeError("Unsupported contribution base or format.")
        overlay = staged_file or format == "text"
        if overlay and (action not in {"install", "prepare_profile"} or base == "profile_settings"):
            raise ModApiRuntimeError("Source overlays require installation or private profile preparation.")
        if action == "prepare_profile" and base not in {"profile", "profile_settings"}:
            raise ModApiRuntimeError("Profile preparation cannot write shared client or global files.")
        if base == "evejs" and path.split("/")[0] != ("server" if overlay else "config"):
            raise ModApiRuntimeError("EveJS overlays must stay inside server/." if overlay else "EveJS contributions must stay inside config/.")
        if overlay and (path.split("/")[0].casefold() in {"_local", ".git"}
                or (base == "evejs" and [part.casefold() for part in path.split("/")[:2]] == ["server", "certs"])):
            raise ModApiRuntimeError("The overlay targets launcher state or server certificates.")
        value = row["value"]
        if value is not None and type(value) not in (str, int, float, bool):
            raise ModApiRuntimeError("Public helper contributions contain scalar values only.")
        if format == "text":
            if not isinstance(value, str) or len(value) > 65536 or "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ModApiRuntimeError("A text region replacement must be bounded valid text.")
        elif isinstance(value, str) and not staged_file:
            _plain(value, "Contribution value", 65536, empty=True)
        if type(value) is float and not math.isfinite(value):
            raise ModApiRuntimeError("Contribution numbers must be finite.")
        key = canonical_key(format, row["key"])
        if any(len(part) > 128 for part in key):
            raise ModApiRuntimeError("Contribution key components exceed the size limit.")
        file = SettingsFile(str(index), base, path, format)
        files.append(file)
        edits.append(KeyEdit(context.target(file), tuple(row["key"]), value,
            accept_current=action == "prepare_profile" and base == "profile" and not overlay))
    if files:
        context.store_root(tuple(files))  # reject ambiguous cross-store batches
    return tuple(edits), tuple(files)


def _receipt(row: object, descriptor: ModApiDescriptor, context: ModSettingsContext) -> ModHelperReceipt:
    if not isinstance(row, dict) or set(row) != {"base", "path", "state", "schemaVersion"}:
        raise ModApiRuntimeError("The binary receipt reference is invalid.")
    base, path = row["base"], _relative(row["path"])
    if not isinstance(base, str) or base not in {"client", "mod", "evejs"} or not isinstance(row["state"], str) or row["state"] not in {"active", "restored", "recoverable"} or type(row["schemaVersion"]) is not int or not 1 <= row["schemaVersion"] <= 65535:
        raise ModApiRuntimeError("The binary receipt state or schema is unsupported.")
    # Receipt references may use _local/ under the root. They are read-only
    # evidence and are not arbitrary writable settings-file declarations.
    roots = {"client": context.client_root, "mod": context.mod_folder, "evejs": context.evejs_root}
    root = roots[base]
    if root is None:
        raise ModApiRuntimeError("The receipt references an unavailable root.")
    target = FileTarget.capture(root / path, root, "json")
    content = read_target(target)
    if content is None:
        raise ModApiRuntimeError("The helper referenced a missing binary receipt.")
    document = _json(content)
    if type(document.get("schemaVersion")) is not int or document.get("schemaVersion") != row["schemaVersion"] or document.get("state") != row["state"] or document.get("modIdentity") != descriptor.identity:
        raise ModApiRuntimeError("The binary receipt does not match this mod and operation.")
    if context.client_root is not None:
        recorded = document.get("clientRoot")
        if not isinstance(recorded, str) or os.path.normcase(recorded) != os.path.normcase(str(context.client_root)):
            raise ModApiRuntimeError("The binary receipt belongs to a different physical client.")
    return ModHelperReceipt(target, row["state"], row["schemaVersion"], hashlib.sha256(content).hexdigest())


def validate_helper_result(payload: dict, descriptor: ModApiDescriptor, context: ModSettingsContext, action: str, request_id: str, request_path: Path) -> ModHelperResult:
    required = {"protocol", "requestId", "success", "state", "message", "restartRequired", "contributions", "environment", "arguments"}
    if not isinstance(payload, dict) or not required <= payload.keys() or payload.keys() - required - {"receipt"}:
        raise ModApiRuntimeError("The helper reply has missing or unsupported fields.")
    if payload["protocol"] != PROTOCOL or payload["requestId"] != request_id:
        raise ModApiRuntimeError("The helper reply does not match this request and protocol.")
    success, state = payload["success"], payload["state"]
    if type(success) is not bool or not isinstance(state, str) or state not in {"ready", "pending", "failed"} or (success and state == "failed") or (not success and state == "ready"):
        raise ModApiRuntimeError("The helper returned an inconsistent result state.")
    restarts = payload["restartRequired"]
    if not isinstance(restarts, list) or len(restarts) > 3 or any(not isinstance(value, str) or value not in _RESTARTS for value in restarts) or len(set(restarts)) != len(restarts) or ("none" in restarts and len(restarts) > 1):
        raise ModApiRuntimeError("The helper returned invalid restart requirements.")
    edits, files = _contributions(payload["contributions"], action, context)
    if files and context.store_root(files) != helper_coordination_root(descriptor, context, action):
        raise ModApiRuntimeError("The helper reply cannot change its captured coordination root.")
    receipt = _receipt(payload["receipt"], descriptor, context) if "receipt" in payload else None
    if action in NOTIFICATION_ACTIONS and (edits or payload["environment"] or payload["arguments"] or restarts or receipt):
        raise ModApiRuntimeError("Notification replies cannot request mutations or launch changes.")
    if success and state == "ready" and descriptor.kind == "client-package" and action != "prepare_profile" and action not in NOTIFICATION_ACTIONS and receipt is None:
        raise ModApiRuntimeError("A completed client package action requires a bound binary receipt.")
    if receipt is not None and success and state == "ready":
        expected = ({"restored"} if action in {"prepare_disable", "prepare_remove"}
                    else {"active", "restored"} if action in {"verify", "recover"} else {"active"})
        if receipt.state not in expected:
            raise ModApiRuntimeError("The receipt does not prove the requested binary state.")
    return ModHelperResult(descriptor, context, action, request_id, success, state,
        _plain(payload["message"], "Helper message", 4096, empty=True), tuple(restarts), edits, files,
        _environment(payload["environment"]), _arguments(payload["arguments"]), receipt, request_path)


def run_mod_helper(descriptor: ModApiDescriptor, action: str, context: ModSettingsContext, *, backend: str = "native", timeout: float | None = None, runner: Callable | None = None, event: dict | None = None) -> ModHelperResult:
    with acquire_mod_lifecycle_lock(helper_coordination_root(descriptor, context, action)):
        return run_mod_helper_locked(descriptor, action, context, backend=backend, timeout=timeout, runner=runner, event=event)


def run_mod_helper_locked(descriptor: ModApiDescriptor, action: str, context: ModSettingsContext, *, backend: str = "native", timeout: float | None = None, runner: Callable | None = None, event: dict | None = None) -> ModHelperResult:
    """Caller holds helper_coordination_root's lease through the terminal result."""
    context = _captured_context(descriptor, context, action, backend)
    if action in NOTIFICATION_ACTIONS:
        if not isinstance(event, dict) or set(event) != {"launchId", "status", "pid", "exitCode", "errorType"}:
            raise ModApiRuntimeError("A notification requires a captured client event.")
        uuid.UUID(event["launchId"])
        if event["status"] not in ({"started", "failed"} if action == "launch_result" else {"exited"}):
            raise ModApiRuntimeError("The client event does not match its notification action.")
        if ((event["pid"] is not None and (type(event["pid"]) is not int or event["pid"] <= 0))
                or (event["exitCode"] is not None and type(event["exitCode"]) is not int)):
            raise ModApiRuntimeError("The client event has an invalid process identity or exit code.")
        _plain(event["errorType"], "Notification error type", 128, empty=True)
    elif event is not None:
        raise ModApiRuntimeError("Only notifications accept client events.")
    timeout = _ACTION_TIMEOUTS.get(action, DEFAULT_HELPER_TIMEOUT) if timeout is None else timeout
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600 or not math.isfinite(timeout):
        raise ModApiRuntimeError("A helper timeout must be between zero and one hour.")
    helper = descriptor.launcher_api.helper
    fingerprint = _fingerprint(helper.path)
    request_id = str(uuid.uuid4())
    settings = _settings_values(descriptor, context)
    root = helper_coordination_root(descriptor, context, action)
    folder = _request_folder(root, request_id)
    request_path, result_path = folder / "request.json", folder / "result.json"
    request = {
        "protocol": PROTOCOL, "requestId": request_id, "action": action,
        "mod": {"id": descriptor.id, "version": descriptor.version, "identity": descriptor.identity,
                "root": str(descriptor.root), "path": str(descriptor.folder)},
        "runtime": {"backend": backend, "evejsRoot": str(context.evejs_root),
                    "clientRoot": str(context.client_root) if context.client_root is not None else None},
        "profile": {"id": context.profile_id, "root": str(context.profile_root),
                    "settingsRoot": str(context.profile_settings_root), "modDataRoot": str(context.mod_data_root)} if context.profile_id else None,
        "settings": settings,
    }
    if event is not None:
        request["event"] = dict(event)
    encoded = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_REPLY_BYTES:
        raise ModApiRuntimeError("The helper request exceeds the size limit.")
    with request_path.open("xb") as stream:
        stream.write(encoded)
    command, environment = _command(descriptor, request_path, result_path)
    completed = (runner or run_helper_process)(command, cwd=descriptor.folder, environment=environment, timeout=timeout, output_directory=folder)
    if read_api_manifest(context.evejs_root, context.mod_folder) != descriptor or _fingerprint(helper.path) != fingerprint:
        raise ModApiRuntimeError("The mod helper or declaration changed during the operation.")
    content = read_target(FileTarget.capture(result_path, root, "json"))
    if content is None:
        raise ModApiRuntimeError(f"{descriptor.display_name} did not return a correlated helper result (exit {completed.returncode}).")
    result = validate_helper_result(_json(content), descriptor, context, action, request_id, request_path)
    if completed.returncode != 0 and result.success:
        raise ModApiRuntimeError("The helper reported success after a failed process exit.")
    return result


def _revalidate_result(result: ModHelperResult) -> None:
    result.require_ready()
    if result.contribution_files and result.context.store_root(result.contribution_files) != helper_coordination_root(result.descriptor, result.context, result.action):
        raise ModApiRuntimeError("The helper reply cannot change its captured coordination root.")
    if read_api_manifest(result.context.evejs_root, result.context.mod_folder) != result.descriptor:
        raise ModApiRuntimeError("The helper declaration changed before contribution commit.")
    if result.receipt is not None:
        content = read_target(result.receipt.target)
        if content is None or hashlib.sha256(content).hexdigest() != result.receipt.digest:
            raise ModApiRuntimeError("The binary receipt changed before contribution commit.")


def commit_helper_contributions(result: ModHelperResult):
    """Commit a ready helper's proposed key edits; never run its binary helper."""
    root = result.context.store_root(result.contribution_files) if result.contribution_files else helper_coordination_root(result.descriptor, result.context, result.action)
    with acquire_mod_lifecycle_lock(root):
        return commit_helper_contributions_locked(result)


def commit_helper_contributions_locked(result: ModHelperResult):
    _revalidate_result(result)
    root = result.context.store_root(result.contribution_files) if result.contribution_files else helper_coordination_root(result.descriptor, result.context, result.action)
    store = ContributionStore(root, allowed_roots={edit.target.allowed_root for edit in result.contributions})
    owner = result.context.owner
    try:
        plan = store.plan_edits_locked(owner, result.contributions)
    except ContributionConflict:
        apply = store.plan_edits_locked(owner, (replace(edit, allow_override=True) for edit in result.contributions))
        preserve = ContributionPlan(root, tuple(FileChange(file.target, file.before, file.before) for file in apply.files),
                                    apply.index_before, apply.index_before)
        owners = tuple(dict.fromkeys([owner, *(item for file in apply.files for item in store.owners_for_path(file.target.path))]))
        raise RemovalReviewRequired(ConfigurationReview(preserve, apply, owners, "helper")) from None
    return store.commit_locked(plan)


def merge_launch_options(results: Iterable[ModHelperResult], *, protected_environment: Mapping[str, str] | None = None, protected_arguments: Iterable[str] = ()) -> tuple[dict[str, str], tuple[str, ...]]:
    environment, arguments = {}, []
    known_environment = {key.upper(): value for key, value in (protected_environment or {}).items()}
    known_arguments = {}
    for argument in protected_arguments:
        match = _ARGUMENT.fullmatch(argument)
        if match:
            known_arguments[match[1].casefold()] = argument
    for result in results:
        result.require_ready()
        for key, value in result.environment.items():
            if key.upper() in known_environment and known_environment[key.upper()] != value:
                raise ModApiRuntimeError(f"Mod launch environment conflicts on {key}.")
            if key.upper() not in known_environment:
                environment[key] = value
            known_environment[key.upper()] = value
        for argument in result.arguments:
            name = _ARGUMENT.fullmatch(argument)[1].casefold()
            if name in known_arguments and known_arguments[name] != argument:
                raise ModApiRuntimeError(f"Mod launch arguments conflict on {name}.")
            if name not in known_arguments:
                arguments.append(argument)
            known_arguments[name] = argument
    return environment, tuple(arguments)


def public_client_mods(evejs_root: str | Path, *, backend: str = "native", mods: Iterable[Mod] | None = None) -> tuple[Mod, ...]:
    """Optional invalid/unselected rows never become launch dependencies."""
    from .local_mod_packages import LocalModPackages
    from .mod_relationships import plan_mod_order
    discovered = tuple(scan_mods(evejs_root) if mods is None else mods)
    def selected_for_launch(mod):
        return (mod.valid and mod.active and mod.supports_backend(backend)
                and mod.api_descriptor is not None and (mod.api_descriptor.kind == "client-package"
                or (mod.api_descriptor.launcher_api is not None and
                    ({"prepare_profile"} | NOTIFICATION_ACTIONS).intersection(mod.api_descriptor.launcher_api.capabilities))))
    if not any(selected_for_launch(mod) for mod in discovered):
        return ()
    if mods is None:
        discovered = LocalModPackages(evejs_root).sort_mods(discovered)
    plan = plan_mod_order(discovered, backend=backend)
    selected = tuple(mod for mod in plan.mods if selected_for_launch(mod))
    plan.require_valid(selected)
    return selected


def public_package_owns_legacy_folder(evejs_root: str | Path, relative_folder: str, *, mods: Iterable[Mod] | None = None) -> bool:
    """Route an exact package to its public adapter, including its disabled state."""
    root = Path(evejs_root)
    if not root.is_dir():
        return False
    selected = root.resolve() / relative_folder
    return any(mod.valid and mod.api_descriptor is not None and mod.path == selected
               and mod.api_descriptor.kind == "client-package" for mod in (scan_mods(root) if mods is None else mods))


def prepare_public_client_mods(evejs_root: str | Path, client_root: str | Path, profile_tq_path: Path, *, backend: str = "native", protected_environment: Mapping[str, str] | None = None, protected_arguments: Iterable[str] = (), runner: Callable | None = None, mods: Iterable[Mod] | None = None) -> PreparedClientMods:
    mods = public_client_mods(evejs_root, backend=backend, mods=mods)
    if not mods:
        return PreparedClientMods(MappingProxyType({}), (), ())
    client = Path(client_root).resolve(strict=True)
    profile = profile_tq_path.parent.resolve(strict=True)
    profile_id = profile_identity(profile)
    settings = platform_api.get_eve_settings_path(str(profile_tq_path))
    local_appdata = Path(os.environ["LOCALAPPDATA"]).resolve(strict=True)
    results, notifications = [], []
    with acquire_mod_lifecycle_lock(client):
        for mod in mods:
            descriptor = mod.api_descriptor
            context = ModSettingsContext(Path(evejs_root), descriptor.folder, client, profile_id, profile, settings,
                profile_settings_storage_root=local_appdata)
            capabilities = descriptor.launcher_api.capabilities if descriptor.launcher_api is not None else ()
            if NOTIFICATION_ACTIONS.intersection(capabilities):
                notifications.append((descriptor, context))
            if descriptor.kind == "client-package" and "verify" in capabilities:
                verified = run_mod_helper_locked(descriptor, "verify", context, backend=backend, runner=runner).require_ready()
                if verified.receipt is None or verified.receipt.state != "active":
                    raise ModApiRuntimeError("The enabled client mod is not installed. Install it or disable it before launching.")
                if verified.contributions:
                    raise ModApiRuntimeError("Client verification must not propose configuration mutations.")
                results.append(verified)
            elif descriptor.kind == "client-package":
                raise ModApiRuntimeError("An active client package must declare binary verification before launch.")
            if "prepare_profile" in capabilities:
                results.append(run_mod_helper_locked(descriptor, "prepare_profile", context, backend=backend, runner=runner).require_ready())
        environment, arguments = merge_launch_options(results, protected_environment=protected_environment, protected_arguments=protected_arguments)
        for result in results:
            _revalidate_result(result)
        roots = {edit.target.allowed_root for result in results for edit in result.contributions}
        store = ContributionStore(client, allowed_roots=roots)
        plan = store.plan_batch_locked((result.context.owner, result.contributions) for result in results if result.contributions)
        store.commit_locked(plan)
    return PreparedClientMods(MappingProxyType(environment), arguments, tuple(results), tuple(notifications))
