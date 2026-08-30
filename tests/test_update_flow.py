"""Focused behavioral tests for the transparent launcher update flow."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

import pytest

from src.core import platform


def test_install_worker_relays_download_progress_and_staged_handoff(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The UI worker must expose download bytes and the post-download handoff."""
    from src.updater.installer import UpdateInstallWorker

    current_exe = tmp_path / "EveJS-Launcher-V1.exe"
    phases: list[tuple[str, str]] = []
    progress: list[tuple[int, int]] = []
    completed: list[tuple[bool, str]] = []

    def fake_run_updater(
        download_url: str,
        exe_path: Path,
        *,
        progress_callback=None,
        status_callback=None,
    ) -> bool:
        assert download_url == "https://example.invalid/EveJS-Launcher-V1.zip"
        assert Path(exe_path) == current_exe
        assert progress_callback is not None
        assert status_callback is not None
        status_callback("download", "Downloading update…")
        progress_callback(1, 4)
        progress_callback(4, 4)
        status_callback("install", "Starting the updater…")
        return True

    monkeypatch.setattr(platform, "run_updater", fake_run_updater)
    worker = UpdateInstallWorker(
        "https://example.invalid/EveJS-Launcher-V1.zip",
        current_exe,
    )
    worker.stage_changed.connect(lambda stage, detail: phases.append((stage, detail)))
    worker.download_progress.connect(lambda done, total: progress.append((done, total)))
    worker.completed.connect(lambda success, error: completed.append((success, error)))

    worker.run()

    assert phases == [
        ("download", "Downloading update…"),
        ("install", "Starting the updater…"),
    ]
    assert progress == [(1, 4), (4, 4)]
    assert completed == [(True, "")]


def test_legacy_update_wrapper_forwards_its_progress_callback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep the public helper useful for non-UI callers while the UI uses a worker."""
    from src.updater import installer

    seen: list[tuple[int, int]] = []

    def fake_run_updater(_url, _exe, *, progress_callback=None, status_callback=None):  # type: ignore[no-untyped-def]
        assert status_callback is None
        assert progress_callback is not None
        progress_callback(7, 10)
        return True

    monkeypatch.setattr(platform, "run_updater", fake_run_updater)

    assert installer.download_and_install(
        "https://example.invalid/update.zip",
        tmp_path / "EveJS-Launcher-V1.exe",
        progress_callback=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(7, 10)]


def test_progress_dialog_turns_live_download_bytes_into_clear_status(
    qapp,
) -> None:
    """The visible updater should make both byte progress and phases explicit."""
    from src.updater.progress_dialog import UpdateProgressDialog

    dialog = UpdateProgressDialog("v1.0.33")
    try:
        dialog.set_download_progress(2 * 1024 * 1024, 4 * 1024 * 1024)

        assert dialog.status_label.text() == "Downloading update"
        assert dialog.detail_label.text() == "2.0 MB of 4.0 MB"
        assert dialog.progress_bar.minimum() == 0
        assert dialog.progress_bar.maximum() == 100
        assert dialog.progress_bar.value() == 50

        dialog.set_stage("prepare", "Extracting the new launcher…")

        assert dialog.status_label.text() == "Preparing update"
        assert dialog.detail_label.text() == "Extracting the new launcher…"
        assert dialog.progress_bar.minimum() == 0
        assert dialog.progress_bar.maximum() == 0
    finally:
        dialog.allow_close()
        dialog.close()


def test_progress_dialog_reuses_the_launchers_space_art(qapp) -> None:
    """The update window should feel like part of the launcher, not a system popup."""
    from src.updater.progress_dialog import UpdateProgressDialog

    dialog = UpdateProgressDialog("v1.0.33")
    try:
        assert dialog.hero_banner.pixmap() is not None
        assert not dialog.hero_banner.pixmap().isNull()
    finally:
        dialog.allow_close()
        dialog.close()


def test_platform_updater_stages_release_and_launches_the_new_build_as_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The old launcher must hand off to the staged new build, not go dark."""
    from src.core import platform_win
    from src.updater import github

    exe_name = "EveJS-Launcher-V1.exe"
    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    current_exe = install_dir / exe_name
    current_exe.write_bytes(b"old build")

    release_archive = tmp_path / "EveJS-Launcher-V1.zip"
    with zipfile.ZipFile(release_archive, "w") as archive:
        archive.writestr(f"EveJS-Launcher-V1/{exe_name}", b"new build")
        archive.writestr("EveJS-Launcher-V1/_internal/runtime.bin", b"runtime")

    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    progress: list[tuple[int, int]] = []
    phases: list[tuple[str, str]] = []
    launched: list[tuple[list[str], dict]] = []

    def fake_download(
        _url: str,
        destination: str | Path,
        progress_callback=None,
    ) -> bool:
        if progress_callback is not None:
            progress_callback(1, 2)
        shutil.copyfile(release_archive, destination)
        if progress_callback is not None:
            progress_callback(2, 2)
        return True

    class FakeProcess:
        def poll(self):  # type: ignore[no-untyped-def]
            return None

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        launched.append((list(args), kwargs))
        return FakeProcess()

    monkeypatch.setattr(github, "download_asset", fake_download)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix: str(staging_root))
    monkeypatch.setattr(platform_win.subprocess, "Popen", fake_popen)

    success = platform_win.run_updater(
        "https://example.invalid/EveJS-Launcher-V1.zip",
        current_exe,
        progress_callback=lambda done, total: progress.append((done, total)),
        status_callback=lambda stage, detail: phases.append((stage, detail)),
    )

    staged_dir = staging_root / "staged" / "EveJS-Launcher-V1"
    assert success is True
    assert progress == [(1, 2), (2, 2)]
    assert phases[0] == ("download", "Downloading update…")
    assert ("prepare", "Unpacking update package…") in phases
    assert phases[-1] == ("install", "Starting the updater…")
    assert launched[0][0][0] == str(staged_dir / exe_name)
    assert "--apply-update" in launched[0][0]
    assert str(install_dir) in launched[0][0]
    assert str(staged_dir) in launched[0][0]


