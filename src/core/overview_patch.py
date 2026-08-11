"""Build-gated, reversible client patch for silent overview transfer."""
from __future__ import annotations

from copy import copy
import ctypes
from dataclasses import dataclass
from enum import Enum
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
import zlib

from .client_autologin import SUPPORTED_BUILD
from .platform import get_hidden_process_flags


SUPPORTED_CODE_SHA256 = (
    "89696509EFDC1B081F7371B40CA3D459059DB0E43B5DB328FE373C0F2A9B1A86"
)
TARGET_ENTRY = "eve/client/script/parklife/overview/presetservice.pyj"
MARKER_ENTRY = "evejs_launcher/overview_bridge_v1.json"
PATCH_VERSION = 3
BACKUP_NAME = "code.ccp.evejs-launcher-original"
_SOURCE_MARKER = "EVEJS_LAUNCHER_OVERVIEW_BRIDGE_V3"
_PY27_MAGIC = b"\x03\xf3\r\n"
_COMPILE_LOCK = threading.Lock()


class OverviewPatchState(Enum):
    MISSING = "missing"
    READY = "ready"
    PATCHED = "patched"
    LEGACY = "legacy"
    UNSUPPORTED = "unsupported"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class OverviewPatchStatus:
    state: OverviewPatchState
    reason: str
    build: int | None = None

    @property
    def can_patch(self) -> bool:
        return self.state is OverviewPatchState.READY

    @property
    def can_restore(self) -> bool:
        return self.state in {
            OverviewPatchState.PATCHED,
            OverviewPatchState.LEGACY,
        }


