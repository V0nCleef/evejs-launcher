"""Server launcher — starts EveJS game server and market server.

Both processes redirect their stdout/stderr to temp log files so the
launcher's built-in console panel can tail them for a 1:1 mirror of
what would normally appear in a CMD window.
"""
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from ..config import CONFIG_DIR
from .platform import get_hidden_process_flags, get_market_binary_name

# ── Console log paths (temp files) ────────────────────────────────────
_LOGS_DIR = CONFIG_DIR / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

SERVER_CONSOLE_LOG = _LOGS_DIR / "server_console.log"
MARKET_CONSOLE_LOG = _LOGS_DIR / "market_console.log"


def get_server_log_path(evejs_root: str) -> Path:
    """Return path to the server's own log file (written by EveJS internally)."""
    return Path(evejs_root) / "server" / "logs" / "server.log"


def get_server_console_log() -> Path:
    """Return the path to the live server console log (1:1 stdout mirror)."""
    return SERVER_CONSOLE_LOG


def get_market_console_log() -> Path:
    """Return the path to the live market console log (1:1 stdout mirror)."""
    return MARKET_CONSOLE_LOG


# ── Pipe reader thread ────────────────────────────────────────────────

def _pipe_to_file(pipe, path: Path) -> None:
    """Read lines from *pipe* and append them to *path* in a daemon thread."""
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            for line in iter(pipe.readline, b""):
                try:
                    text = line.decode("utf-8", errors="replace")
                except Exception:
                    text = line.decode("latin-1", errors="replace")
                f.write(text)
                f.flush()
    except (OSError, ValueError):
        pass  # pipe closed


# ── Mod discovery ──────────────────────────────────────────────────────

def _find_mod_preloads(evejs_root: str) -> list[str]:
    """Scan mods/*/loader.js and return --require flags for active mods."""
    mods_dir = Path(evejs_root) / "mods"
    args = []
    if mods_dir.is_dir():
        for item in sorted(mods_dir.iterdir()):
            if item.is_dir() and (item / "loader.js").exists():
                args.extend(["--require", str(item / "loader.js")])
    return args


def build_game_server_command(evejs_root: str | Path, mode: str) -> list[str]:
    """Build the direct-Node game-server command for an explicit mode."""
    if mode not in {"vanilla", "modded"}:
        raise ValueError(f"Unsupported server mode: {mode}")
    command = [
        "node",
        "--report-on-fatalerror",
        "--report-uncaught-exception",
        "--report-dir=./logs/node-reports",
        "--max-old-space-size=8192",
    ]
    if mode == "modded":
        command.extend(_find_mod_preloads(str(evejs_root)))
    command.append(".")
    return command


# ── Game server (direct Node.js launch with stdout capture) ────────────

def start_game_server(evejs_root: str, mode: str) -> subprocess.Popen:
    """Start the game server by launching Node.js directly.

    Stdout and stderr are piped to a temp log file so the launcher's
    console panel shows a 1:1 mirror of the server's terminal output.
    """
    server_dir = Path(evejs_root) / "server"
    index_js = server_dir / "index.js"
    if not index_js.exists():
        raise FileNotFoundError(f"Server entry point not found: {index_js}")

    cmd = build_game_server_command(evejs_root, mode)

    env = os.environ.copy()
    env["EVEJS_PROXY_LOCAL_INTERCEPT"] = "1"

    # Truncate the console log for a fresh start
    SERVER_CONSOLE_LOG.write_text("", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(server_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **get_hidden_process_flags(),
    )

    # Start a daemon thread that writes stdout to the console log file
    threading.Thread(
        target=_pipe_to_file,
        args=(proc.stdout, SERVER_CONSOLE_LOG),
        daemon=True,
    ).start()

    return proc


# ── Market server (direct cargo launch with stdout capture) ────────────

def start_market_server(evejs_root: str) -> subprocess.Popen:
    """Start the market server directly via cargo (no batch wrapper).

    Stdout and stderr are piped to a temp log file so the launcher's
    console panel shows a 1:1 mirror of the market server's output.
    """
    market_dir = Path(evejs_root) / "externalservices" / "market-server"
    cargo_toml = market_dir / "Cargo.toml"
    if not cargo_toml.exists():
        raise FileNotFoundError(f"Market server project not found: {cargo_toml}")

    # Use the pre-built binary if available, otherwise cargo run
    binary = market_dir / "target" / "release" / get_market_binary_name()
    if binary.exists():
        cmd = [
            str(binary),
            "--config", "config/market-server.local.toml",
            "serve",
        ]
    else:
        cmd = [
            "cargo", "run", "--release", "--",
            "--config", "config/market-server.local.toml",
            "serve",
        ]

    env = os.environ.copy()

    # Truncate the console log for a fresh start
    MARKET_CONSOLE_LOG.write_text("", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        cwd=str(market_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **get_hidden_process_flags(),
    )

    threading.Thread(
        target=_pipe_to_file,
        args=(proc.stdout, MARKET_CONSOLE_LOG),
        daemon=True,
    ).start()

    return proc


# ── Shared utilities ───────────────────────────────────────────────────

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