def test_v1047_to_v1048_handoff_uses_new_agent_and_preserves_root_siblings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the release boundary: v1.0.48 owns its swap when started by v1.0.47."""
    from src.core import platform_win
    from src.updater import github
    from src.updater.handoff import apply_staged_update, parse_update_handoff_args

    exe_name = "EveJS-Launcher-V1.exe"
    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    current_exe = install_dir / exe_name
    current_exe.write_bytes(b"v1.0.47 launcher")
    (install_dir / "_internal" / "runtime.bin").write_bytes(b"v1.0.47 runtime")

    evejs_sentinel = install_dir / "evejs" / "server" / "data" / "game-store.db"
    evejs_sentinel.parent.mkdir(parents=True)
    evejs_sentinel.write_bytes(b"user-owned EveJS data")
    neighboring_file = install_dir / "keep-me.txt"
    neighboring_file.write_text("user-owned sibling", encoding="utf-8")

    release_archive = tmp_path / "EveJS-Launcher-V1.zip"
    with zipfile.ZipFile(release_archive, "w") as archive:
        archive.writestr(f"EveJS-Launcher-V1/{exe_name}", b"v1.0.48 launcher")
        archive.writestr(
            "EveJS-Launcher-V1/_internal/runtime.bin",
            b"v1.0.48 runtime",
        )

    staging_root = tmp_path / "staging"
    staging_root.mkdir()
    launched: list[list[str]] = []

    def fake_download(
        _url: str,
        destination: str | Path,
        progress_callback=None,
    ) -> bool:
        shutil.copyfile(release_archive, destination)
        return True

    class FakeProcess:
        def poll(self):  # type: ignore[no-untyped-def]
            return None

    def fake_popen(args, **_kwargs):  # type: ignore[no-untyped-def]
        launched.append(list(args))
        return FakeProcess()

    monkeypatch.setattr(github, "download_asset", fake_download)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix: str(staging_root))
    monkeypatch.setattr(platform_win.subprocess, "Popen", fake_popen)

    assert platform_win.run_updater(
        "https://example.invalid/EveJS-Launcher-V1.zip",
        current_exe,
    ) is True
    assert len(launched) == 1
    staged_exe = Path(launched[0][0])
    assert staged_exe.read_bytes() == b"v1.0.48 launcher"

    result = apply_staged_update(
        parse_update_handoff_args(launched[0]),
        is_process_running=lambda _pid: False,
        settle_seconds=0,
    )

    assert result.success is True
    assert current_exe.read_bytes() == b"v1.0.48 launcher"
    assert (install_dir / "_internal" / "runtime.bin").read_bytes() == b"v1.0.48 runtime"
    assert evejs_sentinel.read_bytes() == b"user-owned EveJS data"
    assert neighboring_file.read_text(encoding="utf-8") == "user-owned sibling"


def test_staged_handoff_preserves_the_old_install_until_new_files_verify(
    tmp_path: Path,
) -> None:
    """A failed/unfinished swap must never leave the user without a launcher."""
    from src.updater.handoff import UpdateHandoff, apply_staged_update

    exe_name = "EveJS-Launcher-V1.exe"
    target_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (target_dir / "_internal").mkdir(parents=True)
    (target_dir / exe_name).write_bytes(b"old build")
    (target_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")

    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    (source_dir / "_internal").mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"new build")
    (source_dir / "_internal" / "runtime.bin").write_bytes(b"new runtime")

    phases: list[tuple[str, str]] = []
    copied: list[tuple[int, int]] = []
    result = apply_staged_update(
        UpdateHandoff(
            target_dir=target_dir,
            source_dir=source_dir,
            exe_name=exe_name,
            parent_pid=12345,
        ),
        is_process_running=lambda _pid: False,
        stage_callback=lambda stage, detail: phases.append((stage, detail)),
        copy_progress_callback=lambda done, total: copied.append((done, total)),
        settle_seconds=0,
    )

    backup_dir = target_dir / ".evejs-launcher-update-backup"
    assert result.success is True
    assert result.installed_exe == target_dir / exe_name
    assert result.backup_dir == backup_dir
    assert (target_dir / exe_name).read_bytes() == b"new build"
    assert (backup_dir / exe_name).read_bytes() == b"old build"
    assert copied[-1] == (2, 2)
    assert phases[0] == ("install", "Waiting for the launcher to close…")


def test_staged_handoff_replaces_only_launcher_owned_entries(
    tmp_path: Path,
) -> None:
    """Updating beside an EveJS install must never move, replace, or delete it."""
    from src.updater.handoff import UpdateHandoff, apply_staged_update

    exe_name = "EveJS-Launcher-V1.exe"
    target_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (target_dir / "_internal").mkdir(parents=True)
    (target_dir / exe_name).write_bytes(b"old launcher")
    (target_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    (target_dir / "_internal" / "obsolete.bin").write_bytes(b"remove with runtime")

    evejs_sentinel = target_dir / "evejs" / "server" / "data" / "game-store.db"
    evejs_sentinel.parent.mkdir(parents=True)
    evejs_sentinel.write_bytes(b"irreplaceable user data")
    user_file = target_dir / "keep-me.txt"
    user_file.write_text("also user-owned", encoding="utf-8")
    legacy_backup_sentinel = (
        target_dir.with_name(f"{target_dir.name}.old")
        / "evejs"
        / "data"
        / "legacy-backup.db"
    )
    legacy_backup_sentinel.parent.mkdir(parents=True)
    legacy_backup_sentinel.write_bytes(b"older updater data must survive too")

    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    (source_dir / "_internal").mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"new launcher")
    (source_dir / "_internal" / "runtime.bin").write_bytes(b"new runtime")
    (source_dir / "evejs" / "server").mkdir(parents=True)
    (source_dir / "evejs" / "server" / "foreign.txt").write_text(
        "a release package does not own this name",
        encoding="utf-8",
    )
    (source_dir / "keep-me.txt").write_text(
        "must not overwrite the user's file",
        encoding="utf-8",
    )

    result = apply_staged_update(
        UpdateHandoff(target_dir, source_dir, exe_name, parent_pid=0),
        is_process_running=lambda _pid: False,
        settle_seconds=0,
    )

    assert result.success is True
    assert (target_dir / exe_name).read_bytes() == b"new launcher"
    assert (target_dir / "_internal" / "runtime.bin").read_bytes() == b"new runtime"
    assert not (target_dir / "_internal" / "obsolete.bin").exists()
    assert evejs_sentinel.read_bytes() == b"irreplaceable user data"
    assert not (target_dir / "evejs" / "server" / "foreign.txt").exists()
    assert user_file.read_text(encoding="utf-8") == "also user-owned"
    assert legacy_backup_sentinel.read_bytes() == b"older updater data must survive too"

    assert result.backup_dir is not None
    assert not (result.backup_dir / "evejs").exists()
    assert not (result.backup_dir / user_file.name).exists()


def test_staged_handoff_copy_failure_restores_only_launcher_owned_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Rollback must restore the launcher without removing colocated user data."""
    from src.updater import handoff

    exe_name = "EveJS-Launcher-V1.exe"
    target_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (target_dir / "_internal").mkdir(parents=True)
    (target_dir / exe_name).write_bytes(b"old launcher")
    (target_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    evejs_sentinel = target_dir / "evejs" / "data" / "game-store.db"
    evejs_sentinel.parent.mkdir(parents=True)
    evejs_sentinel.write_bytes(b"live user data")
    user_file = target_dir / "keep-me.txt"
    user_file.write_text("still here", encoding="utf-8")

    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    (source_dir / "_internal").mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"new launcher")
    (source_dir / "_internal" / "runtime.bin").write_bytes(b"new runtime")

    def fail_after_partial_copy(
        _source_dir: Path,
        install_dir: Path,
        _exe_name: str,
        _progress_callback,
    ) -> None:
        (install_dir / exe_name).write_bytes(b"partial launcher")
        (install_dir / "_internal").mkdir()
        (install_dir / "_internal" / "partial.bin").write_bytes(b"partial runtime")
        raise OSError("simulated copy failure")

    monkeypatch.setattr(handoff, "_copy_install_tree", fail_after_partial_copy)

    result = handoff.apply_staged_update(
        handoff.UpdateHandoff(target_dir, source_dir, exe_name, parent_pid=0),
        is_process_running=lambda _pid: False,
        settle_seconds=0,
    )

    assert result.success is False
    assert "previous launcher was restored" in result.error
    assert (target_dir / exe_name).read_bytes() == b"old launcher"
    assert (target_dir / "_internal" / "runtime.bin").read_bytes() == b"old runtime"
    assert not (target_dir / "_internal" / "partial.bin").exists()
    assert evejs_sentinel.read_bytes() == b"live user data"
    assert user_file.read_text(encoding="utf-8") == "still here"


