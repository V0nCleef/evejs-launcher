"""Pure, bounded installation registry; selecting a world never moves its data."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import uuid


WORLD_DEFAULTS = {
    "evejs_root": "", "client_path": "", "runtime_backend": "native",
    "proxy_url": "http://127.0.0.1:26002", "game_port": 26000,
    "docker_compose_file": "", "docker_project_name": "",
    "docker_control_policy": "connect_only", "docker_keep_running_on_exit": True,
    "server_mode": "modded", "server_start_preference": "ask",
    "auto_start_server": False, "auto_start_market": False,
    "auto_login_enabled": True, "stagger_delay_sec": 3,
    "client_settings_mode": "separate", "shared_settings_folder": "",
    "shared_settings_source": {}, "hidden_characters": [],
    "hide_test_characters": True, "never_hide_characters": [],
    "docker_readiness_timeout_sec": 180,
}
WORLD_FIELDS = frozenset(WORLD_DEFAULTS)
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


class InstallationProfileError(ValueError):
    pass


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80 or any(ord(c) < 32 for c in value):
        raise InstallationProfileError("Installation name must be 1-80 characters without control characters.")
    return value.strip()


def _settings(values):
    if not isinstance(values, dict):
        raise InstallationProfileError("Installation settings must be an object.")
    result = {}
    ranges = {"game_port": (1,65535), "stagger_delay_sec": (0,30), "docker_readiness_timeout_sec": (30,900)}
    for key, default in WORLD_DEFAULTS.items():
        value = deepcopy(values.get(key, default))
        if isinstance(default,bool):
            value = value if type(value) is bool else default
        elif isinstance(default,str):
            value = value if isinstance(value,str) else default
        elif key in ranges:
            low, high = ranges[key]
            value = value if type(value) is int and low <= value <= high else default
        elif isinstance(default,list):
            value = [item for item in value if isinstance(item,str)] if isinstance(value,list) else []
        elif isinstance(default,dict):
            value = value if isinstance(value,dict) else {}
        result[key] = value
    for key, options in (("runtime_backend", {"native","docker_compose"}),
                         ("docker_control_policy", {"managed","connect_only"}),
                         ("client_settings_mode", {"shared","separate"}),
                         ("server_mode", {"modded","vanilla"})):
        if result[key] not in options:
            result[key] = WORLD_DEFAULTS[key]
    return result


def _unique(entries):
    names, roots = set(), set()
    for entry in entries:
        name = entry["name"].casefold()
        if name in names:
            raise InstallationProfileError("Choose a different installation name; this name is already registered.")
        names.add(name)
        root = entry["settings"].get("evejs_root")
        if root:
            actual = os.path.normcase(str(Path(root).resolve(strict=False)))
            if actual in roots:
                raise InstallationProfileError("This physical EveJS installation is already registered. Select its existing profile.")
            roots.add(actual)


def ensure_profiles(cfg: dict) -> dict:
    result = deepcopy(cfg)
    entries = result.get("installation_profiles")
    if entries is None:
        result["installation_profiles"] = [{"id": "default", "name": "Default", "settings": _settings(cfg)}]
        result["active_installation_profile"] = "default"
        return result
    if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
        raise InstallationProfileError("The installation registry must contain 1-64 profiles.")
    normalized, ids = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "name", "settings"}:
            raise InstallationProfileError("An installation profile is malformed.")
        identity = entry["id"]
        if not isinstance(identity, str) or not _ID.fullmatch(identity) or identity in ids:
            raise InstallationProfileError("Installation IDs must be safe and unique.")
        ids.add(identity)
        normalized.append({"id": identity, "name": _name(entry["name"]), "settings": _settings(entry["settings"])})
    active = result.get("active_installation_profile", "default")
    if active not in ids:
        raise InstallationProfileError("The selected installation profile does not exist.")
    result["installation_profiles"] = normalized
    result["active_installation_profile"] = active
    _unique(normalized)
    result.update(deepcopy(next(entry["settings"] for entry in normalized if entry["id"] == active)))
    return result


def sync_active_profile(cfg: dict) -> dict:
    # Capture edited top-level settings before normalizing: legacy consumers
    # retain their established top-level view of the currently selected world.
    values = _settings(cfg)
    result = ensure_profiles(cfg)
    active = result["active_installation_profile"]
    for entry in result["installation_profiles"]:
        if entry["id"] == active:
            entry["settings"] = values
    result.update(deepcopy(values))
    _unique(result["installation_profiles"])
    return result


def select_profile(cfg: dict, identity: str) -> dict:
    result = sync_active_profile(cfg)
    if not any(entry["id"] == identity for entry in result["installation_profiles"]):
        raise InstallationProfileError("The selected installation profile does not exist.")
    result["active_installation_profile"] = identity
    return ensure_profiles(result)


def add_profile(cfg: dict, name: str, settings: dict) -> dict:
    result = sync_active_profile(cfg)
    if len(result["installation_profiles"]) >= 64:
        raise InstallationProfileError("At most 64 installation profiles are supported.")
    result["installation_profiles"].append({"id": uuid.uuid4().hex, "name": _name(name), "settings": _settings(settings)})
    _unique(result["installation_profiles"])
    return result


def rename_profile(cfg: dict, identity: str, name: str) -> dict:
    result = sync_active_profile(cfg)
    for entry in result["installation_profiles"]:
        if entry["id"] == identity:
            entry["name"] = _name(name)
            _unique(result["installation_profiles"])
            return result
    raise InstallationProfileError("The selected installation profile does not exist.")


def remove_profile(cfg: dict, identity: str) -> dict:
    result = sync_active_profile(cfg)
    if identity == result["active_installation_profile"]:
        raise InstallationProfileError("Select another installation before removing this profile.")
    entries = [entry for entry in result["installation_profiles"] if entry["id"] != identity]
    if len(entries) == len(result["installation_profiles"]):
        raise InstallationProfileError("The selected installation profile does not exist.")
    result["installation_profiles"] = entries
    return result


def client_profiles_root(cfg: dict, legacy_root: Path) -> Path:
    identity = cfg.get("active_installation_profile", "default")
    if not isinstance(identity, str) or not _ID.fullmatch(identity):
        raise InstallationProfileError("The installation ID is invalid.")
    return Path(legacy_root) if identity == "default" else Path(legacy_root).parent / "Installations" / identity / "Profiles"
