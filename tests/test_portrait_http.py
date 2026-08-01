"""Strict Phase 3 tests for target-aware Native and Docker portraits."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QColor, QImage

from src.core.runtime.docker_compose import Endpoint
from src.core.runtime.portraits import (
    MAX_PORTRAIT_BYTES,
    PortraitLoadError,
    PortraitProvider,
    PortraitRequest,
    PortraitTarget,
)


class _Headers:
    def __init__(self, values: dict[str, str | list[str]]) -> None:
        self._values = {key.casefold(): value for key, value in values.items()}

    def get_all(self, name: str) -> list[str] | None:
        value = self._values.get(name.casefold())
        if value is None:
            return None
        return list(value) if isinstance(value, list) else [value]


class _Response:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        content_type: str | None = "image/jpeg",
        content_length: str | None = None,
        extra_headers: dict[str, str | list[str]] | None = None,
    ) -> None:
        self.status = status
        headers: dict[str, str | list[str]] = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if content_length is not None:
            headers["Content-Length"] = content_length
        elif content_length is None and body:
            headers["Content-Length"] = str(len(body))
        if extra_headers:
            headers.update(extra_headers)
        self.headers = _Headers(headers)
        self._stream = BytesIO(body)

    def read(self, amount: int = -1) -> bytes:
        return self._stream.read(amount)

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Open:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, request, timeout: float):  # type: ignore[no-untyped-def]
        self.calls.append((request.full_url, timeout))
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _image_bytes(fmt: str = "JPEG", *, width: int = 64, height: int = 64) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(20, 80, 140))
    payload = QByteArray()
    buffer = QBuffer(payload)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt)
    return bytes(payload)


def _docker_target(
    identity: str = "docker:fixture",
    *,
    monitor_generation: int = 7,
) -> PortraitTarget:
    return PortraitTarget(
        target_identity=identity,
        settings_identity="docker-settings:fixture",
        monitor_generation=monitor_generation,
        image_endpoint=Endpoint(
            service="server",
            host="127.0.0.1",
            port=32601,
            target=26001,
            protocol="tcp",
        ),
    )


def _request(
    identity: str = "docker:fixture",
    *,
    settings_identity: str | None = "docker-settings:fixture",
    monitor_generation: int | None = 7,
) -> PortraitRequest:
    return PortraitRequest(
        target_identity=identity,
        character_id=9001,
        size=64,
        generation=7,
        token="fixture-token",
        settings_identity=settings_identity,
        monitor_generation=monitor_generation,
    )


def test_docker_portrait_uses_effective_remapped_endpoint_and_decodes_qimage(
    tmp_path: Path,
) -> None:
    body = _image_bytes()
    opener = _Open(_Response(body))
    provider = PortraitProvider(
        _docker_target(),
        cache_dir=tmp_path,
        http_open=opener,
    )

    result = provider.load(_request())

    assert opener.calls == [
        ("http://127.0.0.1:32601/Character/9001_64.jpg", 3.0)
    ]
    assert isinstance(result.image, QImage)
    assert (result.image.width(), result.image.height()) == (64, 64)
    assert result.source == "http"


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (_Response(b"x", status=404), "http_status"),
        (_Response(b"x", content_type=None, extra_headers={"Content-Length": "1"}), "content_type"),
        (_Response(b"x", content_type="text/plain"), "content_type"),
        (
            _Response(
                b"x",
                extra_headers={"Content-Type": ["image/jpeg", "image/jpeg"]},
            ),
            "content_type",
        ),
        (_Response(b"x", extra_headers={"Content-Length": []}), "content_length"),
        (_Response(b"x", extra_headers={"Content-Length": "invalid"}), "content_length"),
        (_Response(b"x", extra_headers={"Content-Length": "0"}), "content_length"),
        (
            _Response(
                b"x",
                extra_headers={"Content-Length": str(MAX_PORTRAIT_BYTES + 1)},
            ),
            "content_length",
        ),
        (
            _Response(
                b"x",
                extra_headers={"Content-Length": ["1", "1"]},
            ),
            "content_length",
        ),
    ],
    ids=[
        "status",
        "missing-type",
        "wrong-type",
        "duplicate-type",
        "missing-length",
        "malformed-length",
        "zero-length",
        "oversized-length",
        "duplicate-length",
    ],
)
def test_http_portrait_rejects_invalid_response_envelope(
    tmp_path: Path,
    response: _Response,
    expected_code: str,
) -> None:
    provider = PortraitProvider(
        _docker_target(),
        cache_dir=tmp_path,
        http_open=_Open(response),
    )

    with pytest.raises(PortraitLoadError) as error:
        provider.load(_request())

    assert error.value.code == expected_code
    assert len(str(error.value)) <= 160


@pytest.mark.parametrize(
    ("body", "declared_length", "content_type", "expected_code"),
    [
        (_image_bytes()[:-2], len(_image_bytes()), "image/jpeg", "body_length"),
        (_image_bytes() + b"x", len(_image_bytes()), "image/jpeg", "body_length"),
        (_image_bytes("PNG"), len(_image_bytes("PNG")), "image/jpeg", "signature"),
        (b"\xff\xd8\xffnot-an-image", 15, "image/jpeg", "decode"),
        (_image_bytes(width=64, height=32), len(_image_bytes(width=64, height=32)), "image/jpeg", "dimensions"),
        (_image_bytes(width=128, height=128), len(_image_bytes(width=128, height=128)), "image/jpeg", "dimensions"),
        (_image_bytes(width=2049, height=2049), len(_image_bytes(width=2049, height=2049)), "image/jpeg", "dimensions"),
    ],
    ids=[
        "short-body",
        "long-body",
        "mime-signature",
        "decode",
        "non-square",
        "wrong-size",
        "huge-dimensions",
    ],
)
def test_http_portrait_rejects_invalid_body_or_image(
    tmp_path: Path,
    body: bytes,
    declared_length: int,
    content_type: str,
    expected_code: str,
) -> None:
    response = _Response(
        body,
        content_type=content_type,
        extra_headers={"Content-Length": str(declared_length)},
    )
    provider = PortraitProvider(
        _docker_target(),
        cache_dir=tmp_path,
        http_open=_Open(response),
    )

    with pytest.raises(PortraitLoadError) as error:
        provider.load(_request())

    assert error.value.code == expected_code


def test_valid_disk_cache_serves_docker_portrait_offline(
    tmp_path: Path,
) -> None:
    body = _image_bytes()
    online = _Open(_Response(body))
    first = PortraitProvider(
        _docker_target(),
        cache_dir=tmp_path,
        http_open=online,
    ).load(_request())
    offline = _Open(OSError("private network detail"))

    second = PortraitProvider(
        _docker_target(),
        cache_dir=tmp_path,
        http_open=offline,
    ).load(_request())

    assert first.source == "http"
    assert second.source == "disk"
    assert offline.calls == []
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_disk_cache_isolated_by_runtime_target_identity(tmp_path: Path) -> None:
    body = _image_bytes()
    opener = _Open(_Response(body), _Response(body))

    PortraitProvider(
        _docker_target("docker:first"),
        cache_dir=tmp_path,
        http_open=opener,
    ).load(_request("docker:first"))
    PortraitProvider(
        _docker_target("docker:second"),
        cache_dir=tmp_path,
        http_open=opener,
    ).load(_request("docker:second"))

    assert len(opener.calls) == 2
    assert len(list(tmp_path.glob("*.png"))) == 2


def test_disk_cache_and_validation_are_isolated_by_monitor_generation(
    tmp_path: Path,
) -> None:
    body = _image_bytes()
    opener = _Open(_Response(body), _Response(body))

    PortraitProvider(
        _docker_target(monitor_generation=7),
        cache_dir=tmp_path,
        http_open=opener,
    ).load(_request(monitor_generation=7))
    PortraitProvider(
        _docker_target(monitor_generation=8),
        cache_dir=tmp_path,
        http_open=opener,
    ).load(_request(monitor_generation=8))

    assert len(opener.calls) == 2
    assert len(list(tmp_path.glob("*.png"))) == 2

    with pytest.raises(PortraitLoadError, match="current runtime target"):
        PortraitProvider(
            _docker_target(monitor_generation=8),
            cache_dir=tmp_path,
            http_open=opener,
        ).load(_request(monitor_generation=7))


def test_corrupt_disk_cache_is_removed_then_replaced_from_http(
    tmp_path: Path,
) -> None:
    body = _image_bytes()
    opener = _Open(_Response(body))
    provider = PortraitProvider(
        _docker_target(),
        cache_dir=tmp_path,
        http_open=opener,
    )
    cache_path = provider._cache_path(_request())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"not-an-image")

    result = provider.load(_request())

    assert result.source == "http"
    assert len(opener.calls) == 1
    assert cache_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_docker_http_failure_never_falls_back_to_native_files(
    tmp_path: Path,
) -> None:
    native_path = (
        tmp_path
        / "native"
        / "server"
        / "src"
        / "_secondary"
        / "image"
        / "generated"
        / "Character"
        / "9001_64.jpg"
    )
    native_path.parent.mkdir(parents=True)
    native_path.write_bytes(_image_bytes())
    docker = _docker_target()
    target = PortraitTarget(
        target_identity=docker.target_identity,
        native_root=tmp_path / "native",
        image_endpoint=docker.image_endpoint,
        settings_identity=docker.settings_identity,
        monitor_generation=docker.monitor_generation,
    )
    opener = _Open(OSError("private network detail"))

    with pytest.raises(PortraitLoadError) as error:
        PortraitProvider(
            target,
            cache_dir=tmp_path / "cache",
            http_open=opener,
        ).load(_request())

    assert error.value.code == "network"
    assert len(opener.calls) == 1


def test_native_portrait_preserves_local_search_and_never_uses_http(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    image_path = (
        root
        / "server"
        / "src"
        / "_secondary"
        / "image"
        / "generated"
        / "Character"
        / "9001_64.jpg"
    )
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_image_bytes(width=96, height=64))
    target = PortraitTarget(
        target_identity="native:fixture",
        native_root=root,
    )
    opener = _Open(OSError("Native portrait must not open HTTP"))

    result = PortraitProvider(
        target,
        cache_dir=tmp_path / "cache",
        http_open=opener,
    ).load(
        _request(
            "native:fixture",
            settings_identity=None,
            monitor_generation=None,
        )
    )

    assert result.source == "native"
    assert (result.image.width(), result.image.height()) == (64, 64)
    assert opener.calls == []
