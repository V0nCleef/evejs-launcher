"""Auto-login — keyboard macro that types credentials into the EVE login window."""
import subprocess
import time
import logging
from pathlib import Path

# CREATE_NO_WINDOW prevents PowerShell console windows from flashing on screen.
# 0x08000000 is the Windows API value; subprocess.CREATE_NO_WINDOW is available
# in Python 3.7+ but we use getattr for safety.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

try:
    import pyautogui
    HAS_AUTOGUI = True
except ImportError:
    HAS_AUTOGUI = False

try:
    import pygetwindow as gw
except ImportError:
    gw = None  # type: ignore

from ..config import CONFIG_DIR

LOG_DIR = CONFIG_DIR / "logs"
LOG_FILE = LOG_DIR / "autologin.log"

logger = logging.getLogger("autologin")
logger.setLevel(logging.DEBUG)


def _ensure_log() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        h = logging.FileHandler(LOG_FILE, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(h)


def wait_for_window(title_substring: str, timeout: int = 30) -> object | None:
    """Wait for a window whose title contains the given substring.

    Returns a pygetwindow Window object or None on timeout.
    """
    if not HAS_AUTOGUI:
        logger.warning("pyautogui not available — auto-login disabled")
        return None

    _ensure_log()

    if gw is None:
        # pygetwindow not available — use PowerShell fallback window detection
        return _find_window_via_powershell(title_substring, timeout)

    # Common window titles that happen to contain "EVE" but are not the game client.
    _FALSE_POSITIVES = {"launcher", "explorer", "file", "folder", "paint", "notepad",
                         "settings", "control", "panel", "powershell", "cmd", "brave",
                         "chrome", "firefox", "edge", "hermes", "visual studio"}

    start = time.time()
    while time.time() - start < timeout:
        for win in gw.getAllWindows():
            title_lower = win.title.lower()
            # Must contain the target substring (e.g. "EVE") …
            if title_substring.lower() not in title_lower:
                continue
            # … but NOT be a known false-positive (File Explorer, launcher, browser, etc.)
            if any(fp in title_lower for fp in _FALSE_POSITIVES):
                continue
            # … and be a real-sized window (not a tooltip or hidden stub).
            if win.width < 300 or win.height < 200:
                continue
            logger.info(f"Found window: {win.title}")
            return win
        time.sleep(0.5)

    logger.warning(f"Window '{title_substring}' not found after {timeout}s")
    return None


def auto_login(
    username: str,
    password: str = "password",
    character_name: str = "",
    window_title: str = "EVE",
    delay: float = 2.0,
    timeout: int = 45,
) -> bool:
    """Type username and password into the EVE login window, then optionally
    select a specific character on the character-selection screen.

    Args:
        username: Account username.
        password: Account password (default "password" — EveJS skips validation).
        character_name: If non-empty, wait for the character-select screen and
            press Enter to enter the game.  The first character is auto-selected;
            for single-character accounts this is sufficient.
        window_title: Substring to match window title.
        delay: Seconds to wait after finding window before typing.
        timeout: Max seconds to wait for the window to appear.

    Returns:
        True if login was attempted, False if window was never found.
    """
    if not HAS_AUTOGUI or gw is None:
        # Either pyautogui or pygetwindow is missing — use pure PowerShell fallback.
        # (Previously used `and` which meant if pyautogui was available but
        # pygetwindow wasn't, wait_for_window() would return a bool and
        # win.activate() would crash with "'bool' object has no attribute 'activate'".)
        return _auto_login_powershell(username, password, character_name, window_title, delay, timeout)

    _ensure_log()
    logger.info(f"Auto-login started for '{username}'")

    win = wait_for_window(window_title, timeout=timeout)
    if not win:
        logger.error(f"Login window not found for '{username}'")
        return False

    time.sleep(delay)

    try:
        win.activate()
        time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Could not activate window: {e}")

    try:
        if HAS_AUTOGUI:
            pyautogui.typewrite(username, interval=0.05)
            pyautogui.press("tab")
            time.sleep(0.2)
            pyautogui.typewrite(password, interval=0.05)
            pyautogui.press("enter")
        else:
            # PowerShell SendKeys fallback (works without pyautogui)
            _sendkeys_via_powershell(username + "{TAB}" + password + "{ENTER}")
        logger.info(f"Credentials submitted for '{username}'")
    except Exception as e:
        logger.error(f"Auto-login typing failed: {e}")
        return False

    # ── Character selection (if requested) ──────────────────────────
    if character_name:
        time.sleep(5)
        try:
            if HAS_AUTOGUI:
                win.activate()
                time.sleep(0.3)
                pyautogui.press("enter")
            else:
                _sendkeys_via_powershell("{ENTER}")
            logger.info(f"Character select confirmed for '{character_name}'")
        except Exception as e:
            logger.warning(f"Character select step failed: {e}")

    logger.info(f"Auto-login complete for '{username}'")
    return True


def is_available() -> bool:
    """Check if auto-login dependencies are available."""
    if HAS_AUTOGUI:
        return True
    # Fallback: PowerShell SendKeys via WScript.Shell (always available on Windows)
    return True  # always try; auto_login will use PowerShell fallback


def _sendkeys_via_powershell(keys: str) -> None:
    """Send keystrokes via PowerShell's WScript.Shell SendKeys (no pyautogui needed)."""
    # Use AppActivate to focus EVE first, then send keys
    script = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$ws.AppActivate(\\"EVE\\") | Out-Null; '
        f'Start-Sleep -Milliseconds 500; '
        f'$ws.SendKeys(\\"{keys}\\")'
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=15,
        creationflags=_CREATE_NO_WINDOW,
    )


