"""Immutable effective endpoint values shared across runtime layers."""
from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
import socket
from typing import Callable


@dataclass(frozen=True)
class Endpoint:
    """One effective loopback publication for a semantic service endpoint."""

    service: str
    host: str
    port: int
    target: int
    protocol: str


@dataclass(frozen=True)
class RuntimeEndpoints:
    """Complete authoritative endpoint set resolved from one runtime target."""

    game: Endpoint
    image: Endpoint
    proxy: Endpoint
    assets: Endpoint
    xmpp: Endpoint
    market: Endpoint


def probe_endpoint(endpoint: Endpoint, *, timeout: float = 0.5) -> bool:
    """Return whether one effective TCP endpoint accepts a bounded connection."""
    if timeout <= 0:
        raise ValueError("Endpoint probe timeout must be positive.")
    if endpoint.protocol.casefold() != "tcp":
        return False
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_http_health(
    endpoint: Endpoint,
    *,
    timeout: float = 0.5,
    connection_factory: Callable[..., object] = HTTPConnection,
) -> bool:
    """Return whether an effective endpoint serves a bounded HTTP ``/health``."""
    if timeout <= 0:
        raise ValueError("Endpoint probe timeout must be positive.")
    if endpoint.protocol.casefold() != "tcp":
        return False
    connection = None
    try:
        connection = connection_factory(
            endpoint.host,
            endpoint.port,
            timeout=timeout,
        )
        connection.request("GET", "/health")
        response = connection.getresponse()
        response.read(1)
        return 200 <= int(response.status) < 300
    except (HTTPException, OSError, ValueError):
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
