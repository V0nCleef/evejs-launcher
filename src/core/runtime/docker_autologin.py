"""Read-only verification of Docker server settings for client auto-login."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from src.core.client_autologin import is_loopback_host
from src.core.launcher import ClientLaunchContext
from src.core.runtime.data import docker_project_identity
from src.core.runtime.docker_compose import (
    ComposeInspector,
    ComposeTarget,
    ContainerRecord,
    PreflightReport,
)
from src.core.runtime.endpoints import Endpoint


_UNVERIFIED_REASON = "Docker server password bypass could not be verified."
_DISABLED_REASON = "Docker server password bypass is disabled."
_ENABLED_REASON = "Docker server password bypass is enabled."


class DockerAutoLoginContextError(RuntimeError):
    """The selected Docker target no longer matches the captured launch context."""


@dataclass(frozen=True)
class DockerAutoLoginProbe:
    """Exact selected Compose target plus the observed server runtime identity."""

    target: ComposeTarget
    expected_game_runtime_identity: str


@dataclass(frozen=True)
class DockerAutoLoginCheck:
    """Privacy-safe outcome of the effective server setting probe."""

    enabled: bool
    verified: bool
    reason: str


@dataclass(frozen=True)
class _SelectedServer:
    container_id: str
    runtime_identity: str
    record: ContainerRecord


def inspect_docker_auto_login_capability(
    probe: DockerAutoLoginProbe,
    launch_context: ClientLaunchContext,
    *,
    inspector: object | None = None,
) -> DockerAutoLoginCheck:
    """Verify the running target, probe its config, then verify its identity again.

    Target, endpoint, or observed-container mismatches raise
    :class:`DockerAutoLoginContextError`; a verified disabled bypass and a
    failed config probe return a safe manual-login fallback result.
    """
    selected_inspector = inspector or _new_inspector()
    before = _inspect_selected_server(probe, launch_context, selected_inspector)
    try:
        enabled = selected_inspector.server_password_bypass_enabled(
            probe.target,
            before.record,
            before.container_id,
        )
        probe_verified = isinstance(enabled, bool)
    except Exception:  # Docker, Node, and module versions vary; expose no raw output.
        enabled = False
        probe_verified = False

    after = _inspect_selected_server(probe, launch_context, selected_inspector)
    if after != before:
        raise DockerAutoLoginContextError(
            "The selected Docker server changed while automatic login was checked."
        )
    if not probe_verified:
        return DockerAutoLoginCheck(False, False, _UNVERIFIED_REASON)
    if not enabled:
        return DockerAutoLoginCheck(False, True, _DISABLED_REASON)
    return DockerAutoLoginCheck(True, True, _ENABLED_REASON)


def assert_docker_auto_login_context_current(
    probe: DockerAutoLoginProbe,
    launch_context: ClientLaunchContext,
    *,
    inspector: object | None = None,
) -> None:
    """Revalidate the exact selected target and server immediately before spawn."""
    _inspect_selected_server(probe, launch_context, inspector or _new_inspector())


def _new_inspector() -> ComposeInspector:
    from src.core.runtime.docker_cli import DockerCommandRunner

    return ComposeInspector(DockerCommandRunner())


def _inspect_selected_server(
    probe: DockerAutoLoginProbe,
    launch_context: ClientLaunchContext,
    inspector: object,
) -> _SelectedServer:
    _validate_captured_identity(probe, launch_context)
    try:
        report = inspector.preflight(probe.target)
        selected = _select_server(report, probe, launch_context, inspector)
    except DockerAutoLoginContextError:
        raise
    except Exception:
        # Docker exceptions can retain full command details. Keep only a static
        # diagnostic across the worker/UI boundary.
        raise DockerAutoLoginContextError(
            "The selected Docker launch target could not be verified."
        ) from None
    return selected


def _validate_captured_identity(
    probe: DockerAutoLoginProbe,
    launch_context: ClientLaunchContext,
) -> None:
    if (
        not isinstance(launch_context.target_identity, str)
        or not launch_context.target_identity
        or not isinstance(launch_context.settings_identity, str)
        or not launch_context.settings_identity
        or isinstance(launch_context.monitor_generation, bool)
        or not isinstance(launch_context.monitor_generation, int)
        or launch_context.monitor_generation < 0
        or not isinstance(probe.expected_game_runtime_identity, str)
        or not probe.expected_game_runtime_identity
    ):
        raise DockerAutoLoginContextError(
            "The captured Docker launch identity is incomplete."
        )


def _select_server(
    report: PreflightReport,
    probe: DockerAutoLoginProbe,
    launch_context: ClientLaunchContext,
    inspector: object,
) -> _SelectedServer:
    if not report.ok or report.config is None or report.records is None:
        raise DockerAutoLoginContextError(
            "The selected Docker launch target could not be verified."
        )
    target_identity = docker_project_identity(
        probe.target,
        report.config.project_name,
        config=report.config,
    )
    if target_identity != launch_context.target_identity:
        raise DockerAutoLoginContextError(
            "The selected Docker project changed after its endpoints were observed."
        )
    _require_matching_endpoints(report.config.endpoints, launch_context)

    record = report.records.get("server")
    if (
        not isinstance(record, ContainerRecord)
        or not record.exists
        or record.raw_state != "running"
        or not isinstance(record.short_id, str)
    ):
        raise DockerAutoLoginContextError(
            "The selected Docker server is no longer running."
        )
    container_id, runtime_identity = inspector.container_runtime_id_and_identity(
        probe.target,
        record,
    )
    if runtime_identity != probe.expected_game_runtime_identity:
        raise DockerAutoLoginContextError(
            "The selected Docker server changed after its endpoints were observed."
        )
    return _SelectedServer(container_id, runtime_identity, record)


def _require_matching_endpoints(endpoints: object, context: ClientLaunchContext) -> None:
    game = getattr(endpoints, "game", None)
    proxy = getattr(endpoints, "proxy", None)
    image = getattr(endpoints, "image", None)
    if not isinstance(game, Endpoint) or not isinstance(proxy, Endpoint):
        raise DockerAutoLoginContextError(
            "The selected Docker launch endpoints are incomplete."
        )
    if (
        not is_loopback_host(game.host)
        or not is_loopback_host(proxy.host)
        or not _endpoint_matches_url(game, context.game_host, context.game_port)
        or not _url_matches_endpoint(context.proxy_url, proxy)
        or not _url_matches_endpoint(context.image_url, image)
    ):
        raise DockerAutoLoginContextError(
            "The selected Docker endpoint mapping changed after it was observed."
        )


def _endpoint_matches_url(endpoint: Endpoint, host: str, port: int) -> bool:
    return (
        endpoint.host.casefold() == host.casefold()
        and endpoint.port == port
    )


def _url_matches_endpoint(url: str | None, endpoint: object) -> bool:
    if not isinstance(url, str) or not isinstance(endpoint, Endpoint):
        return False
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and hostname is not None
        and port == endpoint.port
        and hostname.casefold() == endpoint.host.casefold()
    )
