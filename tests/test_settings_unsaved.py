"""Regression tests for Settings drafts and guarded page navigation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src import app as app_module
from src import config
from src.app import MainWindow
from src.constants import Page
from src.core.runtime.docker_compose import PreflightReport
from src.core.runtime.docker_setup import DockerPreflightResult
from src.pages.settings_page import SettingsPage


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def _client_fixture(tmp_path: Path) -> tuple[Path, Path]:
    tq = tmp_path / "SharedCache" / "tq"
    executable = tq / "bin64" / "exefile.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    (tq / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    return tq, executable


def test_settings_client_browse_selects_and_stores_the_tq_folder(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tq, executable = _client_fixture(tmp_path)
    page = SettingsPage()
    file_picker_calls: list[bool] = []
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args: str(executable.parent),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args: file_picker_calls.append(True) or ("", ""),
    )

    page._browse_client_directory()
    page.save_settings()

    assert page.client_path_edit.text() == str(tq)
    assert config.load()["client_path"] == str(tq)
    assert file_picker_calls == []
    page.deleteLater()
    qapp.processEvents()


def test_settings_dirty_state_clears_only_after_load_or_successful_save(
    qapp: QApplication,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = SettingsPage()
    outcomes: list[bool] = []
    page.save_finished.connect(outcomes.append)

    assert page.is_dirty() is False
    page.proxy_url_edit.setText("http://127.0.0.1:26009")
    assert page.is_dirty() is True
    page.load_settings()
    assert page.is_dirty() is False

    page.proxy_url_edit.setText("http://127.0.0.1:26010")
    page.save_settings()
    assert page.is_dirty() is False
    assert outcomes == [True]

    page.proxy_url_edit.setText("http://127.0.0.1:26011")
    monkeypatch.setattr(config, "save", lambda _cfg: (_ for _ in ()).throw(OSError()))
    page.save_settings()
    assert page.is_dirty() is True
    assert outcomes == [True, False]
    page.deleteLater()
    qapp.processEvents()


def test_invalid_client_path_blocks_settings_save(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    page = SettingsPage()
    outcomes: list[bool] = []
    saved: list[dict] = []
    page.save_finished.connect(outcomes.append)
    page.settings_saved.connect(saved.append)
    page.client_path_edit.setText(str(tmp_path / "not-a-client" / "bin64"))

    page.save_settings()

    assert outcomes == [False]
    assert saved == []
    assert page.is_dirty() is True
    assert not isolated_config.exists()
    page.deleteLater()
    qapp.processEvents()


def test_lifecycle_busy_blocks_direct_settings_persistence(
    qapp: QApplication,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    outcomes: list[bool] = []
    saved: list[dict] = []
    page.save_finished.connect(outcomes.append)
    page.settings_saved.connect(saved.append)
    page.proxy_url_edit.setText("http://127.0.0.1:26099")

    page.set_lifecycle_busy(True)
    page.save_settings()

    assert not page.save_btn.isEnabled()
    assert outcomes == [False]
    assert saved == []
    assert page.is_dirty()
    assert not isolated_config.exists()
    page.set_lifecycle_busy(False)
    assert page.save_btn.isEnabled()
    page.deleteLater()
    qapp.processEvents()


def test_lifecycle_busy_cancels_pending_docker_preflight_save(
    qapp: QApplication,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    page = SettingsPage()
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )
    page.docker_compose_edit.setText(str(compose))
    requests: list[object] = []
    outcomes: list[bool] = []
    saved: list[dict] = []
    page.docker_preflight_requested.connect(requests.append)
    page.save_finished.connect(outcomes.append)
    page.settings_saved.connect(saved.append)

    page.save_settings()
    assert len(requests) == 1
    request = requests[0]
    assert page._save_after_docker_preflight

    # A lifecycle can begin after the read-only worker starts but before its
    # queued result returns. That must permanently revoke this save intent.
    page.set_lifecycle_busy(True)
    assert outcomes == [False]
    assert not page._save_after_docker_preflight
    page.apply_docker_preflight_result(
        DockerPreflightResult(
            request.token,
            request.draft_fingerprint,
            PreflightReport(True, ("ready",)),
        )
    )

    assert saved == []
    assert outcomes == [False]
    assert not isolated_config.exists()
    assert not page.save_btn.isEnabled()
    page.set_lifecycle_busy(False)
    assert page.save_btn.isEnabled()
    assert page.is_dirty()
    page.deleteLater()
    qapp.processEvents()


@pytest.fixture
def settings_window(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MainWindow:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": str(tmp_path),
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    persisted = deepcopy(cfg)
    monkeypatch.setattr(config, "load", lambda: deepcopy(persisted))
    monkeypatch.setattr(
        config,
        "save",
        lambda updated: persisted.clear() or persisted.update(deepcopy(updated)),
    )
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    window._switch_page(int(Page.SETTINGS))
    yield window
    window._status_timer.stop()
    window._prune_timer.stop()
    window._overview_ack_timer.stop()
    update_timer = getattr(window, "_update_timer", None)
    if update_timer is not None:
        update_timer.stop()
    window._cancel_data_loads()
    deadline = time.monotonic() + 1.0
    while window._data_load_active() and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(2)
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_cancelled_dirty_navigation_preserves_settings_draft_and_selection(
    settings_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = settings_window._settings_page
    page.proxy_url_edit.setText("http://127.0.0.1:26099")
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Cancel,
    )

    settings_window._nav.btn_home.click()

    assert settings_window._stack.currentIndex() == int(Page.SETTINGS)
    assert settings_window._nav.btn_settings.isChecked()
    assert page.proxy_url_edit.text() == "http://127.0.0.1:26099"
    assert page.is_dirty() is True


def test_discarded_dirty_navigation_reloads_then_switches(
    settings_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = settings_window._settings_page
    original = page.proxy_url_edit.text()
    page.proxy_url_edit.setText("http://127.0.0.1:26099")
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Discard,
    )

    settings_window._nav.btn_home.click()

    assert settings_window._stack.currentIndex() == int(Page.HOME)
    assert settings_window._nav.btn_home.isChecked()
    assert page.proxy_url_edit.text() == original
    assert page.is_dirty() is False


def test_saved_dirty_navigation_waits_for_success_then_switches(
    settings_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = settings_window._settings_page
    page.proxy_url_edit.setText("http://127.0.0.1:26099")
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Save,
    )

    settings_window._switch_page(int(Page.HOME))

    assert settings_window._stack.currentIndex() == int(Page.HOME)
    assert page.is_dirty() is False
    assert config.load()["proxy_url"] == "http://127.0.0.1:26099"


def test_failed_save_blocks_dirty_navigation(
    settings_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = settings_window._settings_page
    page.proxy_url_edit.setText("http://127.0.0.1:26099")
    monkeypatch.setattr(config, "save", lambda _cfg: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Save,
    )

    settings_window._switch_page(int(Page.HOME))

    assert settings_window._stack.currentIndex() == int(Page.SETTINGS)
    assert settings_window._nav.btn_settings.isChecked()
    assert page.is_dirty() is True


def test_docker_save_defers_navigation_until_preflight_and_persist_succeed(
    settings_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = settings_window._settings_page
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )
    page.docker_compose_edit.setText(str(compose))
    requests: list[object] = []
    page.docker_preflight_requested.disconnect()
    page.docker_preflight_requested.connect(requests.append)
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Save,
    )

    settings_window._switch_page(int(Page.HOME))

    assert settings_window._stack.currentIndex() == int(Page.SETTINGS)
    assert settings_window._nav.btn_settings.isChecked()
    assert len(requests) == 1
    request = requests[0]

    page.apply_docker_preflight_result(
        DockerPreflightResult(
            request.token,
            request.draft_fingerprint,
            PreflightReport(True, ("ready",)),
        )
    )

    assert settings_window._stack.currentIndex() == int(Page.HOME)
    assert page.is_dirty() is False
    assert config.load()["runtime_backend"] == "docker_compose"


def test_cancelling_pending_docker_save_clears_deferred_navigation(
    settings_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = settings_window._settings_page
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )
    page.docker_compose_edit.setText(str(compose))
    page.docker_preflight_requested.disconnect()
    page.docker_preflight_requested.connect(lambda _request: None)
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Save,
    )

    settings_window._switch_page(int(Page.HOME))
    assert settings_window._pending_settings_intent == ("page", int(Page.HOME))

    page.discard_changes()

    assert settings_window._pending_settings_intent is None
    settings_window._switch_page(int(Page.HOME))
    assert settings_window._stack.currentIndex() == int(Page.HOME)


def test_close_save_waits_for_success_before_reentering_close(
    settings_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    event = CloseEvent()
    scheduled: list[object] = []
    settings_window._settings_page.proxy_url_edit.setText(
        "http://127.0.0.1:26099"
    )
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Save,
    )
    monkeypatch.setattr(
        app_module.QTimer,
        "singleShot",
        lambda _delay, callback: scheduled.append(callback),
    )

    settings_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert settings_window._settings_page.is_dirty() is False
    assert config.load()["proxy_url"] == "http://127.0.0.1:26099"
    assert scheduled == [settings_window.close]


def test_close_cancel_with_dirty_settings_has_no_shutdown_side_effects(
    settings_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CloseEvent:
        accepted = False
        ignored = False

        def accept(self) -> None:
            self.accepted = True

        def ignore(self) -> None:
            self.ignored = True

    class LaunchQueue:
        def cancel(self) -> None:
            cancellations.append(True)

    cancellations: list[bool] = []
    event = CloseEvent()
    settings_window._settings_page.proxy_url_edit.setText(
        "http://127.0.0.1:26099"
    )
    settings_window._launch_queue = LaunchQueue()
    monkeypatch.setattr(
        settings_window,
        "_ask_unsaved_settings",
        lambda: QMessageBox.StandardButton.Cancel,
    )

    settings_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert cancellations == []
    assert settings_window._close_in_progress is False
