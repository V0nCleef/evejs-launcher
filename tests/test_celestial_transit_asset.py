"""Release contract for the approved original Celestial Transit track."""
from __future__ import annotations

import hashlib
from pathlib import Path
import wave


ROOT = Path(__file__).resolve().parent.parent
TRACK = ROOT / "assets" / "audio" / "music" / "celestial_transit.wav"
EXPECTED_SHA256 = (
    "451978321c360315010e8871945cdb99a92e05bee2d81148deb0600e64d66fc9"
)


def test_approved_celestial_transit_asset_is_exact_and_release_packaged() -> None:
    assert hashlib.sha256(TRACK.read_bytes()).hexdigest() == EXPECTED_SHA256
    with wave.open(str(TRACK), "rb") as stream:
        assert stream.getnchannels() == 2
        assert stream.getsampwidth() == 2
        assert stream.getframerate() == 44_100
        assert stream.getnframes() == 2_419_200

    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert "assets/audio/music/*.wav" in spec
    assert "assets/audio/music/*.mp3" not in spec


def test_celestial_transit_has_reproducible_source_and_provenance() -> None:
    assert (ROOT / "scripts" / "generate_celestial_transit.py").is_file()
    assert (ROOT / "scripts" / "celestial_transit_synthesis.py").is_file()
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    ).casefold()
    assert "celestial transit" in notices
    assert EXPECTED_SHA256 in notices
    assert "personal music paths" in notices
    assert "never copied" in notices
