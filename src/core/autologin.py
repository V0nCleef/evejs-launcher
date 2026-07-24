"""Auto-login — keyboard macro that types credentials into the EVE login window."""
import time
import logging
from pathlib import Path

try:
    import pyautogui
    import pygetwindow as gw
    HAS_AUTOGUI = True
except ImportError:
    HAS_AUTOGUI = False

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
    start = time.time()
    while time.time() - start < timeout:
        for win in gw.getAllWindows():
            if title_substring.lower() in win.title.lower():
                logger.info(f"Found window: {win.title}")
                return win
        time.sleep(0.5)

    logger.warning(f"Window '{title_substring}' not found after {timeout}s")
    return None


def auto_login(
    username: str,
    password: str = "password",
    window_title: str = "EVE",
    delay: float = 2.0,
    timeout: int = 45,
) -> bool:
    """Type username and password into the EVE login window.

    Args:
        username: Account username.
        password: Account password (default "password" — EveJS skips validation).
        window_title: Substring to match window title.
        delay: Seconds to wait after finding window before typing.
        timeout: Max seconds to wait for the window to appear.

    Returns:
        True if login was attempted, False if window was never found.
    """
    if not HAS_AUTOGUI:
        logger.error("Cannot auto-login: pyautogui not available")
        return False

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
        pyautogui.typewrite(username, interval=0.05)
        pyautogui.press("tab")
        time.sleep(0.2)
        pyautogui.typewrite(password, interval=0.05)
        pyautogui.press("enter")
        logger.info(f"Auto-login complete for '{username}'")
        return True
    except Exception as e:
        logger.error(f"Auto-login failed: {e}")
        return False


def is_available() -> bool:
    """Check if auto-login dependencies are available."""
    return HAS_AUTOGUI
