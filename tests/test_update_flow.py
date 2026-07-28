"""Focused behavioral tests for the transparent launcher update flow."""
from __future__ import annotations

from copy import deepcopy
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

    dialog = UpdateProgressDialog("v1.0.32")
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

    dialog = UpdateProgressDialog("v1.0.32")
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
    install_dir.mkdir(parents=True)
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

    backup_dir = target_dir.with_name(f"{target_dir.name}.old")
    assert result.success is True
    assert result.installed_exe == target_dir / exe_name
    assert result.backup_dir == backup_dir
    assert (target_dir / exe_name).read_bytes() == b"new build"
    assert (backup_dir / exe_name).read_bytes() == b"old build"
    assert copied[-1] == (2, 2)
    assert phases[0] == ("install", "Waiting for the launcher to close…")


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
    source_dir.mkdir(parents=True)
    (source_dir / exe_name).write_bytes(b"new build")

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
            assert version == "v1.0.32"
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
    window._latest_version = "v1.0.32"
    window._latest_download_url = "https://example.invalid/EveJS-Launcher-V1.zip"
    try:
        window._begin_update_install()

        assert events == ["show", "start"]
    finally:
        window._update_install_worker = None
        window._update_progress_dialog = None
        window.deleteLater()
