"""Strict launcher-native removal for externally installed EveJS mods.

Runtime manifests remain declarative.  Executable authority comes from a
separate installer-written registration under the launcher's fixed registry
namespace, and every path/hash/root binding is revalidated before execution.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from typing import Mapping
import winreg

from .mod_activation_state import retire_removed_mod_activation
from .mod_lifecycle_lock import acquire_mod_lifecycle_lock
from .mod_manifest import Mod, scan_mods
from .mod_runtime_state import mod_contract_sha256


MANAGED_MOD_REGISTRY_ROOT = r"Software\EveJS Launcher\Managed Mods"
MANAGED_MOD_SCHEMA_VERSION = 2
INNO_USER_PROVIDER = "inno-user-v2"
SELF_DELETE_WAIT_SECONDS = 10.0
REMOVAL_INVENTORY_SCHEMA_VERSION = 1
MAX_REMOVAL_INVENTORY_BYTES = 1024 * 1024

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_APP_ID_PATTERN = re.compile(
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}\Z"
)
_EXPECTED_REGISTRY_TYPES = {
    "SchemaVersion": winreg.REG_DWORD,
    "Provider": winreg.REG_SZ,
    "AppId": winreg.REG_SZ,
    "ModId": winreg.REG_SZ,
    "DisplayName": winreg.REG_SZ,
    "PackageVersion": winreg.REG_SZ,
    "EveJSPath": winreg.REG_SZ,
    "BundleSha256": winreg.REG_SZ,
    "ExpandHelperSha256": winreg.REG_SZ,
    "CurrentPointerSha256": winreg.REG_SZ,
    "RemovalInventorySha256": winreg.REG_SZ,
    "UninstallerSha256": winreg.REG_SZ,
    "UninstallerDataSha256": winreg.REG_SZ,
    "SupportsPurgeState": winreg.REG_DWORD,
}
_STANDARD_UNINSTALL_AUTHORITY_FIELDS = (
    "UninstallString",
    "InstallLocation",
    "DisplayVersion",
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_REGISTRY_VIEWS = (
    getattr(winreg, "KEY_WOW64_64KEY", 0),
    getattr(winreg, "KEY_WOW64_32KEY", 0),
)


class ModManagementError(RuntimeError):
    """A launcher-managed install/removal contract is missing or unsafe."""


class ModNotManagedError(ModManagementError):
    """No installer enrollment exists for this mod and selected root."""


class ModDataPolicy(str, Enum):
    """Supported state handling for launcher-native removal."""

    KEEP = "keep"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class RemovalInventoryEntry:
    """One exact EveJS path expectation after managed removal."""

    relative_path: str
    expected_state: str
    expected_sha256: str | None = None


@dataclass(frozen=True)
class ManagedModRegistration:
    """One fully revalidated, immutable removal provider binding."""

    schema_version: int
    provider: str
    app_id: str
    mod_id: str
    display_name: str
    package_version: str
    evejs_root: Path
    activation_contract_sha256: str
    bundle_sha256: str
    expand_helper_sha256: str
    current_pointer_sha256: str
    removal_inventory_path: Path
    removal_inventory_sha256: str
    removal_inventory: tuple[RemovalInventoryEntry, ...]
    uninstaller_path: Path
    uninstaller_sha256: str
    uninstaller_data_path: Path
    uninstaller_data_sha256: str
    supports_purge_state: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "evejs_root", Path(self.evejs_root))
        object.__setattr__(self, "removal_inventory_path", Path(self.removal_inventory_path))
        object.__setattr__(self, "uninstaller_path", Path(self.uninstaller_path))
        object.__setattr__(self, "uninstaller_data_path", Path(self.uninstaller_data_path))


@dataclass(frozen=True)
class ManagedModRemovalRequest:
    """Exact removal request frozen by the GUI before its worker starts."""

    registration: ManagedModRegistration
    policy: ModDataPolicy


@dataclass(frozen=True)
class ManagedModRemovalResult:
    """Terminal launcher-native removal result."""

    request: ManagedModRemovalRequest
    success: bool
    message: str
    log_path: Path | None = None
    warning: str = ""


def read_managed_mod_registration(mod: Mod) -> ManagedModRegistration:
    """Read and revalidate the fixed installer enrollment for ``mod``."""

    if not isinstance(mod, Mod):
        raise TypeError("mod must be a Mod instance.")
    if not mod.valid:
        raise ModManagementError(
            f"Cannot manage removal for '{mod.name}': its runtime manifest is invalid."
        )
    if mod.evejs_root is None:
        raise ModManagementError(
            f"Cannot manage removal for '{mod.name}': it is not bound to an EveJS root."
        )
    registry_path = managed_mod_registry_path(mod.id)
    registrations = []
    for registry_view in dict.fromkeys(_REGISTRY_VIEWS):
        values = _read_registry_values(
            registry_path,
            registry_view=registry_view,
            unsupported_view_is_missing=True,
        )
        if values is not None:
            registrations.append((registry_view, values))
    if not registrations:
        raise ModNotManagedError(
            "This mod was not enrolled by a launcher-compatible installer. "
            "Run its matching Setup once to add or repair launcher removal support."
        )
    if len(registrations) > 1 and any(
        candidate_values != registrations[0][1]
        for _candidate_view, candidate_values in registrations[1:]
    ):
        raise ModManagementError(
            "The launcher removal registration has conflicting values across "
            "Windows registry views."
        )
    # HKCU\Software can be shared/aliased between the 32-bit and 64-bit
    # registry views.  Seeing the same typed values through both view flags is
    # therefore one enrollment, not two competing sources of authority.
    _registry_view, values = registrations[0]
    app_id = _require_registry_text(values, "AppId")
    if not _APP_ID_PATTERN.fullmatch(app_id):
        raise ModManagementError("The launcher removal AppId is invalid.")
    standard_registrations = []
    standard_path = _windows_uninstall_registry_path(app_id)
    for registry_view, _managed_values in registrations:
        standard_values = _read_registry_values(
            standard_path,
            registry_view=registry_view,
        )
        if standard_values is None:
            raise ModManagementError(
                "The standard Windows uninstall registration is missing from "
                "a registry view used by the launcher enrollment."
            )
        standard_registrations.append((registry_view, standard_values))
    first_standard_values = standard_registrations[0][1]
    first_standard_authority = tuple(
        first_standard_values.get(name)
        for name in _STANDARD_UNINSTALL_AUTHORITY_FIELDS
    )
    if any(
        tuple(candidate_values.get(name) for name in _STANDARD_UNINSTALL_AUTHORITY_FIELDS)
        != first_standard_authority
        for _candidate_view, candidate_values in standard_registrations[1:]
    ):
        raise ModManagementError(
            "The Windows uninstall authority has conflicting values across "
            "registry views."
        )
    return validate_managed_mod_registration(
        mod,
        values,
        standard_uninstall_values=first_standard_values,
    )


def validate_managed_mod_registration(
    mod: Mod,
    values: Mapping[str, tuple[object, int]],
    *,
    standard_uninstall_values: Mapping[str, tuple[object, int]] | None,
    local_app_data: str | Path | None = None,
) -> ManagedModRegistration:
    """Validate raw registry values without executing or parsing a command line.

    This pure boundary is intentionally public so tests can exercise hostile
    registrations without writing to the real Windows registry.
    """

    if not isinstance(mod, Mod):
        raise TypeError("mod must be a Mod instance.")
    if not mod.valid or mod.evejs_root is None:
        raise ModManagementError("The current mod contract is invalid or unbound.")
    if set(values) != set(_EXPECTED_REGISTRY_TYPES):
        raise ModManagementError("The launcher removal registration fields are not exact.")
    for name, expected_type in _EXPECTED_REGISTRY_TYPES.items():
        raw = values[name]
        if type(raw) is not tuple or len(raw) != 2 or raw[1] != expected_type:
            raise ModManagementError(
                f"The launcher removal registration value {name!r} has the wrong type."
            )

    schema_version = _require_registry_dword(values, "SchemaVersion")
    if schema_version != MANAGED_MOD_SCHEMA_VERSION:
        raise ModManagementError("Unsupported launcher removal registration schema.")
    provider = _require_registry_text(values, "Provider")
    if provider != INNO_USER_PROVIDER:
        raise ModManagementError("Unsupported launcher removal provider.")
    app_id = _require_registry_text(values, "AppId")
    if not _APP_ID_PATTERN.fullmatch(app_id):
        raise ModManagementError("The launcher removal AppId is invalid.")

    mod_id = _require_registry_text(values, "ModId")
    display_name = _require_registry_text(values, "DisplayName")
    package_version = _require_registry_text(values, "PackageVersion")
    if mod_id != mod.id or display_name != mod.name or package_version != mod.version:
        raise ModManagementError(
            "The launcher removal registration does not match this installed mod."
        )

    root = _canonical_directory(mod.evejs_root, "selected EveJS root")
    registered_root = _canonical_directory(
        _require_registry_text(values, "EveJSPath"),
        "registered EveJS root",
    )
    if _path_identity(root) != _path_identity(registered_root):
        raise ModManagementError(
            "The launcher removal registration belongs to a different EveJS root."
        )

    try:
        local_root_text = (
            os.fspath(local_app_data)
            if local_app_data is not None
            else os.environ.get("LOCALAPPDATA", "")
        )
    except TypeError as exc:
        raise ModManagementError("LOCALAPPDATA is unavailable.") from exc
    if type(local_root_text) is not str or not local_root_text.strip():
        raise ModManagementError("LOCALAPPDATA is unavailable.")
    local_root_value = Path(local_root_text)
    local_root = _canonical_directory(local_root_value, "LOCALAPPDATA")
    programs_root = local_root / "Programs"
    kit_root = programs_root / "EveJS Mods" / mod.id
    _require_safe_directory_chain(local_root, kit_root)
    uninstaller_path = kit_root / "unins000.exe"
    uninstaller_data_path = kit_root / "unins000.dat"
    bundle_path = kit_root / f"{mod.id}-package.zip"
    inventory_path = kit_root / f"{mod.id}-removal-inventory.json"
    helper_path = kit_root / "bootstrap" / "Expand-EmbeddedPackage.ps1"
    for path, label in (
        (uninstaller_path, "registered uninstaller"),
        (uninstaller_data_path, "registered uninstaller data"),
        (bundle_path, "persistent package bundle"),
        (inventory_path, "removal inventory"),
        (helper_path, "package verification helper"),
    ):
        _require_safe_regular_file(path, label)

    bundle_sha256 = _require_sha256(values, "BundleSha256")
    helper_sha256 = _require_sha256(values, "ExpandHelperSha256")
    inventory_sha256 = _require_sha256(values, "RemovalInventorySha256")
    uninstaller_sha256 = _require_sha256(values, "UninstallerSha256")
    uninstaller_data_sha256 = _require_sha256(values, "UninstallerDataSha256")
    if _sha256_stable_file(bundle_path) != bundle_sha256:
        raise ModManagementError("The persistent mod package hash does not match.")
    if _sha256_stable_file(helper_path) != helper_sha256:
        raise ModManagementError("The package verification helper hash does not match.")
    if _sha256_stable_file(uninstaller_path) != uninstaller_sha256:
        raise ModManagementError("The registered uninstaller hash does not match.")
    if _sha256_stable_file(uninstaller_data_path) != uninstaller_data_sha256:
        raise ModManagementError(
            "The registered uninstaller data hash does not match."
        )
    inventory_bytes = _read_stable_file_bytes(
        inventory_path,
        maximum=MAX_REMOVAL_INVENTORY_BYTES,
        label="removal inventory",
    )
    if hashlib.sha256(inventory_bytes).hexdigest() != inventory_sha256:
        raise ModManagementError("The removal inventory hash does not match.")
    removal_inventory = _parse_removal_inventory(
        inventory_bytes,
        expected_mod_id=mod.id,
    )
    try:
        manifest_relative = mod.manifest_path.resolve(strict=True).relative_to(root).as_posix()
    except (AttributeError, OSError, ValueError) as exc:
        raise ModManagementError(
            "The installed launcher manifest path is unavailable or unsafe."
        ) from exc
    if not any(
        entry.relative_path == manifest_relative
        and entry.expected_state == "absent"
        for entry in removal_inventory
    ):
        raise ModManagementError(
            "The removal inventory must prove the launcher manifest is absent "
            "after uninstall."
        )

    pointer_path = root / "_local" / mod.id / "install" / "current.json"
    _require_safe_directory_chain(root, pointer_path.parent)
    _require_safe_regular_file(pointer_path, "active install journal pointer")
    pointer_sha256 = _require_sha256(values, "CurrentPointerSha256")
    if _sha256_stable_file(pointer_path) != pointer_sha256:
        raise ModManagementError(
            "The active install journal changed after launcher enrollment."
        )

    if standard_uninstall_values is None:
        raise ModManagementError("The standard Windows uninstall registration is missing.")
    uninstall_string = _standard_registry_text(
        standard_uninstall_values,
        "UninstallString",
    )
    install_location = _standard_registry_text(
        standard_uninstall_values,
        "InstallLocation",
    )
    display_version = _standard_registry_text(
        standard_uninstall_values,
        "DisplayVersion",
    )
    if _parse_exact_executable(uninstall_string) != _path_identity(uninstaller_path):
        raise ModManagementError(
            "The Windows uninstall command is not the exact registered executable."
        )
    try:
        registered_location = Path(install_location).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ModManagementError("The Windows uninstall location is unavailable.") from exc
    if _path_identity(registered_location) != _path_identity(kit_root):
        raise ModManagementError("The Windows uninstall location does not match the kit.")
    if display_version != mod.version:
        raise ModManagementError("The Windows uninstall version does not match the mod.")

    supports_purge = _require_registry_dword(values, "SupportsPurgeState")
    if supports_purge not in (0, 1):
        raise ModManagementError("SupportsPurgeState must be a Windows DWORD boolean.")
    try:
        activation_contract_sha256 = mod_contract_sha256(mod)
    except Exception as exc:
        raise ModManagementError("The installed mod activation contract is invalid.") from exc

    return ManagedModRegistration(
        schema_version=schema_version,
        provider=provider,
        app_id=app_id,
        mod_id=mod_id,
        display_name=display_name,
        package_version=package_version,
        evejs_root=root,
        activation_contract_sha256=activation_contract_sha256,
        bundle_sha256=bundle_sha256,
        expand_helper_sha256=helper_sha256,
        current_pointer_sha256=pointer_sha256,
        removal_inventory_path=inventory_path,
        removal_inventory_sha256=inventory_sha256,
        removal_inventory=removal_inventory,
        uninstaller_path=uninstaller_path,
        uninstaller_sha256=uninstaller_sha256,
        uninstaller_data_path=uninstaller_data_path,
        uninstaller_data_sha256=uninstaller_data_sha256,
        supports_purge_state=bool(supports_purge),
    )


def remove_managed_mod(
    request: ManagedModRemovalRequest,
    *,
    timeout: float | None = None,
) -> ManagedModRemovalResult:
    """Serialize, run, and verify one exact registered uninstaller."""

    if not isinstance(request, ManagedModRemovalRequest):
        raise TypeError("request must be a ManagedModRemovalRequest.")
    if not isinstance(request.policy, ModDataPolicy):
        raise TypeError("request.policy must be a ModDataPolicy.")
    registration = request.registration
    if not isinstance(registration, ManagedModRegistration):
        raise TypeError("request.registration must be a ManagedModRegistration.")
    if (
        request.policy is ModDataPolicy.QUARANTINE
        and not registration.supports_purge_state
    ):
        raise ModManagementError("This registered mod cannot quarantine saved state.")
    with _managed_mod_operation_mutex(registration.app_id) as authorization_token:
        return _remove_managed_mod_under_operation_lock(
            request,
            timeout=timeout,
            authorization_token=authorization_token,
        )


def _remove_managed_mod_under_operation_lock(
    request: ManagedModRemovalRequest,
    *,
    timeout: float | None,
    authorization_token: str,
) -> ManagedModRemovalResult:
    """Keep Setup excluded from final validation through terminal proof."""

    registration = request.registration

    current_mod = _matching_current_mod(registration)
    current_registration = read_managed_mod_registration(current_mod)
    if current_registration != registration:
        raise ModManagementError(
            "The mod or its removal registration changed before removal started."
        )

    log_path = _new_launcher_uninstall_log(registration.mod_id)
    state_switch = (
        "/PURGESTATE"
        if request.policy is ModDataPolicy.QUARANTINE
        else "/KEEPSTATE"
    )
    argv = [
        str(registration.uninstaller_path),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/LAUNCHERTOKEN={authorization_token}",
        f"/LAUNCHERROOT={registration.evejs_root}",
        state_switch,
        f"/LOG={log_path}",
    ]
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ModManagementError(
            "The mod uninstaller was terminated after the configured test/safety "
            "timeout. Removal state is indeterminate; inspect the EveJS root and "
            f"uninstall log before retrying. Log: {log_path}"
        ) from exc
    except OSError as exc:
        raise ModManagementError("The registered mod uninstaller could not start.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        suffix = f" Last output: {detail[-1]}" if detail else ""
        raise ModManagementError(
            f"The mod uninstaller failed with exit code {completed.returncode}.{suffix} "
            f"Log: {log_path}"
        )

    kit_root = registration.uninstaller_path.parent
    bundle_path = kit_root / f"{registration.mod_id}-package.zip"
    helper_path = kit_root / "bootstrap" / "Expand-EmbeddedPackage.ps1"
    deadline = time.monotonic() + SELF_DELETE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if (
            not _registry_key_exists(managed_mod_registry_path(registration.mod_id))
            and not _registry_key_exists(
                _windows_uninstall_registry_path(registration.app_id)
            )
            and not any(
                _path_present(path)
                for path in (
                    registration.uninstaller_path,
                    registration.uninstaller_data_path,
                    bundle_path,
                    registration.removal_inventory_path,
                    helper_path,
                    kit_root,
                )
            )
        ):
            break
        time.sleep(0.05)

    warning = ""
    with acquire_mod_lifecycle_lock(registration.evejs_root):
        remaining = tuple(
            mod
            for mod in scan_mods(registration.evejs_root)
            if mod.id == registration.mod_id
        )
        pointer = (
            registration.evejs_root
            / "_local"
            / registration.mod_id
            / "install"
            / "current.json"
        )
        if remaining or _path_present(pointer):
            raise ModManagementError(
                "The uninstaller exited successfully, but executable integration "
                "or its active journal is still present. The launcher will not "
                "claim removal succeeded."
            )
        if _registry_key_exists(managed_mod_registry_path(registration.mod_id)):
            raise ModManagementError(
                "The mod payload was removed, but its launcher registration remains."
            )
        if _registry_key_exists(_windows_uninstall_registry_path(registration.app_id)):
            raise ModManagementError(
                "The mod payload was removed, but Windows still reports its uninstall kit."
            )
        remaining_kit_artifacts = tuple(
            path
            for path in (
                registration.uninstaller_path,
                registration.uninstaller_data_path,
                bundle_path,
                registration.removal_inventory_path,
                helper_path,
                kit_root,
            )
            if _path_present(path)
        )
        if remaining_kit_artifacts:
            raise ModManagementError(
                "The mod payload was removed, but its launcher uninstall kit remains "
                "on disk. The launcher will not claim complete removal."
            )
        _verify_removal_inventory(registration)
        try:
            retire_removed_mod_activation(
                registration.evejs_root,
                registration.mod_id,
                registration.activation_contract_sha256,
            )
        except Exception as exc:
            warning = (
                "The mod was removed, but its old launcher activation record could "
                f"not be retired: {exc}"
            )

    return ManagedModRemovalResult(
        request=request,
        success=True,
        message=f"{registration.display_name} was removed from EveJS.",
        log_path=log_path,
        warning=warning,
    )


def managed_mod_registry_path(mod_id: str) -> str:
    if (
        type(mod_id) is not str
        or not mod_id
        or len(mod_id) > 64
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", mod_id)
    ):
        raise ModManagementError("The managed mod id is invalid.")
    return MANAGED_MOD_REGISTRY_ROOT + "\\" + mod_id


def managed_mod_operation_mutex_name(app_id: str) -> str:
    """Return the provider-wide Setup/uninstall serialization mutex."""

    if type(app_id) is not str or not _APP_ID_PATTERN.fullmatch(app_id):
        raise ModManagementError("The launcher removal AppId is invalid.")
    identifier = app_id[1:-1].replace("-", "").upper()
    return rf"Local\EveJSLauncher.ManagedMod.{identifier}"


@contextmanager
def _managed_mod_operation_mutex(app_id: str):
    """Exclude compatible Setup/uninstall mutation through terminal proof.

    The launcher owns the provider mutex while its child receives a one-use
    authorization mutex token. Compatible Inno uninstallers verify both names
    and therefore do not attempt to acquire the provider mutex themselves.
    """

    mutex_name = managed_mod_operation_mutex_name(app_id)
    token = os.urandom(32).hex()
    authorization_name = rf"Local\EveJSLauncher.ManagedModAuth.{token}"
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise ModManagementError(
            "Windows managed-mod operation locking is unavailable."
        ) from exc
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    ctypes.set_last_error(0)
    provider_handle = create_mutex(None, False, mutex_name)
    provider_error = ctypes.get_last_error()
    if not provider_handle:
        raise ModManagementError(
            "The managed mod operation lock could not be created."
        )
    if provider_error == 183:
        close_handle(provider_handle)
        raise ModManagementError(
            "Another compatible Setup or uninstall operation is already running."
        )
    ctypes.set_last_error(0)
    authorization_handle = create_mutex(None, False, authorization_name)
    authorization_error = ctypes.get_last_error()
    if not authorization_handle or authorization_error == 183:
        if authorization_handle:
            close_handle(authorization_handle)
        close_handle(provider_handle)
        raise ModManagementError(
            "The launcher uninstall authorization lock could not be created."
        )
    try:
        yield token
    finally:
        close_handle(authorization_handle)
        close_handle(provider_handle)


def _matching_current_mod(registration: ManagedModRegistration) -> Mod:
    matches = tuple(
        mod
        for mod in scan_mods(registration.evejs_root)
        if mod.id == registration.mod_id
    )
    if len(matches) != 1 or not matches[0].valid:
        raise ModManagementError(
            "The installed mod contract disappeared or became invalid before removal."
        )
    current = matches[0]
    try:
        fingerprint = mod_contract_sha256(current)
    except Exception as exc:
        raise ModManagementError("The current mod contract cannot be verified.") from exc
    if fingerprint != registration.activation_contract_sha256:
        raise ModManagementError("The installed mod contract changed before removal.")
    return current


def _read_registry_values(
    path: str,
    *,
    registry_view: int = 0,
    unsupported_view_is_missing: bool = False,
) -> dict[str, tuple[object, int]] | None:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            path,
            0,
            winreg.KEY_READ | registry_view,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        if unsupported_view_is_missing and getattr(exc, "winerror", None) in (2, 87):
            return None
        raise ModManagementError("The launcher removal registration cannot be read.") from exc
    values: dict[str, tuple[object, int]] = {}
    try:
        index = 0
        while True:
            try:
                name, value, value_type = winreg.EnumValue(key, index)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 259:
                    break
                raise
            if name in values:
                raise ModManagementError("The launcher removal registration is ambiguous.")
            values[name] = (value, value_type)
            index += 1
    except ModManagementError:
        raise
    except OSError as exc:
        raise ModManagementError("The launcher removal registration cannot be read.") from exc
    finally:
        winreg.CloseKey(key)
    return values


def _registry_key_exists(path: str) -> bool:
    for registry_view in dict.fromkeys(_REGISTRY_VIEWS):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                path,
                0,
                winreg.KEY_READ | registry_view,
            )
        except FileNotFoundError:
            continue
        except OSError as exc:
            if getattr(exc, "winerror", None) in (2, 87):
                continue
            raise ModManagementError(
                "A Windows removal registration cannot be inspected."
            ) from exc
        winreg.CloseKey(key)
        return True
    return False


def _windows_uninstall_registry_path(app_id: str) -> str:
    return (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        + "\\"
        + app_id
        + "_is1"
    )


def _require_registry_text(
    values: Mapping[str, tuple[object, int]],
    name: str,
) -> str:
    raw = values.get(name)
    if raw is None or raw[1] != winreg.REG_SZ or type(raw[0]) is not str:
        raise ModManagementError(f"The launcher removal value {name!r} is invalid.")
    text = raw[0]
    if (
        not text
        or text != text.strip()
        or "\0" in text
        or any(ord(character) < 32 for character in text)
    ):
        raise ModManagementError(f"The launcher removal value {name!r} is invalid.")
    return text


def _standard_registry_text(
    values: Mapping[str, tuple[object, int]],
    name: str,
) -> str:
    raw = values.get(name)
    if raw is None or raw[1] not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        raise ModManagementError(f"The Windows uninstall value {name!r} is missing.")
    if type(raw[0]) is not str or not raw[0].strip():
        raise ModManagementError(f"The Windows uninstall value {name!r} is invalid.")
    return raw[0].strip()


def _require_registry_dword(
    values: Mapping[str, tuple[object, int]],
    name: str,
) -> int:
    raw = values.get(name)
    if raw is None or raw[1] != winreg.REG_DWORD or type(raw[0]) is not int:
        raise ModManagementError(f"The launcher removal value {name!r} is invalid.")
    return raw[0]


def _require_sha256(
    values: Mapping[str, tuple[object, int]],
    name: str,
) -> str:
    value = _require_registry_text(values, name)
    if not _SHA256_PATTERN.fullmatch(value):
        raise ModManagementError(f"The launcher removal value {name!r} is not SHA-256.")
    return value


def _canonical_directory(value: str | Path, label: str) -> Path:
    raw_path = Path(value)
    try:
        metadata = raw_path.lstat()
    except (OSError, TypeError, ValueError) as exc:
        raise ModManagementError(f"The {label} is unavailable.") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ModManagementError(f"The {label} is unsafe.")
    try:
        return raw_path.resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ModManagementError(f"The {label} is unavailable.") from exc


def _require_safe_directory_chain(base: Path, destination: Path) -> None:
    try:
        relative = destination.relative_to(base)
    except ValueError as exc:
        raise ModManagementError("The registered uninstall kit escapes LOCALAPPDATA.") from exc
    current = base
    for part in relative.parts:
        current = current / part
        _canonical_directory(current, "registered uninstall kit directory")


def _require_safe_regular_file(path: Path, label: str) -> None:
    _canonical_directory(path.parent, f"{label} parent")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ModManagementError(f"The {label} is unavailable.") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ModManagementError(f"The {label} is unsafe.")


def _parse_exact_executable(value: str) -> str:
    if value.startswith('"'):
        if len(value) < 3 or not value.endswith('"') or '"' in value[1:-1]:
            raise ModManagementError("The Windows uninstall command is malformed.")
        candidate = value[1:-1]
    else:
        if '"' in value:
            raise ModManagementError("The Windows uninstall command is malformed.")
        candidate = value
    try:
        path = Path(candidate).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ModManagementError("The Windows uninstall executable is unavailable.") from exc
    return _path_identity(path)


def _parse_removal_inventory(
    content: bytes,
    *,
    expected_mod_id: str,
) -> tuple[RemovalInventoryEntry, ...]:
    if content.startswith(b"\xef\xbb\xbf"):
        raise ModManagementError("The removal inventory must not contain a BOM.")

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ModManagementError(
                    "The removal inventory contains a duplicate JSON key."
                )
            result[key] = value
        return result

    try:
        payload = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=exact_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ModManagementError(
                    f"The removal inventory contains invalid number {value!r}."
                )
            ),
        )
    except ModManagementError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ModManagementError(
            "The removal inventory is not strict UTF-8 JSON."
        ) from exc
    if type(payload) is not dict or set(payload) != {
        "schemaVersion",
        "modId",
        "entries",
    }:
        raise ModManagementError("The removal inventory root schema is invalid.")
    if (
        type(payload["schemaVersion"]) is not int
        or payload["schemaVersion"] != REMOVAL_INVENTORY_SCHEMA_VERSION
        or payload["modId"] != expected_mod_id
        or type(payload["modId"]) is not str
        or type(payload["entries"]) is not list
        or not payload["entries"]
        or len(payload["entries"]) > 4096
    ):
        raise ModManagementError("The removal inventory identity or entries are invalid.")

    entries: list[RemovalInventoryEntry] = []
    seen: set[str] = set()
    for raw_entry in payload["entries"]:
        if type(raw_entry) is not dict or set(raw_entry) != {"path", "postRemove"}:
            raise ModManagementError("A removal inventory entry schema is invalid.")
        relative_path = _require_inventory_relative_path(raw_entry["path"])
        folded = relative_path.casefold()
        if folded in seen:
            raise ModManagementError("Removal inventory paths must be unique.")
        seen.add(folded)
        expectation = raw_entry["postRemove"]
        if type(expectation) is not dict or "kind" not in expectation:
            raise ModManagementError("A removal inventory expectation is invalid.")
        kind = expectation["kind"]
        if kind == "absent" and set(expectation) == {"kind"}:
            entries.append(RemovalInventoryEntry(relative_path, "absent"))
        elif kind == "sha256" and set(expectation) == {"kind", "sha256"}:
            digest = expectation["sha256"]
            if type(digest) is not str or not _SHA256_PATTERN.fullmatch(digest):
                raise ModManagementError(
                    "A removal inventory SHA-256 expectation is invalid."
                )
            entries.append(RemovalInventoryEntry(relative_path, "sha256", digest))
        else:
            raise ModManagementError(
                "A removal inventory post-remove kind is unsupported."
            )
    return tuple(entries)


def _require_inventory_relative_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 512
        or value != value.strip()
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or "\0" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ModManagementError("A removal inventory path is unsafe.")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ModManagementError("A removal inventory path is unsafe.")
    return value


def _read_stable_file_bytes(
    path: Path,
    *,
    maximum: int,
    label: str,
) -> bytes:
    _require_safe_regular_file(path, label)
    try:
        before = path.lstat()
        if before.st_size > maximum:
            raise ModManagementError(f"The {label} exceeds its size limit.")
        with path.open("rb") as stream:
            content = stream.read(maximum + 1)
            opened = os.fstat(stream.fileno())
        after = path.lstat()
    except ModManagementError:
        raise
    except OSError as exc:
        raise ModManagementError(f"The {label} could not be read safely.") from exc
    if len(content) > maximum:
        raise ModManagementError(f"The {label} exceeds its size limit.")
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or getattr(after, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size != len(content)
        or after.st_size != len(content)
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ModManagementError(f"The {label} changed while it was read.")
    return content


def _sha256_stable_file(path: Path) -> str:
    _require_safe_regular_file(path, f"registered file {path.name}")
    digest = hashlib.sha256()
    try:
        before = path.lstat()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            opened = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise ModManagementError(f"Could not hash registered file: {path.name}") from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or getattr(after, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or opened.st_size != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ModManagementError(f"Registered file changed while hashing: {path.name}")
    return digest.hexdigest()


def _path_identity(path: Path) -> str:
    return os.path.normcase(str(path))


def _path_present(path: Path) -> bool:
    """Return true for normal entries, junctions, and broken symlinks."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ModManagementError(
            f"A managed uninstall-kit path cannot be inspected: {path.name}"
        ) from exc
    return True


