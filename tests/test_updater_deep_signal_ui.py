"""Focused visual-state contracts for the Deep Signal updater surfaces."""
from __future__ import annotations

from pathlib import Path

import pytest

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QDialog

from src.i18n import format_ui_phrase, set_language, translate_ui_phrase
from src.updater.dialog import UpdateDialog
from src.updater import handoff
from src.updater.handoff import UpdateHandoff, UpdateHandoffWindow
from src.updater.progress_dialog import UpdateProgressDialog


def _phase_states(dialog: UpdateProgressDialog) -> list[str]:
    return [str(frame.property("phaseState")) for frame in dialog._phase_frames]


def test_available_update_uses_release_uplink_hierarchy_without_losing_choices(
    qapp,
) -> None:
    dialog = UpdateDialog(
        "1.0.32",
        "v1.0.33",
        "# Changes\n\n- Safer update handoff",
        "https://example.invalid/EveJS-Launcher-V1.zip",
        "2026-08-13T12:00:00Z",
    )
    try:
        assert dialog.objectName() == "updateAvailableDialog"
        assert dialog.property("deepSignal") is True
        assert dialog.accessibleName() == "Launcher update available"
        assert dialog.width() >= dialog.minimumSizeHint().width()
        assert dialog.current_version_label.text() == "1.0.32"
        assert dialog.new_version_label.text() == "1.0.33"
        assert dialog.hero_banner.pixmap() is not None
        assert not dialog.hero_banner.pixmap().isNull()
        assert "Safer update handoff" in dialog._changelog_view.toPlainText()
        assert dialog._skip_btn.text() == "Skip This Version"
        assert dialog._remind_btn.text() == "Remind Me Later"
        # Qt uses a doubled ampersand to render the literal label character.
        assert dialog._install_btn.text() == "Download && Install"
        assert dialog._skip_btn.property("class") == "ghost"
        assert dialog._remind_btn.property("class") == "secondary"
        assert dialog._install_btn.property("class") == "primary"

        dialog._skip_btn.click()
        assert dialog.skip_requested is True
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()


def test_available_update_preserves_remind_and_install_results(qapp) -> None:
    def make_dialog() -> UpdateDialog:
        return UpdateDialog(
            "1.0.32",
            "v1.0.33",
            "# Changes",
            "https://example.invalid/EveJS-Launcher-V1.zip",
            "2026-08-13T12:00:00Z",
        )

    remind_dialog = make_dialog()
    try:
        remind_dialog._remind_btn.click()
        assert remind_dialog.result() == QDialog.DialogCode.Rejected
        assert remind_dialog.skip_requested is False
    finally:
        remind_dialog.close()

    install_dialog = make_dialog()
    try:
        install_dialog._install_btn.click()
        assert install_dialog.result() == QDialog.DialogCode.Accepted
        assert install_dialog.skip_requested is False
    finally:
        install_dialog.close()


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_available_update_localizes_dynamic_release_framing(
    qapp,
    language: str,
) -> None:
    set_language(language)
    changelog = "# Settings\n\nKeep Market and 用户 values verbatim."
    dialog = UpdateDialog(
        "1.0.32",
        "v1.0.33",
        changelog,
        "https://example.invalid/EveJS-Launcher-V1.zip",
        "2026-08-13T12:00:00Z",
    )
    try:
        expected_header = format_ui_phrase(
            "What's new in v{version}",
            version="1.0.33",
        )
        expected_date = format_ui_phrase(
            "Released: {date}",
            date=dialog._format_date("2026-08-13T12:00:00Z"),
        )
        assert dialog.changelog_header.text() == expected_header
        assert dialog.changelog_header.text() != "What's new in v1.0.33"
        assert dialog.date_label.text() == expected_date
        assert not dialog.date_label.text().startswith("Released:")
        assert "Settings" in dialog._changelog_view.toPlainText()
        assert "Market" in dialog._changelog_view.toPlainText()
        assert "用户" in dialog._changelog_view.toPlainText()
        assert dialog._install_btn.text() == translate_ui_phrase(
            "Download && Install"
        )
    finally:
        set_language("en")
        dialog.close()


def test_available_update_localizes_empty_changelog_fallback(qapp) -> None:
    set_language("ja")
    dialog = UpdateDialog(
        "1.0.32",
        "v1.0.33",
        "",
        "https://example.invalid/EveJS-Launcher-V1.zip",
        "2026-08-13T12:00:00Z",
    )
    try:
        assert dialog._changelog_view.toPlainText() == translate_ui_phrase(
            "No changelog provided."
        )
        assert dialog._changelog_view.toPlainText() != "No changelog provided."
    finally:
        set_language("en")
        dialog.close()