def test_staged_handoff_refuses_an_unknown_preexisting_backup(
    tmp_path: Path,
) -> None:
    """A colliding rollback directory is user-owned until proven otherwise."""
    from src.updater.handoff import UpdateHandoff, apply_staged_update

    exe_name = "EveJS-Launcher-V1.exe"
    target_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (target_dir / "_internal").mkdir(parents=True)
    (target_dir / exe_name).write_bytes(b"old launcher")
    (target_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    backup_sentinel = (
        target_dir
        / ".evejs-launcher-update-backup"
        / "evejs"
        / "data"
        / "game-store.db"
    )
    backup_sentinel.parent.mkdir(parents=True)
    backup_sentinel.write_bytes(b"do not delete")

    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    (source_dir / "_internal").mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"new launcher")
    (source_dir / "_internal" / "runtime.bin").write_bytes(b"new runtime")

    result = apply_staged_update(
        UpdateHandoff(target_dir, source_dir, exe_name, parent_pid=0),
        is_process_running=lambda _pid: False,
        settle_seconds=0,
    )

    assert result.success is False
    assert "left untouched" in result.error
    assert (target_dir / exe_name).read_bytes() == b"old launcher"
    assert (target_dir / "_internal" / "runtime.bin").read_bytes() == b"old runtime"
    assert backup_sentinel.read_bytes() == b"do not delete"


