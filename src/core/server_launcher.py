"""Server launcher — starts EveJS game server and market server.

Both processes redirect their stdout/stderr to temp log files so the
launcher's built-in console panel can tail them for a 1:1 mirror of
what would normally appear in a CMD window.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
import sqlite3
import socket
import subprocess
import threading
import time
import tomllib
from pathlib import Path

from ..constants import Ports
from ..config import CONFIG_DIR
from .mod_manager import active_loader_mods, scan_mods
from .mod_runtime_state import (
    ModRuntimePlan,
    ModRuntimeStateError,
    NATIVE_BACKEND,
    native_mod_preload_paths,
    validate_mod_runtime_plan,
)
from .platform import (
    get_graceful_server_process_flags,
    get_hidden_process_flags,
    get_market_binary_name,
    terminate_process_tree,
)

# ── Console log paths (temp files) ────────────────────────────────────
_LOGS_DIR = CONFIG_DIR / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)

SERVER_CONSOLE_LOG = _LOGS_DIR / "server_console.log"
MARKET_CONSOLE_LOG = _LOGS_DIR / "market_console.log"
DEFAULT_NATIVE_MARKET_DATABASE = "data/generated/market.sqlite"
NATIVE_GAME_DEPENDENCY_TIMEOUT_SEC = 15 * 60
NATIVE_GAME_PROBE_TIMEOUT_SEC = 30
_BETTER_SQLITE3_PROBE_SCRIPT = (
    "const Database = require('better-sqlite3');"
    "const database = new Database(':memory:');"
    "try {"
    "const row = database.prepare('SELECT 1 AS value').get();"
    "if (!row || row.value !== 1) throw new Error('SQLite probe query failed');"
    "} finally { database.close(); }"
)


@dataclass(frozen=True)
class NativeModRuntimeLaunchReceipt:
    """Immutable binding between one plan-bound launch and its raw evidence."""

    plan_sha256: str
    runtime_identity: str
    status_log_path: Path


def get_server_log_path(evejs_root: str) -> Path:
    """Return path to the server's own log file (written by EveJS internally)."""
    return Path(evejs_root) / "server" / "logs" / "server.log"


def get_server_console_log() -> Path:
    """Return the path to the live server console log (1:1 stdout mirror)."""
    return SERVER_CONSOLE_LOG


def get_native_mod_status_log(evejs_root: str | Path) -> Path:
    """Return the stable raw-stdout evidence path for one canonical root.

    Only a SHA-256 root identity is present in the launcher-owned filename, so
    a private installation path is never copied into the log namespace.  The
    file is reused and truncated for each Native start rather than accumulated
    as one unbounded file per launch attempt.
    """

    try:
        root = Path(evejs_root).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("The EveJS root is unavailable.") from exc
    if not root.is_dir():
        raise ValueError("The EveJS root is not a directory.")
    root_identity = os.path.normcase(str(root))
    digest = hashlib.sha256(root_identity.encode("utf-8")).hexdigest()
    return _LOGS_DIR / "native_mod_status" / f"{digest}.log"


def get_market_console_log() -> Path:
    """Return the path to the live market console log (1:1 stdout mirror)."""
    return MARKET_CONSOLE_LOG


def native_market_database_status(
    evejs_root: str | Path,
) -> tuple[bool, str]:
    """Return whether the configured optional Native Market seed is usable."""
    market_dir = Path(evejs_root) / "externalservices" / "market-server"
    config_path = market_dir / "config" / "market-server.local.toml"
    if not config_path.is_file():
        return False, (
            f"Optional Market config is missing: {config_path}. "
            "Repair the EveJS installation before starting Market."
        )

    try:
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return False, (
            f"Optional Market config is invalid: {config_path} ({exc}). "
            "Fix or restore it before starting Market."
        )

    storage = config.get("storage", {})
    if not isinstance(storage, dict):
        return False, (
            f"Optional Market config has an invalid [storage] section: "
            f"{config_path}. Fix or restore it before starting Market."
        )
    database_value = storage.get(
        "database_path",
        DEFAULT_NATIVE_MARKET_DATABASE,
    )
    if not isinstance(database_value, str):
        return False, (
            f"Optional Market config has an invalid [storage].database_path: "
            f"{config_path}. Fix or restore it before starting Market."
        )

    try:
        # Preserve the configured path exactly. Leading/trailing whitespace is
        # meaningful inside a TOML string and is also preserved by the daemon.
        database_path = Path(database_value)
        if not database_path.is_absolute():
            database_path = market_dir / database_path
        if not database_path.is_file():
            return False, (
                f"Optional Market database is missing: {database_path}. "
                "Build it with Tools > Market Seed Builder."
            )

        database_uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            manifest_row = connection.execute(
                "SELECT 1 FROM manifest WHERE key = ? LIMIT 1",
                ("manifest_json",),
            ).fetchone()
    except (OSError, ValueError) as exc:
        return False, (
            f"Optional Market database path is invalid: {database_value} ({exc}). "
            "Fix [storage].database_path before starting Market."
        )
    except sqlite3.Error as exc:
        return False, (
            f"Optional Market database is not a readable seeded SQLite database: "
            f"{database_path} ({exc}). Rebuild it with Tools > Market Seed Builder."
        )

    if manifest_row is None:
        return False, (
            f"Optional Market database is incomplete: {database_path} does not "
            "contain the required manifest_json manifest row. Rebuild it with "
            "Tools > Market Seed Builder."
        )

    return True, ""


