"""Source and frozen-build resource discovery for launcher audio."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .events import VoiceLine

MUSIC_EXTENSIONS = frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})
VOICE_MANIFEST_NAME = "manifest.json"


def audio_assets_root(module_file: str | Path = __file__) -> Path:
    """Return ``assets/audio`` beside the source or frozen ``src`` package."""
    return Path(module_file).resolve().parent.parent.parent / "assets" / "audio"


def bundled_music_tracks(root: str | Path | None = None) -> tuple[Path, ...]:
    """Return deterministically ordered, supported music files if any exist."""
    music_root = Path(root) if root is not None else audio_assets_root() / "music"
    if not music_root.is_dir():
        return ()
    return tuple(
        sorted(
            (
                path
                for path in music_root.iterdir()
                if path.is_file() and path.suffix.casefold() in MUSIC_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
    )


def voice_assets_root(root: str | Path | None = None) -> Path:
    """Return the fixed LYRA catalog directory in source or frozen layouts."""
    return (
        Path(root)
        if root is not None
        else audio_assets_root() / "voice" / "lyra"
    )


def bundled_voice_clips(
    root: str | Path | None = None,
) -> dict[VoiceLine, Path]:
    """Return only approved catalog WAVs that physically exist.

    Filenames come exclusively from :class:`VoiceLine`; directory contents
    cannot invent runtime utterances or smuggle private names into captions.
    """
    catalog_root = voice_assets_root(root)
    return {
        line: path
        for line in VoiceLine
        if (path := catalog_root / line.filename).is_file()
    }


def missing_voice_lines(
    root: str | Path | None = None,
) -> tuple[VoiceLine, ...]:
    """Return approved lines whose prerecorded WAV is not yet bundled."""
    clips = bundled_voice_clips(root)
    return tuple(line for line in VoiceLine if line not in clips)


def _voice_manifest(root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            (root / VOICE_MANIFEST_NAME).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def voice_catalog_ready(root: str | Path | None = None) -> bool:
    """Return true only for a complete, provenance-matched fixed catalog."""
    catalog_root = voice_assets_root(root)
    clips = bundled_voice_clips(catalog_root)
    if set(clips) != set(VoiceLine):
        return False

    manifest = _voice_manifest(catalog_root)
    entries = manifest.get("clips") if manifest is not None else None
    if not isinstance(entries, dict):
        return False
    if set(entries) != {line.value for line in VoiceLine}:
        return False

    for line, path in clips.items():
        entry = entries.get(line.value)
        if not isinstance(entry, dict):
            return False
        if (
            entry.get("text") != line.text
            or entry.get("filename") != line.filename
        ):
            return False
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str):
            return False
        try:
            if _sha256(path) != expected_hash.casefold():
                return False
        except OSError:
            return False
    return True
