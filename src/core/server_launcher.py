"""Server launcher — starts EveJS game server and market server.

Game server: Launches Node.js directly (no batch file wrapper) so that
Ctrl+C shutdown reaches Node.js without the cmd.exe prompt.

Processes run with CREATE_NO_WINDOW — no visible CMD window since the
launcher's built-in console panel tails server.log for live output.
"""
import socket
import subprocess
import time
import tempfile
import os
import ctypes
from pathlib import Path

# ── Keep the CMD window hidden unless user explicitly wants it ────────
_HIDDEN = subprocess.CREATE_NO_WINDOW


# ── Mod discovery ────────────────────────────────────────────────────────


def _find_mod_preloads(evejs_root: str) -> list[str]:
    """Scan mods/*/loader.js and return --require flags for active mods."""
    mods_dir = Path(evejs_root) / "mods"
    args = []
    if mods_dir.is_dir():
        for item in sorted(mods_dir.iterdir()):
            if item.is_dir() and (item / "loader.js").exists():
                args.extend(["--require", str(item / "loader.js")])
    return args


# ── Server log path ──────────────────────────────────────────────────────


def get_server_log_path(evejs_root: str) -> Path:
    """Return path to the live server log file that EveJS writes."""
    return Path(evejs_root) / "server" / "logs" / "server.log"


# ── Game server (direct Node.js launch) ─────────────────────────────────


def start_game_server(evejs_root: str, mode: str = "modded") -> subprocess.Popen:
    """Start the game server by launching Node.js DIRECTLY.

    Bypassing cmd.exe means:
      - No batch prompt on Ctrl+C
      - Ctrl+C reaches Node.js → graceful shutdown
      - EveJS logs to server/logs/server.log — launcher tails that file
    """
    server_dir = Path(evejs_root) / "server"
    index_js = server_dir / "index.js"
    if not index_js.exists():
        raise FileNotFoundError(f"Server entry point not found: {index_js}")

    cmd = [
        "node",
        "--report-on-fatalerror",
        "--report-uncaught-exception",
        "--report-dir=./logs/node-reports",
        "--max-old-space-size=8192",
    ]
    if mode == "modded":
        cmd.extend(_find_mod_preloads(evejs_root))
    cmd.append(".")

    env = os.environ.copy()
    env["EVEJS_PROXY_LOCAL_INTERCEPT"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=str(server_dir),
        env=env,
        creationflags=_HIDDEN,
    )
    return proc


# ── Market server ────────────────────────────────────────────────────────


def _make_market_wrapper(bat: Path) -> str:
    """Wrapper that uses start /b to avoid batch-job prompt on Ctrl+C."""
    wrapper = (
        f'@echo off\r\n'
        f'break=off\r\n'
        f'cd /d "{bat.parent}"\r\n'
        f'echo 1| start /b /wait "" cmd /c ""{bat.name}""\r\n'
    )
    wrapper_path = os.path.join(tempfile.gettempdir(), "evejs_market.bat")
    with open(wrapper_path, "w") as f:
        f.write(wrapper)
    return wrapper_path


def start_market_server(evejs_root: str) -> subprocess.Popen:
    """Start market server in a VISIBLE CMD window."""
    bat = Path(evejs_root) / "StartMarketServer.bat"
    if not bat.exists():
        raise FileNotFoundError(f"Market server script not found: {bat}")

    wrapper = _make_market_wrapper(bat)
    proc = subprocess.Popen(
        ["cmd", "/c", wrapper],
        cwd=str(bat.parent),
        creationflags=_HIDDEN,
    )
    return proc


# ── Shared utilities ─────────────────────────────────────────────────────


def detect_server_scripts(evejs_root: str) -> dict[str, Path]:
    """Detect which server start scripts exist."""
    root = Path(evejs_root)
    scripts = {}
    if (root / "StartServer.bat").exists():
        scripts["vanilla"] = root / "StartServer.bat"
    if (root / "StartServerWithMods.bat").exists():
        scripts["modded"] = root / "StartServerWithMods.bat"
    return scripts


def wait_for_server_ready(host: str = "127.0.0.1", port: int = 26000,
                          timeout: int = 60) -> bool:
    """Wait until the server port accepts connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(1)
    return False


def is_server_running(host: str = "127.0.0.1", port: int = 26000) -> bool:
    """Quick check if the server is currently running."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False
