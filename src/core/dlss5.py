"""Per-profile runtime isolation for the optional EveJS DLSS5 client mod."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from . import platform as platform_api
from .discovery import find_dlss5_launch_environment
from .mod_manifest import ActivationKind, Mod


_PROFILE_BASE_DIRECTORY = "DLSS5"
_PROFILE_CONFIG_NAME = "ReShade.ini"
_REQUIRED_CLIENT_FILES = (
    "dxgi.dll",
    "renodx-dlss5.addon64",
)

_CLIENT_PACKAGE_FOLDER = "DLSS5"
_CLIENT_MANIFEST_NAME = "evejs-launcher.client-mod.json"
_CLIENT_MANIFEST_SCHEMA_VERSIONS = frozenset({1, 2, 3})
_CLIENT_MANAGER_PROTOCOL = "evejs_dlss5_manager_v1"
_CLIENT_MANAGER_RELATIVE_PATH = Path("EveJS-Integration") / "Manage-EveJSDLSS5.ps1"
_CLIENT_MANAGER_MAX_BYTES = 512 * 1024
_CLIENT_MANIFEST_MAX_BYTES = 64 * 1024
# A first public-package Ensure may perform three sequential, hash-verified
# downloads (each independently bounded at 900 seconds), archive extraction,
# Authenticode checks, and a locally generated client guard. Installed clients
# normally take the read-only fast path, but the outer launcher must not kill a
# valid first preparation inside the manager's own documented bounds.
_CLIENT_MANAGER_PREPARE_TIMEOUT_SECONDS = 60 * 60
_CLIENT_MANAGER_OUTPUT_MAX_BYTES = 256 * 1024
_CLIENT_MANAGER_REAP_SECONDS = 5
_CLIENT_MOD_ID = "evejs-dlss5"
_CLIENT_MOD_NAME = "EveJS DLSS5"
_CLIENT_PROFILE = "DLSS5"
# Schemas 1 and 2 are historical exact-version contracts. Schema 3 deliberately
# removes this launcher allow-list from new packages: DLSS5 modifies one pinned
# copied-client build, not EveJS server code. Keeping the legacy list separate
# lets existing packages and receipts remain fail-closed without making every
# future EveJS release require another launcher patch.
_SUPPORTED_EVEJS_VERSIONS = frozenset({"0.12.6", "0.12.7", "0.12.7.1"})
_SUPPORTED_CLIENT_BUILD = 3396210
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_VERSION_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}\Z")

# Executing a path supplied by arbitrary manifest data would be a spectacularly
# bad mod API. Only reviewed manager implementations are executable. Payload
# releases whose manager bytes change need a reviewed launcher trust update.
# The public manager also pins its helper and raw payload manifest, so changing
# either intentionally changes this top-level trust anchor.
_TRUSTED_MANAGER_SHA256 = frozenset(
    {
        "AFD954362BF35141FA7E615E41A3394F87C618EE83F59BD2071971B27C40A9E6",
        "8AFF0AE3FBEB22D95FD853A4A8FB832580F47C1FF62281DA6D11300F09E1FACE",
        "8C3310AFACC7BB0AED36BE8923BFD10CBB13A706B3549A2498A425281A434D30",
        "6723D31BF55C2E221453038412E0DF19EF781F20A9020D5DF9B91CA36F0784DA",
        "2321744C25719313C520774659E63F02C0080DE97A90031E7F05E36A899ED3D4",
        "64275F97BDA248FC5C35BED90E6E3EBB1B330F6F49AA13AF8D4F28B52BC4DECF",
        "38D111E80035D5BB201744C1D3586BB90D992900D6B8A510A6CAAC092758FDEF",
        "26A81A5834A7154615002C427277CDBCE541C676D84C45FC5565DF7236BB2D0F",
        "D75FB3C09836B9E22207FC7F2AA17FC3CE2573870497C13BEDD0CE79A479CE31",
    }
)
# Schema 3 changes state ownership and root-handoff semantics, so it must never
# execute a merely historical manager whose bytes implement the schema-4
# root-local contract. Bind each reviewed package version to its exact manager;
# a trusted older manager cannot be relabelled as a newer hotfix.
_CLIENT_SCOPED_MANAGER_PACKAGE_VERSIONS = {
    "F841291D2939931D02B5C5E8AC009DD55AEA3C1315DDE08DC92D222B2666B5DC": frozenset({"0.5.6"}),
    "799B17BDBAD0B5808A47096F484B9072E829469AEA3A153BDDC1FA67B24F0FB3": frozenset({"0.5.7"}),
}
_CLIENT_SCOPED_MANAGER_SHA256: frozenset[str] = frozenset(
    _CLIENT_SCOPED_MANAGER_PACKAGE_VERSIONS
)

# Offline launch-only contracts for the reviewed standalone development packages.
# These are launcher-owned trust anchors, NOT hashes learned from a receipt.
# Keep in sync only after reviewing a new payload. No package scripts are run.
_STANDALONE_NATIVE_FILES = {
    "bin64/nvngx_dlssnr.dll": (165840496, "E16BCF15E16E13F527491CDF7845B2FE6521A738D8F7C9C721866A8496E1FC8E"),
    "bin64/sl.dlss_nr.dll": (401024, "9F6672E5E0170DC118A3188D21BDA187E1FC1AA3502895B21AB846D23165C11D"),
    "bin64/renodx-dlss5.addon64": (1732608, "D5ADF82EB44B065F4C590AC91FE824BAB07AFEA0EB9F994BDE936710C8593952"),
    "bin64/dxgi.dll": (5591040, "77C3168A7661FA2230D494C4982FE7EAFEDC9370BD1DA48EBFDF9C2A51662CC8"),
}
_STANDALONE_PAYLOADS_BY_VERSION = {
    "0.5.0-dev": {
        **_STANDALONE_NATIVE_FILES,
        "code.ccp": (30760908, "04B27D576BD897091140D86A65AD57FDEF2ABEEB3DA3FDF9A8348EB5560951E3"),
    },
    "0.5.1-dev": {
        **_STANDALONE_NATIVE_FILES,
        "code.ccp": (30761488, "595A756A91B3C6E40DD55432682B2595CF07FB87531C3C22F56FD7E3EE28B5D8"),
    },
    "0.5.2-dev": {
        **_STANDALONE_NATIVE_FILES,
        "code.ccp": (30766379, "C980719606DDCF58D218991255FE390672FBC82E3EA89C572D460C158AD7CD44"),
    },
    "0.5.3-dev": {
        **_STANDALONE_NATIVE_FILES,
        # V10 scopes physical F6 to the foreground client. Older receipts must
        # continue to verify against their original V9 DLL for safe rollback.
        "bin64/dxgi.dll": (5591552, "8BAD71B96C4CB92CE04E18D661DCC508B30258C196F4CF01B639E58326BD6471"),
        "code.ccp": (30766379, "C980719606DDCF58D218991255FE390672FBC82E3EA89C572D460C158AD7CD44"),
    },
    "0.5.4-dev": {
        **_STANDALONE_NATIVE_FILES,
        # V11 exposes process-local NR state; V12 consumes its acknowledgement.
        # Historical contracts remain unchanged for installed-client rollback.
        "bin64/dxgi.dll": (5594112, "26EBDD0C2AE67EED8D305BC8B7A3A67B606F74D19979EFDFE6E584ACB27B78BF"),
        "code.ccp": (30763542, "BC8DD57471B376D3CC37A1908CEE64174E98EDB6D3D94B9F04437BDCE33686CC"),
    },
    "0.5.5-dev": {
        **_STANDALONE_NATIVE_FILES,
        # Local source derivation changes packaging, not the accepted runtime.
        "bin64/dxgi.dll": (5594112, "26EBDD0C2AE67EED8D305BC8B7A3A67B606F74D19979EFDFE6E584ACB27B78BF"),
        "code.ccp": (30763542, "BC8DD57471B376D3CC37A1908CEE64174E98EDB6D3D94B9F04437BDCE33686CC"),
    },
    "0.5.5": {
        **_STANDALONE_NATIVE_FILES,
        # Final package identity; the accepted renderer bytes are unchanged.
        "bin64/dxgi.dll": (5594112, "26EBDD0C2AE67EED8D305BC8B7A3A67B606F74D19979EFDFE6E584ACB27B78BF"),
        "code.ccp": (30763542, "BC8DD57471B376D3CC37A1908CEE64174E98EDB6D3D94B9F04437BDCE33686CC"),
    },
    "0.5.6": {
        **_STANDALONE_NATIVE_FILES,
        # State ownership moved beside the physical copied client. Renderer
        # bytes remain the exact reviewed 0.5.5 payload.
        "bin64/dxgi.dll": (5594112, "26EBDD0C2AE67EED8D305BC8B7A3A67B606F74D19979EFDFE6E584ACB27B78BF"),
        "code.ccp": (30763542, "BC8DD57471B376D3CC37A1908CEE64174E98EDB6D3D94B9F04437BDCE33686CC"),
    },
}
_STANDALONE_EXE = (1003152, "2AAF7A9A8DFCDE85E4ADB50C1ECCD3756A4D29AEB854DFE69629846BA56EE979")
_STANDALONE_RECEIPT_MAX_BYTES = 256 * 1024
_CLIENT_SCOPED_RECEIPT_VERSIONS = frozenset({"0.5.6"})
_DLSS5_UNIQUE_PAYLOAD_PATHS = frozenset(
    {
        "bin64/nvngx_dlssnr.dll",
        "bin64/sl.dlss_nr.dll",
        "bin64/renodx-dlss5.addon64",
    }
)


class DLSS5ClientModError(RuntimeError):
    """The automatic DLSS5 client package is missing, invalid, or failed."""


def discover_dlss5_client_mod(evejs_root: str | Path) -> Mod | None:
    """Return the fixed DLSS5 client package row, including invalid installs.

    Client packages are intentionally separate from ``scan_mods`` so corrupt
    renderer metadata cannot block or contaminate Game-server mod planning.
    The Mods page combines the two read-only discovery results for display.
    """

    raw_root = str(evejs_root or "").strip()
    if not raw_root:
        return None
    selected = Path(raw_root)
    if not selected.is_absolute():
        return None
    try:
        root = selected.resolve(strict=True)
    except OSError:
        return None
    if not root.is_dir():
        return None

    package_path = root / "mods" / _CLIENT_PACKAGE_FOLDER
    if not _path_entry_exists(package_path):
        return None
    manifest_path = package_path / _CLIENT_MANIFEST_NAME
    try:
        return _read_dlss5_client_mod(root, package_path, manifest_path)
    except (DLSS5ClientModError, OSError, UnicodeError, ValueError) as error:
        return Mod(
            name=_CLIENT_MOD_NAME,
            path=package_path,
            active=False,
            id=_CLIENT_MOD_ID,
            description="Automatic DLSS5 renderer integration for EveJS clients.",
            activation_kind=ActivationKind.CLIENT_PACKAGE,
            supported_backends=(),
            restart_scope="client_launch",
            manifest_path=manifest_path,
            valid=False,
            error=str(error) or "The DLSS5 client-package contract is invalid.",
            evejs_root=root,
        )


def ensure_dlss5_client_mod(
    evejs_root: str | Path,
    client_path: str | Path,
) -> dict[str, str]:
    """Apply or verify the automatic package and return its launch environment.

    Without a package, completed standalone installations are verified offline
    against launcher-pinned payload hashes. No receipt/marker means historical
    non-DLSS behavior. Partial or unknown installations still fail closed.
    """

    package = discover_dlss5_client_mod(evejs_root)
    if package is None:
        return _standalone_dlss5_launch_environment(evejs_root, client_path)
    if not package.valid:
        raise DLSS5ClientModError(
            "The detected mods\\DLSS5 package is invalid: "
            + (package.error or "unknown package error")
        )
    if package.manager_path is None or not package.manager_sha256:
        raise DLSS5ClientModError("The validated DLSS5 package has no manager contract.")

    root = Path(evejs_root).resolve(strict=True)
    client = Path(client_path).resolve(strict=True)
    state_root = _resolve_dlss5_state_root(client)
    _run_dlss5_manager(
        package,
        workspace_root=root.parent,
        evejs_root=root,
        client_root=client,
        state_root=state_root,
    )

    environment = find_dlss5_launch_environment(str(root), client)
    if not environment:
        raise DLSS5ClientModError(
            "The DLSS5 manager exited successfully but did not install its "
            "verified launch marker. Nothing was launched."
        )
    return environment


def _resolve_dlss5_state_root(client_path: str | Path) -> Path:
    """Return state owned by the physical copied client, never an EveJS root.

    ``client_path`` is the selected ``tq`` directory. Resolving it first keeps
    launcher profile junctions from creating a second, profile-owned receipt.
    The manager performs its own boundary/reparse checks before writing here.
    """
    selected = Path(client_path)
    if not selected.is_absolute():
        raise DLSS5ClientModError("The DLSS5 copied-client path must be absolute.")
    try:
        client = selected.resolve(strict=True)
    except OSError as error:
        raise DLSS5ClientModError(
            f"The DLSS5 copied-client path is unavailable: {selected}"
        ) from error
    if not client.is_dir() or client.name.casefold() != "tq":
        raise DLSS5ClientModError(
            "The DLSS5 copied-client path must resolve to its physical tq directory."
        )
    return client.parent / "_evejs" / "dlss5" / "install"


def _known_dlss5_payload_files(client: Path) -> tuple[str, ...]:
    """Identify DLSS5-owned paths plus exact pins for shared/generic names."""
    expected_by_name: dict[str, set[tuple[int, str]]] = {}
    for payload in _STANDALONE_PAYLOADS_BY_VERSION.values():
        for name, identity in payload.items():
            expected_by_name.setdefault(name, set()).add(identity)

    matches = []
    for name, identities in expected_by_name.items():
        path = client / name
        try:
            if name in _DLSS5_UNIQUE_PAYLOAD_PATHS and _path_entry_exists(path):
                # These names are owned by this integration. Drift, a link, or
                # even a directory at one of them is evidence of an incomplete
                # installation, not a reason to silently call the client stock.
                matches.append(name)
                continue
            if not path.is_file() or _is_reparse_point(path):
                continue
            size = path.stat().st_size
            hashes = {digest for expected_size, digest in identities if expected_size == size}
            if hashes and _sha256_file(path) in hashes:
                matches.append(name)
        except OSError:
            # Failure to inspect a uniquely owned path is itself unsafe. A
            # generic code.ccp/dxgi candidate still needs exact bytes before we
            # attribute it to this integration.
            if name in _DLSS5_UNIQUE_PAYLOAD_PATHS:
                matches.append(name)
    return tuple(sorted(matches))


def _standalone_dlss5_launch_environment(
    evejs_root: str | Path, client_path: str | Path,
) -> dict[str, str]:
    """Verify an installed payload without installing, repairing, or downloading.

    The receipt has no installation-mode field. A removed MOD package and a
    standalone installation are deliberately treated identically *only* when
    their complete, reviewed installed bytes pass this launch-only contract.
    Neither becomes a Mods row, and neither is silently reinstalled on launch.
    """
    try:
        environment = find_dlss5_launch_environment(str(evejs_root), client_path)
        selected_root = Path(evejs_root)
        selected_client = Path(client_path)
        _require_plain_directory(selected_client, selected_client, "Standalone client root")
        client = selected_client.resolve(strict=True)
        client_state = _resolve_dlss5_state_root(client)
        legacy_state = selected_root / "_local" / "dlss5" / "install"
        client_receipt = client_state / "active-install.json"
        legacy_receipt = legacy_state / "active-install.json"
        if _path_entry_exists(client_receipt):
            state = client_state
            expected_schema = 5
        elif _path_entry_exists(legacy_receipt):
            state = legacy_state
            expected_schema = 4
        else:
            known_payload = _known_dlss5_payload_files(client)
            if environment or known_payload:
                detail = (
                    " DLSS5-owned or reviewed payload paths: " +
                    ", ".join(known_payload) + "."
                    if known_payload else ""
                )
                raise DLSS5ClientModError(
                    "The selected EveJS root has no matching DLSS5 package or "
                    "valid installation receipt for this copied client." + detail +
                    " Copy the current DLSS5 package into this root's mods folder "
                    "and let the launcher prepare it before launching."
                )
            return {}

        _require_plain_directory(selected_root, selected_root, "Standalone EveJS root")
        root = selected_root.resolve(strict=True)
        if expected_schema == 4:
            state = root / "_local" / "dlss5" / "install"
        if expected_schema == 5:
            state_boundary = client.parent
            for directory in (
                state_boundary,
                state_boundary / "_evejs",
                state_boundary / "_evejs" / "dlss5",
                state,
            ):
                _require_plain_directory(directory, state_boundary, "DLSS5 receipt directory")
        else:
            state_boundary = root
            for directory in (root / "_local", root / "_local/dlss5", state):
                _require_plain_directory(directory, root, "DLSS5 receipt directory")
        receipt_path = state / "active-install.json"
        _require_plain_file(receipt_path, state, "DLSS5 installation receipt")
        receipt = _read_json_object(receipt_path, maximum_bytes=_STANDALONE_RECEIPT_MAX_BYTES,
                                    label="DLSS5 installation receipt")
        if not environment:
            if receipt.get("status") in ("restored", "rolledBack"):
                known_payload = _known_dlss5_payload_files(client)
                if known_payload:
                    raise DLSS5ClientModError(
                        "A completed DLSS5 receipt still has reviewed payload files in the "
                        "copied client: " + ", ".join(known_payload)
                    )
                return {}
            raise DLSS5ClientModError("DLSS5 installation is incomplete or its launch marker is missing.")

        version = receipt.get("integrationVersion")
        if (type(receipt.get("schemaVersion")) is not int
                or receipt["schemaVersion"] != expected_schema
                or type(version) is not str or version not in _STANDALONE_PAYLOADS_BY_VERSION
                or receipt.get("status") != "installed" or receipt.get("profile") != "DLSS5"):
            raise DLSS5ClientModError("Unsupported or incomplete standalone DLSS5 receipt.")
        if expected_schema == 5:
            if (version not in _CLIENT_SCOPED_RECEIPT_VERSIONS
                    or receipt.get("stateScope") != "client"):
                raise DLSS5ClientModError("Unsupported client-scoped DLSS5 receipt contract.")
            if receipt.get("stateDirectory") not in (None, ""):
                raise DLSS5ClientModError(
                    "Client-scoped DLSS5 receipts cannot declare a legacy stateDirectory."
                )
        elif version in _CLIENT_SCOPED_RECEIPT_VERSIONS or "stateScope" in receipt:
            raise DLSS5ClientModError("A legacy DLSS5 receipt cannot claim client-scoped state.")
        payload_files = _STANDALONE_PAYLOADS_BY_VERSION[version]
        actual_evejs_version = _read_evejs_version(root)
        if expected_schema == 4 and actual_evejs_version not in _SUPPORTED_EVEJS_VERSIONS:
            raise DLSS5ClientModError(
                "Standalone DLSS5 requires EveJS 0.12.6, 0.12.7 or 0.12.7.1."
            )
        if expected_schema == 5:
            _require_receipt_path(receipt.get("workspaceRoot"), root.parent, "workspaceRoot")
        for key, expected in (("evejsRoot", root), ("clientRoot", client), ("stateRoot", state)):
            _require_receipt_path(receipt.get(key), expected, key)
        _require_plain_directory(client / "bin64", client, "DLSS5 client bin64")
        for name in ("bin64/blue.dll", "bin64/_trinity_dx12.dll", "start.ini"):
            _require_plain_file(client / name, client, name)
        start = _read_standalone_ini(client / "start.ini")
        if (_get_ini_value(start, "main", "build") != str(_SUPPORTED_CLIENT_BUILD)
                or _get_ini_value(start, "main", "server") != "127.0.0.1"):
            raise DLSS5ClientModError("Standalone DLSS5 requires local EveJS client build 3396210.")

        operations = receipt.get("operations")
        if type(operations) is not list or len(operations) != len(payload_files):
            raise DLSS5ClientModError("Standalone DLSS5 receipt does not cover the exact payload.")
        seen = set()
        for operation in operations:
            if type(operation) is not dict or type(operation.get("destination")) is not str:
                raise DLSS5ClientModError("Invalid standalone DLSS5 operation.")
            name = operation["destination"].replace("\\", "/")
            if name not in payload_files or name in seen:
                raise DLSS5ClientModError("Unknown or duplicate standalone DLSS5 destination.")
            seen.add(name)
            size, digest = payload_files[name]
            if (operation.get("applied") is not True
                    or operation.get("kind") not in (("replace",) if name == "code.ccp" else ("add", "replace"))
                    or type(operation.get("installedBytes")) is not int
                    or operation["installedBytes"] != size
                    or _require_sha256(operation.get("installedSha256"), name) != digest):
                raise DLSS5ClientModError(f"Standalone DLSS5 receipt does not match reviewed {name}.")
            _verify_standalone_file(client / name, client, size, digest)

        executable = receipt.get("executable")
        if (type(executable) is not dict or executable.get("path") != "bin64\\exefile.exe"
                or executable.get("modified") is not False
                or _require_sha256(executable.get("sha256"), "executable") != _STANDALONE_EXE[1]):
            raise DLSS5ClientModError("Standalone DLSS5 executable receipt is invalid.")
        _verify_standalone_file(client / "bin64/exefile.exe", client, *_STANDALONE_EXE)

        config = receipt.get("config")
        if type(config) is not dict or config.get("applied") is not True:
            raise DLSS5ClientModError("Standalone DLSS5 config receipt is invalid.")
        config_path = root / "tools/ClientSETUP/scripts/EvEJSConfig.bat"
        _require_receipt_path(config.get("path"), config_path, "config")
        for directory in (root / "tools", root / "tools/ClientSETUP", config_path.parent):
            _require_plain_directory(directory, root, "DLSS5 launch config directory")
        _require_plain_file(config_path, root, "DLSS5 launch config")
        if _sha256_file(config_path) != _require_sha256(config.get("installedSha256"), "config"):
            raise DLSS5ClientModError("EvEJSConfig.bat changed after standalone DLSS5 installation.")
        config_text = _read_standalone_ini(config_path)
        for variable in ("EVEJS_CLIENT_PATH", "TRINITYPLATFORM", "EVEJS_DLSS5"):
            assignments = re.findall(rf'^\s*@?set\s+"?{variable}\s*=', config_text,
                                     flags=re.IGNORECASE | re.MULTILINE)
            if len(assignments) != 1:
                raise DLSS5ClientModError(f"Standalone DLSS5 config requires one {variable} assignment.")

        reshade = receipt.get("reshadeConfig")
        if (type(reshade) is not dict or type(reshade.get("schemaVersion")) is not int
                or reshade["schemaVersion"] != 2 or reshade.get("path") != "bin64\\ReShade.ini"):
            raise DLSS5ClientModError("Standalone DLSS5 has no valid ReShade ownership record.")
        ini_path = client / "bin64/ReShade.ini"
        _require_plain_file(ini_path, client, "Standalone ReShade config")
        ini = _read_standalone_ini(ini_path)
        if (_get_ini_value(ini, "ADDON", "LoadFromDllMain") != "renodx-dlss5.addon64"
                or _get_ini_value(ini, "RenoDX.DLSS5", "EnableHooks") != "2"
                or _get_ini_value(ini, "RenoDX.DLSS5", "NeuralUplift") not in ("0", "1")
                or _get_ini_value(ini, "INSTALL", "BasePath")):
            raise DLSS5ClientModError("Standalone ReShade settings do not support safe profile isolation.")
        return environment
    except (OSError, ValueError, RuntimeError) as error:
        raise DLSS5ClientModError(
            f"Standalone DLSS5 verification failed: {error}\n"
            "Nothing was installed or repaired. Use the matching standalone installer "
            "to verify or uninstall this installation."
        ) from error


def _require_receipt_path(value: object, expected: Path, label: str) -> None:
    # Compare against our fixed path without resolving or following receipt data.
    if (type(value) is not str or not Path(value).is_absolute()
            or os.path.normcase(os.path.normpath(value)) != os.path.normcase(str(expected))):
        raise DLSS5ClientModError(f"Standalone DLSS5 {label} targets a different installation.")


def _verify_standalone_file(path: Path, boundary: Path, size: int, digest: str) -> None:
    _require_plain_file(path, boundary, path.name)
    if path.stat().st_size != size or _sha256_file(path) != digest:
        raise DLSS5ClientModError(f"Installed standalone DLSS5 file does not match reviewed bytes: {path.name}")


def _read_standalone_ini(path: Path) -> str:
    if path.stat().st_size > _STANDALONE_RECEIPT_MAX_BYTES:
        raise DLSS5ClientModError(f"Standalone DLSS5 INI exceeds the supported size: {path.name}")
    return _read_ini(path)


def _read_dlss5_client_mod(
    root: Path,
    package_path: Path,
    manifest_path: Path,
) -> Mod:
    mods_path = root / "mods"
    _require_plain_directory(mods_path, root, "Mods directory")
    _require_plain_directory(package_path, mods_path, "DLSS5 package directory")
    _require_plain_file(manifest_path, package_path, "DLSS5 client manifest")

    payload = _read_json_object(
        manifest_path,
        maximum_bytes=_CLIENT_MANIFEST_MAX_BYTES,
        label="DLSS5 client manifest",
    )
    _require_exact_keys(
        payload,
        {
            "schemaVersion",
            "id",
            "displayName",
            "version",
            "description",
            "kind",
            "activation",
            "restart",
            "manager",
            "compatibility",
        },
        "DLSS5 client manifest",
    )
    schema_version = payload["schemaVersion"]
    if (
        type(schema_version) is not int
        or schema_version not in _CLIENT_MANIFEST_SCHEMA_VERSIONS
    ):
        raise DLSS5ClientModError("Unsupported DLSS5 client manifest schemaVersion.")
    if payload["id"] != _CLIENT_MOD_ID:
        raise DLSS5ClientModError(f"DLSS5 client manifest id must be '{_CLIENT_MOD_ID}'.")
    if payload["displayName"] != _CLIENT_MOD_NAME:
        raise DLSS5ClientModError(f"DLSS5 displayName must be '{_CLIENT_MOD_NAME}'.")
    version = _require_text(payload["version"], "DLSS5 version", maximum=64)
    if not _VERSION_PATTERN.fullmatch(version):
        raise DLSS5ClientModError("DLSS5 version contains unsupported characters.")
    description = _require_text(
        payload["description"],
        "DLSS5 description",
        maximum=500,
        allow_empty=True,
    )
    if payload["kind"] != "client-package":
        raise DLSS5ClientModError("DLSS5 manifest kind must be 'client-package'.")
    if payload["activation"] != "automatic":
        raise DLSS5ClientModError("DLSS5 activation must be 'automatic'.")
    if payload["restart"] != "client_launch":
        raise DLSS5ClientModError("DLSS5 restart scope must be 'client_launch'.")

    manager = payload["manager"]
    if type(manager) is not dict:
        raise DLSS5ClientModError("DLSS5 manager must be an object.")
    _require_exact_keys(
        manager,
        {"path", "protocol", "sha256"},
        "DLSS5 manager",
    )
    expected_manager_path = _CLIENT_MANAGER_RELATIVE_PATH.as_posix()
    if manager["path"] != expected_manager_path:
        raise DLSS5ClientModError(
            f"DLSS5 manager path must be '{expected_manager_path}'."
        )
    if manager["protocol"] != _CLIENT_MANAGER_PROTOCOL:
        raise DLSS5ClientModError("Unsupported DLSS5 manager protocol.")
    declared_hash = _require_sha256(manager["sha256"], "DLSS5 manager sha256")
    if declared_hash not in (_TRUSTED_MANAGER_SHA256 | _CLIENT_SCOPED_MANAGER_SHA256):
        raise DLSS5ClientModError(
            "This DLSS5 manager implementation is not trusted by this launcher version."
        )

    manager_path = package_path / _CLIENT_MANAGER_RELATIVE_PATH
    _require_plain_file(manager_path, package_path, "DLSS5 manager")
    manager_size = manager_path.stat().st_size
    if manager_size < 1 or manager_size > _CLIENT_MANAGER_MAX_BYTES:
        raise DLSS5ClientModError("DLSS5 manager size is outside the supported limit.")
    actual_hash = _sha256_file(manager_path)
    if actual_hash != declared_hash:
        raise DLSS5ClientModError(
            f"DLSS5 manager hash mismatch (expected {declared_hash}, got {actual_hash})."
        )

    if schema_version == 3:
        if declared_hash not in _CLIENT_SCOPED_MANAGER_SHA256:
            raise DLSS5ClientModError(
                "This DLSS5 manager is trusted only for a historical state contract, "
                "not schema-3 client-scoped state."
            )
        allowed_versions = _CLIENT_SCOPED_MANAGER_PACKAGE_VERSIONS.get(
            declared_hash, frozenset()
        )
        if version not in allowed_versions:
            raise DLSS5ClientModError(
                "The DLSS5 schema-3 package version does not match its trusted manager."
            )
    elif declared_hash not in _TRUSTED_MANAGER_SHA256:
        raise DLSS5ClientModError(
            "A client-scoped DLSS5 manager cannot be relabelled as a historical package."
        )

    compatibility = payload["compatibility"]
    if type(compatibility) is not dict:
        raise DLSS5ClientModError("DLSS5 compatibility must be an object.")
    actual_evejs_version = _read_evejs_version(root)
    if schema_version == 1:
        _require_exact_keys(
            compatibility,
            {"evejsVersion", "clientBuild", "profile"},
            "DLSS5 compatibility",
        )
        declared_version = compatibility["evejsVersion"]
        if type(declared_version) is not str:
            raise DLSS5ClientModError("DLSS5 evejsVersion must be a string.")
        declared_evejs_versions = (declared_version,)
    elif schema_version == 2:
        _require_exact_keys(
            compatibility,
            {"evejsVersions", "clientBuild", "profile"},
            "DLSS5 compatibility",
        )
        declared_versions = compatibility["evejsVersions"]
        if type(declared_versions) is not list or not declared_versions:
            raise DLSS5ClientModError(
                "DLSS5 evejsVersions must be a non-empty array of strings."
            )
        if len(declared_versions) > 16 or any(
            type(item) is not str for item in declared_versions
        ):
            raise DLSS5ClientModError(
                "DLSS5 evejsVersions must contain at most 16 strings."
            )
        declared_evejs_versions = tuple(declared_versions)
        if len(set(declared_evejs_versions)) != len(declared_evejs_versions):
            raise DLSS5ClientModError("DLSS5 evejsVersions contains duplicates.")
    else:
        _require_exact_keys(
            compatibility,
            {"evejsVersionPolicy", "clientBuild", "profile"},
            "DLSS5 compatibility",
        )
        if compatibility["evejsVersionPolicy"] != "any":
            raise DLSS5ClientModError(
                "DLSS5 schema-3 evejsVersionPolicy must be 'any'."
            )
        declared_evejs_versions = None

    if declared_evejs_versions is not None:
        unsupported_versions = tuple(
            version
            for version in declared_evejs_versions
            if version not in _SUPPORTED_EVEJS_VERSIONS
        )
        if unsupported_versions:
            supported_versions = ", ".join(sorted(_SUPPORTED_EVEJS_VERSIONS))
            raise DLSS5ClientModError(
                "This launcher supports historical DLSS5 packages for EveJS "
                f"{supported_versions}; the package also declares unsupported "
                f"versions: {', '.join(unsupported_versions)}."
            )
        if actual_evejs_version not in _SUPPORTED_EVEJS_VERSIONS:
            supported_versions = ", ".join(sorted(_SUPPORTED_EVEJS_VERSIONS))
            raise DLSS5ClientModError(
                f"This historical package supports DLSS5 on EveJS {supported_versions}; "
                f"the selected root is EveJS {actual_evejs_version}."
            )
        if actual_evejs_version not in declared_evejs_versions:
            raise DLSS5ClientModError(
                f"This DLSS5 package does not declare support for the selected "
                f"EveJS {actual_evejs_version} root."
            )
    if (
        type(compatibility["clientBuild"]) is not int
        or compatibility["clientBuild"] != _SUPPORTED_CLIENT_BUILD
    ):
        raise DLSS5ClientModError(
            f"This package does not support client build {_SUPPORTED_CLIENT_BUILD}."
        )
    if compatibility["profile"] != _CLIENT_PROFILE:
        raise DLSS5ClientModError("DLSS5 compatibility profile must be 'DLSS5'.")

    return Mod(
        name=_CLIENT_MOD_NAME,
        path=package_path,
        active=True,
        id=_CLIENT_MOD_ID,
        version=version,
        description=description,
        activation_kind=ActivationKind.CLIENT_PACKAGE,
        supported_backends=("client",),
        restart_scope="client_launch",
        manifest_path=manifest_path,
        manager_path=manager_path,
        manager_protocol=_CLIENT_MANAGER_PROTOCOL,
        manager_sha256=actual_hash,
        client_build=_SUPPORTED_CLIENT_BUILD,
        evejs_version=actual_evejs_version,
        evejs_root=root,
    )


def _read_evejs_version(root: Path) -> str:
    package_path = root / "package.json"
    _require_plain_file(package_path, root, "EveJS package manifest")
    package = _read_json_object(
        package_path,
        maximum_bytes=_CLIENT_MANIFEST_MAX_BYTES,
        label="EveJS package manifest",
    )
    if package.get("name") != "eve.js":
        raise DLSS5ClientModError(
            "The selected root package.json is not an EveJS installation."
        )
    version = _require_text(
        package.get("version"),
        "EveJS package version",
        maximum=64,
    )
    if not _VERSION_PATTERN.fullmatch(version):
        raise DLSS5ClientModError("EveJS package version contains unsupported characters.")
    return version


def _run_dlss5_manager(
    package: Mod,
    *,
    workspace_root: Path,
    evejs_root: Path,
    client_root: Path,
    state_root: Path,
    action: str = "Ensure",
) -> None:
    if action not in {"Ensure", "Restore"}:
        raise DLSS5ClientModError("Unsupported trusted DLSS5 manager action.")
    manager_path = package.manager_path
    if manager_path is None:
        raise DLSS5ClientModError("The DLSS5 package manager path is missing.")
    expected_hash = package.manager_sha256.upper()
    if _sha256_file(manager_path) != expected_hash:
        raise DLSS5ClientModError(
            "The DLSS5 manager changed after package discovery. Refresh Mods and retry."
        )

    powershell = _windows_powershell_path()
    manager_environment = _windows_powershell_environment(powershell)
    command = [
        str(powershell),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(manager_path),
        "-Action",
        action,
        "-Profile",
        _CLIENT_PROFILE,
        "-WorkspaceRoot",
        str(workspace_root),
        "-EveJSRootPath",
        str(evejs_root),
        "-ClientRoot",
        str(client_root),
        "-StateRootPath",
        str(state_root),
    ]
    try:
        completed = _run_dlss5_preparation_process(
            command,
            cwd=package.path,
            environment=manager_environment,
            timeout=_CLIENT_MANAGER_PREPARE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise DLSS5ClientModError(
            "DLSS5 preparation exceeded one hour and was stopped. "
            "Nothing was launched; close all clients before retrying."
        ) from error
    except OSError as error:
        raise DLSS5ClientModError(
            f"Windows could not safely run the trusted DLSS5 manager: {error}"
        ) from error

    if _sha256_file(manager_path) != expected_hash:
        raise DLSS5ClientModError(
            "The DLSS5 manager changed while it was running. Nothing was launched."
        )
    if completed.returncode != 0:
        detail = _manager_output_detail(completed.stdout, completed.stderr)
        raise DLSS5ClientModError(
            "DLSS5 could not be safely prepared for this client.\n\n" + detail
        )


def _run_dlss5_preparation_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Contain PowerShell and its guard-builder descendants before execution.

    A root-only kill can orphan the generator. The existing platform Job Object
    contract starts suspended, assigns the complete tree, then resumes it. A
    timeout or launcher exit cannot leave that tree running independently.
    Temporary output files also avoid inherited-pipe deadlocks and unbounded
    in-memory output. This never starts or owns the game client itself.
    """
    if timeout <= 0:
        raise ValueError("DLSS5 preparation timeout must be positive.")
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        process = subprocess.Popen(
            command,
            shell=False,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            text=False,
            env=environment,
            **platform_api.get_suspended_hidden_process_flags(),
        )
        job_handle = None
        try:
            process_handle = getattr(process, "_handle", None)
            if not isinstance(process_handle, int) or process_handle <= 0:
                raise OSError("DLSS5 preparation process handle is unavailable.")
            job_handle = platform_api.create_kill_on_close_job(process_handle)
            if job_handle is None:
                raise OSError("Could not contain the DLSS5 preparation process tree.")
            if not platform_api.resume_process(process_handle):
                raise OSError("Could not resume the contained DLSS5 preparation.")
            returncode = process.wait(timeout=timeout)
        finally:
            # Closing this exact launcher-owned job is kill-on-close, including
            # any descendant surviving a normal PowerShell exit. Before job
            # assignment the suspended root has never been allowed to run.
            if job_handle is not None:
                try:
                    if process.poll() is None:
                        platform_api.terminate_job(job_handle)
                finally:
                    platform_api.close_job(job_handle)
            elif process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=_CLIENT_MANAGER_REAP_SECONDS)
            except subprocess.TimeoutExpired as error:
                raise OSError("DLSS5 preparation cleanup could not confirm process exit.") from error

        outputs = []
        for stream in (stdout_file, stderr_file):
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - _CLIENT_MANAGER_OUTPUT_MAX_BYTES))
            outputs.append(stream.read().decode("utf-8", errors="replace"))
        return subprocess.CompletedProcess(command, returncode, outputs[0], outputs[1])


