"""Validated, target-aware portrait loading for Native and Docker runtimes."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage, QImageReader

from src.core.runtime.docker_compose import Endpoint


MAX_PORTRAIT_BYTES = 4 * 1024 * 1024
_HTTP_TIMEOUT = 3.0
_READ_CHUNK = 64 * 1024
_MAX_DIMENSION = 2048
_MAX_PIXELS = 4_194_304
_ALLOWED_SIZES = frozenset({64, 128, 256, 512})

_PORTRAIT_SEARCH_PATHS = (
    "server/src/_secondary/image/generated/Character/{character_id}_{size}.jpg",
    "server/src/_secondary/image/generated/Character/{character_id}_{size}.png",
    "server/src/_secondary/image/generated/Character/{character_id}_256.jpg",
    "server/src/_secondary/image/generated/Character/{character_id}_256.png",
    "server/src/_secondary/image/generated/Character/{character_id}_128.jpg",
    "server/src/_secondary/image/generated/Character/{character_id}_128.png",
    "server/src/_secondary/image/generated/Character/{character_id}_512.jpg",
    "server/src/_secondary/image/generated/Character/{character_id}_512.png",
    "server/src/_secondary/image/generated/Character/{character_id}_64.jpg",
    "server/src/_secondary/image/generated/Character/{character_id}_64.png",
    "server/src/_secondary/image/images/hi.jpg",
    "server/src/_secondary/image/images/hi.png",
)


class PortraitLoadError(RuntimeError):
    """Bounded portrait failure with no target, URL, or filesystem detail."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message[:160])


@dataclass(frozen=True)
class PortraitTarget:
    """One authoritative source; Docker never falls back to Native files."""

    target_identity: str
    native_root: Path | None = None
    image_endpoint: Endpoint | None = None
    settings_identity: str | None = None
    monitor_generation: int | None = None


@dataclass(frozen=True)
class PortraitRequest:
    """Immutable request attribution used for stale-result suppression."""

    target_identity: str
    character_id: int
    size: int
    generation: int
    token: object
    settings_identity: str | None = None
    monitor_generation: int | None = None


@dataclass(frozen=True)
class PortraitImageResult:
    """A validated worker-safe QImage attributed to one exact request."""

    request: PortraitRequest
    image: QImage
    source: str


def portrait_cache_key(request: PortraitRequest) -> str:
    """Return the canonical process and disk cache identity."""
    return (
        f"portrait:v2:{request.target_identity}:{request.settings_identity}:"
        f"{request.monitor_generation}:{request.generation}:"
        f"{request.character_id}:{request.size}"
    )


