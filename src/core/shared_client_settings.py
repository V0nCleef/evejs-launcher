"""Opt-in shared EVE settings, without moving the original account settings."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import zipfile

from . import platform
from .profiles import _atomic_write_text, _is_directory_link, _path_entry_exists

SHARED_PROFILE_NAME = "EveJSLauncherShared"
SHARED_PROFILE_ARGUMENT = "/settingsprofile=" + SHARED_PROFILE_NAME
_EVE_PREFS_SHA256 = "c79f54ef7882f755c53219343a402b6a7f18c89d82f062ce49565338ab3751e1"


def _create_junction(target: Path, alias: Path) -> None:
    """Create an unprivileged NTFS junction without a command interpreter."""
    import ctypes
    from ctypes import wintypes as W
    import struct

    substitute = ("\\??\\" + str(target)).encode("utf-16-le")
    display = str(target).encode("utf-16-le")
    paths = substitute + b"\x00\x00" + display + b"\x00\x00"
    payload = struct.pack("<IHHHHHH", 0xA0000003, 8 + len(paths), 0,
                          0, len(substitute), len(substitute) + 2, len(display)) + paths
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE)
    kernel.CreateFileW.restype = W.HANDLE
    kernel.DeviceIoControl.argtypes = (W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD,
                                       ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p)
    kernel.DeviceIoControl.restype = W.BOOL
    kernel.CloseHandle.argtypes = (W.HANDLE,)
    alias.mkdir()
    handle = kernel.CreateFileW(str(alias), 0x40000000, 7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        alias.rmdir()
        raise ctypes.WinError(error)
    try:
        returned = W.DWORD()
        buffer = ctypes.create_string_buffer(payload)
        if not kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(payload), None, 0,
                                       ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        kernel.CloseHandle(handle)
        alias.rmdir()
        raise
    else:
        kernel.CloseHandle(handle)


def _remove_junction(alias: Path) -> None:
    if not _is_directory_link(alias):
        raise SharedSettingsError("The shared settings link is not owned by the launcher.")
    alias.rmdir()


class SharedSettingsError(ValueError):
    """A shared-settings selection cannot be used safely."""


@dataclass(frozen=True)
class SharedClientSettings:
    folder: Path
    alias: Path
    server_host: str = "127.0.0.1"
    profile_name: str = SHARED_PROFILE_NAME

    @property
    def argument(self) -> str:
        return "/settingsprofile=" + self.profile_name


def validate_shared_folder(value: str | Path) -> Path:
    """Validate a selected settings directory without creating or editing it."""
    text = str(value).strip()
    if not text or any(c in text for c in '\r\n\x00"') or not Path(text).is_absolute():
        raise SharedSettingsError("Choose an absolute shared settings folder.")
    try:
        folder = Path(text).resolve(strict=True)
        if folder.drive.startswith("\\\\"):
            raise SharedSettingsError("Shared settings require a local folder.")
        if not folder.is_dir():
            raise OSError("not a directory")
        # Accept the settings directory itself, never guess a parent folder.
        entries = folder.iterdir()
        first = next(entries, None)
        def settings_file(p: Path) -> bool:
            return (p.name.lower() == "prefs.ini" or p.name.lower().startswith("core_")) and p.is_file()
        if first is not None and not settings_file(first) and not any(settings_file(p) for p in entries):
            raise SharedSettingsError("Choose an EVE settings folder or an empty folder.")
    except OSError as exc:
        raise SharedSettingsError("Choose an existing shared settings folder.") from exc
    return folder


def resolve_shared_settings_folder(
    custom_folder: str | Path = "", *, profiles_root: Path | None = None,
    create: bool = False,
) -> Path:
    """Resolve an optional override or the launcher's managed settings folder.

    Merely saving Shared mode does not create anything. Launch preparation (or
    editing a mod's shared settings) creates the managed directory on demand.
    Use the captured profiles root so workers never consult mutable config.
    """
    if str(custom_folder).strip():
        return validate_shared_folder(custom_folder)
    from .. import config

    parent = Path(profiles_root).parent if profiles_root is not None else config.CONFIG_DIR
    folder = parent / "SharedSettings"
    try:
        if not folder.is_absolute() or folder.drive.startswith("\\\\"):
            raise OSError("managed settings require a local absolute path")
        if _path_entry_exists(folder) and _is_directory_link(folder):
            raise OSError("managed settings directory is redirected")
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return validate_shared_folder(folder) if folder.exists() else folder
    except (OSError, SharedSettingsError) as exc:
        raise SharedSettingsError(
            "The automatic shared settings folder is unavailable. Choose a custom folder in Advanced."
        ) from exc


def require_shared_settings_support(client: str | Path) -> None:
    """Gate the named-profile contract against the inspected client code."""
    try:
        with zipfile.ZipFile(Path(client) / "code.ccp") as archive:
            digest = hashlib.sha256(archive.read("eveprefs/__init__.pyj")).hexdigest()
        if digest == _EVE_PREFS_SHA256:
            return
    except (OSError, KeyError, zipfile.BadZipFile):
        pass
    raise SharedSettingsError("Shared settings are not supported by this EVE client build.")


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _read_marker(marker: Path, alias: Path) -> dict | None:
    if not marker.exists():
        return None
    try:
        if marker.stat().st_size > 16384:
            raise ValueError("too large")
        data = json.loads(marker.read_text(encoding="utf-8"))
        if (not isinstance(data, dict)
                or set(data) != {"schemaVersion", "alias", "target", "previousTarget"}
                or data["schemaVersion"] != 1
                or data["alias"] != str(alias)
                or not isinstance(data["target"], str)
                or not Path(data["target"]).is_absolute()
                or (data["previousTarget"] is not None and (
                    not isinstance(data["previousTarget"], str)
                    or not Path(data["previousTarget"]).is_absolute()))):
            raise ValueError("invalid marker")
        return data
    except (OSError, ValueError, TypeError) as exc:
        raise SharedSettingsError("The shared settings binding could not be verified.") from exc


def _write_marker(marker: Path, data: dict) -> None:
    _atomic_write_text(marker, json.dumps(data, indent=2) + "\n")


def shared_settings_alias(profile_tq: Path, host: str = "127.0.0.1", *, profile_name: str = SHARED_PROFILE_NAME) -> tuple[Path, str]:
    """Mirror the verified client's path formula, including its server suffix."""
    if not re.fullmatch(r"EveJSLauncherShared(?:_[0-9a-f]{16})?", profile_name):
        raise SharedSettingsError("The shared settings binding could not be verified.")
    host = "127.0.0.1" if host.casefold() == "localhost" else host
    try:
        if not ipaddress.IPv4Address(host).is_loopback:
            raise ValueError("not loopback")
    except ValueError as exc:
        # This build parses /server by splitting at ':', so IPv6 cannot be
        # supplied using its per-process override. Never silently reroute it.
        raise SharedSettingsError("Shared settings require an IPv4 loopback Game endpoint.") from exc
    key = os.path.normpath(str(profile_tq).lower())
    key = key.replace("/", "_").replace("\\", "_").replace(":", "").replace(" ", "_")
    base = platform.get_eve_settings_path(str(profile_tq)).parent.parent
    return base / (key + "_" + host) / ("settings_" + profile_name), host


def binding_marker(profile_tq: Path, alias: Path) -> Path:
    identity = hashlib.sha256(str(alias).encode("utf-8")).hexdigest()[:16]
    return profile_tq.parent / ("shared-settings-binding-" + identity + ".json")


def prepare_shared_settings(profile_tq: Path, folder: str | Path = "", *, host: str = "127.0.0.1") -> SharedClientSettings:
    """Prepare existing account bindings before the first shared client starts.

    A folder selection applies to every account. Rebinding only the account
    being launched leaves the others on the old target, where the live-client
    guard correctly refuses to rebind them after this process has started.
    Keep the same cross-process lease through the caller's eventual spawn.
    """
    from .overview_patch import is_eve_client_running

    with platform.serialize_evejs_client_trust_and_spawn():
        result = _prepare_shared_settings_binding(profile_tq, folder, host=host)
        if is_eve_client_running():
            return result
        for profile in sorted(profile_tq.parent.parent.iterdir()):
            if profile == profile_tq.parent or not profile.is_dir() or _is_directory_link(profile):
                continue
            peer_tq = profile / "tq"
            if not peer_tq.is_dir():
                continue
            peer_alias, _ = shared_settings_alias(peer_tq, result.server_host)
            # Only previously enrolled accounts at this endpoint are ours to
            # reconcile. The binding helper verifies the journal and junction.
            if binding_marker(peer_tq, peer_alias).exists():
                _prepare_shared_settings_binding(peer_tq, result.folder, host=result.server_host)
        return result


def _prepare_shared_settings_binding(profile_tq: Path, folder: str | Path = "", *, host: str = "127.0.0.1", profile_name: str = SHARED_PROFILE_NAME) -> SharedClientSettings:
    """Bind only our named profile; preserve all existing default settings.

    The journal records both targets during a change, allowing interrupted link
    creation/rebinding to resume. Removing a junction never removes its target.
    """
    from .overview_patch import is_eve_client_running

    target = resolve_shared_settings_folder(folder, profiles_root=profile_tq.parent.parent, create=True)
    alias, host = shared_settings_alias(profile_tq, host, profile_name=profile_name)
    if target == alias or alias in target.parents or target in alias.parents:
        raise SharedSettingsError("The shared settings folder cannot point back into its launcher link.")
    marker = binding_marker(profile_tq, alias)
    with platform.serialize_evejs_client_trust_and_spawn():
        data = _read_marker(marker, alias)
        exists = _path_entry_exists(alias)
        previous = None
        if exists:
            if not _is_directory_link(alias) or data is None:
                raise SharedSettingsError("The shared settings link is not owned by the launcher.")
            known = [Path(data["target"])]
            if data["previousTarget"]:
                known.append(Path(data["previousTarget"]))
            previous = next((p for p in known if _same_path(alias, p)), None)
            if previous is None:
                raise SharedSettingsError("The shared settings binding could not be verified.")
            if _same_path(alias, target):
                return SharedClientSettings(target, alias, host, profile_name)
        changing = previous is not None or (data is not None and not _same_path(Path(data["target"]), target))
        if changing and is_eve_client_running():
            raise SharedSettingsError("Close EVE clients before changing the shared settings folder.")
        alias.parent.mkdir(parents=True, exist_ok=True)
        if os.path.normcase(str(alias.parent.resolve())) != os.path.normcase(str(alias.parent.absolute())):
            raise SharedSettingsError("The shared settings binding could not be verified.")
        pending = {"schemaVersion": 1, "alias": str(alias), "target": str(target),
                   "previousTarget": str(previous) if previous is not None else None}
        _write_marker(marker, pending)
        try:
            if exists:
                _remove_junction(alias)
            _create_junction(target, alias)
            if not _is_directory_link(alias) or not _same_path(alias, target):
                raise SharedSettingsError("The shared settings binding could not be verified.")
            _write_marker(marker, {**pending, "previousTarget": None})
        except Exception:
            # Restore only the junction we just created, never a settings tree.
            if _path_entry_exists(alias) and _is_directory_link(alias) and _same_path(alias, target):
                _remove_junction(alias)
            if previous is not None and not _path_entry_exists(alias):
                _create_junction(previous, alias)
            if data is not None:
                _write_marker(marker, data)
            else:
                marker.unlink(missing_ok=True)
            raise
    return SharedClientSettings(target, alias, host, profile_name)
