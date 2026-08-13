"""Curated local OST discovery without redistributing the recordings."""
from __future__ import annotations

from pathlib import Path
import random

from src.audio.backends import MusicBackend
from src.audio.controller import AudioController
from src.audio.music_catalog import (
    CURATED_OST_TRACKS,
    curated_music_title,
    discover_curated_music_tracks,
    downloads_search_roots,
)


class _MusicRecorder(MusicBackend):
    available = True

    def __init__(self) -> None:
        self.sources: list[Path] = []
        self.current: Path | None = None
        self.finished = None

    def set_source(self, path: Path) -> bool:
        self.current = path
        self.sources.append(path)
        return True

    def set_track_finished_callback(self, callback) -> bool:
        self.finished = callback
        return True

    def play(self) -> bool:
        return self.current is not None

    def stop(self) -> None:
        pass

    def finish(self) -> None:
        assert self.finished is not None
        self.finished()


def _write_catalog(root: Path) -> tuple[Path, ...]:
    root.mkdir(parents=True)
    paths = tuple(root / track.filename for track in CURATED_OST_TRACKS)
    for path in paths:
        path.write_bytes(b"local-only test recording")
    return paths


def test_curated_catalog_discovers_only_exact_known_downloads(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    expected = _write_catalog(downloads)
    (downloads / "unrelated.mp3").write_bytes(b"not part of the catalog")

    assert discover_curated_music_tracks([downloads]) == expected
    assert [curated_music_title(path) for path in expected] == [
        "Primordial Star Clouds",
        "Red Glowing Dust (Jukebox)",
    ]
    assert curated_music_title(downloads / "unrelated.mp3") is None


def test_curated_catalog_is_case_insensitive_and_missing_files_fail_soft(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    track = CURATED_OST_TRACKS[0]
    local = downloads / track.filename.upper()
    local.write_bytes(b"local-only test recording")

    assert discover_curated_music_tracks([tmp_path / "missing", downloads]) == (
        local.resolve(),
    )


def test_download_roots_prefer_redirected_known_folder_with_home_fallback(
    tmp_path: Path,
) -> None:
    redirected = tmp_path / "Redirected Downloads"
    home = tmp_path / "profile"

    assert downloads_search_roots(
        home=home,
        known_folder_resolver=lambda: redirected,
    ) == (redirected, home / "Downloads")
    assert downloads_search_roots(
        home=home,
        known_folder_resolver=lambda: None,
    ) == (home / "Downloads",)


def test_playlist_treats_discovered_tracks_as_curated_and_deduplicates_legacy(
    qapp,
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    curated = _write_catalog(downloads)
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "launcher_original.wav").write_bytes(b"original fixture")
    backend = _MusicRecorder()
    controller = AudioController(
        {"audio_music_library": [str(curated[0])]},
        music_factory=lambda _parent: backend,
        music_root=bundled,
        curated_music_roots=[downloads],
        rng=random.Random(4),
    )

    assert controller.start_music() is True
    for _ in range(2):
        backend.finish()

    assert {path.name for path in backend.sources} == {
        "launcher_original.wav",
        *(track.filename for track in CURATED_OST_TRACKS),
    }
    assert len(backend.sources) == 3


def test_catalog_metadata_and_package_never_embed_personal_paths_or_mp3s() -> None:
    project = Path(__file__).resolve().parents[1]
    module_text = (project / "src/audio/music_catalog.py").read_text(
        encoding="utf-8"
    )
    build_text = (project / "build.spec").read_text(encoding="utf-8")

    assert all(not Path(track.filename).is_absolute() for track in CURATED_OST_TRACKS)
    assert "C:\\Users\\" not in module_text
    current_profile_fragment = Path.home().name
    if len(current_profile_fragment) >= 5:
        assert current_profile_fragment.casefold() not in module_text.casefold()
    assert not list((project / "assets/audio").rglob("*.mp3"))
    assert "assets/audio/music/*.mp3" not in build_text
    assert not (project / "assets/audio/ui").exists()
    assert "assets/audio/ui" not in build_text
    assert "src.audio.ui_sounds" not in build_text
