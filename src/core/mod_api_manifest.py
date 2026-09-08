"""Declarative schema-3 mod interface. Parsing never starts an author helper."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


API_MANIFEST_FILENAME = "evejs-launcher.mod.json"
MAX_API_MANIFEST_BYTES = 1024 * 1024
_KINDS = {"loader", "source-integrated", "client-package", "settings"}
_STRATEGIES = {"loader_rename", "json_boolean", "client_package", "package"}
NOTIFICATION_ACTIONS = frozenset({"launch_result", "client_exit"})
SOURCE_PACKAGE_CAPABILITIES = frozenset({"install", "prepare_disable", "prepare_remove"})
_CAPABILITIES = {"prepare_profile", "install", "verify", "prepare_disable", "prepare_remove", "recover"} | NOTIFICATION_ACTIONS
_RESERVED = {"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


class ModApiManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ModApiHelper:
    runtime: str
    path: Path


@dataclass(frozen=True)
class LauncherApiSpec:
    version: int
    min_launcher_version: str
    helper: ModApiHelper | None
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ModApiDescriptor:
    id: str
    display_name: str
    version: str
    description: str
    kind: str
    supported_backends: tuple[str, ...]
    restart_scope: str
    activation_strategy: str
    config_path: Path | None
    config_key: tuple[str, ...]
    allowed_config_schema_versions: tuple[int, ...]
    launcher_api: LauncherApiSpec | None
    settings: dict | None
    manifest_path: Path
    root: Path
    folder: Path
    identity: str
    requires: tuple[str, ...] = ()
    load_before: tuple[str, ...] = ()
    load_after: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    updates: object | None = None
    evejs_versions: tuple[str, ...] | None = None


def _text(value, label: str, maximum: int = 128, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or (not value and not empty) or len(value) > maximum
            or value != value.strip() or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ModApiManifestError(f"{label} must be bounded plain text.")
    return value


def _keys(value, required: set[str], optional: set[str], label: str) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ModApiManifestError(f"{label} has missing or unsupported fields.")
    return value


def _relative_path(value, label: str) -> PurePosixPath:
    text = _text(value, label, 240)
    if "\\" in text or text.startswith("/"):
        raise ModApiManifestError(f"{label} must be a relative path with forward slashes.")
    parts = text.split("/")
    for part in parts:
        if (part in {"", ".", ".."} or part.endswith((".", " ")) or any(c in ':*?<>|"' for c in part)
                or part.split(".", 1)[0].casefold() in _RESERVED):
            raise ModApiManifestError(f"{label} contains an unsafe path component.")
    return PurePosixPath(*parts)


def _safe_path(base: Path, relative: PurePosixPath, label: str, *, file: bool = False) -> Path:
    candidate = base.joinpath(*relative.parts)
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if os.path.lexists(cursor):
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ModApiManifestError(f"{label} must not use linked/reparse paths.")
    try:
        candidate.resolve().relative_to(base.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ModApiManifestError(f"{label} escapes its declared root.") from exc
    if file and not candidate.is_file():
        raise ModApiManifestError(f"{label} is not an existing file.")
    return candidate


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ModApiManifestError(f"Duplicate manifest key: {key}.")
        value[key] = item
    return value


def read_api_manifest(root: str | Path, folder: str | Path, payload: dict | None = None) -> ModApiDescriptor:
    root = Path(root).resolve(strict=True)
    folder = Path(folder)
    try:
        relative_folder = folder.relative_to(root)
        folder = _safe_path(root, PurePosixPath(relative_folder.as_posix()), "Mod folder")
        manifest = _safe_path(folder, PurePosixPath(API_MANIFEST_FILENAME), "Mod descriptor", file=True)
        if payload is None:
            if manifest.stat().st_size > MAX_API_MANIFEST_BYTES:
                raise ModApiManifestError("The mod descriptor is too large.")
            def reject(_):
                raise ModApiManifestError("Non-finite JSON values are unsupported.")
            with manifest.open("rb") as stream:
                content = stream.read(MAX_API_MANIFEST_BYTES + 1)
            if len(content) > MAX_API_MANIFEST_BYTES:
                raise ModApiManifestError("The mod descriptor is too large.")
            payload = json.loads(content, object_pairs_hook=_object, parse_constant=reject)
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, ModApiManifestError):
            raise
        raise ModApiManifestError("The public mod descriptor is unreadable or unsafe.") from exc
    payload = _keys(payload, {"schemaVersion", "id", "displayName", "version", "kind", "activation", "restart"},
                    {"description", "supportedBackends", "launcherApi", "settings",
                     "requires", "loadBefore", "loadAfter", "conflicts", "updates", "compatibility"}, "Mod descriptor")
    if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 3:
        raise ModApiManifestError("The public mod descriptor must use schemaVersion 3.")
    mod_id = _text(payload["id"], "Mod id")
    name = _text(payload["displayName"], "Display name", 100)
    version = _text(payload["version"], "Mod version", 64)
    description = _text(payload.get("description", ""), "Description", 1000, empty=True)
    kind = _text(payload["kind"], "Mod kind")
    if kind not in _KINDS:
        raise ModApiManifestError("Unsupported public mod kind.")
    restart = _text(payload["restart"], "Restart scope")
    if restart not in {"none", "game_server", "client", "launcher"}:
        raise ModApiManifestError("Unsupported restart scope.")
    backends = payload.get("supportedBackends", ["native", "docker"] if kind == "loader" else ["native"])
    if (not isinstance(backends, list) or not backends or any(not isinstance(value, str) or value not in {"native", "docker"} for value in backends)
            or len(set(backends)) != len(backends)):
        raise ModApiManifestError("supportedBackends must contain distinct supported backend names.")
    activation = _keys(payload["activation"], {"strategy"},
                       {"configPath", "property", "allowedConfigSchemaVersions"}, "Activation")
    strategy = _text(activation["strategy"], "Activation strategy")
    if strategy not in _STRATEGIES:
        raise ModApiManifestError("Unsupported public activation strategy.")
    allowed = {
        "loader": {"loader_rename"}, "source-integrated": {"json_boolean", "package"},
        "client-package": {"client_package", "package"}, "settings": {"json_boolean", "package"},
    }
    if strategy not in allowed[kind]:
        raise ModApiManifestError("The activation strategy does not match the package kind.")
    config_path = None
    config_key: tuple[str, ...] = ()
    versions: tuple[int, ...] = ()
    if strategy == "json_boolean":
        if "configPath" not in activation or "property" not in activation:
            raise ModApiManifestError("JSON activation needs configPath and property.")
        relative = _relative_path(activation["configPath"], "Activation configPath")
        if relative.suffix.casefold() != ".json":
            raise ModApiManifestError("Activation configPath must reference a JSON file.")
        if tuple(part.casefold() for part in relative.parts[:2]) == ("_local", "launcher-mods"):
            raise ModApiManifestError("A mod cannot edit the launcher's ownership registry.")
        if tuple(part.casefold() for part in relative.parts[:2]) == ("_local", "gamestore"):
            raise ModApiManifestError("Shared EveJS GameStore data cannot be used as an activation setting.")
        config_path = _safe_path(root, relative, "Activation configuration")
        keys = activation["property"]
        keys = [keys] if isinstance(keys, str) else keys
        if not isinstance(keys, list) or not keys or len(keys) > 16:
            raise ModApiManifestError("Activation property must be a key or a nonempty array of nested keys.")
        config_key = tuple(_text(key, "Configuration key") for key in keys)
        values = activation.get("allowedConfigSchemaVersions", [])
        if (not isinstance(values, list) or len(values) > 32
                or any(type(value) is not int or value < 1 or value > 65535 for value in values)
                or len(set(values)) != len(values)):
            raise ModApiManifestError("allowedConfigSchemaVersions must contain distinct positive integers.")
        versions = tuple(values)
    elif set(activation) != {"strategy"}:
        raise ModApiManifestError("Only JSON activation accepts configuration path fields.")
    launcher_api = None
    if "launcherApi" in payload:
        api = _keys(payload["launcherApi"], {"version"}, {"minLauncherVersion", "helper", "capabilities"}, "launcherApi")
        if type(api["version"]) is not int or api["version"] != 1:
            raise ModApiManifestError("Unsupported launcher API version.")
        minimum = _text(api.get("minLauncherVersion", "1.0.53"), "Minimum launcher version", 32)
        if not re.fullmatch(r"\d+\.\d+\.\d+", minimum):
            raise ModApiManifestError("Minimum launcher version must have three numeric components.")
        helper = None
        if "helper" in api:
            entry = _keys(api["helper"], {"runtime", "path"}, set(), "Launcher helper")
            runtime = _text(entry["runtime"], "Helper runtime")
            if runtime not in {"node", "powershell", "executable"}:
                raise ModApiManifestError("Unsupported helper runtime.")
            helper = ModApiHelper(runtime, _safe_path(folder, _relative_path(entry["path"], "Helper path"), "Helper", file=True))
        capabilities = api.get("capabilities", [])
        if (not isinstance(capabilities, list) or any(not isinstance(value, str) or value not in _CAPABILITIES for value in capabilities)
                or len(set(capabilities)) != len(capabilities)):
            raise ModApiManifestError("Unsupported or repeated launcher capability.")
        if capabilities and helper is None:
            raise ModApiManifestError("Executable launcher capabilities require a helper.")
        launcher_api = LauncherApiSpec(1, minimum, helper, tuple(capabilities))
    if kind == "source-integrated" and strategy == "package" and (launcher_api is None
            or not SOURCE_PACKAGE_CAPABILITIES.issubset(launcher_api.capabilities)):
        raise ModApiManifestError("Source overlay packages must declare install, prepare_disable and prepare_remove.")
    settings = payload.get("settings")
    if settings is not None and not isinstance(settings, dict):
        raise ModApiManifestError("settings must be an object.")
    identity = os.path.normcase(str(root)) + "|" + relative_folder.as_posix().casefold()
    relationships = []
    for key in ("requires", "loadBefore", "loadAfter", "conflicts"):
        values = payload.get(key, [])
        if not isinstance(values, list) or len(values) > 64:
            raise ModApiManifestError(f"{key} must be an array of at most 64 mod IDs.")
        values = tuple(_text(value, f"{key} mod ID") for value in values)
        normalized = tuple(value.casefold() for value in values)
        if len(set(normalized)) != len(values) or mod_id.casefold() in normalized:
            raise ModApiManifestError(f"{key} must not repeat an ID or reference the mod itself.")
        if values and key in {"loadBefore", "loadAfter"} and kind != "loader":
            raise ModApiManifestError("Only loader mods can declare preload ordering.")
        relationships.append(values)
    updates = None
    from .mod_evejs_compatibility import parse_evejs_versions
    compatibility = _keys(payload.get('compatibility', {}), set(), {'evejsVersions'}, 'Compatibility')
    try:
        evejs_versions = parse_evejs_versions(compatibility.get('evejsVersions'))
    except ValueError as exc:
        raise ModApiManifestError(str(exc)) from exc
    if "updates" in payload:
        from .mod_update_source import parse_update_source, Version, ModUpdateError
        try:
            updates = parse_update_source(payload["updates"])
            Version.parse(version)
        except ModUpdateError as exc:
            raise ModApiManifestError(str(exc)) from exc
    return ModApiDescriptor(mod_id, name, version, description, kind, tuple(backends), restart, strategy,
                            config_path, config_key, versions, launcher_api, settings, manifest, root, folder, identity,
                            *relationships, updates, evejs_versions)
