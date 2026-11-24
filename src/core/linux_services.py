"""Native Linux services for an installation already prepared by Linux Setup."""
from __future__ import annotations

from pathlib import Path
from functools import wraps
import signal
from http.client import HTTPConnection, HTTPException

from .linux_installation import LinuxInstallation, LinuxSetupError
from .linux_client import require_client_trust
from .linux_process import run_bounded


def _serialize_start(function):
    @wraps(function)
    def start(*args, **kwargs):
        from .linux_client import client_launch_lock
        with client_launch_lock(timeout_seconds=1):
            return function(*args, **kwargs)
    return start


def http_health(port: int, *, timeout: float = 1) -> bool:
    connection = HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        response.read(1)
        return 200 <= response.status < 300
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def observe_backend(root: str, game_port: int) -> tuple[bool, bool]:
    from .server_launcher import is_server_running
    market = http_health(40110) and is_server_running(port=40111)
    if not is_server_running(port=game_port):
        return False, market
    try:
        from .linux_client import require_backend_tls
        installation = LinuxInstallation.load(root)
        require_backend_tls(installation, game_port)
        game = http_health(26002)
    except (OSError, ValueError):
        game = False
    return game, market


def _prepared(root: str) -> LinuxInstallation:
    installation = LinuxInstallation.load(root)
    installation.require_supported_mods()
    require_client_trust(installation)
    return installation


@_serialize_start
def start_game(root: str, mode: str, *, mod_runtime_plan=None):
    from .server_launcher import (get_server_console_log, NativeModRuntimeLaunchReceipt,
                                  build_game_server_command, get_native_mod_status_log,
                                  _prepare_native_game_store_environment, _append_game_console)
    installation = _prepared(root)
    import socket
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', installation.cdn_port))
    except PermissionError as exc:
        raise LinuxSetupError('This user cannot bind HTTPS port 443. In Linux maintenance, choose Use unprivileged HTTPS port 8443 before starting Game.') from exc
    installation.verified_market_binary()
    env = installation.server_environment()
    server = installation.root / "server"
    console = get_server_console_log(installation.root)
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_bytes(b'')
    from .native_config_preflight import validate_native_server_config
    checked = validate_native_server_config(installation.root, env=env)
    if checked.supported and checked.valid is not True:
        raise LinuxSetupError(checked.message)
    _append_game_console(console, checked.message)
    # Existing prepared dependencies are checked, never reinstalled or rebuilt.
    run_bounded([str(installation.node), "-e", "const D=require('better-sqlite3');const d=new D(':memory:');d.prepare('SELECT 1').get();d.close()"],
                cwd=server, env=env, timeout=30)
    if mode not in ("vanilla", "modded"):
        raise LinuxSetupError("Invalid Native server mode.")
    _prepare_native_game_store_environment(installation.root, server, env)
    images = server / 'src/gameStore/migrateRuntimeImages.js'
    if images.is_file():
        run_bounded([str(installation.node), str(images)], cwd=server, env=env, timeout=60)
    command = build_game_server_command(root, mode, **({"mod_runtime_plan": mod_runtime_plan} if mod_runtime_plan is not None else {}))
    command[0] = str(installation.node)
    if mod_runtime_plan is not None:
        from .shared_mod_menu import stage_shared_menu
        stage_shared_menu(installation.root, mod_runtime_plan.shared_menu)
    from .evejs_compatibility import inspect_evejs_runtime
    inspect_evejs_runtime(root).node_report_directory.mkdir(parents=True, exist_ok=True)
    offset = console.stat().st_size
    from .linux_supervisor import start_supervised_service
    process = start_supervised_service(command, cwd=server, env=env, log=console)
    if mod_runtime_plan is not None:
        status = get_native_mod_status_log(root)
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_bytes(b"")
        process.mod_runtime_receipt = NativeModRuntimeLaunchReceipt(mod_runtime_plan.plan_sha256,
            mod_runtime_plan.runtime_identity, status, offset, console)
        from .server_launcher import resume_native_console_evidence
        resume_native_console_evidence(process)
    return process


@_serialize_start
def start_market(root: str):
    from .server_launcher import get_market_console_log
    installation = _prepared(root)
    binary = installation.verified_market_binary()
    console = get_market_console_log(installation.root)
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_bytes(b"")
    command = [str(binary), "--config", "config/market-server.local.toml", "serve"]
    from .linux_supervisor import start_supervised_service
    return start_supervised_service(command, cwd=installation.root / 'externalservices/market-server',
        env=installation.server_environment(), log=console, termination_signal=signal.SIGINT)