def test_available_update_keeps_actions_visible_on_low_height_screen(qapp) -> None:
    dialog = UpdateDialog(
        "1.0.32",
        "v1.0.33",
        "# Changes\n\n" + "- Release note\n" * 30,
        "https://example.invalid/EveJS-Launcher-V1.zip",
        "2026-08-13T12:00:00Z",
    )
    try:
        dialog._fit_to_available_screen(600)
        dialog.show()
        qapp.processEvents()

        assert dialog.height() <= 600 - dialog._SCREEN_MARGIN
        assert dialog.height() >= dialog.minimumSizeHint().height()
        for button in (
            dialog._skip_btn,
            dialog._remind_btn,
            dialog._install_btn,
        ):
            bottom_right = button.mapTo(dialog, button.rect().bottomRight())
            assert bottom_right.y() < dialog.height()
            assert button.isVisibleTo(dialog)
        assert dialog._changelog_view.verticalScrollBar().maximum() > 0
    finally:
        dialog.close()


def test_progress_surface_tracks_download_install_and_restart_states(qapp) -> None:
    dialog = UpdateProgressDialog("v1.0.33")
    try:
        assert dialog.objectName() == "updateProgressDialog"
        assert dialog.property("deepSignal") is True
        assert len(dialog._phase_frames) == 4
        assert dialog.width() >= dialog.minimumSizeHint().width()

        dialog.set_download_progress(2 * 1024 * 1024, 4 * 1024 * 1024)
        assert dialog.status_label.text() == "Downloading update"
        assert dialog.detail_label.text() == "2.0 MB of 4.0 MB"
        assert dialog.progress_bar.value() == 50
        assert _phase_states(dialog) == ["active", "pending", "pending", "pending"]

        dialog.set_copy_progress(7, 10)
        assert dialog.status_label.text() == "Installing update"
        assert dialog.detail_label.text() == "Copied 7 of 10 files"
        assert dialog.progress_bar.value() == 70
        assert dialog.state_badge.text() == "INSTALL HANDOFF"
        assert _phase_states(dialog) == ["complete", "complete", "active", "pending"]

        dialog.set_stage("restart", "Restarting EveJS Launcher…")
        assert dialog.status_label.text() == "Restarting launcher"
        assert dialog.detail_label.text() == "Restarting EveJS Launcher…"
        assert dialog.state_badge.property("state") == "restart"
        assert dialog.hero_state_label.text() == "RESTART SEQUENCE"
        assert _phase_states(dialog) == ["complete", "complete", "complete", "active"]
    finally:
        dialog.allow_close()
        dialog.close()


def test_progress_failure_is_visible_and_dismissable(qapp) -> None:
    dialog = UpdateProgressDialog("v1.0.33")
    try:
        dialog.set_stage("prepare", "Verifying package…")
        dialog.show_error("Package verification failed.")

        assert dialog.status_label.text() == "Update could not finish"
        assert dialog.detail_label.text() == "Package verification failed."
        assert dialog.status_panel.property("state") == "error"
        assert dialog.progress_bar.property("state") == "error"
        assert dialog.progress_bar.value() == 100
        assert dialog.state_badge.text() == "LINK FAILURE"
        assert dialog._close_button.isVisibleTo(dialog)
        assert _phase_states(dialog) == ["complete", "error", "pending", "pending"]
    finally:
        dialog.allow_close()
        dialog.close()


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_progress_rail_and_formatted_progress_localize_without_touching_values(
    qapp,
    language: str,
) -> None:
    set_language(language)
    dialog = UpdateProgressDialog("v1.0.33")
    try:
        sources = ("DOWNLOAD", "PREPARE", "INSTALL", "RESTART")
        rendered = [label.text() for label in dialog._phase_labels]
        assert rendered == [translate_ui_phrase(source) for source in sources]
        assert rendered != list(sources)

        dialog.set_download_progress(2 * 1024 * 1024, 4 * 1024 * 1024)
        assert dialog.detail_label.text() == format_ui_phrase(
            "{downloaded} of {total}",
            downloaded="2.0 MB",
            total="4.0 MB",
        )

        raw_error = r"C:\用户\EveJS\package Settings checksum failed"
        dialog.show_error(raw_error)
        assert dialog.detail_label.text() == raw_error
    finally:
        set_language("en")
        dialog.allow_close()
        dialog.close()


