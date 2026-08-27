"""Windows platform implementations for the EveJS Launcher.

Extracted from the original codebase — these are the native Windows
paths that were previously inline in launcher.py, profiles.py, etc.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import ctypes
import logging
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable


log = logging.getLogger(__name__)

# ── Native Win32 API handles (loaded once at module level) ────────────────
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll

_CREATE_SUSPENDED = 0x00000004
_ATTACH_PARENT_PROCESS = 0xFFFFFFFF
_CTRL_BREAK_EVENT = 1
_CLIENT_TRUST_SPAWN_MUTEX_NAME = "Local\\EveJSLauncherClientTrustSpawnV1"
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_DIRECTORY_LINK_TIMEOUT_SECONDS = 10
_CONSOLE_SIGNAL_LOCK = threading.Lock()
_CONSOLE_CTRL_HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)


@_CONSOLE_CTRL_HANDLER
def _ignore_console_control(_event: int) -> bool:
    """Keep the launcher alive while it shares the server's console."""
    return True


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateJobObject.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.AttachConsole.argtypes = [wintypes.DWORD]
kernel32.AttachConsole.restype = wintypes.BOOL
kernel32.FreeConsole.argtypes = []
kernel32.FreeConsole.restype = wintypes.BOOL
kernel32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
kernel32.GetConsoleProcessList.argtypes = [
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
]
kernel32.GetConsoleProcessList.restype = wintypes.DWORD
kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel32.ReleaseMutex.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
ntdll.NtResumeProcess.restype = wintypes.LONG


# ═══════════════════════════════════════════════════════════════════════════
# Subprocess flags
# ═══════════════════════════════════════════════════════════════════════════

def get_client_process_flags() -> dict[str, int]:
    """Popen kwargs for spawning the EVE client (detached, no console)."""
    return {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS,
    }


def launch_eve_client(
    exe_path: Path,
    env: dict[str, str],
    cwd: Path,
    *,
    arguments: tuple[str, ...] = (),
) -> subprocess.Popen:
    """Launch the EVE client executable directly (native Windows)."""
    # The client MUST be given real standard handles.  The launcher is a
    # windowed build (build.spec sets console=False) so its own fd 0/1/2 are
    # already invalid, and DETACHED_PROCESS means the client gets no console of
    # its own either.  Without explicit handles the client inherits that
    # invalid trio, and every raw ``print >> sys.stderr`` inside the client's
    # logmodule raises ``IOError: [Errno 9] Bad file descriptor``.
    #
    # That is not a cosmetic logging failure.  ``logmodule.LogTraceback`` ends
    # with an unguarded stderr print, so the IOError unwinds out of whatever
    # tasklet happened to log a warning -- including UI construction.  A single
    # benign warning during window setup then kills the window: opening
    # Neocom -> Industry logs "Possible service deadlock detected!" while
    # blueprintSvc is still starting, and the window is never built.
    #
    # DEVNULL is enough because NUL is a valid descriptor: the write succeeds
    # and is discarded.  Play.bat solves the same problem by redirecting to a
    # file (see its EBADF comment) if the client stream is ever wanted.
    return subprocess.Popen(
        [str(exe_path), *arguments],
        env=env,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **get_client_process_flags(),
    )


