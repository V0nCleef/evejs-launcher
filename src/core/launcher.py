"""Client launcher — spawns exefile.exe with correct environment."""
import os
import subprocess
from pathlib import Path


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
) -> subprocess.Popen:
    """Launch exefile.exe from a profile junction.

    Args:
        evejs_root: Path to EveJS installation root.
        profile_tq_path: Path to the profile's tq junction.
        proxy_url: Proxy URL for EveJS.

    Returns:
        subprocess.Popen for the launched process.
    """
    exe = profile_tq_path / "bin64" / "exefile.exe"
    if not exe.exists():
        raise FileNotFoundError(f"Client executable not found: {exe}")

    env = build_env(evejs_root, proxy_url)

    # Resolve the real client path for ResFiles
    real_tq = profile_tq_path.resolve()
    cache_root = real_tq.parent
    resfiles = cache_root / "ResFiles"
    if resfiles.exists():
        env["EO_REMOTEFILECACHEFOLDER"] = str(resfiles)

    return subprocess.Popen(
        [str(exe)],
        env=env,
        cwd=str(exe.parent),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS,
    )
