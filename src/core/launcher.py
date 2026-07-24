"""Client launcher — spawns the EVE client with correct environment."""
from __future__ import annotations

import os
from pathlib import Path

from .platform import get_client_exe_path, launch_eve_client


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
) -> subprocess.Popen:
    """Launch the EVE client executable from a profile junction.

    Args:
        evejs_root: Path to EveJS installation root.
        profile_tq_path: Path to the profile's tq junction.
        proxy_url: Proxy URL for EveJS.
        client_path: The user-configured EVE client tq folder.  Used to
            derive the ResFiles cache (mirrors Play.bat behaviour).

    Returns:
        subprocess.Popen for the launched process.
    """
    exe = get_client_exe_path(profile_tq_path)
    if not exe.exists():
        raise FileNotFoundError(f"Client executable not found: {exe}")

    env = build_env(evejs_root, proxy_url)

    # ── ResFiles: derive from the configured client path, NOT the junction ──
    # Play.bat resolves EVEJS_CLIENT_PATH\\..\\ResFiles — the ResFiles that
    # lives beside the user's configured client copy.  Resolving through the
    # junction could land on the real TQ client's cache, poisoning the client
    # with official resource files instead of the EveJS-managed ones.
    if client_path:
        cache_root = Path(client_path).parent
    else:
        # Fallback for callers that don't pass client_path (backward compat).
        cache_root = profile_tq_path.resolve().parent

    resfiles = cache_root / "ResFiles"
    if resfiles.exists():
        env["EO_REMOTEFILECACHEFOLDER"] = str(resfiles)

    return launch_eve_client(exe, env, exe.parent)