def _find_window_via_powershell(title_substring: str, timeout: int = 30) -> bool:
    """Wait for a window whose title contains *title_substring* using PowerShell.

    Filters out known false positives (launcher, explorer, browsers, etc.)
    the same way the pygetwindow path does.
    """

    # Same false-positive list as the pygetwindow path in wait_for_window().
    _FALSE_POSITIVES_PS = (
        "launcher", "explorer", "file", "folder", "paint", "notepad",
        "settings", "control", "panel", "powershell", "cmd", "brave",
        "chrome", "firefox", "edge", "hermes", "visual studio",
    )
    # Build a PowerShell filter that excludes windows whose title contains
    # any of these substrings (case-insensitive via -notmatch).
    exclude_pattern = "|".join(_FALSE_POSITIVES_PS)
    ps_filter = (
        f"Get-Process | Where-Object {{ "
        f"$_.MainWindowTitle -like '*{title_substring}*' -and "
        f"$_.MainWindowTitle -notmatch '({exclude_pattern})' "
        f"}} | Measure-Object | Select-Object -ExpandProperty Count"
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_filter],
            capture_output=True, text=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        try:
            count = int(result.stdout.strip())
        except ValueError:
            count = 0
        if count > 0:
            logger.info(f"Found {count} window(s) matching '{title_substring}' via PowerShell")
            return True
        time.sleep(1)
    return False


def _auto_login_powershell(
    username: str,
    password: str = "password",
    character_name: str = "",
    window_title: str = "EVE",
    delay: float = 2.0,
    timeout: int = 45,
) -> bool:
    """Pure PowerShell auto-login — no pyautogui or pygetwindow needed."""

    _ensure_log()
    logger.info(f"Auto-login (PowerShell) started for '{username}'")

    # Wait for EVE window via PowerShell
    if not _find_window_via_powershell(window_title, timeout):
        logger.error(f"Login window not found for '{username}'")
        return False

    time.sleep(delay)

    # Type credentials
    _sendkeys_via_powershell(username + "{TAB}" + password + "{ENTER}")
    logger.info(f"Credentials submitted for '{username}'")

    # Character selection
    if character_name:
        time.sleep(5)
        _sendkeys_via_powershell("{ENTER}")
        logger.info(f"Character select confirmed for '{character_name}'")

    logger.info(f"Auto-login complete for '{username}'")
    return True
