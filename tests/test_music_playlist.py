"""Focused tests for shuffled local background-music playback."""
from __future__ import annotations

from pathlib import Path
import random

from src.audio.backends import MusicBackend
from src.audio.controller import AudioController


class _PlaylistMusic(MusicBackend):
    available = True

    def __init__(
        self,
        *,
        rejected: set[str] | None = None,
        rejected_plays: set[str] | None = None,
        fail_synchronously_once: bool = False,
    ) -> None:
        self.rejected = rejected or set()
        self.rejected_plays = rejected_plays or set()
        self.fail_synchronously_once = fail_synchronously_once
        self.sources: list[Path] = []
        self.current_source: Path | None = None
        self.play_count = 0
        self.stop_count = 0
        self.finished_callback = None
        self.failed_callback = None

    def set_source(self, path: Path) -> bool:
        self.sources.append(path)
        self.current_source = path
        return path.name not in self.rejected

    def set_track_finished_callback(self, callback) -> bool:
        self.finished_callback = callback
        return True

    def set_track_failed_callback(self, callback) -> bool:
        self.failed_callback = callback
        return True

    def play(self) -> bool:
        self.play_count += 1
        if self.fail_synchronously_once:
            self.fail_synchronously_once = False
            assert self.failed_callback is not None
            self.failed_callback()
            return True
        return bool(
            self.current_source
            and self.current_source.name not in self.rejected_plays
        )

    def stop(self) -> None:
        self.stop_count += 1

    def finish(self) -> None:
        assert self.finished_callback is not None
        self.finished_callback()

    def fail(self) -> None:
        assert self.failed_callback is not None
        self.failed_callback()


class _AsyncPlaylistMusic(_PlaylistMusic):
    def __init__(self) -> None:
        super().__init__()
        self.state_callback = None

    def set_playback_state_callback(self, callback) -> bool:
        self.state_callback = callback
        callback(False)
        return True

    def report_state(self, active: bool) -> None:
        assert self.state_callback is not None
        self.state_callback(active)


def _track(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"local audio fixture")
    return path


def test_playlist_combines_bundled_and_external_tracks_without_duplicates(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "bundled"
    bundled = _track(music_root, "bundled.wav")
    external = _track(tmp_path, "external.mp3")
    unsupported = _track(tmp_path, "notes.txt")
    backend = _PlaylistMusic()
    controller = AudioController(
        {
            "audio_music_library": [
                str(external),
                str(bundled),
                str(unsupported),
                str(tmp_path / "missing.ogg"),
            ]
        },
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(11),
    )

    assert controller.start_music() is True
    backend.finish()

    assert {path.name for path in backend.sources} == {
        "bundled.wav",
        "external.mp3",
    }
    assert len(backend.sources) == 2


def test_track_names_are_truthful_and_readable(qapp, tmp_path: Path) -> None:
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
    )
    celestial = _track(tmp_path, "v2_celestial_transit_melodic.mp3")
    numbered_eve = _track(
        tmp_path,
        "Eve Online - 12 - Primordial Star Clouds.mp3",
    )
    jukebox_eve = _track(
        tmp_path,
        "Eve Online OST - Red Glowing Dust (Jukebox) - ambient music.mp3",
    )

    assert controller.start_music(celestial) is True
    assert controller.music_track_name == "v2 celestial transit melodic"
    assert controller.start_music(numbered_eve) is True
    assert controller.music_track_name == "Primordial Star Clouds"
    assert controller.start_music(jukebox_eve) is True
    assert controller.music_track_name == "Red Glowing Dust (Jukebox)"