def _windows_powershell_path() -> Path:
    windows_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not windows_root:
        raise DLSS5ClientModError("Windows PowerShell could not be located.")
    powershell = (
        Path(windows_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not powershell.is_file():
        raise DLSS5ClientModError(f"Windows PowerShell is missing: {powershell}")
    return powershell


def _windows_powershell_environment(powershell: Path) -> dict[str, str]:
    """Keep Windows PowerShell from importing incompatible PS7/Codex modules."""

    module_root = powershell.parent / "Modules"
    if not module_root.is_dir():
        raise DLSS5ClientModError(
            f"Windows PowerShell's built-in module directory is missing: {module_root}"
        )
    environment = dict(os.environ)
    environment["PSModulePath"] = str(module_root)
    return environment


def _manager_output_detail(stdout: str, stderr: str) -> str:
    lines = [
        line.strip()
        for line in (stdout + "\n" + stderr).splitlines()
        if line.strip()
    ]
    detail = "\n".join(lines)
    if len(detail) > 4_000:
        detail = detail[-4_000:]
    return detail or "The manager exited without a diagnostic message."


def _read_json_object(path: Path, *, maximum_bytes: int, label: str) -> dict[str, object]:
    size = path.stat().st_size
    if size < 1 or size > maximum_bytes:
        raise DLSS5ClientModError(f"{label} size is outside the supported limit.")
    raw = path.read_bytes()

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise DLSS5ClientModError(f"{label} contains duplicate key {key!r}.")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise DLSS5ClientModError(f"{label} is not valid UTF-8 JSON.") from error
    if type(payload) is not dict:
        raise DLSS5ClientModError(f"{label} must contain one JSON object.")
    return payload


def _require_exact_keys(payload: dict[str, object], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details: list[str] = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if unknown:
        details.append("unknown " + ", ".join(unknown))
    raise DLSS5ClientModError(f"{label} fields are invalid: {'; '.join(details)}.")


def _require_text(
    value: object,
    label: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str or value != value.strip():
        raise DLSS5ClientModError(f"{label} must be trimmed text.")
    if (not value and not allow_empty) or len(value) > maximum:
        raise DLSS5ClientModError(f"{label} length is invalid.")
    return value


def _require_sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256_PATTERN.fullmatch(value):
        raise DLSS5ClientModError(f"{label} must be 64 hexadecimal characters.")
    return value.upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _require_plain_directory(path: Path, boundary: Path, label: str) -> None:
    if not path.is_dir() or _is_reparse_point(path):
        raise DLSS5ClientModError(f"{label} must be an unlinked directory: {path}")
    resolved = path.resolve(strict=True)
    boundary_resolved = boundary.resolve(strict=True)
    if resolved != boundary_resolved and boundary_resolved not in resolved.parents:
        raise DLSS5ClientModError(f"{label} resolves outside its authorized boundary.")


def _require_plain_file(path: Path, boundary: Path, label: str) -> None:
    if not path.is_file() or _is_reparse_point(path):
        raise DLSS5ClientModError(f"{label} must be an unlinked regular file: {path}")
    resolved = path.resolve(strict=True)
    boundary_resolved = boundary.resolve(strict=True)
    if boundary_resolved not in resolved.parents:
        raise DLSS5ClientModError(f"{label} resolves outside its authorized boundary.")


def prepare_dlss5_profile_environment(
    profile_tq_path: Path,
    client_path: str | Path,
) -> dict[str, str]:
    """Prepare isolated ReShade state for one launcher character profile.

    DLSS binaries remain in the shared copied client. Only ReShade's mutable
    configuration, log, and transition-guard preference live beside the
    launcher profile, preventing two clients from reading or overwriting one
    another's F6 state.
    """
    profile_tq = Path(profile_tq_path)
    client_tq = Path(client_path)
    client_bin = client_tq / "bin64"

    missing = [name for name in _REQUIRED_CLIENT_FILES if not (client_bin / name).is_file()]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            "The selected EveJS installation enables DLSS5, but required client "
            f"files are missing: {joined}. Repair the DLSS5 mod before launching."
        )

    profile_parent = profile_tq.parent
    if not profile_parent.is_dir():
        raise RuntimeError(f"DLSS5 profile directory is unavailable: {profile_parent}")

    profile_base = profile_parent / _PROFILE_BASE_DIRECTORY
    if _path_entry_exists(profile_base) and _is_reparse_point(profile_base):
        raise RuntimeError(
            f"Refusing to use a linked DLSS5 profile-state directory: {profile_base}"
        )
    profile_base.mkdir(parents=False, exist_ok=True)

    shared_config = client_bin / _PROFILE_CONFIG_NAME
    shared_text = _read_optional_ini(shared_config)
    configured_base = _get_ini_value(shared_text, "INSTALL", "BasePath")
    if configured_base:
        raise RuntimeError(
            "The shared ReShade.ini defines [INSTALL] BasePath, which overrides "
            "the launcher's per-profile DLSS5 isolation. Remove that setting or "
            "repair the DLSS5 mod before launching."
        )

    profile_config = profile_base / _PROFILE_CONFIG_NAME
    if _path_entry_exists(profile_config) and _is_reparse_point(profile_config):
        raise RuntimeError(f"Refusing to replace a linked ReShade config: {profile_config}")

    if profile_config.is_file():
        profile_text = _read_ini(profile_config)
    else:
        profile_text = shared_text

    try:
        addon_path = os.path.relpath(profile_tq / "bin64", profile_base)
    except ValueError as error:
        raise RuntimeError(
            "The DLSS5 profile state and copied client must be on the same drive."
        ) from error

    updated = profile_text
    updated = _set_ini_value(updated, "ADDON", "AddonPath", addon_path)
    updated = _set_ini_value(
        updated,
        "ADDON",
        "LoadFromDllMain",
        "renodx-dlss5.addon64",
    )
    updated = _set_ini_value(updated, "RenoDX.DLSS5", "EnableHooks", "2")
    updated = _set_ini_value(
        updated,
        "RenoDX.DLSS5",
        "NeuralUplift",
        "1",
        preserve_existing=True,
    )

    if not profile_config.is_file() or updated != profile_text:
        _write_text_atomic(profile_config, updated)

    return {"RESHADE_BASE_PATH_OVERRIDE": str(profile_base)}


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _read_optional_ini(path: Path) -> str:
    if not path.is_file():
        return ""
    return _read_ini(path)


def _read_ini(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Could not read DLSS5 ReShade config: {path}") from error


def _get_ini_value(text: str, section: str, key: str) -> str | None:
    lines = text.splitlines()
    section_start, section_end = _find_ini_section(lines, section)
    if section_start is None:
        return None
    matches = _find_ini_keys(lines, section_start + 1, section_end, key)
    if len(matches) > 1:
        raise RuntimeError(f"Duplicate ReShade.ini key: [{section}] {key}")
    if not matches:
        return None
    return lines[matches[0]].split("=", 1)[1].strip()


def _set_ini_value(
    text: str,
    section: str,
    key: str,
    value: str,
    *,
    preserve_existing: bool = False,
) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    section_start, section_end = _find_ini_section(lines, section)

    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{section}]", f"{key}={value}"))
    else:
        matches = _find_ini_keys(lines, section_start + 1, section_end, key)
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate ReShade.ini key: [{section}] {key}")
        if matches:
            if preserve_existing:
                return text
            lines[matches[0]] = f"{key}={value}"
        else:
            lines.insert(section_end, f"{key}={value}")

    return newline.join(lines) + newline


def _find_ini_section(
    lines: list[str],
    section: str,
) -> tuple[int | None, int]:
    target = f"[{section}]".casefold()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.strip().casefold() == target
    ]
    if len(starts) > 1:
        raise RuntimeError(f"Duplicate ReShade.ini section: [{section}]")
    if not starts:
        return None, len(lines)
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    return start, end


def _find_ini_keys(
    lines: list[str],
    start: int,
    end: int,
    key: str,
) -> list[int]:
    target = key.casefold()
    matches: list[int] = []
    for index in range(start, end):
        stripped = lines[index].lstrip()
        if not stripped or stripped.startswith((";", "#")) or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip().casefold()
        if name == target:
            matches.append(index)
    return matches


def _write_text_atomic(path: Path, text: str) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
