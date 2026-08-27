"""Client launcher — spawns the EVE client with correct environment."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
import logging
import os
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit

from .client_autologin import AutoLoginLaunch, require_auto_login_arguments
from .overview_state import OverviewBridgeLaunch
from .platform import (
    get_client_exe_path,
    launch_eve_client,
    prepare_evejs_client_certificate_trust,
    serialize_evejs_client_trust_and_spawn,
)
from .runtime.endpoints import RuntimeEndpoints, validate_port


log = logging.getLogger(__name__)

_MIN_RESOURCE_CACHE_HEX_DIRECTORIES = 240
_MIN_RESOURCE_CACHE_FILES = 50_000
_CLIENT_ENDPOINT_READINESS_TIMEOUT_SEC = 30.0
_CLIENT_ENDPOINT_POLL_INTERVAL_SEC = 0.5


def _http_url(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{rendered_host}:{port}"


def validate_proxy_origin(proxy_url: str) -> str:
    """Return one canonical supported local-client proxy origin."""
    normalized = str(proxy_url or "").strip()
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("EveJS proxy URL is invalid.") from exc
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or port is None
        or not 1 <= port <= 65535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "EveJS proxy URL must be an HTTP origin with an explicit port."
        )
    return _http_url(parsed.hostname, port)


def _probe_game_endpoint(host: str, port: int, *, timeout: float = 0.75) -> bool:
    """Return whether the exact client Game endpoint accepts a connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_proxy_health(proxy_url: str, *, timeout: float = 2.0) -> bool:
    """Return whether the exact client proxy answers its ``/health`` route."""
    try:
        parsed = urlsplit(validate_proxy_origin(proxy_url))
        connection = HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    except (OSError, TypeError, ValueError):
        return False

    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        response.read(1)
        # A listening HTTP process is not enough: require the gateway's actual
        # health route to succeed so an unrelated listener cannot pass.
        return 200 <= int(response.status) < 300
    except (HTTPException, OSError, TypeError, ValueError):
        return False
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _resolve_client_resource_cache(
    profile_tq_path: Path,
    client_path: str,
) -> Path:
    client_tq = Path(client_path) if client_path else profile_tq_path.resolve()
    cache_root = client_tq.parent
    resfiles = cache_root / "ResFiles"
    resource_index = cache_root / "index_tranquility.txt"
    if not resfiles.is_dir():
        raise FileNotFoundError(f"Client ResFiles folder not found: {resfiles}")
    if not resource_index.is_file():
        raise FileNotFoundError(f"Client resource index not found: {resource_index}")
    _validate_client_resource_cache_contents(resfiles)
    return resfiles


def _validate_client_resource_cache_contents(resfiles: Path) -> None:
    try:
        hex_directories = sum(
            1
            for entry in resfiles.iterdir()
            if entry.is_dir()
            and len(entry.name) == 2
            and all(character in "0123456789abcdefABCDEF" for character in entry.name)
        )
        if hex_directories < _MIN_RESOURCE_CACHE_HEX_DIRECTORIES:
            raise RuntimeError(
                "Client ResFiles cache is incomplete "
                f"({hex_directories} hexadecimal directories found; "
                f"expected at least {_MIN_RESOURCE_CACHE_HEX_DIRECTORIES})."
            )

        file_count = 0
        for _root, _directories, files in os.walk(resfiles):
            file_count += len(files)
            if file_count >= _MIN_RESOURCE_CACHE_FILES:
                return
    except OSError as exc:
        raise RuntimeError(f"Unable to inspect client ResFiles cache: {resfiles}") from exc

    raise RuntimeError(
        "Client ResFiles cache is incomplete "
        f"({file_count} files found; expected at least {_MIN_RESOURCE_CACHE_FILES})."
    )