def _verify_removal_inventory(registration: ManagedModRegistration) -> None:
    """Prove every enrolled executable integration path reached its end state."""

    root = registration.evejs_root
    for entry in registration.removal_inventory:
        target = root.joinpath(*entry.relative_path.split("/"))
        parent_missing = False
        current = root
        for part in entry.relative_path.split("/")[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                parent_missing = True
                break
            except OSError as exc:
                raise ModManagementError(
                    f"Removal inventory path could not be inspected: {entry.relative_path}"
                ) from exc
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISDIR(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0)
                & _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ModManagementError(
                    f"Removal inventory path has an unsafe parent: {entry.relative_path}"
                )
        if parent_missing:
            if entry.expected_state == "absent":
                continue
            raise ModManagementError(
                f"A file that should have been restored is absent: {entry.relative_path}"
            )
        present = _path_present(target)
        if entry.expected_state == "absent":
            if present:
                raise ModManagementError(
                    "The uninstaller left enrolled executable integration behind: "
                    + entry.relative_path
                )
            continue
        if not present or entry.expected_sha256 is None:
            raise ModManagementError(
                f"A file that should have been restored is absent: {entry.relative_path}"
            )
        _require_safe_regular_file(target, "restored removal-inventory file")
        if _sha256_stable_file(target) != entry.expected_sha256:
            raise ModManagementError(
                "An enrolled source file was not restored to its original hash: "
                + entry.relative_path
            )

    runtime_mod_directory = root / "server" / "mods" / registration.mod_id
    if _path_present(runtime_mod_directory):
        raise ModManagementError(
            "The source-integrated mod directory remains after uninstall."
        )


def _new_launcher_uninstall_log(mod_id: str) -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        raise ModManagementError("LOCALAPPDATA is unavailable for uninstall logging.")
    directory = Path(local) / "EveJS Launcher" / "mod-logs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ModManagementError("The launcher mod log directory cannot be created.") from exc
    _canonical_directory(directory, "launcher mod log directory")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return directory / f"{stamp}-{mod_id}-uninstall.log"


__all__ = [
    "INNO_USER_PROVIDER",
    "MANAGED_MOD_REGISTRY_ROOT",
    "MANAGED_MOD_SCHEMA_VERSION",
    "ManagedModRegistration",
    "ManagedModRemovalRequest",
    "ManagedModRemovalResult",
    "ModDataPolicy",
    "ModManagementError",
    "ModNotManagedError",
    "RemovalInventoryEntry",
    "managed_mod_registry_path",
    "managed_mod_operation_mutex_name",
    "read_managed_mod_registration",
    "remove_managed_mod",
    "validate_managed_mod_registration",
]
