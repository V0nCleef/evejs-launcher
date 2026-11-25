"""Snapshot EVE's numeric account and character UI preferences for a save transfer."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from . import platform
from .db import _read_accounts
from .host import IS_LINUX
from .installation_profiles import client_profiles_root
from .profiles import PROFILES_ROOT, account_profiles_root
from .save_migration_storage import SaveMigrationError, plain
from .shared_settings_profile import (
    SharedSettingsError, SharedSettingsSource, _json_bytes, _read_file, _replace_settings,
    shared_profile_main_folder,
)


@dataclass(frozen=True)
class ClientSettingsLocation:
    profiles: Path
    profile_id: str
    settings: dict


def main_location(installation):
    return ClientSettingsLocation(PROFILES_ROOT, installation.profile_id,
                                  installation.settings)


def _docker_client_scope(installation, storage):
    if not storage.runner:
        return None, None
    from .runtime.data import docker_project_identity
    from .runtime.docker_compose import _parse_compose_config_output
    from .runtime.docker_setup import DockerSetupDraft, build_compose_target

    options = installation.settings
    target = build_compose_target(DockerSetupDraft(
        str(installation.root), options.get("docker_compose_file", ""), options.get("docker_project_name", ""),
        options.get("docker_control_policy", "connect_only"), options.get("docker_keep_running_on_exit", True),
        options.get("client_path", ""),
    ))
    config = storage.runner.run_parsed(
        target.compose_args(storage.runner.executable, "config", "--format", "json"),
        cwd=installation.root, parser=_parse_compose_config_output,
    )
    if config.project_name != storage.project:
        raise SaveMigrationError("The Compose project changed while locating client settings.")
    return docker_project_identity(target, config.project_name, config=config), config.game_store_identity


def _linux_settings(installation, storage, *, target_identity=None):
    from .linux_client import account_profile_name, settings_base
    from .linux_installation import LinuxInstallation

    options = installation.settings
    setup_root = (options.get("linux_setup_root") or installation.root) if storage.runner else installation.root
    prepared = LinuxInstallation.load(setup_root)
    if prepared.client != Path(options.get("client_path", "")).resolve():
        raise SaveMigrationError("The selected Proton client differs from the installation's prepared client.")
    base = settings_base(prepared)
    scope = target_identity
    if scope is None and storage.runner:
        scope, _ = _docker_client_scope(installation, storage)
    return lambda username: (base / ("settings_" + account_profile_name(prepared, username, scope or "")),)


def _windows_settings(location, usernames):
    base = client_profiles_root({"active_installation_profile": location.profile_id}, location.profiles)
    def paths(username):
        root = account_profiles_root(base, username, usernames)
        return (platform.get_eve_settings_path(str(root / username / "tq")),)
    return paths


def _legacy_shared_profiles(root, profile_tq, installation, target_identity):
    """Read only legacy manifests bound to this server and Compose target."""
    manifests = list(root.glob("*/active.json"))
    if len(manifests) > 1024:
        raise SaveMigrationError("Too many shared client profiles to verify safely.")
    for manifest in manifests:
        if not re.fullmatch(r"[0-9a-f]{64}", manifest.parent.name):
            continue
        try:
            saved = json.loads(_read_file(plain(manifest)))
            identity = saved["identity"]
            if (identity.get("serverRoot") != os.path.normcase(str(installation.root))
                    or identity.get("targetIdentity") != target_identity):
                continue
            source = SharedSettingsSource.from_config(identity["source"])
            if source is None or saved["schemaVersion"] != 1:
                continue
            main = shared_profile_main_folder(profile_tq, source, evejs_root=str(installation.root),
                                              target_identity=target_identity, storage_root=root)
            if (identity["settingsFolder"] == os.path.normcase(str(main))
                    and manifest.parent.name == hashlib.sha256(_json_bytes(identity)).hexdigest()):
                yield source, main
        except (AttributeError, KeyError, OSError, TypeError, ValueError, RuntimeError):
            continue


def _shared_folders(installation, account, usernames, target_identity, group_identity):
    from .groups import installation_group_identity
    from .native_drone_profiles import personal_settings_file

    options = {**installation.settings, "active_installation_profile": installation.profile_id}
    base = client_profiles_root(options, PROFILES_ROOT)
    profile_tq = account_profiles_root(base, account.username, usernames) / account.username / "tq"
    root = plain((profile_tq.parent.parent.parent if IS_LINUX else base.parent) / "SharedProfiles")
    scope = installation_group_identity(options, SimpleNamespace(group_identity=group_identity))
    legacy_root = plain(profile_tq.parent.parent.parent / "SharedProfiles")
    legacy = tuple(_legacy_shared_profiles(legacy_root, profile_tq, installation, target_identity))
    for character in account.characters:
        if not 0 < character.char_id < 2**63:
            continue
        if not IS_LINUX and scope.startswith("installation-groups:"):
            owned = personal_settings_file(root, scope, account.account_id, character.char_id)
            if owned is not None:
                yield character.char_id, plain(owned.parent)
                continue
        for owner, main in legacy:
            if (owner.account_id, owner.character_id) == (account.account_id, character.char_id):
                yield character.char_id, plain(main)
                continue
            pointer = plain(main.parent / "pilots" / f"{account.account_id}-{character.char_id}.json")
            if not pointer.is_file():
                continue
            saved = json.loads(_read_file(pointer))
            session = saved.get("session")
            if (saved.get("schemaVersion"), saved.get("accountID"), saved.get("characterID")) == (
                1, account.account_id, character.char_id
            ) and isinstance(session, str) and re.fullmatch(r"[0-9a-f]{32}", session):
                yield character.char_id, plain(main.parent / "sessions" / session)


def _preferences(file):
    plain(file)
    data = _read_file(file)
    if len(data) < 7 or not data.startswith(b"~"):
        raise SaveMigrationError("An EVE client settings file uses an unsupported format.")
    return data


def _numbered(account):
    if type(account.account_id) is not int or not 0 < account.account_id < 2**63:
        return ()
    return (f"core_user_{account.account_id}.dat", *(f"core_char_{c.char_id}.dat" for c in account.characters
                                                   if 0 < c.char_id < 2**63))


def capture_client_settings(request, game_db, source, target):
    """Read only numbered settings for exact identities in the staged game save."""
    if request.destination.settings.get("client_settings_mode", "separate") != "separate":
        raise SaveMigrationError("Set the destination's client settings to Separate before copying per-character settings.")
    with closing(sqlite3.connect(game_db.as_uri() + "?mode=ro", uri=True)) as db:
        accounts = _read_accounts(db, lambda _: "")
    names = tuple(account.username for account in accounts)
    if any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", name) or name.endswith(".")
           for name in names):
        raise SaveMigrationError("An account name cannot safely be mapped to a client settings folder.")
    shared = request.source.settings.get("client_settings_mode") == "shared"
    target_identity, group_identity = (_docker_client_scope(request.source, source)
                                       if shared or IS_LINUX else (None, None))
    separate_source = (_linux_settings(request.source, source, target_identity=target_identity) if IS_LINUX else
                       _windows_settings(main_location(request.source), names))
    destination = (_linux_settings(request.destination, target) if IS_LINUX else
                   _windows_settings(main_location(request.destination), names))
    changes = {}
    source_folders = set()
    total = 0
    for account in accounts:
        filenames = _numbered(account)
        if not filenames:
            continue
        folder = plain(destination(account.username)[0])
        fallback = list(separate_source(account.username))
        shared_folders = ()
        if shared:
            shared_folders = tuple(_shared_folders(request.source, account, names, target_identity, group_identity))
            legacy = request.source.settings.get("shared_settings_folder")
            if legacy:
                fallback.insert(0, Path(legacy))
        for name in filenames:
            matching = [path / name for character_id, path in shared_folders
                        if name == filenames[0] or name == f"core_char_{character_id}.dat"]
            matching = [plain(path) for path in matching if os.path.lexists(path)]
            matching.sort(key=lambda path: (path.stat().st_mtime_ns, str(path)), reverse=True)
            candidates = (*matching, *(plain(path) / name for path in fallback))
            source_folders.update(os.path.normcase(str(path.parent)) for path in candidates)
            original = next((path for path in candidates if os.path.lexists(path)), None)
            if original is None:
                continue
            output = plain(folder / name)
            if output in changes or (output.exists() and output.samefile(original)):
                raise SaveMigrationError("These installations share client settings. Select independent profiles.")
            data = _preferences(original)
            total += len(data)
            if total > 128 * 1024 * 1024:
                raise SaveMigrationError("The selected client settings exceed the transfer size limit.")
            changes[output] = data
    if any(os.path.normcase(str(path.parent)) in source_folders for path in changes):
        raise SaveMigrationError("These installations share client settings. Select independent profiles.")
    if not changes:
        raise SaveMigrationError("No saved client settings were found for the characters in this playthrough.")
    return changes


def apply_client_settings(changes, backup):
    try:
        _replace_settings(changes, backup)
    except SharedSettingsError as exc:
        raise SaveMigrationError(str(exc)) from exc