def get_hidden_process_flags() -> dict[str, int]:
    """Popen kwargs for background server processes (no console window)."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


@contextmanager
def serialize_evejs_client_trust_and_spawn(
    *,
    timeout_seconds: float = 660,
) -> Iterator[None]:
    """Serialize per-user certificate rotation through EVE process creation.

    The EVE certificate bundles and CurrentUser trust store are shared by every
    launcher process. Holding one Windows named mutex until ``Popen`` returns
    prevents another root from replacing the CA between validation and spawn.
    """
    if timeout_seconds <= 0:
        raise ValueError("Client launch serialization timeout must be positive.")
    handle = kernel32.CreateMutexW(
        None,
        False,
        _CLIENT_TRUST_SPAWN_MUTEX_NAME,
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), "Could not create client launch mutex.")

    acquired = False
    try:
        timeout_ms = min(int(timeout_seconds * 1_000), 0xFFFFFFFE)
        wait_result = int(
            kernel32.WaitForSingleObject(
                wintypes.HANDLE(handle),
                wintypes.DWORD(timeout_ms),
            )
        )
        if wait_result == _WAIT_TIMEOUT:
            raise RuntimeError(
                "Timed out waiting for another EveJS Launcher to finish "
                "preparing the shared EVE client."
            )
        if wait_result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
            raise OSError(
                ctypes.get_last_error(),
                f"Could not lock the shared EVE client (Windows result {wait_result}).",
            )
        acquired = True
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(wintypes.HANDLE(handle))
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _client_certificate_bundle_paths(client: Path) -> tuple[Path, ...]:
    """Return existing bundles from the official installer's bounded locations."""
    started_at = time.perf_counter()
    fixed = (
        client / "bin64" / "cacert.pem",
        client / "bin64" / "packages" / "certifi" / "cacert.pem",
        client / "bin" / "cacert.pem",
        client / "bin" / "packages" / "certifi" / "cacert.pem",
    )
    unique: dict[str, Path] = {}
    for path in fixed:
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        unique.setdefault(os.path.normcase(str(resolved)), resolved)
    bundles = tuple(unique.values())
    log.debug(
        "Checked %d bounded EVE certificate bundle paths; found %d in %.3fs",
        len(fixed),
        len(bundles),
        time.perf_counter() - started_at,
    )
    return bundles


def _normalize_pem_text(value: str) -> str:
    return value.replace("\r\n", "\n").strip()


def _selected_ca_is_in_client_bundles(root: Path, client: Path) -> bool:
    ca_path = root / "server" / "certs" / "xmpp-ca-cert.pem"
    try:
        ca_text = _normalize_pem_text(
            ca_path.read_text(encoding="utf-8", errors="strict")
        )
    except (OSError, UnicodeError):
        return False
    if not ca_text:
        return False

    bundles = _client_certificate_bundle_paths(client)
    if not bundles:
        return False
    for bundle in bundles:
        try:
            bundle_text = _normalize_pem_text(
                bundle.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            return False
        if ca_text not in bundle_text:
            return False
    return True


def prepare_evejs_client_certificate_trust(
    evejs_root: str | Path,
    client_path: str | Path,
    *,
    timeout_seconds: float = 600,
) -> bool:
    """Run EveJS's official certificate preparation before a client launch.

    EveJS v0.12.6 gives every installation its own local CA.  Its stock
    ``Play.bat`` therefore runs ``Install-EvEJSCerts.ps1`` before every launch
    so the selected CA replaces stale EveJS certificates in CurrentUser\\Root
    and in the EVE client's certifi bundles.  Directly spawning ExeFile.exe
    without this step makes chat and gateway TLS fail after switching roots.

    Older EveJS layouts without the official installer retain their previous
    launch path; no certificate files or stores are touched for those roots.
    """
    root = Path(evejs_root)
    client = Path(client_path)
    installer = (
        root
        / "tools"
        / "ClientSETUP"
        / "scripts"
        / "Install-EvEJSCerts.ps1"
    )
    if not installer.is_file():
        return False
    if not client.is_dir():
        raise FileNotFoundError(
            f"EVE client folder not found while preparing chat certificates: {client}"
        )
    if timeout_seconds <= 0:
        raise ValueError("Certificate preparation timeout must be positive.")
    client_bundles_are_current = _selected_ca_is_in_client_bundles(root, client)

    # The bundle belongs to the shared copied client, not to one account
    # profile. Never rotate it underneath a live EVE process. Multiple clients
    # from the same selected root remain supported because the official
    # installer is idempotent when every bundle already has the selected CA.
    if not client_bundles_are_current:
        from .overview_patch import is_eve_client_running

        if is_eve_client_running():
            raise RuntimeError(
                "Close every EVE client before switching EveJS installations. "
                "The selected installation uses a different local chat certificate."
            )

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = (
        system_root
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not powershell.is_file():
        # Windows PowerShell is present on supported Windows releases, but the
        # PATH fallback keeps source/test environments usable with a relocated
        # system directory.
        powershell = Path("powershell.exe")

    command = [
        str(powershell),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer),
        "-ClientPath",
        str(client),
    ]
    if client_bundles_are_current:
        # The official script still verifies and repairs CurrentUser trust, but
        # must not rediscover or rewrite already-verified client bundles.
        command.append("-SkipClientBundles")
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=float(timeout_seconds),
            **get_hidden_process_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Timed out while EveJS prepared the selected installation's "
            "chat certificates. Close every EVE client and try again."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            "Could not start EveJS's certificate preparation. "
            f"Windows reported: {exc}"
        ) from exc

    if completed.returncode != 0:
        output = "\n".join(
            line.strip()
            for line in (completed.stdout + "\n" + completed.stderr).splitlines()
            if line.strip()
        )
        if len(output) > 2_000:
            output = output[-2_000:]
        detail = output or f"PowerShell exited with code {completed.returncode}."
        raise RuntimeError(
            "EveJS could not prepare chat certificate trust for the selected "
            f"installation.\n\n{detail}"
        )
    if not _selected_ca_is_in_client_bundles(root, client):
        raise RuntimeError(
            "EveJS reported successful certificate preparation, but the selected "
            "CA is still missing from the EVE client certificate bundles."
        )
    return True


