"""Curated, locally-discovered music for the launcher playlist.

The launcher intentionally does not redistribute the optional EVE Online OST
recordings.  This module contains only catalog metadata and resolves matching
files from the current Windows user's Downloads known folder at runtime.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import sys


@dataclass(frozen=True, slots=True)
class CuratedMusicTrack:
    """One launcher-recognized recording that may already exist locally."""

    filename: str
    title: str


CURATED_OST_TRACKS: tuple[CuratedMusicTrack, ...] = (
    CuratedMusicTrack(
        filename="Eve Online - 12 - Primordial Star Clouds.mp3",
        title="Primordial Star Clouds",
    ),
    CuratedMusicTrack(
        filename=(
            "Eve Online OST - Red Glowing Dust (Jukebox) - ambient music.mp3"
        ),
        title="Red Glowing Dust (Jukebox)",
    ),
)

# Windows KNOWNFOLDERID for Downloads.  It identifies the redirected location
# configured by Explorer instead of assuming ``<home>/Downloads``.
_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


class _Guid(ctypes.Structure):
    _fields_ = (
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    )

    @classmethod
    def parse(cls, value: str) -> "_Guid":
        compact = value.strip("{}").replace("-", "")
        raw = bytes.fromhex(compact)
        return cls(
            int.from_bytes(raw[0:4], "big"),
            int.from_bytes(raw[4:6], "big"),
            int.from_bytes(raw[6:8], "big"),
            (ctypes.c_ubyte * 8)(*raw[8:]),
        )


def windows_downloads_known_folder() -> Path | None:
    """Resolve Explorer's Downloads known folder, failing softly everywhere."""
    if sys.platform != "win32":
        return None
    result = ctypes.c_wchar_p()
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        folder_id = _Guid.parse(_FOLDERID_DOWNLOADS)
        status = shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(result),
        )
        if status != 0 or not result.value:
            return None
        return Path(result.value)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    finally:
        if result.value:
            try:
                ole32.CoTaskMemFree(result)
            except (AttributeError, OSError, UnboundLocalError):
                pass


def downloads_search_roots(
    *,
    home: str | os.PathLike[str] | None = None,
    known_folder_resolver: Callable[[], Path | None] = (
        windows_downloads_known_folder
    ),
) -> tuple[Path, ...]:
    """Return redirected Downloads first, then a portable home fallback."""
    candidates: list[Path] = []
    try:
        known_folder = known_folder_resolver()
    except (OSError, TypeError, ValueError):
        known_folder = None
    if known_folder is not None:
        candidates.append(Path(known_folder))

    try:
        home_path = Path(home).expanduser() if home is not None else Path.home()
        candidates.append(home_path / "Downloads")
    except (OSError, RuntimeError, TypeError, ValueError):
        pass

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = os.path.normcase(str(candidate)).casefold()
        if identity not in seen:
            seen.add(identity)
            roots.append(candidate)
    return tuple(roots)


def discover_curated_music_tracks(
    search_roots: Iterable[str | os.PathLike[str]] | None = None,
) -> tuple[Path, ...]:
    """Find recognized OST files without exposing arbitrary local audio.

    ``search_roots`` is an injection seam for tests and portable deployments.
    Normal launcher startup leaves it as ``None`` and uses the current user's
    Downloads known folder.  Only exact catalog filenames are admitted.
    """
    roots = (
        tuple(Path(value) for value in search_roots)
        if search_roots is not None
        else downloads_search_roots()
    )
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            entries = {
                entry.name.casefold(): entry
                for entry in root.iterdir()
                if entry.is_file()
            }
        except (OSError, TypeError, ValueError):
            continue
        for track in CURATED_OST_TRACKS:
            candidate = entries.get(track.filename.casefold())
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve()
            except (OSError, RuntimeError):
                continue
            identity = os.path.normcase(str(resolved)).casefold()
            if identity not in seen:
                seen.add(identity)
                found.append(resolved)
    return tuple(found)


def curated_music_title(path: str | os.PathLike[str]) -> str | None:
    """Return curated display metadata for a recognized filename."""
    try:
        filename = Path(path).name.casefold()
    except (OSError, TypeError, ValueError):
        return None
    return next(
        (
            track.title
            for track in CURATED_OST_TRACKS
            if track.filename.casefold() == filename
        ),
        None,
    )