def test_staged_handoff_rejects_an_empty_runtime_before_moving_old_files(
    tmp_path: Path,
) -> None:
    """An empty ``_internal`` is not a usable PyInstaller onedir release."""
    from src.updater.handoff import UpdateHandoff, apply_staged_update

    exe_name = "EveJS-Launcher-V1.exe"
    target_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (target_dir / "_internal").mkdir(parents=True)
    (target_dir / exe_name).write_bytes(b"old launcher")
    (target_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    preserved = target_dir / "evejs" / "data" / "game-store.db"
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b"preserve me")

    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    (source_dir / "_internal").mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"incomplete launcher")

    result = apply_staged_update(
        UpdateHandoff(target_dir, source_dir, exe_name, parent_pid=0),
        is_process_running=lambda _pid: False,
        settle_seconds=0,
    )

    assert result.success is False
    assert "incomplete" in result.error
    assert (target_dir / exe_name).read_bytes() == b"old launcher"
    assert (target_dir / "_internal" / "runtime.bin").read_bytes() == b"old runtime"
    assert preserved.read_bytes() == b"preserve me"
    assert not (target_dir / ".evejs-launcher-update-backup").exists()


def test_staged_handoff_keeps_the_user_informed_while_windows_releases_files(
    tmp_path: Path,
) -> None:
    """Retain the proven lock-release pause, but make the wait visible."""
    from src.updater.handoff import UpdateHandoff, apply_staged_update

    exe_name = "EveJS-Launcher-V1.exe"
    target_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    target_dir.mkdir(parents=True)
    (target_dir / exe_name).write_bytes(b"old build")
    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    (source_dir / "_internal").mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"new build")
    (source_dir / "_internal" / "runtime.bin").write_bytes(b"new runtime")

    phases: list[tuple[str, str]] = []
    sleeps: list[float] = []
    result = apply_staged_update(
        UpdateHandoff(target_dir, source_dir, exe_name, parent_pid=0),
        is_process_running=lambda _pid: False,
        stage_callback=lambda stage, detail: phases.append((stage, detail)),
        settle_seconds=2,
        sleep_func=lambda seconds: sleeps.append(seconds),
    )

    assert result.success is True
    assert sleeps == [1.0, 1.0]
    assert ("install", "Releasing old launcher files (2 seconds remaining)…") in phases
    assert ("install", "Releasing old launcher files (1 second remaining)…") in phases