def test_shuffle_bag_plays_each_track_once_and_never_repeats_at_boundary(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("alpha.wav", "bravo.wav", "charlie.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(7),
    )

    assert controller.start_music() is True
    for _ in range(5):
        backend.finish()

    names = [path.name for path in backend.sources]
    assert len(set(names[:3])) == 3
    assert len(set(names[3:6])) == 3
    assert names[2] != names[3]


def test_playlist_order_is_deterministic_with_injected_rng(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("one.wav", "two.wav", "three.wav", "four.wav"):
        _track(music_root, name)

    observed: list[list[str]] = []
    for _ in range(2):
        backend = _PlaylistMusic()
        controller = AudioController(
            {},
            music_factory=lambda _parent, item=backend: item,
            music_root=music_root,
            rng=random.Random(91),
        )
        assert controller.start_music() is True
        for _index in range(3):
            backend.finish()
        observed.append([path.name for path in backend.sources])
        controller.shutdown()

    assert observed[0] == observed[1]


def test_only_explicit_track_finished_callback_advances_playlist(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    _track(music_root, "first.wav")
    _track(music_root, "second.wav")
    backend = _AsyncPlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(1),
    )

    assert controller.start_music() is True
    selected = list(backend.sources)
    backend.report_state(True)
    backend.report_state(False)  # pause/stop/error-style state is not EndOfMedia
    assert backend.sources == selected

    backend.finish()
    assert len(backend.sources) == 2
    assert backend.sources[-1] != backend.sources[0]


def test_invalid_backend_sources_are_skipped_in_one_bounded_pass(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("bad-a.wav", "bad-b.wav", "good.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic(rejected={"bad-a.wav", "bad-b.wav"})
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(3),
    )

    assert controller.start_music() is True
    assert backend.sources[-1].name == "good.wav"
    assert len(backend.sources) <= 3

    rejecting = _PlaylistMusic(
        rejected={"bad-a.wav", "bad-b.wav", "good.wav"}
    )
    failed = AudioController(
        {},
        music_factory=lambda _parent: rejecting,
        music_root=music_root,
        rng=random.Random(3),
    )
    assert failed.start_music() is False
    assert len(rejecting.sources) == 3
    assert failed.music_active is False


def test_backend_play_rejections_retire_bad_tracks_and_try_each_only_once(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("bad-a.wav", "bad-b.wav", "good.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic(rejected_plays={"bad-a.wav", "bad-b.wav"})
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(3),
    )

    assert controller.start_music() is True
    assert backend.current_source is not None
    assert backend.current_source.name == "good.wav"
    assert backend.play_count <= 3
    assert len(backend.sources) <= 3

    # Retired decoder/play failures remain out of later shuffle bags. The one
    # valid track can loop naturally without retrying the corrupt entries.
    for _ in range(4):
        backend.finish()
    assert [path.name for path in backend.sources].count("bad-a.wav") <= 1
    assert [path.name for path in backend.sources].count("bad-b.wav") <= 1
    assert backend.current_source.name == "good.wav"


def test_decoder_failure_retires_current_track_and_advances_once(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    _track(music_root, "first.wav")
    _track(music_root, "second.wav")
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(1),
    )

    assert controller.start_music() is True
    failed = backend.current_source
    backend.fail()

    assert failed is not None
    assert backend.current_source is not None
    assert backend.current_source != failed
    assert len(backend.sources) == 2
    for _ in range(3):
        backend.finish()
    assert backend.sources.count(failed) == 1


def test_synchronous_decoder_failure_is_deferred_outside_play_call(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    _track(music_root, "first.wav")
    _track(music_root, "second.wav")
    backend = _PlaylistMusic(fail_synchronously_once=True)
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(1),
    )

    assert controller.start_music() is True
    first = backend.current_source
    assert len(backend.sources) == 1

    qapp.processEvents()

    assert first is not None
    assert len(backend.sources) == 2
    assert backend.current_source != first


def test_decoder_failure_never_advances_after_explicit_stop_mute_or_disable(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    _track(music_root, "first.wav")
    _track(music_root, "second.wav")
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(1),
    )

    assert controller.start_music() is True
    controller.stop_music()
    selected = len(backend.sources)
    backend.fail()
    assert len(backend.sources) == selected

    assert controller.start_music() is True
    controller.set_master_muted(True)
    selected = len(backend.sources)
    backend.fail()
    assert len(backend.sources) == selected

    controller.set_master_muted(False)
    controller.apply_settings({"audio_music_enabled": False})
    selected = len(backend.sources)
    backend.fail()
    assert len(backend.sources) == selected


def test_library_update_mute_disable_and_shutdown_preserve_lifecycle(
    qapp,
    tmp_path: Path,
) -> None:
    empty_bundled_root = tmp_path / "bundled"
    empty_bundled_root.mkdir()
    first = _track(tmp_path, "first.mp3")
    second = _track(tmp_path, "second.mp3")
    backend = _PlaylistMusic()
    controller = AudioController(
        {"audio_music_library": [str(first)]},
        music_factory=lambda _parent: backend,
        music_root=empty_bundled_root,
    )
    states: list[tuple[bool, str]] = []
    controller.music_playback_changed.connect(
        lambda active, name: states.append((active, name))
    )

    assert controller.start_music() is True
    assert controller.music_track_name == "first"
    assert states[-1] == (True, "first")

    controller.apply_settings({"audio_music_library": [str(second)]})
    assert controller.music_track_name == "second"
    assert controller.music_active is True
    assert states[-1] == (True, "second")

    selected_count = len(backend.sources)
    controller.set_master_muted(True)
    backend.finish()
    assert len(backend.sources) == selected_count
    controller.set_master_muted(False)
    assert len(backend.sources) == selected_count
    assert controller.music_active is True

    controller.apply_settings(
        {
            "audio_music_enabled": False,
            "audio_music_library": [str(second)],
        }
    )
    backend.finish()
    assert len(backend.sources) == selected_count
    controller.apply_settings(
        {
            "audio_music_enabled": True,
            "audio_music_library": [str(second)],
        }
    )
    assert len(backend.sources) == selected_count

    controller.shutdown()
    backend.finish()
    assert len(backend.sources) == selected_count
    assert controller.music_active is False


def test_startup_uses_a_fresh_shuffled_bag_instead_of_catalog_order(
    qapp,
    tmp_path: Path,
) -> None:
    class ReverseRandom(random.Random):
        def shuffle(self, values) -> None:
            values.reverse()

    music_root = tmp_path / "music"
    for name in ("alpha.wav", "bravo.wav", "charlie.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=ReverseRandom(),
    )

    assert controller.start_music() is True
    assert backend.current_source is not None
    assert backend.current_source.name == "charlie.wav"


def test_manual_previous_and_next_follow_history_then_forward_path(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("alpha.wav", "bravo.wav", "charlie.wav", "delta.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(19),
    )

    assert controller.start_music() is True
    first = backend.current_source
    assert controller.next_music() is True
    second = backend.current_source
    assert controller.next_music() is True
    third = backend.current_source
    assert first is not None and second is not None and third is not None
    assert len({first, second, third}) == 3

    assert controller.previous_music() is True
    assert backend.current_source == second
    assert controller.previous_music() is True
    assert backend.current_source == first
    source_count = len(backend.sources)
    assert controller.previous_music() is False
    assert len(backend.sources) == source_count
    assert backend.current_source == first

    assert controller.next_music() is True
    assert backend.current_source == second
    assert controller.next_music() is True
    assert backend.current_source == third


def test_natural_advance_is_recorded_for_previous_and_forward_navigation(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("one.wav", "two.wav", "three.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(5),
    )

    assert controller.start_music() is True
    first = backend.current_source
    backend.finish()
    second = backend.current_source
    assert first is not None and second is not None and first != second

    assert controller.previous_music() is True
    assert backend.current_source == first
    assert controller.next_music() is True
    assert backend.current_source == second


def test_navigation_respects_stop_mute_disable_and_failed_track_retirement(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    for name in ("one.wav", "two.wav", "three.wav"):
        _track(music_root, name)
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(5),
    )

    assert controller.start_music() is True
    first = backend.current_source
    assert controller.next_music() is True
    failed = backend.current_source
    assert first is not None and failed is not None and first != failed
    backend.fail()
    recovered = backend.current_source
    assert recovered is not None and recovered not in {first, failed}

    # The broken entry is not put into navigable history.
    assert controller.previous_music() is True
    assert backend.current_source == first

    controller.set_music_muted(True)
    selected_count = len(backend.sources)
    assert controller.next_music() is False
    assert controller.previous_music() is False
    assert len(backend.sources) == selected_count

    controller.set_music_muted(False)
    controller.apply_settings({"audio_music_enabled": False})
    selected_count = len(backend.sources)
    assert controller.next_music() is False
    assert controller.previous_music() is False
    assert len(backend.sources) == selected_count

    controller.apply_settings({"audio_music_enabled": True})
    controller.stop_music()
    selected_count = len(backend.sources)
    assert controller.next_music() is False
    assert controller.previous_music() is False
    assert len(backend.sources) == selected_count


def test_stale_previous_entry_fails_softly_and_restores_current_track(
    qapp,
    tmp_path: Path,
) -> None:
    music_root = tmp_path / "music"
    _track(music_root, "first.wav")
    _track(music_root, "second.wav")
    backend = _PlaylistMusic()
    controller = AudioController(
        {},
        music_factory=lambda _parent: backend,
        music_root=music_root,
        rng=random.Random(1),
    )

    assert controller.start_music() is True
    previous = backend.current_source
    assert controller.next_music() is True
    current = backend.current_source
    assert previous is not None and current is not None and previous != current
    previous.unlink()

    assert controller.previous_music() is False
    assert backend.current_source == current
    assert controller.music_active is True