def get_graceful_server_process_flags() -> dict[str, object]:
    """Popen kwargs for a hidden server with its own controllable console.

    A private console gives the launcher a narrow Ctrl+Break target for Node's
    ``SIGBREAK`` shutdown hooks.  ``CREATE_NO_WINDOW`` cannot be used here
    because a console control event has nowhere to be delivered.
    """
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NEW_CONSOLE,
        "startupinfo": startup_info,
    }


def _console_process_ids() -> tuple[int, ...]:
    """Return process IDs attached to this process's current console."""
    capacity = 16
    while capacity <= 1024:
        buffer = (wintypes.DWORD * capacity)()
        count = int(kernel32.GetConsoleProcessList(buffer, capacity))
        if count == 0:
            return ()
        if count <= capacity:
            return tuple(int(buffer[index]) for index in range(count))
        capacity = count
    return ()


def request_graceful_server_shutdown(pid: int) -> bool:
    """Send Ctrl+Break only to one launcher's private server console.

    The game server is launched with its own hidden console.  This process
    briefly attaches to that console, handles console control itself, and
    generates the event for every process sharing that private console (the
    owned Node tree).  If source mode already has a console, it is restored
    through a retained peer process before returning.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("Graceful server shutdown requires a positive PID.")

    with _CONSOLE_SIGNAL_LOCK:
        original_processes = _console_process_ids()
        own_pid = os.getpid()
        restore_pids = tuple(
            process_id
            for process_id in original_processes
            if process_id != own_pid
        )
        if original_processes and not restore_pids:
            # Detaching the last process would destroy a console we cannot
            # reconstruct safely.  Let the caller use its exact-PID fallback.
            return False

        detached_original = False
        attached_target = False
        ignore_console_control = False
        signalled = False
        try:
            if original_processes:
                if not kernel32.FreeConsole():
                    return False
                detached_original = True
            if not kernel32.AttachConsole(wintypes.DWORD(pid)):
                return False
            attached_target = True
            if pid not in _console_process_ids():
                # The retained PID exited or did not own the console that was
                # attached.  Never broadcast a control event in that case.
                return False
            if not kernel32.SetConsoleCtrlHandler(_ignore_console_control, True):
                return False
            ignore_console_control = True
            signalled = bool(
                kernel32.GenerateConsoleCtrlEvent(_CTRL_BREAK_EVENT, 0)
            )
            if signalled:
                # Console events are dispatched asynchronously.  Keep the
                # launcher attached and handling the event briefly so Windows can
                # enter the target's handler before this console is detached.
                time.sleep(0.1)
            return signalled
        finally:
            detached_target = False
            if attached_target:
                # FreeConsole/AttachConsole reset this process's handler table.
                # Detach while console control is still handled so an
                # asynchronously dispatched event cannot race a manual handler
                # removal.
                detached_target = bool(kernel32.FreeConsole())
            if ignore_console_control and not detached_target:
                kernel32.SetConsoleCtrlHandler(_ignore_console_control, False)
            if detached_original:
                restored = any(
                    kernel32.AttachConsole(wintypes.DWORD(restore_pid))
                    for restore_pid in restore_pids
                )
                if not restored:
                    kernel32.AttachConsole(wintypes.DWORD(_ATTACH_PARENT_PROCESS))


def get_suspended_hidden_process_flags() -> dict[str, int]:
    """Popen kwargs for assigning a hidden child to a Job before it runs."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW | _CREATE_SUSPENDED}


