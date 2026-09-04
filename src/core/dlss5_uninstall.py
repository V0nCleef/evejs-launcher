"""Recoverable, fail-closed removal of the fixed DLSS5 client package.

Archive before Restore: the rollback kit survives every failure and cannot be
silently rediscovered by Ensure. No descriptor commands, Force, or process kills.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from . import dlss5
from . import platform as platform_api
from .mod_manifest import Mod


@dataclass(frozen=True)
class DLSS5UninstallRequest:
    evejs_root: Path
    client_root: Path
    package_path: Path
    manager_sha256: str


@dataclass(frozen=True)
class DLSS5UninstallResult:
    request: DLSS5UninstallRequest
    success: bool
    message: str
    archive_path: Path | None = None


def uninstall_dlss5_client_mod(request: DLSS5UninstallRequest) -> DLSS5UninstallResult:
    """Revalidate a UI snapshot, archive its kit, and restore only its journal.

    Failures before archival do not touch the client. Failures afterwards retain
    the archived manager and original state root for explicit manual recovery.
    Restored receipts are archive-only after verification; never clean up a
    shared client using a stale terminal journal from another installation.
    """
    archive_path = None
    try:
        with platform_api.serialize_evejs_client_trust_and_spawn():
            root, client, package = _validate_request(request)
            state = root / "_local/dlss5/install"
            receipt = _read_receipt(root, client, state)
            terminal = receipt["status"] in {"restored", "rolledBack"}
            _validate_receipt(root, client, state, receipt, clean=terminal)
            pids = _running_client_pids()
            if pids:
                raise dlss5.DLSS5ClientModError(
                    "Close all EVE clients before uninstalling DLSS5. Running PIDs: "
                    + ", ".join(map(str, pids))
                )
            # The lock serializes other launcher instances through their spawn.
            # The trusted manager additionally locks the physical shared client.
            _validate_request(request)
            if _read_receipt(root, client, state) != receipt:
                raise dlss5.DLSS5ClientModError("The install receipt changed; refresh Mods and retry.")
            archive_path = _archive_package(root, package.path)
            archived = replace(
                package, path=archive_path,
                manifest_path=archive_path / dlss5._CLIENT_MANIFEST_NAME,
                manager_path=archive_path / dlss5._CLIENT_MANAGER_RELATIVE_PATH,
            )
            _plain_tree(archive_path)
            if dlss5._sha256_file(archived.manager_path) != request.manager_sha256.upper():
                raise dlss5.DLSS5ClientModError("The archived manager no longer matches the approved snapshot.")
            if _running_client_pids():
                raise dlss5.DLSS5ClientModError("An EVE client appeared during uninstall. Close it before recovery.")
            if not terminal:
                dlss5._run_dlss5_manager(
                    archived, workspace_root=root.parent, evejs_root=root,
                    client_root=client, state_root=state, action="Restore",
                )
            restored = _read_receipt(root, client, state)
            if restored["status"] not in {"restored", "rolledBack"}:
                raise dlss5.DLSS5ClientModError("The manager did not record a completed restore.")
            _validate_receipt(root, client, state, restored, clean=True)
            return DLSS5UninstallResult(
                request, True,
                "DLSS5 was uninstalled. Original client/config state was verified. "
                f"The recoverable package is at {archive_path}. Backups, cache, and the install receipt were retained.",
                archive_path,
            )
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as error:
        detail = str(error) or type(error).__name__
        if archive_path is not None:
            detail = (
                "DLSS5 uninstall is not complete. The package remains outside automatic mod discovery "
                f"at {archive_path}. No automatic reinstall will be attempted. "
                f"Keep this recovery kit and {request.evejs_root}/_local/dlss5/install (receipt/backups/cache). "
                "Use the archived matching manager with the original EveJS/client/state roots for recovery; "
                "do not copy the kit back into mods before recovery.\n\n" + detail
            )
        return DLSS5UninstallResult(request, False, detail, archive_path)


def _plain_directory(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise dlss5.DLSS5ClientModError("Uninstall paths must be absolute and unambiguous.")
    for ancestor in reversed((path, *path.parents)):
        # Ancestors need metadata validation, not open-directory access for
        # GetFinalPathNameByHandle (which restricted Windows tokens can lack).
        if dlss5._is_reparse_point(ancestor) or not ancestor.is_dir():
            raise dlss5.DLSS5ClientModError(f"Uninstall path must be an unlinked directory: {ancestor}")
    return path.resolve(strict=True)


def _plain_file(path: Path, boundary: Path) -> None:
    _plain_directory(path.parent)
    dlss5._require_plain_file(path, boundary, "Uninstall file")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise dlss5.DLSS5ClientModError(f"Uninstall requires a regular file: {path}")


def _plain_tree(root: Path) -> None:
    _plain_directory(root)
    def fail_walk(error: OSError) -> None:
        raise error
    for current, directories, files in os.walk(root, followlinks=False, onerror=fail_walk):
        parent = Path(current)
        for name in directories:
            dlss5._require_plain_directory(parent / name, root, "Uninstall tree directory")
        for name in files:
            _plain_file(parent / name, root)


def _validate_request(request: DLSS5UninstallRequest) -> tuple[Path, Path, Mod]:
    root = _plain_directory(request.evejs_root)
    client = _plain_directory(request.client_root)
    _plain_directory(client / "bin64")
    expected = root / "mods/DLSS5"
    dlss5._require_receipt_path(str(request.package_path), expected, "selected package")
    _plain_tree(expected)
    package = dlss5.discover_dlss5_client_mod(root)
    digest = dlss5._require_sha256(request.manager_sha256, "selected manager hash")
    if (package is None or not package.valid or package.path != expected
            or package.manager_sha256 != digest or digest not in dlss5._TRUSTED_MANAGER_SHA256):
        raise dlss5.DLSS5ClientModError("The DLSS5 package or trusted manager changed. Refresh Mods before uninstalling.")
    return root, client, package


def _read_receipt(root: Path, client: Path, state: Path) -> dict:
    if not dlss5._path_entry_exists(state / "active-install.json"):
        raise dlss5.DLSS5ClientModError("No install receipt exists for this root. Use the matching installer for manual recovery.")
    _plain_tree(state)
    path = state / "active-install.json"
    _plain_file(path, state)
    receipt = dlss5._read_json_object(path, maximum_bytes=dlss5._STANDALONE_RECEIPT_MAX_BYTES, label="DLSS5 uninstall receipt")
    if type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] != 4:
        raise dlss5.DLSS5ClientModError("Only explicit schema-4 install receipts support launcher uninstall.")
    for key, expected in (("workspaceRoot", root.parent), ("evejsRoot", root), ("clientRoot", client), ("stateRoot", state)):
        dlss5._require_receipt_path(receipt.get(key), expected, key)
    if receipt.get("status") not in {"installed", "restored", "rolledBack"}:
        raise dlss5.DLSS5ClientModError("Partial or unknown install receipt: use the matching installer for manual recovery.")
    if receipt.get("stateDirectory") != "state" or receipt.get("profile") != "DLSS5":
        raise dlss5.DLSS5ClientModError("Unsupported DLSS5 receipt state/profile.")
    version = receipt.get("integrationVersion")
    if type(version) is not str or version not in dlss5._STANDALONE_PAYLOADS_BY_VERSION:
        raise dlss5.DLSS5ClientModError("This installation version is not a reviewed uninstall contract.")
    return receipt


def _relative(value: object) -> Path:
    if type(value) is not str or not value or value != value.strip():
        raise dlss5.DLSS5ClientModError("Invalid relative path in uninstall receipt.")
    text = value.replace("\\", "/")
    if text.startswith("/") or any(part in {"", ".", ".."} or ":" in part for part in text.split("/")):
        raise dlss5.DLSS5ClientModError("Uninstall receipt path escapes its fixed boundary.")
    return Path(text)


def _hash_matches(path: Path, boundary: Path, expected: object) -> None:
    _plain_file(path, boundary)
    digest = dlss5._require_sha256(expected, path.name)
    if dlss5._sha256_file(path) != digest:
        raise dlss5.DLSS5ClientModError(f"Uninstall stopped on changed or mismatched file: {path}")


def _validate_receipt(root: Path, client: Path, state: Path, receipt: dict, *, clean: bool) -> None:
    payload = dlss5._STANDALONE_PAYLOADS_BY_VERSION[receipt["integrationVersion"]]
    relative_backup = _relative(receipt.get("backupDirectory"))
    if (len(relative_backup.parts) != 2 or relative_backup.parts[0] != "backups"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", relative_backup.name)
            or relative_backup.name.endswith(".")):
        raise dlss5.DLSS5ClientModError("Uninstall backup directory must be the recorded backups child.")
    backup = _plain_directory(state / relative_backup)
    operations = receipt.get("operations")
    if type(operations) is not list or len(operations) != len(payload):
        raise dlss5.DLSS5ClientModError("Uninstall receipt does not cover the exact reviewed payload.")
    seen = set()
    for operation in operations:
        if type(operation) is not dict:
            raise dlss5.DLSS5ClientModError("Malformed uninstall operation.")
        name = _relative(operation.get("destination")).as_posix()
        if name not in payload or name in seen:
            raise dlss5.DLSS5ClientModError("Unknown or duplicate uninstall target.")
        seen.add(name)
        _relative(operation.get("source"))
        size, expected = payload[name]
        if (type(operation.get("installedBytes")) is not int or operation["installedBytes"] != size
                or dlss5._require_sha256(operation.get("installedSha256"), name) != expected):
            raise dlss5.DLSS5ClientModError("Uninstall operation does not match its reviewed version pin.")
        target = client / name
        _plain_directory(target.parent)
        if operation.get("kind") == "replace":
            backup_name = _relative(operation.get("backup"))
            if backup_name.as_posix() != "client/" + name:
                raise dlss5.DLSS5ClientModError("Uninstall backup does not match the exact client target.")
            _hash_matches(backup / backup_name, backup, operation.get("originalSha256"))
            _hash_matches(target, client, operation.get("originalSha256") if clean else expected)
        elif operation.get("kind") == "add" and operation.get("backup") is None:
            if clean:
                if dlss5._path_entry_exists(target):
                    raise dlss5.DLSS5ClientModError(f"A DLSS5 payload remains in the shared client: {name}")
            else:
                _hash_matches(target, client, expected)
        else:
            raise dlss5.DLSS5ClientModError("Unsupported uninstall operation kind.")
    executable = receipt.get("executable")
    if (type(executable) is not dict or executable.get("modified") is not False
            or _relative(executable.get("path")).as_posix() != "bin64/exefile.exe"
            or executable.get("sha256") != dlss5._STANDALONE_EXE[1]):
        raise dlss5.DLSS5ClientModError("Uninstall executable identity is not the reviewed original.")
    _hash_matches(client / "bin64/exefile.exe", client, dlss5._STANDALONE_EXE[1])
    config = receipt.get("config")
    if type(config) is not dict or _relative(config.get("backup")).as_posix() != "config/EvEJSConfig.bat":
        raise dlss5.DLSS5ClientModError("Invalid uninstall config backup.")
    config_path = root / "tools/ClientSETUP/scripts/EvEJSConfig.bat"
    dlss5._require_receipt_path(config.get("path"), config_path, "uninstall config")
    _hash_matches(backup / "config/EvEJSConfig.bat", backup, config.get("originalSha256"))
    _hash_matches(config_path, root, config.get("originalSha256") if clean else config.get("installedSha256"))
    if clean and dlss5.find_dlss5_launch_environment(str(root), client):
        raise dlss5.DLSS5ClientModError("The DLSS5 launch marker remains enabled after Restore.")
    _validate_reshade(client, backup, receipt.get("reshadeConfig"), clean=clean)


def _validate_reshade(client: Path, backup: Path, record: object, *, clean: bool) -> None:
    if type(record) is not dict or type(record.get("originalExists")) is not bool:
        raise dlss5.DLSS5ClientModError("Uninstall requires a ReShade ownership record.")
    config = client / "bin64/ReShade.ini"
    if dlss5._path_entry_exists(config):
        _plain_file(config, client)
    if not record["originalExists"]:
        if clean and dlss5._path_entry_exists(config):
            raise dlss5.DLSS5ClientModError("Generated ReShade.ini remains active after Restore.")
        return
    original = backup / _relative(record.get("backup"))
    if not original.is_relative_to(backup):
        raise dlss5.DLSS5ClientModError("Invalid ReShade backup boundary.")
    _hash_matches(original, backup, record.get("originalSha256"))
    if not clean:
        return
    _plain_file(config, client)
    original_text = dlss5._read_standalone_ini(original)
    current_text = dlss5._read_standalone_ini(config)
    for section, key in (("ADDON", "LoadFromDllMain"), ("RenoDX.DLSS5", "EnableHooks"), ("RenoDX.DLSS5", "NeuralUplift")):
        if dlss5._get_ini_value(original_text, section, key) != dlss5._get_ini_value(current_text, section, key):
            raise dlss5.DLSS5ClientModError("DLSS5-owned ReShade settings were not restored.")
    def section_lines(text):
        lines = text.splitlines()
        start, end = dlss5._find_ini_section(lines, "RenoDX.DLSS5")
        return [] if start is None else [line.rstrip() for line in lines[start:end] if line.strip()]
    if section_lines(original_text) != section_lines(current_text):
        raise dlss5.DLSS5ClientModError("The original RenoDX section was not restored.")


def _archive_package(root: Path, package: Path) -> Path:
    parent = root
    for part in ("_local", "dlss5", "uninstalled-packages"):
        parent = parent / part
        if not dlss5._path_entry_exists(parent):
            _plain_directory(parent.parent)
            parent.mkdir()
        _plain_directory(parent)
    _plain_tree(package)
    _plain_directory(root / "mods")
    if package != root / "mods/DLSS5":
        raise dlss5.DLSS5ClientModError("Refusing to archive a non-DLSS5 package path.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = parent / (stamp + "-" + uuid4().hex + "-DLSS5")
    if dlss5._path_entry_exists(destination):
        raise dlss5.DLSS5ClientModError("Uninstall archive destination already exists.")
    _plain_directory(parent)
    # Same-volume, no replacement on Windows. No recursive copy or deletion.
    package.rename(destination)
    return destination


def _running_client_pids() -> list[int]:
    """Fail-closed Windows snapshot; any EVE process blocks shared-client writes.

    Enumerating image names avoids treating inaccessible process paths or
    profile junctions as proof that the shared renderer is unused.
    """
    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                    ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for function in (api.Process32FirstW, api.Process32NextW):
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Could not verify that EVE clients are closed.")
    result = []
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        valid = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            if entry.name.casefold() == "exefile.exe":
                result.append(int(entry.pid))
            entry.size = ctypes.sizeof(entry)
            valid = api.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES is the sole successful end.
            raise OSError(ctypes.get_last_error(), "EVE process enumeration was incomplete.")
    finally:
        api.CloseHandle(snapshot)
    return sorted(result)