# ── Pipe reader thread ────────────────────────────────────────────────

def _pipe_to_files(
    pipe,
    paths: tuple[Path, ...],
    mode: str = "a",
) -> None:
    """Mirror one child pipe byte-for-byte to every destination in one read."""

    streams = []
    try:
        binary_mode = mode if "b" in mode else f"{mode}b"
        for path in paths:
            streams.append(open(path, binary_mode))
        while True:
            line = pipe.readline()
            if line == b"" or line == "":
                break
            if isinstance(line, str):
                line = line.encode("utf-8", errors="strict")
            for stream in streams:
                stream.write(line)
                stream.flush()
    except (OSError, ValueError):
        pass  # pipe closed
    finally:
        for stream in streams:
            try:
                stream.close()
            except OSError:
                pass


def _pipe_to_file(pipe, path: Path, mode: str = "a") -> None:
    """Mirror child stdout bytes exactly to one destination."""

    _pipe_to_files(pipe, (path,), mode)


# ── Mod discovery ──────────────────────────────────────────────────────

def _find_mod_preloads(evejs_root: str) -> list[str]:
    """Return only validated active loader paths for Node ``--require``."""
    args: list[str] = []
    for mod in active_loader_mods(scan_mods(evejs_root)):
        args.extend(["--require", str(mod.path / "loader.js")])
    return args


def _native_plan_preloads(
    evejs_root: str | Path,
    mode: str,
    plan: ModRuntimePlan,
) -> tuple[Path, ...]:
    """Validate and freeze the only loader paths authorized for this launch."""

    validate_mod_runtime_plan(plan, backend=NATIVE_BACKEND)
    try:
        root = Path(evejs_root).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ModRuntimeStateError("The Native launch root is unavailable.") from exc
    if not root.is_dir():
        raise ModRuntimeStateError("The Native launch root is not a directory.")
    if plan.root != root:
        raise ModRuntimeStateError(
            "The Native runtime plan belongs to a different EveJS root."
        )
    if plan.mode != mode:
        raise ModRuntimeStateError(
            "The Native runtime plan mode does not match the requested launch mode."
        )

    # Keep the empty result as an explicit frozen selection.  Falling back to
    # discovery here would let a loader added after planning enter the process.
    return tuple(native_mod_preload_paths(plan))


def build_game_server_command(
    evejs_root: str | Path,
    mode: str,
    *,
    mod_runtime_plan: ModRuntimePlan | None = None,
) -> list[str]:
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
    if mod_runtime_plan is None:
        if mode == "modded":
            command.extend(_find_mod_preloads(str(evejs_root)))
    else:
        frozen_preloads = _native_plan_preloads(
            evejs_root,
            mode,
            mod_runtime_plan,
        )
        for preload in frozen_preloads:
            command.extend(["--require", str(preload)])
    command.append(".")
    return command


# ── Game server (direct Node.js launch with stdout capture) ────────────

def _npm_executable() -> str:
    """Return the npm shim name that can be executed without a shell."""
    return "npm.cmd" if os.name == "nt" else "npm"


def _append_game_console(message: str) -> None:
    SERVER_CONSOLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SERVER_CONSOLE_LOG.open("a", encoding="utf-8", errors="replace") as stream:
        stream.write(f"[launcher] {message.rstrip()}\n")


