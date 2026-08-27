"""Regression tests for non-blocking Settings save feedback."""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from src import config
from src.pages.settings_page import SettingsPage


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config"
    config_file = config_dir / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def test_settings_save_shows_then_clears_inline_confirmation(
    qapp: QApplication,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    page.show()
    qapp.processEvents()

    try:
        page.proxy_url_edit.setText("http://127.0.0.1:26009")
        save_button = page.findChild(QPushButton, "settingsSaveButton")
        assert save_button is not None

        save_button.click()

        assert config.load()["proxy_url"] == "http://127.0.0.1:26009"
        feedback = page.findChild(QLabel, "settingsSaveFeedback")
        assert feedback is not None
        assert feedback.isVisible() is True
        assert feedback.accessibleName() == "Settings save feedback"
        assert feedback.text() == "Saved ✓"

        feedback_timer = page.findChild(QTimer, "settingsSaveFeedbackTimer")
        assert feedback_timer is not None
        feedback_timer.timeout.emit()
        qapp.processEvents()

        assert feedback.isHidden() is True
        assert feedback.text() == ""
    finally:
        page.close()
        page.deleteLater()


def test_settings_save_reports_an_inline_failure_without_emitting_success(
    qapp: QApplication,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_save(_cfg: dict) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(config, "save", fail_save)
    page = SettingsPage()
    saved_configs: list[dict] = []
    page.settings_saved.connect(saved_configs.append)
    page.show()
    qapp.processEvents()

    try:
        page.proxy_url_edit.setText("http://127.0.0.1:26010")
        try:
            page.save_settings()
        except OSError:
            pass

        feedback = page.findChild(QLabel, "settingsSaveFeedback")
        assert feedback is not None
        assert feedback.isVisible() is True
        assert feedback.text().startswith("Save failed")
        assert (feedback_timer := page.findChild(QTimer, "settingsSaveFeedbackTimer"))
        assert feedback_timer.isActive() is False
        assert page.proxy_url_edit.text() == "http://127.0.0.1:26010"
        assert saved_configs == []
    finally:
        page.close()
        page.deleteLater()


def test_settings_save_validator_rejects_before_persistence_and_stays_dirty(
    qapp: QApplication,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    saved_configs: list[dict] = []
    outcomes: list[bool] = []
    page.settings_saved.connect(saved_configs.append)
    page.save_finished.connect(outcomes.append)
    page.set_save_validator(lambda _draft: "Stop the Game server. Nothing was saved.")
    page.show()
    qapp.processEvents()

    try:
        page.proxy_url_edit.setText("http://127.0.0.1:26010")
        page.save_settings()

        assert config.load()["proxy_url"] == "http://127.0.0.1:26002"
        assert saved_configs == []
        assert outcomes == [False]
        assert page.is_dirty() is True
        feedback = page.findChild(QLabel, "settingsSaveFeedback")
        assert feedback is not None
        assert feedback.text() == "Stop the Game server. Nothing was saved."
    finally:
        page.close()
        page.deleteLater()


def test_proxy_url_field_explains_its_local_evejs_role(
    qapp: QApplication,
    isolated_config: Path,
) -> None:
    page = SettingsPage()
    try:
        tooltip = page.proxy_url_edit.toolTip()

        assert "Local EveJS client-traffic proxy" in tooltip
        assert "http://127.0.0.1:26002" in tooltip
        assert page.proxy_url_edit.accessibleDescription() == tooltip
    finally:
        page.deleteLater()