def test_main_recognises_the_private_update_handoff_arguments() -> None:
    """The staged executable must enter updater mode instead of the normal app."""
    from main import _parse_update_handoff

    handoff = _parse_update_handoff(
        [
            "--apply-update",
            "--target-dir",
            r"C:\Apps\EveJS-Launcher-V1",
            "--source-dir",
            r"C:\Temp\evejs_launcher_update\EveJS-Launcher-V1",
            "--exe-name",
            "EveJS-Launcher-V1.exe",
            "--parent-pid",
            "12345",
        ]
    )

    assert handoff is not None
    assert handoff.exe_name == "EveJS-Launcher-V1.exe"
    assert handoff.parent_pid == 12345
    assert _parse_update_handoff([]) is None


def test_main_window_shows_update_progress_before_starting_install_worker(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepting an update must show a branded status window before I/O begins."""
    from src import app as app_module
    from src import config
    from src.app import MainWindow

    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "",
            "client_path": "",
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])

    events: list[str] = []

    class FakeSignal:
        def connect(self, _slot, *_args):  # type: ignore[no-untyped-def]
            return None

    class FakeProgressDialog:
        def __init__(self, version, parent=None):  # type: ignore[no-untyped-def]
            assert version == "v1.0.33"
            assert parent is not None

        def set_stage(self, _stage, _detail):  # type: ignore[no-untyped-def]
            return None

        def set_download_progress(self, _done, _total):  # type: ignore[no-untyped-def]
            return None

        def show(self) -> None:
            events.append("show")

    class FakeWorker:
        def __init__(self, url, current_exe, parent=None):  # type: ignore[no-untyped-def]
            assert url == "https://example.invalid/EveJS-Launcher-V1.zip"
            assert current_exe
            assert parent is not None
            self.stage_changed = FakeSignal()
            self.download_progress = FakeSignal()
            self.completed = FakeSignal()
            self.finished = FakeSignal()

        def start(self) -> None:
            events.append("start")

    monkeypatch.setattr(app_module, "UpdateProgressDialog", FakeProgressDialog)
    monkeypatch.setattr(app_module, "UpdateInstallWorker", FakeWorker)

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    window._latest_version = "v1.0.33"
    window._latest_download_url = "https://example.invalid/EveJS-Launcher-V1.zip"
    try:
        window._begin_update_install()

        assert events == ["show", "start"]
    finally:
        window._update_install_worker = None
        window._update_progress_dialog = None
        window.deleteLater()


def test_update_cleanup_is_deferred_to_the_restarted_launcher_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A completed update must not spawn cmd.exe just to remove its own artifacts."""
    from src.updater import handoff

    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    preserved = install_dir / "evejs" / "data" / "game-store.db"
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b"preserve through cleanup")
    legacy_backup = install_dir.with_name(f"{install_dir.name}.old") / "evejs.db"
    legacy_backup.parent.mkdir(parents=True)
    legacy_backup.write_bytes(b"legacy backup is not ours")
    backup_dir = install_dir / ".evejs-launcher-update-backup"
    (backup_dir / "_internal").mkdir(parents=True)
    (backup_dir / "EveJS-Launcher-V1.exe").write_bytes(b"old launcher")
    (backup_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")

    staging_root = Path(tempfile.mkdtemp(prefix="evejs_launcher_update_"))
    source_dir = staging_root / "staged" / "EveJS-Launcher-V1"
    source_dir.mkdir(parents=True)

    spawned: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_popen(*args: object, **kwargs: object) -> None:
        spawned.append((args, kwargs))

    monkeypatch.setattr(handoff.subprocess, "Popen", fake_popen)

    try:
        assert handoff.schedule_update_cleanup(
            install_dir,
            source_dir,
            backup_dir,
            "EveJS-Launcher-V1.exe",
        ) is True

        marker = install_dir / "_internal" / ".evejs-update-cleanup.json"
        assert marker.is_file()
        assert spawned == []
        assert staging_root.is_dir()
        assert backup_dir.is_dir()

        handoff.cleanup_pending_update(install_dir)

        assert not marker.exists()
        assert not staging_root.exists()
        assert not backup_dir.exists()
        assert preserved.read_bytes() == b"preserve through cleanup"
        assert legacy_backup.read_bytes() == b"legacy backup is not ours"
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def test_update_cleanup_marker_write_failure_is_reported_and_preserves_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing cleanup marker must never be silently reported as scheduled."""
    from src.updater import handoff

    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    backup_dir = install_dir / ".evejs-launcher-update-backup"
    (backup_dir / "_internal").mkdir(parents=True)
    (backup_dir / "EveJS-Launcher-V1.exe").write_bytes(b"old launcher")
    (backup_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    staging_root = Path(tempfile.mkdtemp(prefix="evejs_launcher_update_"))
    source_dir = staging_root / "staged" / "EveJS-Launcher-V1"
    source_dir.mkdir(parents=True)

    original_write_text = Path.write_text

    def fail_marker_write(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path.name == ".evejs-update-cleanup.tmp":
            raise OSError("simulated marker failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_marker_write)

    try:
        assert handoff.schedule_update_cleanup(
            install_dir,
            source_dir,
            backup_dir,
            "EveJS-Launcher-V1.exe",
        ) is False
        assert backup_dir.is_dir()
        assert staging_root.is_dir()
        assert not (
            install_dir / "_internal" / ".evejs-update-cleanup.json"
        ).exists()
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def test_update_cleanup_rejects_a_staging_root_that_contains_the_live_install() -> None:
    """Cleanup must never recurse through a staging root that owns the install."""
    from src.updater import handoff

    staging_root = Path(tempfile.mkdtemp(prefix="evejs_launcher_update_"))
    source_dir = staging_root / "staged" / "EveJS-Launcher-V1"
    source_dir.mkdir(parents=True)
    install_dir = staging_root / "installed" / "EveJS-Launcher-V1"
    preserved = install_dir / "evejs" / "data" / "game-store.db"
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b"live user data")
    marker = install_dir / "_internal" / ".evejs-update-cleanup.json"

    try:
        assert handoff.schedule_update_cleanup(
            install_dir,
            source_dir,
            None,
            "EveJS-Launcher-V1.exe",
        ) is False
        assert not marker.exists()

        marker.parent.mkdir(parents=True)
        marker.write_text(
            json.dumps(
                {
                    "source_root": str(staging_root),
                    "backup_dir": None,
                    "exe_name": "EveJS-Launcher-V1.exe",
                }
            ),
            encoding="utf-8",
        )

        assert handoff.cleanup_pending_update(install_dir) is False
        assert staging_root.is_dir()
        assert preserved.read_bytes() == b"live user data"
        assert not marker.exists()
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def test_update_cleanup_rejects_a_marker_that_targets_an_untrusted_path(
    tmp_path: Path,
) -> None:
    """A tampered marker must never turn the launcher into a generic deleter."""
    from src.updater.handoff import cleanup_pending_update

    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    unrelated_dir = tmp_path / "must-not-delete"
    unrelated_dir.mkdir()
    marker = install_dir / "_internal" / ".evejs-update-cleanup.json"
    marker.write_text(
        json.dumps({"source_root": str(unrelated_dir)}),
        encoding="utf-8",
    )

    assert cleanup_pending_update(install_dir) is False
    assert unrelated_dir.is_dir()
    assert not marker.exists()


def test_update_cleanup_retains_marker_for_an_exact_backup_with_unknown_content(
    tmp_path: Path,
) -> None:
    """An invalid exact backup remains retryable without deleting user data."""
    from src.updater.handoff import cleanup_pending_update

    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    backup_dir = install_dir / ".evejs-launcher-update-backup"
    (backup_dir / "_internal").mkdir(parents=True)
    (backup_dir / "EveJS-Launcher-V1.exe").write_bytes(b"old launcher")
    (backup_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    backup_sentinel = (
        backup_dir
        / "evejs"
        / "data"
        / "game-store.db"
    )
    backup_sentinel.parent.mkdir(parents=True)
    backup_sentinel.write_bytes(b"never delete unknown content")
    staging_root = Path(tempfile.mkdtemp(prefix="evejs_launcher_update_"))
    (staging_root / "staged").mkdir()
    marker = install_dir / "_internal" / ".evejs-update-cleanup.json"
    marker_payload = json.dumps(
        {
            "source_root": str(staging_root),
            "backup_dir": str(backup_dir),
            "exe_name": "EveJS-Launcher-V1.exe",
        }
    )
    marker.write_text(marker_payload, encoding="utf-8")

    try:
        for _ in range(2):
            assert cleanup_pending_update(install_dir) is False
        assert (backup_dir / "EveJS-Launcher-V1.exe").read_bytes() == b"old launcher"
        assert (backup_dir / "_internal" / "runtime.bin").read_bytes() == b"old runtime"
        assert backup_sentinel.read_bytes() == b"never delete unknown content"
        assert staging_root.is_dir()
        assert marker.is_file()
        assert marker.read_text(encoding="utf-8") == marker_payload
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def test_updater_path_checks_fail_closed_when_metadata_is_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Access denied is indeterminate, never equivalent to a missing safe path."""
    from src.updater import handoff

    denied_path = tmp_path / "denied"
    original_lstat = Path.lstat

    def denied_lstat(path: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == denied_path:
            raise PermissionError("simulated access denial")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied_lstat)

    assert handoff._path_exists(denied_path) is True
    assert handoff._is_reparse_point(denied_path) is True


def test_updater_public_path_resolution_runtime_errors_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Link-loop resolution failures must be reported instead of escaping."""
    from src.updater import handoff

    source_dir = tmp_path / "staged" / "EveJS-Launcher-V1"
    source_dir.mkdir(parents=True)
    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    original_resolve = Path.resolve
    blocked_paths = {source_dir}

    def fail_link_loop_resolution(
        path: Path,
        *args,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        if path in blocked_paths:
            raise RuntimeError("simulated symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_link_loop_resolution)

    result = handoff.apply_staged_update(
        handoff.UpdateHandoff(
            target_dir=install_dir,
            source_dir=source_dir,
            exe_name="EveJS-Launcher-V1.exe",
            parent_pid=0,
        ),
        is_process_running=lambda _pid: False,
        settle_seconds=0,
    )

    assert result.success is False
    assert "could not be resolved" in result.error
    assert handoff.schedule_update_cleanup(
        install_dir,
        source_dir,
        None,
        "EveJS-Launcher-V1.exe",
    ) is False

    blocked_paths.clear()
    blocked_paths.add(install_dir)
    assert handoff.schedule_update_cleanup(
        install_dir,
        source_dir,
        None,
        "EveJS-Launcher-V1.exe",
    ) is False
    assert handoff.cleanup_pending_update(install_dir) is False


def test_update_cleanup_scheduler_catches_a_late_backup_resolution_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A path race while serializing the marker must fail closed."""
    from src.updater import handoff

    install_dir = tmp_path / "installed" / "EveJS-Launcher-V1"
    (install_dir / "_internal").mkdir(parents=True)
    backup_dir = install_dir / ".evejs-launcher-update-backup"
    (backup_dir / "_internal").mkdir(parents=True)
    (backup_dir / "EveJS-Launcher-V1.exe").write_bytes(b"old launcher")
    (backup_dir / "_internal" / "runtime.bin").write_bytes(b"old runtime")
    staging_root = Path(tempfile.mkdtemp(prefix="evejs_launcher_update_"))
    source_dir = staging_root / "staged" / "EveJS-Launcher-V1"
    source_dir.mkdir(parents=True)
    original_resolve = Path.resolve
    backup_resolve_calls = 0

    def fail_second_backup_resolution(
        path: Path,
        *args,
        **kwargs,
    ):  # type: ignore[no-untyped-def]
        nonlocal backup_resolve_calls
        if path == backup_dir:
            backup_resolve_calls += 1
            if backup_resolve_calls == 2:
                raise RuntimeError("simulated path race")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_second_backup_resolution)

    try:
        assert handoff.schedule_update_cleanup(
            install_dir,
            source_dir,
            backup_dir,
            "EveJS-Launcher-V1.exe",
        ) is False
        assert backup_resolve_calls == 2
        assert backup_dir.is_dir()
        assert staging_root.is_dir()
        assert not (
            install_dir / "_internal" / ".evejs-update-cleanup.json"
        ).exists()
        assert not (
            install_dir / "_internal" / ".evejs-update-cleanup.tmp"
        ).exists()
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def test_frozen_launcher_starts_the_deferred_cleanup_in_a_background_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A successful handoff must actually consume its marker after restart."""
    import main
    from src.updater import handoff

    executable = tmp_path / "EveJS-Launcher-V1" / "EveJS-Launcher-V1.exe"
    calls: list[Path] = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):  # type: ignore[no-untyped-def]
            assert daemon is True
            assert name == "update-cleanup"
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", str(executable))
    monkeypatch.setattr(
        handoff,
        "cleanup_pending_update",
        lambda install_dir: calls.append(install_dir),
    )
    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    main._schedule_pending_update_cleanup()

    assert calls == [executable.parent.resolve()]


def test_fatal_startup_diagnostic_survives_a_legacy_console_encoding() -> None:
    import main

    class Cp1252Stream:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.value = ""

        def write(self, value: str) -> None:
            value.encode(self.encoding)
            self.value += value

        def flush(self) -> None:
            pass

    stream = Cp1252Stream()
    try:
        raise RuntimeError("测试用户")
    except RuntimeError as exc:
        main._write_fatal_diagnostic(exc, stream)

    assert "FATAL ERROR" in stream.value
    assert "\\u6d4b\\u8bd5\\u7528\\u6237" in stream.value
    assert "RuntimeError" in stream.value


def test_fatal_startup_diagnostic_tolerates_a_windowed_frozen_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    monkeypatch.setattr(main.sys, "stderr", None)

    main._write_fatal_diagnostic(RuntimeError("测试用户"))