def create_kill_on_close_job(process_handle: int) -> int | None:
    """Assign one suspended process to a Job inherited by all descendants."""
    if not isinstance(process_handle, int) or isinstance(process_handle, bool) or process_handle <= 0:
        raise ValueError("Job assignment requires a valid process handle.")
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        job,
        wintypes.HANDLE(process_handle),
    )
    if not assigned:
        kernel32.CloseHandle(job)
        return None
    return int(job)


def resume_process(process_handle: int) -> bool:
    """Resume a process created with ``CREATE_SUSPENDED``."""
    if not isinstance(process_handle, int) or isinstance(process_handle, bool) or process_handle <= 0:
        raise ValueError("Process resume requires a valid process handle.")
    return ntdll.NtResumeProcess(wintypes.HANDLE(process_handle)) >= 0


def terminate_job(job_handle: int) -> bool:
    """Terminate every process owned by one launcher-created Job Object."""
    if not isinstance(job_handle, int) or isinstance(job_handle, bool) or job_handle <= 0:
        raise ValueError("Job termination requires a valid handle.")
    return bool(kernel32.TerminateJobObject(wintypes.HANDLE(job_handle), 1))


def close_job(job_handle: int) -> None:
    """Close one launcher-owned Job handle (also enforcing kill-on-close)."""
    if not isinstance(job_handle, int) or isinstance(job_handle, bool) or job_handle <= 0:
        raise ValueError("Job close requires a valid handle.")
    kernel32.CloseHandle(wintypes.HANDLE(job_handle))


def terminate_process_tree(pid: int) -> bool:
    """Force-stop one retained Windows process tree by exact root PID."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("Process-tree termination requires a positive PID.")
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
        result = subprocess.run(
            [
                str(system_root / "System32" / "taskkill.exe"),
                "/F",
                "/T",
                "/PID",
                str(pid),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
            **get_hidden_process_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


_SAFE_TOOL_ARGUMENT = re.compile(r"[A-Za-z0-9_./:=+\-]+\Z")
_TOOL_WRAPPER_ENV_VAR = "EVEJS_LAUNCHER_TOOL_WRAPPER"


def build_tool_batch_command(
    entrypoint: str | Path,
    arguments: tuple[str, ...] = (),
) -> str:
    """Build the exact ``cmd.exe`` command for a curated tool wrapper.

    ``cmd.exe /s /c`` requires the inner executable path and the complete
    command string to have distinct quote pairs.  Passing this command as a
    string avoids Python's Windows argv quoting, which would otherwise escape
    the wrapper quotes with backslashes that ``cmd.exe`` does not understand.
    """
    wrapper = Path(entrypoint)
    wrapper_text = str(wrapper)
    if '"' in wrapper_text or "\r" in wrapper_text or "\n" in wrapper_text:
        raise ValueError(f"Unsupported tool wrapper path: {wrapper}")

    safe_arguments: list[str] = []
    for argument in arguments:
        value = str(argument)
        if not _SAFE_TOOL_ARGUMENT.fullmatch(value):
            raise ValueError(f"Unsupported tool argument: {value!r}")
        safe_arguments.append(value)

    # Do not interpolate the path into the command text.  Expanding one dedicated
    # environment variable keeps literal ``%NAME%`` text in its value from being
    # expanded again, while /v:off preserves legal exclamation marks in paths.
    command = f'cmd.exe /d /v:off /s /c ""%{_TOOL_WRAPPER_ENV_VAR}%"'
    if safe_arguments:
        command += " " + " ".join(safe_arguments)
    return command + '"'


def launch_tool_wrapper(
    entrypoint: str | Path,
    arguments: tuple[str, ...] = (),
) -> subprocess.Popen:
    """Launch a reviewed tool ``.bat`` in a visible independent console."""
    wrapper = Path(entrypoint)
    if wrapper.suffix.casefold() != ".bat":
        raise ValueError(f"Tool wrapper must be a .bat file: {wrapper}")
    if not wrapper.is_file():
        raise FileNotFoundError(f"Tool wrapper not found: {wrapper}")

    command = build_tool_batch_command(wrapper, arguments)
    env = os.environ.copy()
    env[_TOOL_WRAPPER_ENV_VAR] = str(wrapper)
    try:
        return subprocess.Popen(
            command,
            cwd=str(wrapper.parent),
            env=env,
            creationflags=(
                subprocess.CREATE_NEW_CONSOLE
                | subprocess.CREATE_NEW_PROCESS_GROUP
            ),
        )
    except OSError as exc:
        raise RuntimeError(
            f"Failed to launch tool wrapper '{wrapper.name}': {exc}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════
# Profile links (directory junctions)
# ═══════════════════════════════════════════════════════════════════════════

def create_directory_link(target: Path, link: Path) -> None:
    """Create a directory junction (``mklink /J`` — no admin required)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=_DIRECTORY_LINK_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create junction at {link}: {result.stderr.strip()}"
        )


