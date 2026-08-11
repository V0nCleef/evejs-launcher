"""Runtime backend settings acceptance tests using an isolated config file."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog

from src import config
from src.core.client_autologin import AutoLoginCapability
from src.core.runtime.docker_compose import PreflightFailureKind, PreflightReport
from src.core.runtime.docker_setup import DockerPreflightResult
from src.pages import settings_page as settings_page_module
from src.pages.settings_page import SettingsPage


@pytest.fixture
def isolated_config(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path / "config.json"


def test_default_legacy_native_hides_docker_fields_and_shows_scripts(qapp: QApplication, isolated_config) -> None:
    page = SettingsPage()
    assert page.runtime_backend_combo.currentData() == "native"
    assert page.docker_fields.isHidden()
    assert not page.scripts_box.isHidden()


def test_selecting_docker_shows_docker_fields_hides_scripts_then_native_restores(qapp: QApplication, isolated_config) -> None:
    page = SettingsPage()
    page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData("docker_compose"))
    assert not page.docker_fields.isHidden()
    assert page.scripts_box.isHidden()
    page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData("native"))
    assert page.docker_fields.isHidden()
    assert not page.scripts_box.isHidden()


def test_runtime_guidance_explains_native_docker_and_data_boundaries(
    qapp: QApplication,
    isolated_config,
) -> None:
    page = SettingsPage()

    assert "directly on Windows" in page.runtime_backend_help_label.text()
    assert "Docker Desktop is not required" in page.runtime_backend_help_label.text()
    assert "does not move characters" in page.runtime_data_notice_label.text()

    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )

    assert "Docker Desktop" in page.runtime_backend_help_label.text()
    assert "Linux-container mode" in page.runtime_backend_help_label.text()


def test_blank_compose_and_project_name_are_presented_as_recommended_defaults(
    qapp: QApplication,
    isolated_config,
    tmp_path,
) -> None:
    root = tmp_path / "docker-project"
    root.mkdir()
    initial = deepcopy(config.DEFAULT_CONFIG)
    initial.update(
        {
            "evejs_root": str(root),
            "runtime_backend": "docker_compose",
            "docker_compose_file": "",
            "docker_project_name": "",
        }
    )
    config.save(initial)

    page = SettingsPage()

    assert "leave this blank" in page.docker_compose_help_label.text().casefold()
    assert str(root / "compose.yaml") in page.docker_compose_resolved_label.text()
    assert "(automatic)" in page.docker_compose_resolved_label.text()
    assert page.docker_compose_edit.text() == ""
    assert page.docker_project_edit.text() == ""
    assert page.docker_advanced_toggle.isChecked() is False
    assert page.docker_advanced_fields.isHidden()
    assert "Most users should leave this blank" in page.docker_project_help_label.text()

    page.docker_advanced_toggle.setChecked(True)

    assert not page.docker_advanced_fields.isHidden()


def test_saved_custom_project_name_expands_advanced_options(
    qapp: QApplication,
    isolated_config,
) -> None:
    initial = deepcopy(config.DEFAULT_CONFIG)
    initial.update(
        {
            "runtime_backend": "docker_compose",
            "docker_project_name": "evejs-existing",
        }
    )
    config.save(initial)

    page = SettingsPage()

    assert page.docker_advanced_toggle.isChecked()
    assert not page.docker_advanced_fields.isHidden()
    assert page.docker_project_edit.text() == "evejs-existing"


def test_runtime_settings_round_trip_preserves_native_script_preference_and_auto_start(qapp: QApplication, isolated_config, tmp_path) -> None:
    root = tmp_path / "evejs"
    root.mkdir()
    (root / "StartServer.bat").touch()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    initial = deepcopy(config.DEFAULT_CONFIG)
    initial.update({"evejs_root": str(root), "server_start_preference": "StartServer.bat", "auto_start_server": True, "auto_start_market": True})
    config.save(initial)
    page = SettingsPage()
    page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData("docker_compose"))
    page.docker_compose_edit.setText(str(compose))
    page.docker_policy_combo.setCurrentIndex(page.docker_policy_combo.findData("managed"))
    page.docker_project_edit.setText("eve-test")
    page.docker_keep_running_toggle.setChecked(False)
    requests: list[object] = []
    page.docker_preflight_requested.connect(requests.append)
    page.save_settings()
    assert len(requests) == 1
    page.apply_docker_preflight_result(
        DockerPreflightResult(
            requests[0].token,
            requests[0].draft_fingerprint,
            PreflightReport(True, ("ready",)),
        )
    )

    saved = config.load()
    assert {key: saved[key] for key in ("runtime_backend", "docker_compose_file", "docker_control_policy", "docker_project_name", "docker_keep_running_on_exit")} == {
        "runtime_backend": "docker_compose", "docker_compose_file": str(compose), "docker_control_policy": "managed", "docker_project_name": "eve-test", "docker_keep_running_on_exit": False,
    }
    assert saved["server_start_preference"] == "StartServer.bat"
    assert saved["auto_start_server"] is True and saved["auto_start_market"] is True


def test_invalid_persisted_backend_policy_and_non_bool_keep_running_normalize_safely(qapp: QApplication, isolated_config) -> None:
    isolated_config.write_text(json.dumps({"runtime_backend": "bad", "docker_control_policy": "bad", "docker_keep_running_on_exit": "yes"}), encoding="utf-8")
    page = SettingsPage()
    assert page.runtime_backend_combo.currentData() == "native"
    assert page.docker_policy_combo.currentData() == "connect_only"
    assert page.docker_keep_running_toggle.isChecked() is True


def test_supported_auto_login_is_default_off_and_round_trips_only_when_opted_in(
    qapp: QApplication,
    isolated_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_page_module,
        "inspect_auto_login_capability",
        lambda *_args: AutoLoginCapability(
            True,
            "Supported — copied EVE build 3396210; no client patch required.",
            3396210,
        ),
    )
    page = SettingsPage()

    assert page.auto_login_toggle.isEnabled()
    assert page.auto_login_toggle.isChecked() is False
    assert "no client patch" in page.auto_login_status_label.text().casefold()

    page.auto_login_toggle.setChecked(True)
    page.save_settings()

    assert config.load()["auto_login_enabled"] is True


def test_unsupported_auto_login_is_disabled_and_cannot_be_persisted(
    qapp: QApplication,
    isolated_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = deepcopy(config.DEFAULT_CONFIG)
    initial["auto_login_enabled"] = True
    config.save(initial)
    monkeypatch.setattr(
        settings_page_module,
        "inspect_auto_login_capability",
        lambda *_args: AutoLoginCapability(False, "Unsupported fixture client."),
    )

    page = SettingsPage()
    assert page.auto_login_toggle.isEnabled() is False
    assert "unsupported" in page.auto_login_status_label.text().casefold()

    page.save_settings()
    assert config.load()["auto_login_enabled"] is False


def test_supported_auto_login_is_disabled_when_switching_to_docker(
    qapp: QApplication,
    isolated_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_page_module,
        "inspect_auto_login_capability",
        lambda *_args: AutoLoginCapability(True, "Supported Native fixture."),
    )
    page = SettingsPage()
    page.auto_login_toggle.setChecked(True)

    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )

    assert page.auto_login_toggle.isEnabled() is False
    assert "native" in page.auto_login_status_label.text().casefold()
    assert page._collect_settings()["auto_login_enabled"] is False


def test_compose_browse_uses_yaml_filter_with_all_files_fallback(qapp: QApplication, isolated_config, monkeypatch: pytest.MonkeyPatch) -> None:
    page = SettingsPage()
    captured: list[object] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: captured.extend(args) or ("", ""))

    page._browse_file(page.docker_compose_edit, compose=True)

    assert "*.yaml" in captured[3] and "*.yml" in captured[3]
    assert "All Files" in captured[3]
    assert "*.exe" not in captured[3]


def test_keep_running_immediately_tracks_native_connect_only_and_managed_without_losing_check(
    qapp: QApplication, isolated_config,
) -> None:
    page = SettingsPage()
    page.docker_keep_running_toggle.setChecked(False)
    assert not page.docker_keep_running_toggle.isEnabled()
    assert "Managed" in page.docker_keep_running_toggle.toolTip()

    page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData("docker_compose"))
    assert not page.docker_keep_running_toggle.isEnabled()
    assert not page.docker_keep_running_toggle.isChecked()
    assert "Managed" in page.docker_keep_running_toggle.toolTip()
    assert "never starts, stops, or changes" in page.docker_policy_help_label.text()

    page.docker_policy_combo.setCurrentIndex(page.docker_policy_combo.findData("managed"))
    assert page.docker_keep_running_toggle.isEnabled()
    assert not page.docker_keep_running_toggle.isChecked()
    assert "Leave managed" in page.docker_keep_running_toggle.toolTip()
    assert "start, stop, restart" in page.docker_policy_help_label.text()

    page.docker_policy_combo.setCurrentIndex(page.docker_policy_combo.findData("connect_only"))
    assert not page.docker_keep_running_toggle.isEnabled()
    assert not page.docker_keep_running_toggle.isChecked()
    assert "Managed" in page.docker_keep_running_toggle.toolTip()


def test_test_docker_setup_never_writes_config(
    qapp: QApplication,
    isolated_config,
    tmp_path: Path,
) -> None:
    initial = deepcopy(config.DEFAULT_CONFIG)
    config.save(initial)
    before = isolated_config.read_bytes()
    root = tmp_path / "project"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    page = SettingsPage()
    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )
    page.evejs_root_edit.setText(str(root))
    page.docker_compose_edit.setText(str(compose))
    requests: list[object] = []
    page.docker_preflight_requested.connect(requests.append)

    page.test_docker_setup_btn.click()

    assert len(requests) == 1
    assert isolated_config.read_bytes() == before


def test_failed_or_stale_docker_preflight_cannot_change_config(
    qapp: QApplication,
    isolated_config,
    tmp_path: Path,
) -> None:
    initial = deepcopy(config.DEFAULT_CONFIG)
    config.save(initial)
    before = isolated_config.read_bytes()
    root = tmp_path / "project"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    page = SettingsPage()
    page.runtime_backend_combo.setCurrentIndex(
        page.runtime_backend_combo.findData("docker_compose")
    )
    page.evejs_root_edit.setText(str(root))
    page.docker_compose_edit.setText(str(compose))
    requests: list[object] = []
    page.docker_preflight_requested.connect(requests.append)

    page.save_settings()
    request = requests[-1]
    page.docker_project_edit.setText("changed-after-request")
    page.apply_docker_preflight_result(
        DockerPreflightResult(
            request.token,
            request.draft_fingerprint,
            PreflightReport(True, ("ready",)),
        )
    )
    assert isolated_config.read_bytes() == before

    page.save_settings()
    current = requests[-1]
    page.apply_docker_preflight_result(
        DockerPreflightResult(
            current.token,
            current.draft_fingerprint,
            PreflightReport.failed(
                PreflightFailureKind.DAEMON_UNAVAILABLE
            ),
        )
    )
    assert isolated_config.read_bytes() == before
    assert page.save_btn.isEnabled()
    assert page.test_docker_setup_btn.isEnabled()
