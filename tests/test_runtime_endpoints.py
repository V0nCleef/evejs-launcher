"""Deterministic loopback readiness probes for effective runtime endpoints."""
from __future__ import annotations

import socket

from src.core.runtime.endpoints import Endpoint, probe_endpoint, probe_http_health


def test_probe_endpoint_reports_listening_then_closed_loopback_port() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])
    endpoint = Endpoint("server", "127.0.0.1", port, 26000, "tcp")

    try:
        assert probe_endpoint(endpoint, timeout=0.2) is True
    finally:
        listener.close()

    assert probe_endpoint(endpoint, timeout=0.05) is False


def test_probe_http_health_requires_successful_health_response() -> None:
    endpoint = Endpoint("server", "127.0.0.1", 32602, 26002, "tcp")
    calls: list[tuple[object, ...]] = []

    class Response:
        status = 200

        def read(self, amount: int) -> bytes:
            calls.append(("read", amount))
            return b"ok"

    class Connection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            calls.append(("connect", host, port, timeout))

        def request(self, method: str, path: str) -> None:
            calls.append(("request", method, path))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            calls.append(("close",))

    assert probe_http_health(
        endpoint,
        timeout=0.25,
        connection_factory=Connection,
    ) is True
    assert calls == [
        ("connect", "127.0.0.1", 32602, 0.25),
        ("request", "GET", "/health"),
        ("read", 1),
        ("close",),
    ]