def remove_directory_link(link: Path) -> None:
    """Remove a directory junction (``rmdir`` — safe, doesn't delete target)."""
    if link.exists():
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=True)


# ═══════════════════════════════════════════════════════════════════════════
# EVE client paths
# ═══════════════════════════════════════════════════════════════════════════

def get_client_exe_name() -> str:
    return "exefile.exe"


def get_market_binary_name() -> str:
    return "market-server.exe"


def get_client_exe_path(profile_tq_path: Path) -> Path:
    """Full path to the EVE client executable inside a profile."""
    return profile_tq_path / "bin64" / get_client_exe_name()


# ═══════════════════════════════════════════════════════════════════════════
# EVE settings path (under %LOCALAPPDATA%/CCP/EVE/<key>/settings/)
# ═══════════════════════════════════════════════════════════════════════════

def get_eve_settings_path(client_install_path: str) -> Path:
    """Resolve the EVE settings directory for a given install path.

    EVE derives a ``settingsKey`` from the executable path, so a unique
    junction path yields a unique settings folder — this is how per-account
    profile isolation works.
    """
    key = _derive_settings_key(client_install_path)
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    return Path(local_appdata) / "CCP" / "EVE" / key / "settings"


def _derive_settings_key(install_path: str) -> str:
    """Mirror EVE's ``PrepareClientSettings.ps1`` key derivation."""
    key = install_path.lower()
    key = key.replace(":", "")
    key = key.replace("\\", "_").replace("/", "_")
    key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    key = key.strip("_")
    return f"{key}_127.0.0.1"


