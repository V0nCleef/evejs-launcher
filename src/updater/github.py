"""GitHub Releases API client — stdlib only, no external dependencies.

Uses :mod:`urllib.request` to query the GitHub REST API for the latest
release of the EveJS Launcher and to download update assets.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

# ── Configurable repo ────────────────────────────────────────────────────────
GITHUB_REPO: str = "V0nCleef/evejs-launcher"

# ── Internal constants ───────────────────────────────────────────────────────
_API_BASE: str = "https://api.github.com/repos"
_REQUEST_TIMEOUT: int = 10  # seconds according to spec

_logger = logging.getLogger(__name__)


def _read_version() -> str:
    """Read the current app version from the VERSION file at the repo root."""
    version_path = Path(__file__).resolve().parent.parent.parent / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return "0.0.0"


def _build_user_agent() -> str:
    """Compose a custom User-Agent header."""
    return f"EveJS-Launcher-V2/{_read_version()}"


def get_latest_release(repo: str | None = None) -> dict[str, Any] | None:
    """Return metadata for the latest GitHub release, or *None* on failure.

    Parameters
    ----------
    repo:
        ``owner/name`` string.  Defaults to :data:`GITHUB_REPO`.

    Returns
    -------
    dict or None
        Keys: ``tag_name``, ``name``, ``body``, ``html_url``,
        ``assets`` (list of ``{name, browser_download_url, size}``),
        ``published_at``.
    """
    repo = repo or GITHUB_REPO
    url = f"{_API_BASE}/{repo}/releases/latest"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _build_user_agent(),
            "Accept": "application/vnd.github+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _logger.warning("GitHub API returned HTTP %s for %s", exc.code, url)
        return None
    except urllib.error.URLError as exc:
        _logger.warning("GitHub API URL error for %s: %s", url, exc.reason)
        return None
    except (socket.timeout, TimeoutError) as exc:
        _logger.warning("GitHub API request timed out for %s: %s", url, exc)
        return None
    except json.JSONDecodeError as exc:
        _logger.warning("GitHub API returned invalid JSON for %s: %s", url, exc)
        return None
    except OSError as exc:
        _logger.warning("GitHub API OS error for %s: %s", url, exc)
        return None

    # Normalise the assets list to a predictable shape.
    assets: list[dict[str, Any]] = []
    for asset in raw.get("assets", []):
        assets.append(
            {
                "name": asset.get("name", ""),
                "browser_download_url": asset.get("browser_download_url", ""),
                "size": asset.get("size", 0),
            }
        )

    return {
        "tag_name": raw.get("tag_name", ""),
        "name": raw.get("name", ""),
        "body": raw.get("body", ""),
        "html_url": raw.get("html_url", ""),
        "assets": assets,
        "published_at": raw.get("published_at", ""),
    }


def download_asset(
    url: str,
    dest_path: str | Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> bool:
    """Download a release asset from *url* to *dest_path*.

    Parameters
    ----------
    url:
        Direct download URL of the asset (``browser_download_url``).
    dest_path:
        Local filesystem path where the file will be saved.
    progress_callback:
        Called as ``progress_callback(bytes_downloaded, total_bytes)``
        after each chunk.  May be *None*.

    Returns
    -------
    bool
        *True* when the download completes successfully, *False* on any error.
    """
    dest_path = Path(dest_path)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _build_user_agent(),
            "Accept": "application/octet-stream",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 64 * 1024  # 64 KiB

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total)

            # Final callback so the caller sees 100 % even for empty files.
            if progress_callback is not None:
                progress_callback(downloaded, total)

        return True
    except urllib.error.HTTPError as exc:
        _logger.warning("Asset download HTTP %s for %s", exc.code, url)
        return False
    except urllib.error.URLError as exc:
        _logger.warning("Asset download URL error for %s: %s", url, exc.reason)
        return False
    except (socket.timeout, TimeoutError) as exc:
        _logger.warning("Asset download timed out for %s: %s", url, exc)
        return False
    except OSError as exc:
        _logger.warning("Asset download OS error for %s: %s", url, exc)
        return False
