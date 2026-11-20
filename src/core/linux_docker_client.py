"""Validate a prepared Proton client against the exact observed Docker runtime."""
from __future__ import annotations

import socket

from .linux_client import local_ca_context
from .linux_installation import LinuxSetupError
from .runtime.docker_autologin import _select_server
from .runtime.docker_cli import DockerCommandRunner
from .runtime.docker_compose import ComposeInspector
from .runtime.endpoints import probe_endpoint, probe_http_health


def require_docker_runtime(request, installation, *, inspector=None):
    probe = request.docker_auto_login_probe
    if probe is None:
        raise LinuxSetupError('The selected Docker server identity is unavailable. Refresh Docker status and try again.')
    context = request.launch_context
    if context.game_host != '127.0.0.1':
        raise LinuxSetupError('This prepared Proton client requires IPv4 loopback publications.')
    selected = inspector or ComposeInspector(DockerCommandRunner())
    report = selected.preflight(probe.target)
    before = _select_server(report, probe, context, selected)
    endpoints = report.config.endpoints
    # The proven 3396210 patch has fixed gateway/XMPP addresses. Game, image,
    # proxy and Market host ports come from the selected Compose observation.
    if ((endpoints.assets.host, endpoints.assets.port) != ('127.0.0.1', 26003)
            or (endpoints.xmpp.host, endpoints.xmpp.port) != ('127.0.0.1', 5222)):
        raise LinuxSetupError('The prepared Linux client requires Docker assets at 127.0.0.1:26003 and XMPP at 127.0.0.1:5222.')
    actual = selected.container_tcp_publications(probe.target, before.record, before.container_id)
    for endpoint in (endpoints.game, endpoints.image, endpoints.proxy, endpoints.assets, endpoints.xmpp):
        if endpoint not in actual:
            raise LinuxSetupError('The running Docker container does not publish its observed client endpoints. Recreate the selected Compose services.')
    cdn = [endpoint for endpoint in actual if endpoint.host == '127.0.0.1'
           and endpoint.port == installation.cdn_port and endpoint.target in (443, 8443)]
    if len(cdn) != 1:
        raise LinuxSetupError(f'Publish the selected Docker server CDN on 127.0.0.1:{installation.cdn_port}, matching the client prepared by Linux Setup.')
    if not probe_http_health(endpoints.market) or not probe_http_health(endpoints.proxy):
        raise LinuxSetupError('The selected Docker Market/proxy health endpoint is unavailable.')
    if not probe_endpoint(endpoints.image):
        raise LinuxSetupError('The selected Docker image endpoint is unavailable.')
    ca = installation.root / '_local/certs/xmpp-ca-cert.pem'
    trust = local_ca_context(ca)
    for endpoint, hostname in ((endpoints.xmpp, 'localhost'),
                                (endpoints.assets, 'dev-public-gateway.evetech.net'), (cdn[0], 'localhost')):
        try:
            with socket.create_connection((endpoint.host, endpoint.port), timeout=3) as connection:
                with trust.wrap_socket(connection, server_hostname=hostname):
                    pass
        except OSError as exc:
            raise LinuxSetupError('The selected Docker TLS endpoints do not match the CA used to prepare this copied client. Prepare a client for that Docker CA before launching.') from exc
    after = selected.preflight(probe.target)
    if _select_server(after, probe, context, selected) != before:
        raise LinuxSetupError('The selected Docker server changed during client preparation.')