@dataclass(frozen=True)
class ClientLaunchContext:
    """Endpoint values captured once before any per-profile launch mutation."""

    game_host: str
    game_port: int
    proxy_url: str
    image_url: str | None = None
    target_identity: str | None = None
    settings_identity: str | None = None
    monitor_generation: int | None = None

    def __post_init__(self) -> None:
        if not str(self.game_host or "").strip():
            raise ValueError("EveJS game host is required.")
        object.__setattr__(self, "game_host", str(self.game_host).strip())
        object.__setattr__(
            self,
            "game_port",
            validate_port(self.game_port, label="EveJS game"),
        )
        object.__setattr__(self, "proxy_url", validate_proxy_origin(self.proxy_url))

    @classmethod
    def native(
        cls,
        *,
        game_port: int = 26000,
        proxy_url: str = "http://127.0.0.1:26002",
    ) -> "ClientLaunchContext":
        return cls("127.0.0.1", game_port, proxy_url)

    @classmethod
    def from_docker(
        cls,
        endpoints: RuntimeEndpoints | None,
        *,
        target_identity: str,
        settings_identity: str,
        monitor_generation: int,
    ) -> "ClientLaunchContext":
        if endpoints is None:
            raise ValueError("Docker endpoints are unavailable.")
        if endpoints.game is None or endpoints.image is None or endpoints.proxy is None:
            raise ValueError("Docker client endpoints are incomplete.")
        if (
            not target_identity
            or not settings_identity
            or isinstance(monitor_generation, bool)
            or not isinstance(monitor_generation, int)
            or monitor_generation < 0
        ):
            raise ValueError("Docker launch identity is incomplete.")
        return cls(
            game_host=endpoints.game.host,
            game_port=endpoints.game.port,
            proxy_url=_http_url(endpoints.proxy.host, endpoints.proxy.port),
            image_url=_http_url(endpoints.image.host, endpoints.image.port),
            target_identity=target_identity,
            settings_identity=settings_identity,
            monitor_generation=monitor_generation,
        )


