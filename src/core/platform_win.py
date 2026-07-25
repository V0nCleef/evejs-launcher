"""Windows platform implementations for the EveJS Launcher.

Extracted from the original codebase — these are the native Windows
paths that were previously inline in launcher.py, profiles.py, etc.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

# ── Native Win32 API handles (loaded once at module level) ────────────────
user32 = ctypes.windll.user32


# ═══════════════════════════════════════════════════════════════════════════
# Subprocess flags
# ═══════════════════════════════════════════════════════════════════════════

def get_client_process_flags() -> dict[str, int]:
    """Popen kwargs for spawning the EVE client (detached, no console)."""
    return {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS,
    }


def launch_eve_client(exe_path: Path, env: dict[str, str], cwd: Path) -> subprocess.Popen:
    """Launch the EVE client executable directly (native Windows)."""
    return subprocess.Popen(
        [str(exe_path)],
        env=env,
        cwd=str(cwd),
        **get_client_process_flags(),
    )


def get_hidden_process_flags() -> dict[str, int]:
    """Popen kwargs for background server processes (no console window)."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


# ═══════════════════════════════════════════════════════════════════════════
# Profile links (directory junctions)
# ═══════════════════════════════════════════════════════════════════════════

def create_directory_link(target: Path, link: Path) -> None:
    """Create a directory junction (``mklink /J`` — no admin required)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
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
# Auto-updater  (VBScript helper via wscript.exe)
# ═══════════════════════════════════════════════════════════════════════════

def run_updater(download_url: str, current_exe_path: Path) -> bool:
    """Download + replace the launcher via a silent VBScript helper.

    Downloads a zip of the new onedir build, extracts it to a temp folder,
    then spawns a VBS helper that swaps the old install folder for the new
    one and restarts.  The caller should call :func:`hard_exit` after this
    returns ``True``.
    """
    import shutil
    import tempfile
    import zipfile

    from ..updater.github import download_asset

    current_exe_path = Path(current_exe_path)
    install_dir = current_exe_path.parent  # the onedir folder
    exe_name = current_exe_path.name       # e.g. "EveJS-Launcher-V1.exe"
    temp_dir = Path(tempfile.gettempdir())
    zip_path = temp_dir / "evejs_launcher_update.zip"
    extract_dir = temp_dir / "evejs_launcher_update"

    # 1. Download zip
    ok = download_asset(download_url, zip_path)
    if not ok:
        return False

    # 2. Extract zip to temp
    try:
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except (zipfile.BadZipFile, OSError) as exc:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    # 3. Clean up the zip (no longer needed)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass

    # 4. Verify extracted folder contains the launcher exe
    new_exe = _find_exe_in_folder(extract_dir, exe_name)
    if new_exe is None:
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    # 5. Get the actual extracted folder (might be nested one level)
    new_install_dir = new_exe.parent

    # 6. Write VBS helper and spawn
    helper_source = _find_vbs_helper()
    if helper_source is None:
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    helper_dest = temp_dir / "evejs_launcher_update_helper.vbs"
    helper_dest.write_text(helper_source, encoding="utf-8")

    try:
        subprocess.Popen(
            [
                "wscript.exe",
                str(helper_dest),
                str(install_dir),
                str(new_install_dir),
                exe_name,
                "--restart",
            ],
            creationflags=subprocess.DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except OSError:
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False


# ── Updater helpers ──────────────────────────────────────────────────────


def _find_exe_in_folder(folder: Path, exe_name: str) -> Path | None:
    """Search *folder* (and one level of nesting) for *exe_name*.

    Zip archives often wrap the onedir folder one level deep, e.g.::

        evejs-launcher-v1.0.25.zip
        └── EveJS-Launcher-V1/
            └── EveJS-Launcher-V1.exe

    Returns the absolute path to the exe, or *None* if not found.
    """
    # Direct match
    candidate = folder / exe_name
    if candidate.is_file():
        return candidate

    # One level of nesting (GitHub's default zip wrapper)
    try:
        for child in folder.iterdir():
            if child.is_dir():
                nested = child / exe_name
                if nested.is_file():
                    return nested
    except OSError:
        pass

    return None


# ── Embedded VBS helper (not bundled as a separate file) ──────────────────
# Kept as a Python string to avoid AV heuristics flagging a bundled .vbs
# script that deletes/replaces executables.

_VBS_HELPER = r"""' EveJS Launcher V2 — silent update helper (onedir / folder mode)
' Args: oldFolder newFolder exeName [--restart]
' Does: wait → delete old folder → move new folder in → launch exe

Dim oldFolder, newFolder, exeName, restart, fso, attempt, wsh

Set wsh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

oldFolder = WScript.Arguments(0)
newFolder = WScript.Arguments(1)
exeName   = WScript.Arguments(2)
restart   = (WScript.Arguments.Count >= 4 And WScript.Arguments(3) = "--restart")

If Not fso.FolderExists(newFolder) Then WScript.Quit 1

' Wait for old launcher to fully exit (DLLs unload, file locks released)
WScript.Sleep 5000

' Delete old install folder (retry if files still locked)
For attempt = 1 To 30
    On Error Resume Next
    If fso.FolderExists(oldFolder) Then
        fso.DeleteFolder oldFolder, True
    End If
    If Not fso.FolderExists(oldFolder) Then Exit For
    On Error GoTo 0
    WScript.Sleep 1000
Next

' If old folder still exists (locked files), rename it out of the way and
' proceed anyway — the user can clean up the old copy later.
If fso.FolderExists(oldFolder) Then
    On Error Resume Next
    Dim backupName: backupName = oldFolder & ".old"
    If fso.FolderExists(backupName) Then fso.DeleteFolder backupName, True
    fso.MoveFolder oldFolder, backupName
    On Error GoTo 0
End If

' Move new folder into place
On Error Resume Next
fso.MoveFolder newFolder, oldFolder
On Error GoTo 0

If Not fso.FolderExists(oldFolder) Then WScript.Quit 2

' Let filesystem settle
WScript.Sleep 3000

' Launch via explorer.exe — the only method proven to work without
' triggering the "Failed to load Python DLL" dialog.
If restart Then
    Dim exePath: exePath = oldFolder & "\" & exeName
    If fso.FileExists(exePath) Then
        wsh.Run "explorer.exe " & Chr(34) & exePath & Chr(34), 0, False
    End If
End If

WScript.Quit 0
"""


def _find_vbs_helper() -> str | None:
    """Return the VBScript updater helper content.

    The helper is now embedded as a Python string constant rather than read
    from a bundled ``.vbs`` file — this prevents antivirus heuristics from
    flagging the launcher for embedding a file-deletion script.
    """
    return _VBS_HELPER
