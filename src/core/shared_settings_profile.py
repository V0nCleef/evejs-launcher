"""Copy an explicitly selected EVE UI profile without copying login identity.

The supported client's SettingsSvc selects numeric account and character DAT
files by the authenticated session IDs. Their contents are preference groups;
we preserve those opaque bytes and write them under each destination's own IDs.
Anonymous caches, prefs.ini, credentials, and launch arguments are never copied.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .. import config
from . import platform
from .profiles import _is_directory_link, _path_entry_exists
from .shared_client_settings import SharedSettingsError, SharedClientSettings, _prepare_shared_settings_binding

_MAX_FILE_SIZE = 16 * 1024 * 1024
# The verified client's blue.marshal empty dictionary, not an anonymous cache.
_EMPTY_SETTINGS = bytes.fromhex("7e000000001600")


@dataclass(frozen=True)
class SharedSettingsSource:
    username: str
    account_id: int
    character_id: int
    evejs_root: str

    @classmethod
    def from_config(cls, value: object) -> SharedSettingsSource | None:
        normalized = config.normalize_shared_settings_source(value)
        return cls(**normalized) if normalized else None

    def as_dict(self) -> dict:
        return {"username": self.username, "account_id": self.account_id,
                "character_id": self.character_id, "evejs_root": self.evejs_root}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(path: Path) -> bytes:
    if not path.is_file() or _is_directory_link(path) or path.stat().st_size > _MAX_FILE_SIZE:
        raise SharedSettingsError("The selected character profile is unavailable or unsupported.")
    data = path.read_bytes()
    if len(data) > _MAX_FILE_SIZE:
        raise SharedSettingsError("The selected character profile is unavailable or unsupported.")
    return data


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".shared-profile-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _safe_storage(path: Path) -> None:
    if _path_entry_exists(path) and _is_directory_link(path):
        raise SharedSettingsError("The shared profile backup folder is redirected.")
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path.absolute())):
        raise SharedSettingsError("The shared profile backup folder is redirected.")
    path.mkdir(parents=True, exist_ok=True)


def resolve_shared_profile_source(
    profile_tq: Path, settings_folder: Path, requested: SharedSettingsSource | None,
    *, account_id: int, character_id: int, evejs_root: str, custom_folder: bool = False,
    target_identity: str | None = None, initialize: bool = True,
) -> SharedSettingsSource:
    """Persist the first launched character as the automatic managed source."""
    from .overview_patch import is_eve_client_running

    if requested is not None:
        return requested
    if custom_folder:
        raise SharedSettingsError("Choose a character profile for the current EveJS server.")
    identity = {"settingsFolder": os.path.normcase(str(settings_folder.resolve())),
                "serverRoot": os.path.normcase(str(Path(evejs_root).resolve())),
                "targetIdentity": target_identity}
    storage = profile_tq.parent.parent.parent / "SharedProfiles"
    with platform.serialize_evejs_client_trust_and_spawn():
        _safe_storage(storage)
        marker = storage / ("automatic-source-" + _digest(_json_bytes(identity)) + ".json")
        if marker.exists():
            try:
                data = json.loads(_read_file(marker))
                source = SharedSettingsSource.from_config(data.get("source"))
                if data.get("schemaVersion") != 1 or data.get("identity") != identity or source is None:
                    raise ValueError("invalid automatic source")
                return source
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise SharedSettingsError("The saved shared character profile could not be verified.") from exc
        if not initialize:
            raise SharedSettingsError("Choose a character profile for the current EveJS server.")
        if is_eve_client_running():
            raise SharedSettingsError("Close EVE clients before applying the selected shared character profile.")
        source = SharedSettingsSource.from_config({"username": profile_tq.parent.name,
            "account_id": account_id, "character_id": character_id, "evejs_root": evejs_root})
        if source is None:
            raise SharedSettingsError("Choose a character profile for the current EveJS server.")
        _atomic_bytes(marker, _json_bytes({"schemaVersion": 1, "identity": identity, "source": source.as_dict()}))
        return source


def _replace_settings(changes: dict[Path, bytes | None], storage: Path) -> tuple[str, ...]:
    """Back up every changed file before a bounded, recoverable replacement."""
    before: dict[Path, bytes | None] = {}
    for path, wanted in changes.items():
        if _path_entry_exists(path) and (not path.is_file() or _is_directory_link(path)):
            raise SharedSettingsError("The shared character settings could not be verified.")
        existing = _read_file(path) if path.exists() else None
        if existing != wanted:
            before[path] = existing
    if not before:
        return ()
    backups = storage / "backups"
    _safe_storage(backups)
    receipt = {"schemaVersion": 1, "files": []}
    for path, data in before.items():
        digest = _digest(data) if data is not None else None
        if data is not None:
            backup = backups / (digest + ".bin")
            if backup.exists():
                if _read_file(backup) != data:
                    raise SharedSettingsError("The shared profile backup could not be verified.")
            else:
                _atomic_bytes(backup, data)
        receipt["files"].append({"path": str(path), "beforeSha256": digest,
                                 "afterSha256": _digest(changes[path]) if changes[path] is not None else None})
    _atomic_bytes(backups / (uuid.uuid4().hex + ".json"), _json_bytes(receipt))
    applied: list[Path] = []
    try:
        for path in before:
            wanted = changes[path]
            if wanted is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_bytes(path, wanted)
            applied.append(path)
        for path in applied:
            wanted = changes[path]
            if (path.exists() if wanted is None else _read_file(path) != wanted):
                raise SharedSettingsError("The shared character settings could not be verified.")
    except Exception:
        for path in reversed(applied):
            if before[path] is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_bytes(path, before[path])
        raise
    return tuple(path.name for path in applied)


def _load_snapshot(storage: Path, identity: dict) -> tuple[dict[str, bytes], dict] | None:
    manifest = storage / "active.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(_read_file(manifest))
        revision = data["revision"]
        hashes = data["hashes"]
        allowed = {"character.dat", "account.dat", "core_public__.yaml", "core_public__.dat"}
        if (data.get("schemaVersion") != 1 or data.get("identity") != identity
                or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision)
                or not isinstance(hashes, dict) or not {"character.dat", "account.dat"} <= hashes.keys()
                or not hashes.keys() <= allowed or len(hashes) > 3
                or _digest(_json_bytes(hashes)) != revision):
            raise ValueError("invalid snapshot")
        folder = storage / revision
        if not folder.is_dir() or _is_directory_link(folder):
            raise ValueError("redirected snapshot")
        files = {name: _read_file(folder / name) for name in hashes}
        if any(_digest(files[name]) != digest for name, digest in hashes.items()):
            raise ValueError("snapshot hash mismatch")
        return files, data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SharedSettingsError("The saved shared character profile could not be verified.") from exc


def prepare_shared_character_profile(
    profile_tq: Path, settings_folder: Path, source: SharedSettingsSource,
    *, account_id: int, character_id: int, evejs_root: str, custom_folder: bool = False,
    target_identity: str | None = None, automatic_source: bool = False,
    destination_folder: Path | None = None, import_folder: str = "",
) -> tuple[str, ...]:
    """Apply a stable source snapshot before this account's client is spawned.

    Refresh the source only with every EVE client closed. Later group launches
    reuse that immutable snapshot and modify only the account being launched.
    """
    from .overview_patch import is_eve_client_running

    if (SharedSettingsSource.from_config(source.as_dict()) != source
            or os.path.normcase(str(Path(source.evejs_root).resolve())) != os.path.normcase(str(Path(evejs_root).resolve()))
            or any(type(identifier) is not int or not 0 < identifier < 2**63 for identifier in (account_id, character_id))):
        raise SharedSettingsError("Choose a character profile for the current EveJS server.")
    folder = settings_folder.resolve(strict=True)
    destination = destination_folder.resolve(strict=True) if destination_folder is not None else folder
    identity = {"source": source.as_dict(), "settingsFolder": os.path.normcase(str(folder)),
                "serverRoot": os.path.normcase(str(Path(evejs_root).resolve())),
                "targetIdentity": target_identity}
    storage = profile_tq.parent.parent.parent / "SharedProfiles" / _digest(_json_bytes(identity))
    with platform.serialize_evejs_client_trust_and_spawn():
        _safe_storage(storage)
        snapshot = _load_snapshot(storage, identity)
        running = is_eve_client_running()
        if running and snapshot is None:
            raise SharedSettingsError("Close EVE clients before applying the selected shared character profile.")
        fresh = snapshot is None
        if not running:
            source_char = f"core_char_{source.character_id}.dat"
            source_user = f"core_user_{source.account_id}.dat"
            source_folder = folder
            if fresh and not all((folder / name).is_file() for name in (source_char, source_user)):
                source_profile = profile_tq.parent.parent / source.username / "tq"
                source_folder = platform.get_eve_settings_path(str(source_profile)).resolve()
                if import_folder:
                    legacy = Path(import_folder).resolve()
                    if all((legacy / name).is_file() for name in (source_char, source_user)):
                        source_folder = legacy
            source_exists = all((source_folder / name).is_file() for name in (source_char, source_user))
            if not source_exists and not (fresh and automatic_source):
                raise SharedSettingsError("Launch the selected source character once in Separate mode, then close EVE and select its shared profile.")
            files = {"character.dat": _read_file(source_folder / source_char) if (source_folder / source_char).is_file() else _EMPTY_SETTINGS,
                     "account.dat": _read_file(source_folder / source_user) if (source_folder / source_user).is_file() else _EMPTY_SETTINGS}
            if any(not files[name].startswith(b"\x7e") for name in ("character.dat", "account.dat")):
                raise SharedSettingsError("The selected character profile is unavailable or unsupported.")
            public = next((name for name in ("core_public__.yaml", "core_public__.dat")
                           if (source_folder / name).is_file()), None)
            if public:
                files[public] = _read_file(source_folder / public)
            hashes = {name: _digest(data) for name, data in files.items()}
            revision = _digest(_json_bytes(hashes))
            saved = storage / revision
            _safe_storage(saved)
            for name, data in files.items():
                path = saved / name
                if path.exists():
                    if _read_file(path) != data:
                        raise SharedSettingsError("The saved shared character profile could not be verified.")
                else:
                    _atomic_bytes(path, data)
            manifest = {"schemaVersion": 1, "identity": identity, "revision": revision, "hashes": hashes}
        else:
            files, manifest = snapshot
            source_folder = folder
        changes = {destination / f"core_char_{character_id}.dat": files["character.dat"],
                   destination / f"core_user_{account_id}.dat": files["account.dat"]}
        # Only the source's own client writes its persistent main copy. Never
        # reset it to an older snapshot when it relaunches beside other clients.
        if destination == folder and not fresh and running and (account_id, character_id) == (source.account_id, source.character_id):
            changes = {}
        if destination != folder or (fresh and source_folder != folder):
            for name in ("core_public__.yaml", "core_public__.dat"):
                if name in files:
                    changes[destination / name] = files[name]
                elif any(key.startswith("core_public__.") for key in files) and (destination / name).exists():
                    changes[destination / name] = None
        if fresh and source_folder != folder:
            # Preserve the chosen source in the managed folder, so changes it
            # saves there become the next snapshot after all clients close.
            changes[folder / f"core_char_{source.character_id}.dat"] = files["character.dat"]
            changes[folder / f"core_user_{source.account_id}.dat"] = files["account.dat"]
            for name in ("core_public__.yaml", "core_public__.dat"):
                if name in files:
                    changes[folder / name] = files[name]
        changed = _replace_settings(changes, storage)
        if not running:
            _atomic_bytes(storage / "active.json", _json_bytes(manifest))
        return changed


def shared_profile_main_folder(
    profile_tq: Path, source: SharedSettingsSource, *, evejs_root: str,
    target_identity: str | None = None,
) -> Path:
    """Resolve the same persistent source folder for launches and mod settings."""
    if (SharedSettingsSource.from_config(source.as_dict()) != source
            or os.path.normcase(str(Path(source.evejs_root).resolve())) != os.path.normcase(str(Path(evejs_root).resolve()))):
        raise SharedSettingsError("Choose a character profile for the current EveJS server.")
    identity = {"source": source.as_dict(), "serverRoot": os.path.normcase(str(Path(evejs_root).resolve())),
                "targetIdentity": target_identity}
    return profile_tq.parent.parent.parent / "SharedProfiles" / _digest(_json_bytes(identity)) / "main"


def prepare_shared_profile_launch(
    profile_tq: Path, requested: SharedSettingsSource | None,
    *, account_id: int, character_id: int, evejs_root: str, host: str = "127.0.0.1",
    target_identity: str | None = None, legacy_folder: str = "",
) -> SharedClientSettings:
    """Give the source a persistent profile and each follower a fresh copy.

    A private named profile also isolates public preferences and other
    characters on the source's account. No follower can write the baseline.
    """
    with platform.serialize_evejs_client_trust_and_spawn():
        managed = profile_tq.parent.parent.parent / "SharedSettings"
        _safe_storage(managed)
        source = resolve_shared_profile_source(profile_tq, managed, requested, account_id=account_id,
            character_id=character_id, evejs_root=evejs_root, target_identity=target_identity)
        main = shared_profile_main_folder(profile_tq, source, evejs_root=evejs_root,
            target_identity=target_identity)
        storage = main.parent
        _safe_storage(main)
        is_source = (source.account_id, source.character_id) == (account_id, character_id)
        destination = main if is_source else storage / "sessions" / uuid.uuid4().hex
        _safe_storage(destination)
        prepare_shared_character_profile(profile_tq, main, source, account_id=account_id,
            character_id=character_id, evejs_root=evejs_root, target_identity=target_identity,
            automatic_source=requested is None, destination_folder=destination,
            import_folder=legacy_folder or str(managed))
        # A different destination gets a different alias, avoiding live rebinding
        # when other accounts (or another character on this account) are running.
        name = "EveJSLauncherShared_" + _digest(os.path.normcase(str(destination)).encode("utf-8"))[:16]
        return _prepare_shared_settings_binding(profile_tq, destination, host=host, profile_name=name)