@pytest.mark.parametrize(
    ("language", "handoff_text"),
    [
        ("en", "Switching to the standalone updater…"),
        ("zh_CN", "正在切换到独立更新程序…"),
        ("ja", "スタンドアロンアップデーターに切り替え中…"),
        ("ko", "독립 실행형 업데이터로 전환 중…"),
        ("fr", "Passage à l’outil de mise à jour autonome…"),
        ("de", "Wechsel zum eigenständigen Aktualisierungsprogramm…"),
        ("nl", "Overschakelen naar het zelfstandige updateprogramma…"),
        ("ru", "Переход к отдельной программе обновления…"),
    ],
)
def test_handoff_stages_localize_countdown_but_preserve_values_and_diagnostics(
    qapp,
    language: str,
    handoff_text: str,
) -> None:
    set_language(language)
    dialog = UpdateProgressDialog("v1.0.33")
    try:
        dialog.set_stage("install", "Switching to the standalone updater…")
        assert dialog.detail_label.text() == handoff_text

        rendered_countdown: list[str] = []

        def render_stage(stage: str, detail: str) -> None:
            dialog.set_stage(stage, detail)
            rendered_countdown.append(dialog.detail_label.text())

        handoff._wait_for_file_locks_to_settle(
            2,
            render_stage,
            lambda _seconds: None,
        )
        assert rendered_countdown == [
            format_ui_phrase(
                "Releasing old launcher files "
                "({seconds_remaining} seconds remaining)…",
                seconds_remaining=2,
            ),
            format_ui_phrase(
                "Releasing old launcher files "
                "({seconds_remaining} second remaining)…",
                seconds_remaining=1,
            ),
        ]

        # This intentionally resembles a short reviewed template.  Stage
        # diagnostics are not trusted UI framing and must not be reverse-
        # translated merely because they happen to start with ``Using:``.
        raw_diagnostic = r"Using: C:\Users\Pilot\Settings"
        dialog.set_stage("install", raw_diagnostic)
        assert dialog.detail_label.text() == raw_diagnostic
    finally:
        set_language("en")
        dialog.allow_close()
        dialog.close()


def test_progress_surface_blocks_close_until_the_update_is_terminal(qapp) -> None:
    dialog = UpdateProgressDialog("v1.0.33")
    try:
        blocked = QCloseEvent()
        dialog.closeEvent(blocked)
        assert blocked.isAccepted() is False

        dialog.allow_close()
        permitted = QCloseEvent()
        dialog.closeEvent(permitted)
        assert permitted.isAccepted() is True
    finally:
        dialog.allow_close()
        dialog.close()


def test_handoff_window_identifies_the_standalone_update_agent(qapp) -> None:
    window = UpdateHandoffWindow(
        UpdateHandoff(
            target_dir=Path("installed/EveJS-Launcher-V1"),
            source_dir=Path("staged/EveJS-Launcher-V1"),
            exe_name="EveJS-Launcher-V1.exe",
            parent_pid=12345,
        ),
        "v1.0.33",
    )
    try:
        assert window.property("handoffMode") is True
        assert window.context_label.text() == "DEEP SIGNAL // UPDATE AGENT"
        assert window.window_heading.text() == "Applying launcher update"
        assert window.status_label.text() == "Installing update"
        assert window.detail_label.text() == "Waiting for the launcher to close…"
        assert window.state_badge.text() == "INSTALL HANDOFF"
        assert _phase_states(window) == ["complete", "complete", "active", "pending"]
    finally:
        window.allow_close()
        window.close()


def test_successful_handoff_advances_to_restart_before_quitting(
    qapp,
    monkeypatch,
) -> None:
    scheduled: list[tuple[int, object]] = []
    monkeypatch.setattr(
        "src.updater.handoff.QTimer.singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    window = UpdateHandoffWindow(
        UpdateHandoff(
            target_dir=Path("installed/EveJS-Launcher-V1"),
            source_dir=Path("staged/EveJS-Launcher-V1"),
            exe_name="EveJS-Launcher-V1.exe",
            parent_pid=12345,
        ),
        "v1.0.33",
    )
    try:
        window._result = (True, "")
        window._thread_finished = True
        window._finish_handoff_if_ready()

        assert window.status_label.text() == "Restarting launcher"
        assert window.detail_label.text() == "Restarting EveJS Launcher…"
        assert window.state_badge.property("state") == "restart"
        assert _phase_states(window) == [
            "complete",
            "complete",
            "complete",
            "active",
        ]
        assert len(scheduled) == 1
        assert scheduled[0][0] == 850
        assert scheduled[0][1] == window._close_after_restart
    finally:
        window.allow_close()
        window.close()