def _append_market_console_marker(message: str) -> None:
    """Append one durable launcher-owned Market lifecycle marker."""
    MARKET_CONSOLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with MARKET_CONSOLE_LOG.open(
        "a",
        encoding="utf-8",
        errors="replace",
    ) as stream:
        # A leading newline keeps the marker readable if child output ended
        # without its own newline. Existing attempts remain available after a
        # retry instead of being erased before diagnostics can be collected.
        stream.write(f"\n[launcher {timestamp}] {message.rstrip()}\n")


def _run_game_dependency_command(
    command: list[str],
    server_dir: Path,
    *,
    purpose: str,
    timeout_sec: float = NATIVE_GAME_DEPENDENCY_TIMEOUT_SEC,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run one bootstrap command off the GUI thread and mirror exact output."""
    _append_game_console(f"Running: {subprocess.list2cmdline(command)}")
    try:
        with SERVER_CONSOLE_LOG.open("a", encoding="utf-8", errors="replace") as stream:
            process = subprocess.Popen(
                command,
                cwd=str(server_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture_output else stream,
                stderr=subprocess.STDOUT,
                text=capture_output,
                encoding="utf-8" if capture_output else None,
                errors="replace" if capture_output else None,
                env=env,
                **get_hidden_process_flags(),
            )
            try:
                if capture_output:
                    output, _stderr = process.communicate(timeout=timeout_sec)
                    return_code = process.returncode
                    if output:
                        stream.write(output)
                        if not output.endswith("\n"):
                            stream.write("\n")
                        stream.flush()
                else:
                    output = None
                    return_code = process.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired as exc:
                terminated = terminate_process_tree(process.pid)
                outcome = "terminated" if terminated else "could not be terminated"
                _append_game_console(
                    f"Timed out while trying to {purpose}; process tree {outcome}."
                )
                timeout_label = (
                    f"{int(timeout_sec)} seconds"
                    if timeout_sec < 60
                    else f"{int(timeout_sec // 60)} minutes"
                )
                raise RuntimeError(
                    f"Cannot {purpose}: {command[0]} did not finish within "
                    f"{timeout_label}. Its "
                    f"process tree {outcome}. Open the Game Console for the last "
                    "output, correct the reported prerequisite, and retry."
                ) from exc
    except FileNotFoundError as exc:
        executable = command[0]
        raise RuntimeError(
            f"Cannot {purpose}: required command '{executable}' was not found on "
            "PATH. Install the EveJS Node.js/npm prerequisites, restart the "
            "launcher, and try again."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Cannot {purpose}: failed to run '{command[0]}': {exc}. Open the "
            "Game Console for details."
        ) from exc
    return subprocess.CompletedProcess(command, return_code, stdout=output)


def _read_native_game_manifest(server_dir: Path) -> dict:
    package_json = server_dir / "package.json"
    try:
        manifest = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot repair native Game dependencies because {package_json} is "
            f"missing or invalid ({exc}). Restore the EveJS server files and retry."
        ) from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(
            f"Cannot repair native Game dependencies because {package_json} does "
            "not contain a JSON object. Restore the EveJS server files and retry."
        )
    return manifest


def _validate_vetted_native_game_dependency(server_dir: Path) -> None:
    """Approve no script unless better-sqlite3 is direct and not denied."""
    package_json = server_dir / "package.json"
    manifest = _read_native_game_manifest(server_dir)
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, dict) or "better-sqlite3" not in dependencies:
        raise RuntimeError(
            "Cannot safely repair native Game dependencies: the expected direct "
            f"better-sqlite3 entry is missing from {package_json}. Restore the "
            "EveJS server files and retry."
        )

    allow_scripts = manifest.get("allowScripts")
    if not isinstance(allow_scripts, dict):
        return
    denied = [
        key
        for key, allowed in allow_scripts.items()
        if allowed is False
        and (key == "better-sqlite3" or key.startswith("better-sqlite3@"))
    ]
    if denied:
        raise RuntimeError(
            "Cannot automatically approve better-sqlite3 because package.json "
            f"explicitly denies it in allowScripts ({', '.join(denied)}). The "
            "launcher preserved that denial; review it manually before retrying."
        )


def _read_installed_better_sqlite3_version(server_dir: Path) -> str | None:
    """Return the installed version when its package manifest is usable."""
    package_json = server_dir / "node_modules" / "better-sqlite3" / "package.json"
    try:
        manifest = json.loads(package_json.read_text(encoding="utf-8"))
        version = manifest.get("version")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(version, str) or not version.strip():
        return None
    return version.strip()


def _installed_better_sqlite3_version(server_dir: Path) -> str:
    package_json = server_dir / "node_modules" / "better-sqlite3" / "package.json"
    version = _read_installed_better_sqlite3_version(server_dir)
    if version is None:
        raise RuntimeError(
            f"Dependency repair did not produce a readable {package_json} with a "
            "valid version. Open the Game Console for the npm output, restore the "
            "EveJS server files if needed, and retry."
        )
    return version


def _better_sqlite3_is_usable(server_dir: Path) -> bool:
    result = _run_game_dependency_command(
        ["node", "-e", _BETTER_SQLITE3_PROBE_SCRIPT],
        server_dir,
        purpose="verify the better-sqlite3 runtime",
        timeout_sec=NATIVE_GAME_PROBE_TIMEOUT_SEC,
    )
    return result.returncode == 0


def _npm_major_version(npm: str, server_dir: Path) -> int:
    result = _run_game_dependency_command(
        [npm, "--version"],
        server_dir,
        purpose="identify the installed npm version",
        timeout_sec=NATIVE_GAME_PROBE_TIMEOUT_SEC,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Cannot identify the installed npm version needed for safe dependency "
            f"repair (exit code {result.returncode}). Open the Game Console for "
            "the exact npm output, then retry Start Game."
        )
    output = str(result.stdout or "")
    for line in reversed(output.splitlines()):
        candidate = line.strip().split(".", 1)[0]
        if candidate.isdigit():
            return int(candidate)
    raise RuntimeError(
        "Cannot identify the installed npm major version from its output. Open "
        "the Game Console, verify npm is installed correctly, and retry Start Game."
    )


def ensure_native_game_dependencies(server_dir: Path) -> None:
    """Repair npm 12-blocked better-sqlite3, then verify a real SQLite query."""
    _append_game_console("Checking the native better-sqlite3 runtime.")
    if _better_sqlite3_is_usable(server_dir):
        _append_game_console("Native better-sqlite3 runtime is ready.")
        return

    _append_game_console(
        "The better-sqlite3 probe failed; starting the safe npm dependency repair."
    )
    _validate_vetted_native_game_dependency(server_dir)
    npm = _npm_executable()
    npm_major = _npm_major_version(npm, server_dir)
    version = _read_installed_better_sqlite3_version(server_dir)
    if version is None:
        _append_game_console(
            "The installed better-sqlite3 package manifest is missing or unreadable; "
            "restoring the locked dependency tree without running install scripts."
        )
        install = _run_game_dependency_command(
            [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            server_dir,
            purpose="install the locked EveJS dependencies with npm ci",
        )
        if install.returncode != 0:
            raise RuntimeError(
                "Automatic EveJS dependency repair failed during npm ci "
                f"(exit code {install.returncode}). Open the Game Console for the "
                "exact npm output, correct the reported prerequisite, and retry "
                "Start Game."
            )
        version = _installed_better_sqlite3_version(server_dir)
    else:
        _append_game_console(
            f"Preserving the installed better-sqlite3@{version} package and "
            "repairing its native binding in place."
        )

    if npm_major >= 12:
        approval = _run_game_dependency_command(
            [
                npm,
                "install-scripts",
                "approve",
                f"better-sqlite3@{version}",
                "--allow-scripts-pin=true",
            ],
            server_dir,
            purpose="approve the pinned better-sqlite3 install script with npm 12",
        )
        if approval.returncode != 0:
            raise RuntimeError(
                "Automatic EveJS dependency repair could not approve the pinned "
                f"better-sqlite3@{version} install script with npm 12 (exit code "
                f"{approval.returncode}). Open the Game Console for the exact npm "
                "output, then retry Start Game."
            )

        # Re-read after npm's official approval command. This guards against a
        # user-level npm setting silently broadening the policy instead of writing
        # the exact installed-version pin requested above.
        allow_scripts = _read_native_game_manifest(server_dir).get("allowScripts", {})
        pinned_key = f"better-sqlite3@{version}"
        if (
            not isinstance(allow_scripts, dict)
            or allow_scripts.get(pinned_key) is not True
        ):
            raise RuntimeError(
                "npm did not record the required exact install-script approval "
                f"{pinned_key} in package.json. The launcher will not run an "
                "unpinned native install script; review npm's Game Console output "
                "and retry."
            )
    else:
        _append_game_console(
            f"npm {npm_major} predates install-script approvals; proceeding with "
            "the targeted better-sqlite3 rebuild only."
        )

    rebuild_command = [
        npm,
        "rebuild",
        "better-sqlite3",
        "--ignore-scripts=false",
    ]
    if npm_major >= 12:
        rebuild_command.append("--dangerously-allow-all-scripts=false")
    rebuild_command.append("--foreground-scripts")
    rebuild = _run_game_dependency_command(
        rebuild_command,
        server_dir,
        purpose="rebuild the vetted better-sqlite3 native dependency",
    )
    if rebuild.returncode != 0:
        raise RuntimeError(
            "Automatic EveJS dependency repair could not rebuild better-sqlite3 "
            f"(exit code {rebuild.returncode}). Open the Game Console for the exact "
            "native build output, correct the reported prerequisite, and retry."
        )

    _append_game_console("Repair finished; verifying better-sqlite3 again.")
    if not _better_sqlite3_is_usable(server_dir):
        raise RuntimeError(
            "Automatic dependency repair completed, but Node.js still cannot "
            "create, query, and close an in-memory better-sqlite3 database. Open "
            "the Game Console for the exact binding error, verify the EveJS "
            "Node.js/build prerequisites, and retry Start Game."
        )
    _append_game_console("Native Game dependencies repaired and verified.")


def _prepare_native_game_store_environment(
    evejs_root: Path,
    server_dir: Path,
    env: dict[str, str],
) -> None:
    """Apply EveJS's own v0.12.6 legacy migration before selecting gameStore."""
    local_root = evejs_root / "_local"
    legacy_store = local_root / "newDatabase"
    canonical_store = local_root / "gameStore"
    canonical_data = canonical_store / "data"
    # Never inherit an override that belongs to another selected installation.
    env.pop("EVEJS_GAMESTORE_DATA_DIR", None)

    migration_script = (
        server_dir / "src" / "gameStore" / "migrateLegacyNewDatabase.js"
    )
    if not migration_script.is_file():
        # Older EveJS versions may own a _local/newDatabase layout and its
        # environment contract. Do not reinterpret or mutate that layout, but a
        # lone canonical data tree can still be scoped safely to this root.
        if not legacy_store.exists() and canonical_data.is_dir():
            env["EVEJS_GAMESTORE_DATA_DIR"] = str(canonical_data.resolve())
        return

    if legacy_store.exists() and not legacy_store.is_dir():
        raise RuntimeError(
            f"Cannot migrate legacy EveJS data because {legacy_store} is not a "
            "directory. Preserve it and resolve the layout before starting Game."
        )
    if canonical_store.exists() and not canonical_store.is_dir():
        raise RuntimeError(
            f"Cannot use the EveJS GameStore because {canonical_store} is not a "
            "directory. Preserve it and resolve the layout before starting Game."
        )
    if legacy_store.exists() and canonical_store.exists():
        raise RuntimeError(
            "Cannot start Game because the selected EveJS root contains both "
            f"legacy data ({legacy_store}) and v0.12.6 gameStore data "
            f"({canonical_store}). The launcher will not merge or choose between "
            "two possible saves. Back up both directories, resolve the duplicate "
            "layout, and retry."
        )

    legacy_sqlite_paths = tuple(
        canonical_store / f"newdatabase.sqlite{suffix}"
        for suffix in ("", "-wal", "-shm")
    )
    needs_migration = legacy_store.exists() or any(
        path.exists() for path in legacy_sqlite_paths
    )
    if needs_migration:
        _append_game_console(
            "Legacy newDatabase data detected; running EveJS's official v0.12.6 "
            "gameStore migration."
        )
        migration_env = env.copy()
        migration_env.pop("EVEJS_GAMESTORE_DATA_DIR", None)
        migration = _run_game_dependency_command(
            ["node", str(migration_script.resolve())],
            server_dir,
            purpose="migrate legacy EveJS _local/newDatabase data",
            timeout_sec=120,
            env=migration_env,
        )
        if migration.returncode != 0:
            raise RuntimeError(
                "EveJS's official legacy gameStore migration failed "
                f"(exit code {migration.returncode}). The launcher did not start "
                "Game or select a different save. Open the Game Console for the "
                "exact migration output, restore from backup if needed, and retry."
            )
        remaining_legacy_sqlite = [
            path for path in legacy_sqlite_paths if path.exists()
        ]
        if (
            legacy_store.exists()
            or not canonical_store.is_dir()
            or remaining_legacy_sqlite
        ):
            raise RuntimeError(
                "EveJS's official legacy gameStore migration returned success, "
                "but the selected root still has _local/newDatabase or does not "
                "have _local/gameStore. The launcher did not start Game or guess "
                "which save to use. Open the Game Console and inspect the selected "
                "EveJS root before retrying."
            )
        _append_game_console("Legacy gameStore migration completed and verified.")

    # Never inherit a gameStore override from another EveJS installation. A
    # canonical override is safe only when v0.12.6 already has a real data dir.
    if canonical_data.is_dir():
        env["EVEJS_GAMESTORE_DATA_DIR"] = str(canonical_data.resolve())


def start_game_server(
    evejs_root: str,
    mode: str,
    *,
    mod_runtime_plan: ModRuntimePlan | None = None,
) -> subprocess.Popen:
    """Start the game server by launching Node.js directly.

    Stdout and stderr are piped to a temp log file so the launcher's
    console panel shows a 1:1 mirror of the server's terminal output.
    """
    server_dir = Path(evejs_root) / "server"
    index_js = server_dir / "index.js"
    if not index_js.exists():
        raise FileNotFoundError(f"Server entry point not found: {index_js}")

    # Node cannot write fatal reports into a missing directory. Dependency
    # checks and repairs execute here inside ServiceStartWorker's QThread, so a
    # first-run npm install/rebuild does not block the Qt GUI thread.
    (server_dir / "logs" / "node-reports").mkdir(parents=True, exist_ok=True)
    SERVER_CONSOLE_LOG.parent.mkdir(parents=True, exist_ok=True)
    SERVER_CONSOLE_LOG.write_text("", encoding="utf-8")
    ensure_native_game_dependencies(server_dir)

    env = os.environ.copy()
    env["EVEJS_PROXY_LOCAL_INTERCEPT"] = "1"
    _prepare_native_game_store_environment(Path(evejs_root), server_dir, env)

    if mod_runtime_plan is None:
        cmd = build_game_server_command(evejs_root, mode)
    else:
        cmd = build_game_server_command(
            evejs_root,
            mode,
            mod_runtime_plan=mod_runtime_plan,
        )

    # Dependency checks and legacy-store migration can write arbitrary launcher
    # diagnostics to the normal Game console.  The attestation input begins
    # here, immediately before Node is spawned, and contains child bytes only.
    mod_status_log = get_native_mod_status_log(evejs_root)
    mod_status_log.parent.mkdir(parents=True, exist_ok=True)
    mod_status_log.write_bytes(b"")

    proc = subprocess.Popen(
        cmd,
        cwd=str(server_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **get_graceful_server_process_flags(),
    )

    if mod_runtime_plan is not None:
        setattr(
            proc,
            "mod_runtime_receipt",
            NativeModRuntimeLaunchReceipt(
                plan_sha256=mod_runtime_plan.plan_sha256,
                runtime_identity=mod_runtime_plan.runtime_identity,
                status_log_path=mod_status_log,
            ),
        )

    # One reader owns the pipe.  Two readers would split lines nondeterministically
    # and make both the human console and runtime evidence incomplete.
    threading.Thread(
        target=_pipe_to_files,
        args=(proc.stdout, (SERVER_CONSOLE_LOG, mod_status_log), "a"),
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

    _append_market_console_marker(
        f"Market start attempt: {subprocess.list2cmdline(cmd)}"
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(market_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **get_hidden_process_flags(),
        )
    except Exception as exc:
        _append_market_console_marker(
            "Market process creation failed: "
            f"{type(exc).__name__}: {exc}"
        )
        raise
    _append_market_console_marker(f"Market process started with PID {proc.pid}.")

    threading.Thread(
        target=_pipe_to_file,
        args=(proc.stdout, MARKET_CONSOLE_LOG, "a"),
        daemon=True,
    ).start()

    return proc


# ── Shared utilities ───────────────────────────────────────────────────

def wait_for_server_ready(host: str = "127.0.0.1", port: int = int(Ports.GAME_TCP),
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


def is_server_running(host: str = "127.0.0.1", port: int = int(Ports.GAME_TCP)) -> bool:
    """Quick check if the server is currently running."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False