def wait_for_client_endpoints(
    context: ClientLaunchContext,
    *,
    timeout_sec: float = _CLIENT_ENDPOINT_READINESS_TIMEOUT_SEC,
    poll_interval_sec: float = _CLIENT_ENDPOINT_POLL_INTERVAL_SEC,
    game_probe: Callable[[str, int], bool] | None = None,
    proxy_probe: Callable[[str], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until the immutable Game and proxy targets are both usable.

    The client is configured to route its HTTP traffic through the selected
    EveJS proxy, so a listening Game socket alone is not sufficient launch
    evidence.  This runs in the existing client-launch worker, never the Qt
    GUI thread.
    """
    timeout = float(timeout_sec)
    poll_interval = float(poll_interval_sec)
    if timeout <= 0:
        raise ValueError("Client endpoint readiness timeout must be positive.")
    if poll_interval <= 0:
        raise ValueError("Client endpoint poll interval must be positive.")

    probe_game = game_probe or _probe_game_endpoint
    probe_proxy = proxy_probe or _probe_proxy_health
    deadline = clock_fn() + timeout
    game_ready = False
    proxy_ready = False

    while True:
        try:
            game_ready = bool(probe_game(context.game_host, context.game_port))
        except Exception:  # noqa: BLE001 - injected/socket adapters can vary
            game_ready = False
        try:
            proxy_ready = bool(probe_proxy(context.proxy_url))
        except Exception:  # noqa: BLE001 - injected/HTTP adapters can vary
            proxy_ready = False
        if game_ready and proxy_ready:
            return

        remaining = deadline - clock_fn()
        if remaining <= 0:
            break
        sleep_fn(min(poll_interval, remaining))

    game_status = "ready" if game_ready else "not ready"
    proxy_status = "ready" if proxy_ready else "not ready"
    health_url = f"{context.proxy_url.rstrip('/')}/health"
    raise RuntimeError(
        "EveJS is not ready for client launch. "
        f"Game TCP {context.game_host}:{context.game_port} is {game_status}; "
        f"proxy {health_url} is {proxy_status}. Start the server and wait for "
        "both endpoints before trying again."
    )


def require_client_endpoints_ready(
    context: ClientLaunchContext,
    *,
    game_probe: Callable[[str, int], bool] | None = None,
    proxy_probe: Callable[[str], bool] | None = None,
) -> None:
    """Fail unless both immutable client endpoints are ready right now."""
    probe_game = game_probe or _probe_game_endpoint
    probe_proxy = proxy_probe or _probe_proxy_health
    try:
        game_ready = bool(probe_game(context.game_host, context.game_port))
    except Exception:  # noqa: BLE001 - injected/socket adapters can vary
        game_ready = False
    try:
        proxy_ready = bool(probe_proxy(context.proxy_url))
    except Exception:  # noqa: BLE001 - injected/HTTP adapters can vary
        proxy_ready = False
    if game_ready and proxy_ready:
        return

    game_status = "ready" if game_ready else "not ready"
    proxy_status = "ready" if proxy_ready else "not ready"
    health_url = f"{context.proxy_url.rstrip('/')}/health"
    raise RuntimeError(
        "EveJS stopped being ready before the client could start. "
        f"Game TCP {context.game_host}:{context.game_port} is {game_status}; "
        f"proxy {health_url} is {proxy_status}. Try again after the server is stable."
    )


def build_env(evejs_root: str, proxy_url: str = "http://127.0.0.1:26002") -> dict[str, str]:
    """Replicate the environment setup from Play.bat.

    Returns a dict suitable for passing to subprocess.Popen(env=...).
    """
    proxy_url = validate_proxy_origin(proxy_url)

    repo = Path(evejs_root)
    ca_pem = repo / "server" / "certs" / "xmpp-ca-cert.pem"

    env = os.environ.copy()
    # Never let a stale parent-process cache path leak into the client. A
    # verified path for the selected copied client is installed before spawn.
    env.pop("EO_REMOTEFILECACHEFOLDER", None)
    # Overview capture/apply is a one-shot explicit launch capability. Never
    # inherit a command or acknowledgement path from the launcher process.
    env.pop("EVEJS_OVERVIEW_BRIDGE", None)
    env.pop("EVEJS_OVERVIEW_ACK_PATH", None)

    # ── Proxy ──────────────────────────────────────────────────────────
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                 "all_proxy", "ALL_PROXY"):
        env[key] = proxy_url

    env["EVEJS_PROXY_LOCAL_INTERCEPT"] = "1"
    env["EVEJS_PROXY_UNHANDLED_HOST_POLICY"] = "block"
    env["EVEJS_NO_PROXY"] = "127.0.0.1,localhost,::1"
    env["no_proxy"] = env["EVEJS_NO_PROXY"]
    env["NO_PROXY"] = env["no_proxy"]

    # ── Blocked hosts ───────────────────────────────────────────────────
    default_blocked_parts = [
        "api.ipify.org",
        "sentry.io,.sentry.io",
        "google-analytics.com,.google-analytics.com",
    ]
    darkly_blocked_parts = [
        "launchdarkly.com,.launchdarkly.com",
        "clientstream.launchdarkly.com",
        "events.launchdarkly.com",
        "mobile.launchdarkly.com",
        "app.launchdarkly.com",
        "sdk.launchdarkly.com",
        "stream.launchdarkly.com",
        "launchdarkly.us,.launchdarkly.us",
        "launchdarkly.eu,.launchdarkly.eu",
    ]
    darkly_blocked_hosts = ",".join(darkly_blocked_parts)
    inherited_blocked_hosts = env.get("EVEJS_PROXY_BLOCKED_HOSTS", "").strip(", ")
    env["EVEJS_DARKLY_BLOCK_HOSTS"] = darkly_blocked_hosts
    env["EVEJS_PROXY_BLOCKED_HOSTS"] = (
        f"{inherited_blocked_hosts},{darkly_blocked_hosts}"
        if inherited_blocked_hosts
        else ",".join((*default_blocked_parts, *darkly_blocked_parts))
    )

    # ── Sentry / LaunchDarkly off ───────────────────────────────────────
    env["EVE_CLIENT_SENTRY_DSN"] = ""
    env["LD_OFFLINE"] = "true"
    env["LAUNCHDARKLY_OFFLINE"] = "true"
    env["LAUNCHDARKLY_SEND_EVENTS"] = "false"
    env["LD_SEND_EVENTS"] = "false"

    # ── TLS ─────────────────────────────────────────────────────────────
    if ca_pem.exists():
        env["SSL_CERT_FILE"] = str(ca_pem)
        env["REQUESTS_CA_BUNDLE"] = str(ca_pem)
        env["CURL_CA_BUNDLE"] = str(ca_pem)
    env["SSL_CERT_DIR"] = ""

    return env


def launch_client(
    evejs_root: str,
    profile_tq_path: Path,
    proxy_url: str = "http://127.0.0.1:26002",
    client_path: str = "",
    *,
    launch_context: ClientLaunchContext | None = None,
    auto_login: AutoLoginLaunch | None = None,
    overview_bridge: OverviewBridgeLaunch | None = None,
    pre_spawn_check: Callable[[], None] | None = None,
) -> subprocess.Popen:
    """Launch the EVE client executable from a profile junction.

    Args:
        evejs_root: Path to EveJS installation root.
        profile_tq_path: Path to the profile's tq junction.
        proxy_url: Proxy URL for EveJS.
        client_path: The user-configured EVE client tq folder.  Used to
            derive the ResFiles cache (mirrors Play.bat behaviour).
        auto_login: Optional typed local-login intent.  When present, the
        exact copied client and EveJS password-bypass configuration are
            verified before the guarded login, character, and no-console
            switches are added.
        overview_bridge: Optional one-shot capture/apply command for a verified
            launcher-patched client.

    Returns:
        subprocess.Popen for the launched process.
    """
    exe = get_client_exe_path(profile_tq_path)
    if not exe.exists():
        raise FileNotFoundError(f"Client executable not found: {exe}")
    effective_proxy = (
        launch_context.proxy_url if launch_context is not None else proxy_url
    )
    effective_context = launch_context or ClientLaunchContext.native(
        proxy_url=effective_proxy,
    )
    resfiles = _resolve_client_resource_cache(profile_tq_path, client_path)

    with serialize_evejs_client_trust_and_spawn():
        certificate_client_path = (
            Path(client_path) if client_path else profile_tq_path.resolve()
        )
        if prepare_evejs_client_certificate_trust(
            evejs_root,
            certificate_client_path,
        ):
            log.info("Prepared EveJS certificate trust for the selected installation.")

        env = build_env(evejs_root, effective_proxy)
        if overview_bridge is not None:
            env["EVEJS_OVERVIEW_BRIDGE"] = overview_bridge.command
            env["EVEJS_OVERVIEW_ACK_PATH"] = str(overview_bridge.ack_path)

        # The selected copied client's cache was validated before certificate
        # mutation, matching Play.bat's fail-closed launch order.
        env["EO_REMOTEFILECACHEFOLDER"] = str(resfiles)

        arguments: tuple[str, ...] = (f"/port:{effective_context.game_port}",)
        if auto_login is not None:
            arguments += require_auto_login_arguments(
                auto_login,
                evejs_root=evejs_root,
                client_path=client_path or profile_tq_path.resolve(),
                game_host=effective_context.game_host,
            )

        if auto_login is not None:
            # Never log the rendered /login value: even though the supported
            # Native path uses a fixed dummy password, keeping the diagnostic
            # structural prevents future credentials from leaking into logs.
            log.info(
                "Starting EVE with verified local auto-login "
                "(account=%s character_id=%s "
                "switches=noconsole,login,autoSelectCharacter)",
                auto_login.username,
                auto_login.character_id,
            )
        if pre_spawn_check is not None:
            pre_spawn_check()
        return launch_eve_client(exe, env, exe.parent, arguments=arguments)
