"""Client launcher — spawns the EVE client with correct environment."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path

from .client_autologin import AutoLoginLaunch, require_auto_login_arguments
from .overview_state import OverviewBridgeLaunch
from .platform import (
    get_client_exe_path,
    launch_eve_client,
    prepare_evejs_client_certificate_trust,
    serialize_evejs_client_trust_and_spawn,
)
from .runtime.endpoints import RuntimeEndpoints


log = logging.getLogger(__name__)


def _http_url(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{rendered_host}:{port}"


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

    @classmethod
    def native(
        cls,
        *,
        game_port: int = 26000,
        proxy_url: str = "http://127.0.0.1:26002",
    ) -> "ClientLaunchContext":
        return cls("127.0.0.1", int(game_port), proxy_url)

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


def build_env(evejs_root: str, proxy_url: str = "http://127.0.0.1:26002") -> dict[str, str]:
    """Replicate the environment setup from Play.bat.

    Returns a dict suitable for passing to subprocess.Popen(env=...).
    """
    repo = Path(evejs_root)
    ca_pem = repo / "server" / "certs" / "xmpp-ca-cert.pem"

    env = os.environ.copy()

    # ── Proxy ──────────────────────────────────────────────────────────
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                 "all_proxy", "ALL_PROXY"):
        env[key] = proxy_url

    env["no_proxy"] = "127.0.0.1,localhost,::1"
    env["NO_PROXY"] = env["no_proxy"]

    # ── Blocked hosts ───────────────────────────────────────────────────
    blocked_parts = [
        "api.ipify.org",
        "sentry.io,.sentry.io",
        "google-analytics.com,.google-analytics.com",
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
    env["EVEJS_PROXY_BLOCKED_HOSTS"] = ",".join(blocked_parts)

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

    with serialize_evejs_client_trust_and_spawn():
        certificate_client_path = (
            Path(client_path) if client_path else profile_tq_path.resolve()
        )
        if prepare_evejs_client_certificate_trust(
            evejs_root,
            certificate_client_path,
        ):
            log.info("Prepared EveJS certificate trust for the selected installation.")

        effective_proxy = (
            launch_context.proxy_url if launch_context is not None else proxy_url
        )
        env = build_env(evejs_root, effective_proxy)
        if overview_bridge is not None:
            env["EVEJS_OVERVIEW_BRIDGE"] = overview_bridge.command
            env["EVEJS_OVERVIEW_ACK_PATH"] = str(overview_bridge.ack_path)

        # ── ResFiles: derive from configured client, NOT profile junction ──
        # Resolving through the junction could use the official TQ cache instead
        # of the copied client's EveJS-managed resources.
        if client_path:
            cache_root = Path(client_path).parent
        else:
            # Fallback for callers that don't pass client_path (backward compat).
            cache_root = profile_tq_path.resolve().parent

        resfiles = cache_root / "ResFiles"
        if resfiles.exists():
            env["EO_REMOTEFILECACHEFOLDER"] = str(resfiles)

        arguments: tuple[str, ...] = ()
        if auto_login is not None:
            effective_context = launch_context or ClientLaunchContext.native(
                proxy_url=effective_proxy,
            )
            arguments = require_auto_login_arguments(
                auto_login,
                evejs_root=evejs_root,
                client_path=client_path or profile_tq_path.resolve(),
                game_host=effective_context.game_host,
            )

        if arguments:
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
            return launch_eve_client(exe, env, exe.parent, arguments=arguments)
        return launch_eve_client(exe, env, exe.parent)