# ═══════════════════════════════════════════════════════════════════════════
# Window detection (ctypes / native Win32 — no pygetwindow dependency)
# ═══════════════════════════════════════════════════════════════════════════

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _get_window_title(hwnd: int) -> str:
    """Return the text of *hwnd*, or ``\"\"`` on failure."""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Return ``(left, top, right, bottom)`` for *hwnd*."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def has_visible_window_for_pid(pid: int) -> bool:
    """Return whether one process owns a visible application-sized window."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("Window lookup requires a positive process ID.")

    found = False

    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if found:
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if int(owner_pid.value) != pid:
            return True
        left, top, right, bottom = _get_window_rect(hwnd)
        if (right - left) > 200 and (bottom - top) > 200:
            found = True
            return False
        return True

    user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return found


def find_eve_window(title: str = "EVE") -> bool:
    """Check whether an EVE client window is currently visible."""
    found = False

    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal found
        if found:
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        if _get_window_title(hwnd) != title:
            return True
        _left, _top, right, bottom = _get_window_rect(hwnd)
        if (right - _left) > 200 and (bottom - _top) > 200:
            found = True
            return False
        return True

    user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return found


def find_and_focus_eve_window(title: str = "EVE") -> bool:
    """Find the EVE client window and bring it to the foreground.

    Returns True if a matching window was found and focused.
    """
    target_hwnd: int | None = None

    def _callback(hwnd: int, _lparam: int) -> bool:
        nonlocal target_hwnd
        if target_hwnd is not None:
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        win_title = _get_window_title(hwnd)
        if title.lower() not in win_title.lower():
            return True
        _left, _top, right, _bottom = _get_window_rect(hwnd)
        if (right - _left) > 200:
            target_hwnd = hwnd
            return False
        return True

    user32.EnumWindows(_WNDENUMPROC(_callback), 0)

    if target_hwnd is None:
        return False

    # Restore if minimized, then bring to foreground.
    SW_RESTORE = 9
    if user32.IsIconic(target_hwnd):
        user32.ShowWindow(target_hwnd, SW_RESTORE)
    user32.SetForegroundWindow(target_hwnd)
    return True


# ═══════════════════════════════════════════════════════════════════════════
# File helpers
# ═══════════════════════════════════════════════════════════════════════════

def open_text_editor(file_path: Path) -> None:
    """Open *file_path* in Notepad."""
    subprocess.Popen(["notepad.exe", str(file_path)])


def get_exe_file_filter() -> str:
    """Qt file-dialog filter string for executables."""
    return "Executables (*.exe);;All Files (*)"


# ═══════════════════════════════════════════════════════════════════════════
# Process
# ═══════════════════════════════════════════════════════════════════════════

def hard_exit() -> None:
    """Immediate hard exit — no atexit, no Python cleanup.

    Necessary after spawning the updater helper to avoid transient
    ``Failed to load Python DLL`` dialogs during PyInstaller shutdown.
    """
    os._exit(0)


# ═══════════════════════════════════════════════════════════════════════════
# Auto-updater  (staged GUI agent from the downloaded build)
# ═══════════════════════════════════════════════════════════════════════════

def run_updater(
    download_url: str,
    current_exe_path: Path,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    status_callback: Callable[[str, str], None] | None = None,
) -> bool:
    """Stage an update and start the new build in dedicated updater mode.

    The downloaded onedir build supplies the independent GUI process that
    stays visible while the current launcher exits and the folder is swapped.
    This avoids the otherwise unexplained gap between the old app closing and
    the new app restarting.
    """
    import shutil
    import tempfile
    import zipfile

    from ..updater.github import download_asset

    current_exe_path = Path(current_exe_path)
    install_dir = current_exe_path.parent
    exe_name = current_exe_path.name
    staging_root = Path(tempfile.mkdtemp(prefix="evejs_launcher_update_"))
    zip_path = staging_root / "update.zip"
    extract_dir = staging_root / "staged"

    _notify_update_status(status_callback, "download", "Downloading update…")
    if not download_asset(download_url, zip_path, progress_callback=progress_callback):
        shutil.rmtree(staging_root, ignore_errors=True)
        return False

    _notify_update_status(status_callback, "prepare", "Validating update package…")
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            if archive.testzip() is not None:
                raise zipfile.BadZipFile("Update package failed its CRC check")
            _notify_update_status(status_callback, "prepare", "Unpacking update package…")
            _safe_extract_update_archive(archive, extract_dir)
    except (zipfile.BadZipFile, OSError):
        shutil.rmtree(staging_root, ignore_errors=True)
        return False
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass

    new_exe = _find_exe_in_folder(extract_dir, exe_name)
    if new_exe is None:
        shutil.rmtree(staging_root, ignore_errors=True)
        return False

    new_install_dir = new_exe.parent
    _notify_update_status(status_callback, "install", "Starting the updater…")
    try:
        subprocess.Popen(
            [
                str(new_exe),
                "--apply-update",
                "--target-dir",
                str(install_dir),
                "--source-dir",
                str(new_install_dir),
                "--exe-name",
                exe_name,
                "--parent-pid",
                str(os.getpid()),
            ],
            cwd=str(new_install_dir),
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            ),
            close_fds=True,
        )
        return True
    except OSError:
        shutil.rmtree(staging_root, ignore_errors=True)
        return False


# ── Updater helpers ──────────────────────────────────────────────────────


def _notify_update_status(
    callback: Callable[[str, str], None] | None,
    stage: str,
    detail: str,
) -> None:
    """Deliver an optional updater phase without letting UI failures abort it."""
    if callback is None:
        return
    try:
        callback(stage, detail)
    except Exception:
        pass


def _safe_extract_update_archive(archive, destination: Path) -> None:  # type: ignore[no-untyped-def]
    """Extract only archive members that remain inside *destination*."""
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        if not member_path.is_relative_to(destination_root):
            raise OSError(f"Unsafe update archive member: {member.filename}")
    archive.extractall(destination)


def _find_exe_in_folder(folder: Path, exe_name: str) -> Path | None:
    """Search *folder* (and one nested release folder) for *exe_name*."""
    candidate = folder / exe_name
    if candidate.is_file():
        return candidate

    try:
        for child in folder.iterdir():
            if child.is_dir():
                nested = child / exe_name
                if nested.is_file():
                    return nested
    except OSError:
        pass
    return None