class PortraitProvider:
    """Load one validated portrait from disk, Docker HTTP, or Native files."""

    def __init__(
        self,
        target: PortraitTarget,
        *,
        cache_dir: Path,
        http_open: Callable[..., object] | None = None,
        timeout: float = _HTTP_TIMEOUT,
        max_bytes: int = MAX_PORTRAIT_BYTES,
    ) -> None:
        if timeout <= 0 or max_bytes <= 0:
            raise ValueError("Portrait bounds must be positive")
        self.target = target
        self.cache_dir = Path(cache_dir)
        self.http_open = http_open or urlopen
        self.timeout = float(timeout)
        self.max_bytes = int(max_bytes)

    def load(self, request: PortraitRequest) -> PortraitImageResult:
        self._validate_request(request)
        disk = self._load_disk(request)
        if disk is not None:
            return PortraitImageResult(request, disk, "disk")

        if self.target.image_endpoint is not None:
            image = self._load_http(request)
            source = "http"
        elif self.target.native_root is not None:
            image = self._load_native(request)
            source = "native"
        else:
            raise PortraitLoadError(
                "source_unavailable",
                "No authoritative portrait source is available for this runtime.",
            )
        self._write_disk(request, image)
        return PortraitImageResult(request, image, source)

    def _validate_request(self, request: PortraitRequest) -> None:
        if (
            request.target_identity != self.target.target_identity
            or request.settings_identity != self.target.settings_identity
            or request.monitor_generation != self.target.monitor_generation
        ):
            raise PortraitLoadError(
                "target_mismatch",
                "The portrait request does not match the current runtime target.",
            )
        if (
            isinstance(request.character_id, bool)
            or request.character_id <= 0
            or request.size not in _ALLOWED_SIZES
            or request.generation < 0
        ):
            raise PortraitLoadError(
                "invalid_request",
                "The portrait request contains unsupported values.",
            )

    def _cache_path(self, request: PortraitRequest) -> Path:
        digest = hashlib.sha256(
            portrait_cache_key(request).encode("utf-8", errors="strict")
        ).hexdigest()
        return self.cache_dir / f"{digest}.png"

    def _load_disk(self, request: PortraitRequest) -> QImage | None:
        path = self._cache_path(request)
        try:
            if not path.is_file():
                return None
            with path.open("rb") as handle:
                payload = handle.read(self.max_bytes + 1)
            if not payload or len(payload) > self.max_bytes:
                raise PortraitLoadError("disk_invalid", "Cached portrait is invalid.")
            return _decode_image(
                payload,
                expected_mime="image/png",
                expected_size=request.size,
                exact_size=True,
            )
        except (OSError, PortraitLoadError):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _write_disk(self, request: PortraitRequest, image: QImage) -> None:
        try:
            payload = _encode_png(image)
            if not payload or len(payload) > self.max_bytes:
                return
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="portrait-",
                suffix=".tmp",
                dir=self.cache_dir,
                delete=False,
            )
            temporary = Path(handle.name)
            try:
                with handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self._cache_path(request))
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            return

    def _load_http(self, request: PortraitRequest) -> QImage:
        endpoint = self.target.image_endpoint
        if endpoint is None:
            raise PortraitLoadError(
                "source_unavailable",
                "The Docker image endpoint is unavailable.",
            )
        host = endpoint.host
        authority_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        url = (
            f"http://{authority_host}:{endpoint.port}/Character/"
            f"{request.character_id}_{request.size}.jpg"
        )
        network_request = Request(
            url,
            headers={
                "Accept": "image/jpeg, image/png",
                "User-Agent": "EveJS-Launcher/Portrait",
            },
            method="GET",
        )
        try:
            with self.http_open(network_request, timeout=self.timeout) as response:
                status = getattr(response, "status", None)
                if status != 200:
                    raise PortraitLoadError(
                        "http_status",
                        "The portrait endpoint returned an unsupported status.",
                    )
                content_type = _single_header(
                    response.headers,
                    "Content-Type",
                    "content_type",
                ).split(";", 1)[0].strip().casefold()
                if content_type not in {"image/jpeg", "image/png"}:
                    raise PortraitLoadError(
                        "content_type",
                        "The portrait endpoint returned an unsupported content type.",
                    )
                raw_length = _single_header(
                    response.headers,
                    "Content-Length",
                    "content_length",
                ).strip()
                if not raw_length.isdigit():
                    raise PortraitLoadError(
                        "content_length",
                        "The portrait endpoint returned an invalid content length.",
                    )
                declared_length = int(raw_length)
                if declared_length <= 0 or declared_length > self.max_bytes:
                    raise PortraitLoadError(
                        "content_length",
                        "The portrait response exceeds the safe size contract.",
                    )
                payload = _read_exact_body(response, declared_length)
        except PortraitLoadError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
            raise PortraitLoadError(
                "network",
                "The portrait endpoint could not be read within safe bounds.",
            ) from exc
        return _decode_image(
            payload,
            expected_mime=content_type,
            expected_size=request.size,
            exact_size=True,
        )

    def _load_native(self, request: PortraitRequest) -> QImage:
        root = self.target.native_root
        if root is None:
            raise PortraitLoadError(
                "source_unavailable",
                "The Native portrait source is unavailable.",
            )
        for relative in _PORTRAIT_SEARCH_PATHS:
            candidate = Path(root) / relative.format(
                character_id=request.character_id,
                size=request.size,
            )
            try:
                if not candidate.is_file():
                    continue
                with candidate.open("rb") as handle:
                    payload = handle.read(self.max_bytes + 1)
                if not payload or len(payload) > self.max_bytes:
                    continue
                image = _decode_image(
                    payload,
                    expected_mime=None,
                    expected_size=None,
                    exact_size=False,
                )
                side = min(image.width(), image.height())
                image = image.copy(
                    (image.width() - side) // 2,
                    (image.height() - side) // 2,
                    side,
                    side,
                )
                return image.scaled(
                    request.size,
                    request.size,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            except (OSError, PortraitLoadError):
                continue
        raise PortraitLoadError(
            "not_found",
            "No valid portrait is available for this character.",
        )


def _single_header(headers: object, name: str, code: str) -> str:
    get_all = getattr(headers, "get_all", None)
    values = get_all(name) if callable(get_all) else None
    if not values or len(values) != 1 or not isinstance(values[0], str):
        raise PortraitLoadError(
            code,
            f"The portrait endpoint returned an invalid {name} header.",
        )
    return values[0]


def _read_exact_body(response: object, declared_length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = declared_length
    while remaining:
        chunk = response.read(min(_READ_CHUNK, remaining))
        if not isinstance(chunk, bytes) or not chunk:
            raise PortraitLoadError(
                "body_length",
                "The portrait response ended before its declared length.",
            )
        if len(chunk) > remaining:
            raise PortraitLoadError(
                "body_length",
                "The portrait response exceeded its declared length.",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    extra = response.read(1)
    if extra:
        raise PortraitLoadError(
            "body_length",
            "The portrait response exceeded its declared length.",
        )
    return b"".join(chunks)


def _signature_format(payload: bytes) -> str:
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    raise PortraitLoadError(
        "signature",
        "The portrait response has an unsupported image signature.",
    )


def _decode_image(
    payload: bytes,
    *,
    expected_mime: str | None,
    expected_size: int | None,
    exact_size: bool,
) -> QImage:
    signature = _signature_format(payload)
    if expected_mime is not None:
        expected_format = "jpeg" if expected_mime == "image/jpeg" else "png"
        if signature != expected_format:
            raise PortraitLoadError(
                "signature",
                "The portrait signature does not match its declared content type.",
            )

    data = QByteArray(payload)
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise PortraitLoadError("decode", "The portrait image could not be decoded.")
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    image_format = bytes(reader.format()).decode("ascii", errors="ignore").casefold()
    if image_format == "jpg":
        image_format = "jpeg"
    if image_format != signature:
        raise PortraitLoadError("decode", "The portrait image format is invalid.")
    if not reader.canRead():
        raise PortraitLoadError("decode", "The portrait image could not be decoded.")
    dimensions = reader.size()
    width, height = dimensions.width(), dimensions.height()
    if width <= 0 or height <= 0:
        raise PortraitLoadError("decode", "The portrait image could not be decoded.")
    if (
        width > _MAX_DIMENSION
        or height > _MAX_DIMENSION
        or width * height > _MAX_PIXELS
        or (exact_size and width != height)
        or (
            exact_size
            and expected_size is not None
            and (width != expected_size or height != expected_size)
        )
    ):
        raise PortraitLoadError(
            "dimensions",
            "The portrait image dimensions are outside the safe contract.",
        )
    image = reader.read()
    if image.isNull():
        raise PortraitLoadError("decode", "The portrait image could not be decoded.")
    if image.width() != width or image.height() != height:
        raise PortraitLoadError(
            "dimensions",
            "The decoded portrait dimensions changed unexpectedly.",
        )
    return image


def _encode_png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        return b""
    return bytes(data)