class OverviewPatchError(RuntimeError):
    """Raised when patch or restore validation fails closed."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _read_build(start_ini: Path) -> int | None:
    try:
        text = start_ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"(?im)^\s*build\s*=\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else None


def _marker_from_archive(archive: zipfile.ZipFile) -> dict | None:
    infos = [info for info in archive.infolist() if info.filename == MARKER_ENTRY]
    if not infos:
        return None
    if len(infos) != 1:
        raise OverviewPatchError("The overview patch marker is duplicated.")
    try:
        marker = json.loads(archive.read(infos[0]).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OverviewPatchError("The overview patch marker is invalid.") from exc
    return marker if isinstance(marker, dict) else None


def inspect_overview_patch(client_path: str | Path) -> OverviewPatchStatus:
    client = Path(client_path)
    code_path = client / "code.ccp"
    if not client.is_dir() or not code_path.is_file():
        return OverviewPatchStatus(
            OverviewPatchState.MISSING,
            "Select the copied EVE client tq folder first.",
        )
    build = _read_build(client / "start.ini")
    if build != SUPPORTED_BUILD:
        label = "unknown" if build is None else str(build)
        return OverviewPatchStatus(
            OverviewPatchState.UNSUPPORTED,
            f"EVE build {label} is not supported by the overview bridge.",
            build,
        )
    try:
        with zipfile.ZipFile(code_path, "r") as archive:
            marker = _marker_from_archive(archive)
            if marker is not None:
                patch_version = marker.get("patchVersion")
                if (
                    patch_version not in {1, 2, PATCH_VERSION}
                    or marker.get("originalArchiveSHA256") != SUPPORTED_CODE_SHA256
                ):
                    raise OverviewPatchError("The installed overview patch is unknown.")
                target_index = marker.get("targetIndex")
                infos = archive.infolist()
                if (
                    isinstance(target_index, bool)
                    or not isinstance(target_index, int)
                    or target_index < 0
                    or target_index >= len(infos)
                    or infos[target_index].filename != TARGET_ENTRY
                ):
                    raise OverviewPatchError("The patched overview entry is misplaced.")
                target_hash = hashlib.sha256(archive.read(infos[target_index])).hexdigest().upper()
                if target_hash != marker.get("patchedEntrySHA256"):
                    raise OverviewPatchError("The patched overview entry failed verification.")
                backup = client / BACKUP_NAME
                if not backup.is_file() or _sha256(backup) != SUPPORTED_CODE_SHA256:
                    raise OverviewPatchError("The verified original client backup is missing.")
                if patch_version == PATCH_VERSION:
                    return OverviewPatchStatus(
                        OverviewPatchState.PATCHED,
                        "Overview copy bridge v3 installed; original backup verified.",
                        build,
                    )
                return OverviewPatchStatus(
                    OverviewPatchState.LEGACY,
                    "Legacy overview bridge v1/v2 detected. Restore the original, "
                    "then install the corrected v3 bridge.",
                    build,
                )
    except (OSError, zipfile.BadZipFile, OverviewPatchError) as exc:
        return OverviewPatchStatus(OverviewPatchState.CORRUPT, str(exc), build)

    try:
        archive_hash = _sha256(code_path)
    except OSError as exc:
        return OverviewPatchStatus(OverviewPatchState.CORRUPT, str(exc), build)
    if archive_hash != SUPPORTED_CODE_SHA256:
        return OverviewPatchStatus(
            OverviewPatchState.UNSUPPORTED,
            "This code.ccp is modified or does not match the proven build 3396210 archive.",
            build,
        )
    return OverviewPatchStatus(
        OverviewPatchState.READY,
        "Supported EVE build 3396210; ready to install the optional overview bridge.",
        build,
    )


def is_eve_client_running() -> bool:
    """Return whether any EVE client process is visible to the current user."""
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq exefile.exe", "/FO", "CSV", "/NH"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
            **get_hidden_process_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        # Patching a live archive is unsafe. If Windows cannot prove that no
        # client is running, fail closed and make the user retry the check.
        return True
    if completed.returncode != 0:
        return True
    return '"exefile.exe"' in completed.stdout.casefold()


def _decompile_pyc(pyc: bytes) -> str:
    try:
        from uncompyle6.main import decompile_file
    except ImportError as exc:
        raise OverviewPatchError(
            "The overview patch dependency is missing. Reinstall or update the launcher."
        ) from exc
    temporary_path: Path | None = None
    output = StringIO()
    try:
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as temporary:
            temporary.write(pyc)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        decompile_file(str(temporary_path), outstream=output)
    except Exception as exc:
        raise OverviewPatchError("The supported overview module could not be decompiled.") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return output.getvalue()


_BRIDGE_METHODS = r'''
    # EVEJS_LAUNCHER_OVERVIEW_BRIDGE_V3
    def _EveJSOverviewBridgeWaitForSession(self):
        command = os.environ.get('EVEJS_OVERVIEW_BRIDGE', '')
        if not command:
            return
        parts = command.split('|')
        try:
            expectedCharacterID = int(parts[1])
        except Exception:
            return
        for attempt in xrange(300):
            currentCharacterID = int(getattr(session, 'charid', 0) or 0)
            if currentCharacterID == expectedCharacterID:
                blue.synchro.SleepWallclock(5000)
                self._EveJSOverviewBridgeTryRun()
                if getattr(self, '_evejsOverviewBridgeDone', False):
                    return
            blue.synchro.SleepWallclock(1000)
        self._EveJSOverviewBridgeWriteAck('error|%s|character session was not ready' % expectedCharacterID)
        return

    def _EveJSOverviewBridgeWriteAck(self, text):
        ackPath = os.environ.get('EVEJS_OVERVIEW_ACK_PATH', '')
        if not ackPath:
            return
        try:
            ackDirectory = os.path.dirname(ackPath)
            if ackDirectory and not os.path.isdir(ackDirectory):
                os.makedirs(ackDirectory)
            temporaryPath = ackPath + '.tmp'
            ackFile = open(temporaryPath, 'wb')
            try:
                if isinstance(text, unicode):
                    text = text.encode('utf-8')
                ackFile.write(text)
                ackFile.flush()
            finally:
                ackFile.close()
            if os.path.exists(ackPath):
                os.remove(ackPath)
            os.rename(temporaryPath, ackPath)
        except Exception:
            log.exception('EveJS overview bridge could not write its acknowledgement')
        return

    def _EveJSOverviewBridgeTryRun(self):
        if getattr(self, '_evejsOverviewBridgeDone', False):
            return
        command = os.environ.get('EVEJS_OVERVIEW_BRIDGE', '')
        if not command:
            return
        parts = command.split('|')
        try:
            expectedCharacterID = int(parts[1])
        except Exception:
            return
        currentCharacterID = int(getattr(session, 'charid', 0) or 0)
        if currentCharacterID != expectedCharacterID:
            return
        self._evejsOverviewBridgeDone = True
        try:
            action = parts[0]
            if action == 'capture' and len(parts) == 2:
                data = self.GetOverviewDataForSave()
                presetKey = sm.RemoteSvc('overviewPresetMgr').StoreLinkAndGetID(data)
                hashvalue = getattr(presetKey, 'hashvalue', '')
                sqID = int(getattr(presetKey, 'sqID', 0) or 0)
                if not hashvalue or sqID <= 0:
                    raise RuntimeError('The EveJS server did not store the overview snapshot')
                self._EveJSOverviewBridgeWriteAck('capture|%s|%s|%s' % (currentCharacterID, hashvalue, sqID))
            elif action == 'apply' and len(parts) == 4:
                hashvalue = parts[2]
                sqID = int(parts[3])
                presetKey = utillib.KeyVal(hashvalue=hashvalue, sqID=sqID)
                yamlString = sm.RemoteSvc('overviewPresetMgr').GetStoredPreset(presetKey)
                if yamlString is None:
                    raise RuntimeError('The selected EveJS overview snapshot was not found')
                dataList = yaml.safe_load(yamlString)
                data = GetDictFromList(dataList)
                self.StoreCurrentProfileDataInSettings()
                self.LoadOverviewProfileFromDict(data, 'Launcher copy', presetKey, saveInHistory=True)
                self._EveJSOverviewBridgeWriteAck('apply|%s|%s|%s' % (currentCharacterID, hashvalue, sqID))
            else:
                raise RuntimeError('Invalid overview bridge command')
        except Exception as exc:
            message = str(exc).replace('|', ' ').replace('\r', ' ').replace('\n', ' ')[:240]
            self._EveJSOverviewBridgeWriteAck('error|%s|%s' % (currentCharacterID, message))
            log.exception('EveJS overview bridge failed')
        return

'''


# uncompyle6 3.9.3 reconstructs one Python 2.7 conditional attribute expression
# incorrectly in this exact, hash-gated CCP module. For established characters
# ``hadOverviewSettings`` is True, so the decompiled expression tries to call
# ``True._LoadGeneralSettings`` and breaks every overview-dependent UI layer.
# Repair that one known artifact before compiling; fail closed if the supported
# module no longer contains the exact expression we audited.
_DECOMPILER_ARTIFACT = (
    "self.defaultOverviews = DefaultOverviews("
    "general_settings_loader=(self.hadOverviewSettings or self)."
    "_LoadGeneralSettings if 1 else None)"
)
_DECOMPILER_REPAIR = (
    "self.defaultOverviews = DefaultOverviews("
    "general_settings_loader=self._LoadGeneralSettings "
    "if not self.hadOverviewSettings else None)"
)


def _patch_source(source: str) -> str:
    if _SOURCE_MARKER in source:
        raise OverviewPatchError("The overview module is already patched.")
    artifact_count = source.count(_DECOMPILER_ARTIFACT)
    if artifact_count != 1:
        raise OverviewPatchError(
            "The supported overview decompiler repair point was not found exactly once."
        )
    source = source.replace(_DECOMPILER_ARTIFACT, _DECOMPILER_REPAIR, 1)
    source = re.sub(r"\nreturn\s*\Z", "\n", source)
    source, import_count = re.subn(
        r"(^import logging\s*$)",
        r"\1\nimport os\nimport uthread2",
        source,
        count=1,
        flags=re.MULTILINE,
    )
    run_tail = (
        "        self.Initialize()\n"
        "        return\n\n"
        "    def _HadOverviewSettings"
    )
    replacement = (
        "        self.Initialize()\n"
        "        self._evejsOverviewBridgeDone = False\n"
        "        uthread2.StartTasklet(self._EveJSOverviewBridgeWaitForSession)\n"
        "        return\n\n"
        + _BRIDGE_METHODS
        + "    def _HadOverviewSettings"
    )
    if run_tail not in source:
        raise OverviewPatchError("The supported overview Run method was not found.")
    source = source.replace(run_tail, replacement, 1)
    if import_count != 1 or source.count(_SOURCE_MARKER) != 1:
        raise OverviewPatchError("The overview source transformation was incomplete.")
    return "# -*- coding: utf-8 -*-\n" + source


def _compile_with_client_python(source: str, python_dll: Path, header: bytes) -> bytes:
    if os.name != "nt" or not python_dll.is_file():
        raise OverviewPatchError("The supported client's python27.dll is missing.")
    if len(header) != 8 or header[:4] != _PY27_MAGIC:
        raise OverviewPatchError("The overview bytecode header is not Python 2.7.")
    with _COMPILE_LOCK:
        library = ctypes.WinDLL(str(python_dll))
        for name in (
            "Py_NoSiteFlag",
            "Py_IgnoreEnvironmentFlag",
            "Py_DontWriteBytecodeFlag",
        ):
            ctypes.c_int.in_dll(library, name).value = 1
        library.Py_Initialize.argtypes = []
        library.Py_Initialize.restype = None
        library.Py_CompileString.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        library.Py_CompileString.restype = ctypes.c_void_p
        library.PyMarshal_WriteObjectToString.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.PyMarshal_WriteObjectToString.restype = ctypes.c_void_p
        library.PyString_AsStringAndSize.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_ssize_t),
        ]
        library.PyString_AsStringAndSize.restype = ctypes.c_int
        library.Py_DecRef.argtypes = [ctypes.c_void_p]
        library.Py_DecRef.restype = None
        library.Py_Initialize()
        code = library.Py_CompileString(
            source.encode("utf-8"),
            b"eve/client/script/parklife/overview/presetservice.py",
            257,
        )
        if not code:
            raise OverviewPatchError("The patched overview source did not compile.")
        marshaled = library.PyMarshal_WriteObjectToString(code, 2)
        library.Py_DecRef(code)
        if not marshaled:
            raise OverviewPatchError("The patched overview bytecode could not be marshaled.")
        pointer = ctypes.c_char_p()
        size = ctypes.c_ssize_t()
        try:
            if library.PyString_AsStringAndSize(
                marshaled,
                ctypes.byref(pointer),
                ctypes.byref(size),
            ) != 0:
                raise OverviewPatchError("The patched overview bytecode could not be read.")
            payload = ctypes.string_at(pointer, size.value)
        finally:
            library.Py_DecRef(marshaled)
    return header + payload


def _graft_original_overview_methods(
    original_pyc: bytes,
    compiled_pyc: bytes,
    python_dll: Path,
) -> bytes:
    """Restore every original CCP method body except the intentionally changed Run.

    The bridge source must be compiled to add imports and new methods, but a full
    decompile/recompile is too broad for a client patch. This uses the copied
    client's own Python 2.7 runtime to place the *original code objects* back
    into the compiled class. Only ``Run`` and the three launcher bridge methods
    remain compiled from launcher-owned source.
    """
    if (
        len(original_pyc) < 8
        or len(compiled_pyc) < 8
        or original_pyc[:4] != _PY27_MAGIC
        or compiled_pyc[:4] != _PY27_MAGIC
    ):
        raise OverviewPatchError("The overview bytecode header is invalid.")
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as output:
            output_path = Path(output.name)
        script = r'''
import marshal

original_pyc = str(bytearray.fromhex('__ORIGINAL_HEX__'))
compiled_pyc = str(bytearray.fromhex('__COMPILED_HEX__'))
output_path = r'__OUTPUT__'

original_module = marshal.loads(original_pyc[8:])
compiled_module = marshal.loads(compiled_pyc[8:])
code_type = type(original_module)

def code_constants(code):
    return [value for value in code.co_consts if isinstance(value, code_type)]

def one_named(code, name):
    matches = [value for value in code_constants(code) if value.co_name == name]
    if len(matches) != 1:
        raise RuntimeError('Expected one code object named %s, found %s' % (name, len(matches)))
    return matches[0]

def rebuild(code, constants):
    return code_type(
        code.co_argcount,
        code.co_nlocals,
        code.co_stacksize,
        code.co_flags,
        code.co_code,
        tuple(constants),
        code.co_names,
        code.co_varnames,
        code.co_filename,
        code.co_name,
        code.co_firstlineno,
        code.co_lnotab,
        code.co_freevars,
        code.co_cellvars,
    )

original_class = one_named(original_module, 'OverviewPresetSvc')
compiled_class = one_named(compiled_module, 'OverviewPresetSvc')
original_methods = {}
for method in code_constants(original_class):
    if method.co_name in original_methods:
        raise RuntimeError('Duplicate original method code: %s' % method.co_name)
    original_methods[method.co_name] = method

required_bridge_methods = set((
    '_EveJSOverviewBridgeWaitForSession',
    '_EveJSOverviewBridgeWriteAck',
    '_EveJSOverviewBridgeTryRun',
))
compiled_method_names = set(method.co_name for method in code_constants(compiled_class))
if not required_bridge_methods.issubset(compiled_method_names):
    raise RuntimeError('Compiled overview bridge methods are incomplete')
if 'Run' not in compiled_method_names or 'Run' not in original_methods:
    raise RuntimeError('Overview Run method is missing')

class_constants = []
replaced = 0
for value in compiled_class.co_consts:
    if (
        isinstance(value, code_type)
        and value.co_name in original_methods
        and value.co_name != 'Run'
    ):
        value = original_methods[value.co_name]
        replaced += 1
    class_constants.append(value)
expected_replacements = len(original_methods) - 1
if replaced != expected_replacements:
    raise RuntimeError(
        'Preserved %s original methods; expected %s' % (replaced, expected_replacements)
    )
hybrid_class = rebuild(compiled_class, class_constants)

module_constants = []
class_replacements = 0
for value in compiled_module.co_consts:
    if isinstance(value, code_type) and value.co_name == 'OverviewPresetSvc':
        value = hybrid_class
        class_replacements += 1
    module_constants.append(value)
if class_replacements != 1:
    raise RuntimeError('Overview class graft point is ambiguous')
hybrid_module = rebuild(compiled_module, module_constants)
with open(output_path, 'wb') as output_file:
    output_file.write(compiled_pyc[:8] + marshal.dumps(hybrid_module, 2))
'''
        script = script.replace(
            "__ORIGINAL_HEX__",
            original_pyc.hex(),
        ).replace(
            "__COMPILED_HEX__",
            compiled_pyc.hex(),
        ).replace(
            "__OUTPUT__",
            str(output_path).replace("\\", "\\\\").replace("'", "\\'"),
        )
        with _COMPILE_LOCK:
            library = ctypes.WinDLL(str(python_dll))
            library.Py_Initialize.argtypes = []
            library.Py_Initialize.restype = None
            library.PyRun_SimpleString.argtypes = [ctypes.c_char_p]
            library.PyRun_SimpleString.restype = ctypes.c_int
            library.Py_Initialize()
            result = library.PyRun_SimpleString(script.encode("ascii"))
        if result != 0 or not output_path.is_file():
            raise OverviewPatchError(
                "The original CCP overview methods could not be preserved."
            )
        hybrid_pyc = output_path.read_bytes()
    except OverviewPatchError:
        raise
    except Exception as exc:
        raise OverviewPatchError(
            "The original CCP overview methods could not be preserved."
        ) from exc
    finally:
        if output_path is not None and output_path.exists():
            output_path.unlink()
    if len(hybrid_pyc) < 8 or hybrid_pyc[:4] != _PY27_MAGIC:
        raise OverviewPatchError("The preserved overview bytecode is invalid.")
    return hybrid_pyc


def _build_patched_archive(source_path: Path, stage_path: Path, client: Path) -> dict:
    with zipfile.ZipFile(source_path, "r") as source_archive:
        source_infos = source_archive.infolist()
        target_indexes = [
            index for index, info in enumerate(source_infos) if info.filename == TARGET_ENTRY
        ]
        if len(target_indexes) != 1:
            raise OverviewPatchError("The supported overview module is missing or duplicated.")
        target_index = target_indexes[0]
        original_entry = source_archive.read(source_infos[target_index])
        try:
            original_pyc = zlib.decompress(original_entry)
        except zlib.error as exc:
            raise OverviewPatchError("The overview module has an invalid compression layer.") from exc
        source = _decompile_pyc(original_pyc)
        patched_source = _patch_source(source)
        patched_pyc = _compile_with_client_python(
            patched_source,
            client / "bin64" / "python27.dll",
            original_pyc[:8],
        )
        patched_pyc = _graft_original_overview_methods(
            original_pyc,
            patched_pyc,
            client / "bin64" / "python27.dll",
        )
        patched_entry = zlib.compress(patched_pyc, 9)
        marker = {
            "patchVersion": PATCH_VERSION,
            "originalArchiveSHA256": SUPPORTED_CODE_SHA256,
            "targetEntry": TARGET_ENTRY,
            "targetIndex": target_index,
            "originalEntrySHA256": hashlib.sha256(original_entry).hexdigest().upper(),
            "patchedEntrySHA256": hashlib.sha256(patched_entry).hexdigest().upper(),
        }

        with zipfile.ZipFile(stage_path, "w", allowZip64=True) as target_archive:
            target_archive.comment = source_archive.comment
            for index, info in enumerate(source_infos):
                payload = patched_entry if index == target_index else source_archive.read(info)
                target_archive.writestr(copy(info), payload)
            marker_info = zipfile.ZipInfo(MARKER_ENTRY, date_time=(1980, 1, 1, 0, 0, 0))
            marker_info.compress_type = zipfile.ZIP_DEFLATED
            marker_info.external_attr = 0o644 << 16
            target_archive.writestr(
                marker_info,
                json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )

    with zipfile.ZipFile(source_path, "r") as source_archive, zipfile.ZipFile(
        stage_path, "r"
    ) as target_archive:
        if target_archive.testzip() is not None:
            raise OverviewPatchError("The staged client archive failed its CRC test.")
        source_infos = source_archive.infolist()
        target_infos = target_archive.infolist()
        if len(target_infos) != len(source_infos) + 1:
            raise OverviewPatchError("The staged client archive has the wrong entry count.")
        for index, source_info in enumerate(source_infos):
            target_info = target_infos[index]
            if target_info.filename != source_info.filename:
                raise OverviewPatchError("The staged client archive entry order changed.")
            expected = patched_entry if index == target_index else source_archive.read(source_info)
            if target_archive.read(target_info) != expected:
                raise OverviewPatchError("The staged client archive failed payload verification.")
        parsed_marker = _marker_from_archive(target_archive)
        if parsed_marker != marker:
            raise OverviewPatchError("The staged client archive marker failed verification.")
    return marker


def patch_overview_client(client_path: str | Path) -> OverviewPatchStatus:
    client = Path(client_path)
    status = inspect_overview_patch(client)
    if status.state is OverviewPatchState.PATCHED:
        return status
    if not status.can_patch:
        raise OverviewPatchError(status.reason)
    if is_eve_client_running():
        raise OverviewPatchError("Close every EVE client before patching code.ccp.")

    code_path = client / "code.ccp"
    backup_path = client / BACKUP_NAME
    stage_path = client / f".{code_path.name}.evejs-launcher-stage"
    backup_stage = client / f".{BACKUP_NAME}.stage"
    installed_replacement = False
    try:
        if backup_path.exists():
            if not backup_path.is_file() or _sha256(backup_path) != SUPPORTED_CODE_SHA256:
                raise OverviewPatchError("The existing original backup is not trustworthy.")
        else:
            shutil.copy2(code_path, backup_stage)
            if _sha256(backup_stage) != SUPPORTED_CODE_SHA256:
                raise OverviewPatchError("The staged original backup failed verification.")
            os.replace(backup_stage, backup_path)

        _build_patched_archive(code_path, stage_path, client)
        with stage_path.open("r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(stage_path, code_path)
        installed_replacement = True
        installed = inspect_overview_patch(client)
        if installed.state is not OverviewPatchState.PATCHED:
            raise OverviewPatchError(installed.reason)
        return installed
    except Exception as exc:
        if installed_replacement and backup_path.is_file():
            try:
                shutil.copy2(backup_path, stage_path)
                if _sha256(stage_path) != SUPPORTED_CODE_SHA256:
                    raise OverviewPatchError("Automatic patch rollback failed verification.")
                os.replace(stage_path, code_path)
            except Exception as rollback_exc:
                raise OverviewPatchError(
                    "Client patching failed and automatic rollback also failed. "
                    f"The verified original remains at {backup_path}. "
                    f"Rollback error: {rollback_exc}"
                ) from exc
        raise
    finally:
        for temporary in (stage_path, backup_stage):
            if temporary.exists():
                temporary.unlink()


def restore_overview_client(client_path: str | Path) -> OverviewPatchStatus:
    client = Path(client_path)
    status = inspect_overview_patch(client)
    if status.state is OverviewPatchState.READY:
        return status
    if not status.can_restore:
        raise OverviewPatchError(status.reason)
    if is_eve_client_running():
        raise OverviewPatchError("Close every EVE client before restoring code.ccp.")

    code_path = client / "code.ccp"
    backup_path = client / BACKUP_NAME
    stage_path = client / f".{code_path.name}.evejs-launcher-restore"
    try:
        if _sha256(backup_path) != SUPPORTED_CODE_SHA256:
            raise OverviewPatchError("The original client backup failed verification.")
        shutil.copy2(backup_path, stage_path)
        if _sha256(stage_path) != SUPPORTED_CODE_SHA256:
            raise OverviewPatchError("The staged client restore failed verification.")
        os.replace(stage_path, code_path)
        restored = inspect_overview_patch(client)
        if restored.state is not OverviewPatchState.READY:
            raise OverviewPatchError(restored.reason)
        return restored
    finally:
        if stage_path.exists():
            stage_path.unlink()
