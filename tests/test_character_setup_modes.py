"""Online setup must use normal login without entering offline maintenance."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication

from src.app import MainWindow
from src.core.character_creation import normalize_account_name
from src.core.launcher import ClientLaunchContext
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus
from src.i18n import set_language, translate_ui_phrase
from src.widgets.new_character_dialog import NewCharacterDialog


@pytest.fixture(autouse=True)
def _clean_dialogs(qapp: QApplication):
    set_language("en")
    yield
    set_language("en")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _dialog() -> NewCharacterDialog:
    return NewCharacterDialog(
        [], OverviewPatchStatus(OverviewPatchState.READY, "Ready"), set()
    )


def test_online_setup_only_needs_an_account_and_emits_login(qapp: QApplication) -> None:
    dialog = _dialog()
    try:
        offline = QSignalSpy(dialog.create_requested)
        online = QSignalSpy(dialog.account_login_requested)
        QTest.mouseClick(dialog.easy_button, Qt.MouseButton.LeftButton)
        dialog.account_edit.setText("  fixture-account  ")
        # Stale offline choices must not block or leak into normal login.
        dialog.character_edit.setText("\x7f")
        dialog.gm_check.setChecked(True)

        assert dialog.create_button.isEnabled()
        assert dialog.character_edit.isHidden()
        assert dialog.easy_button.title_label.text() == "Simple New Character"
        assert dialog.gm_check.isHidden()
        assert dialog.overview_card.isHidden()
        assert "running" in dialog.intro_label.text()
        QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

        assert list(online) == [["fixture-account"]]
        assert not offline
    finally:
        dialog.deleteLater()


def test_switching_back_to_offline_restores_character_requirements(qapp: QApplication) -> None:
    dialog = _dialog()
    try:
        offline = QSignalSpy(dialog.create_requested)
        online = QSignalSpy(dialog.account_login_requested)
        dialog.account_edit.setText("fixture-account")
        QTest.mouseClick(dialog.easy_button, Qt.MouseButton.LeftButton)
        assert dialog.create_button.isEnabled()

        QTest.mouseClick(dialog.advanced_button, Qt.MouseButton.LeftButton)
        assert not dialog.create_button.isEnabled()
        assert not dialog.character_edit.isHidden()
        assert not dialog.overview_card.isHidden()
        dialog.character_edit.setText("Fixture Pilot")
        QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

        assert len(offline) == 1
        assert offline[0][0].character_name == "Fixture Pilot"
        assert not online
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("username", ["", "../escape", "bad/name"])
def test_online_setup_rejects_unsafe_profile_names(qapp: QApplication, username: str) -> None:
    dialog = _dialog()
    try:
        QTest.mouseClick(dialog.easy_button, Qt.MouseButton.LeftButton)
        dialog.account_edit.setText(username)
        assert not dialog.create_button.isEnabled()
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("username", [None, "", "../escape", "a" * 33])
def test_account_name_validation_also_protects_non_widget_callers(username: object) -> None:
    assert normalize_account_name(username) is None


@pytest.mark.parametrize("game_owned", [True, False])
def test_online_login_keeps_running_services_and_other_clients(
    qapp: QApplication, game_owned: bool
) -> None:
    calls = []

    def unexpected(*_args, **_kwargs):
        pytest.fail("Online setup entered offline maintenance or stopped a service")

    dialog = SimpleNamespace(accept=lambda: calls.append("accepted"))
    window = SimpleNamespace(
        _new_character_dialog=dialog,
        _close_in_progress=False,
        _cfg={"evejs_root": "C:/Games/EveJS", "auto_start_server": True,
              "auto_start_market": True},
        _server_process_alive=lambda: game_owned,
        _native_game_running=lambda: True,
        _is_market_running=lambda: True,
        _lifecycle_active=lambda: False,
        _mod_removal_conflict_active=lambda: False,
        _docker_mode=lambda: False,
        _pending_client_launches=set(),
        _launch_queue=None,
        _client_launch_thread=None,
        _tracker=SimpleNamespace(
            running_count=2,
            is_account_running=lambda _username: False,
        ),
        _run_stop_sequence=unexpected,
        _begin_character_creation_worker=unexpected,
        _start_service_sequence=unexpected,
        _start_client_launch=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    window._ensure_server_if_needed = lambda callback: MainWindow._ensure_server_if_needed(window, callback)
    window._launch_character_after_services = lambda *args: MainWindow._launch_character_after_services(window, *args)
    window._on_character_launch = lambda *args: MainWindow._on_character_launch(window, *args)

    MainWindow._on_new_account_login(window, "fixture-account")

    assert calls == ["accepted", (("fixture-account", ""), {"show_errors": True})]


def test_online_setup_waits_for_maintenance(qapp: QApplication) -> None:
    errors = []
    window = SimpleNamespace(
        _new_character_dialog=SimpleNamespace(show_error=errors.append),
        _close_in_progress=False,
        _lifecycle_active=lambda: False,
        _mod_removal_conflict_active=lambda: True,
    )

    MainWindow._on_new_account_login(window, "fixture-account")

    assert errors == ["Wait for the current launcher operation to finish."]


@pytest.mark.parametrize("enabled", [True, False])
def test_account_only_launch_preserves_auto_login_preference(
    qapp: QApplication, enabled: bool,
) -> None:
    window = SimpleNamespace(
        _cfg={"evejs_root": "C:/Games/EveJS", "client_path": "C:/Games/EVE/tq",
              "auto_login_enabled": enabled},
        _resolve_client_launch_context=lambda: (ClientLaunchContext.native(), ""),
        _resolve_configured_client_path=lambda client, _root: Path(client),
        _tracker=SimpleNamespace(is_account_running=lambda _username: False),
        _pending_client_launches=set(),
        _docker_mode=lambda: False,
    )

    request = MainWindow._make_client_launch_request(window, "fixture-account", "")

    assert request is not None
    assert request.character_id is None
    assert request.auto_login_enabled is enabled
    assert request.hide_console is True
    assert request.overview_bridge is None


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_setup_choices_translate_without_changing_account_input(
    qapp: QApplication, language: str
) -> None:
    set_language(language)
    dialog = _dialog()
    try:
        dialog.account_edit.setText("fixture-account")
        QTest.mouseClick(dialog.easy_button, Qt.MouseButton.LeftButton)
        source = "Simple New Character"

        assert dialog.easy_button.title_label.text() == translate_ui_phrase(source)
        assert dialog.easy_button.title_label.text() != source
        assert dialog.create_button.text() == translate_ui_phrase("LAUNCH CLIENT")
        assert dialog.account_edit.text() == "fixture-account"
        assert dialog.create_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_maintenance_disables_setup_choice_until_finished(qapp: QApplication) -> None:
    dialog = _dialog()
    try:
        dialog.set_busy(True)
        assert not dialog.easy_button.isEnabled()
        assert not dialog.advanced_button.isEnabled()
        assert not dialog.create_button.isEnabled()
        dialog.set_busy(False)
        assert dialog.easy_button.isEnabled()
        assert dialog.advanced_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_creation_methods_are_exclusive_icon_buttons_with_easy_default(qapp: QApplication) -> None:
    dialog = _dialog()
    try:
        assert not hasattr(dialog, "setup_combo")
        assert dialog.setup_mode == "online"
        assert dialog.easy_button.isChecked()
        assert not dialog.advanced_button.isChecked()
        for button in (dialog.easy_button, dialog.advanced_button):
            assert button.isCheckable()
            assert button.accessibleName()
            assert not button.icon_label.pixmap().isNull()
        dialog.account_edit.setText("fixture-account")
        dialog.advanced_button.setFocus()
        QTest.keyClick(dialog.advanced_button, Qt.Key.Key_Space)
        assert dialog.setup_mode == "offline"
        assert not dialog.easy_button.isChecked()
        assert dialog.advanced_button.isChecked()
        assert dialog.account_edit.text() == "fixture-account"
        QTest.keyClick(dialog.advanced_button, Qt.Key.Key_Space)
        assert dialog.advanced_button.isChecked()
    finally:
        dialog.deleteLater()


def test_advanced_form_keeps_footer_reachable_on_short_windows(qapp: QApplication) -> None:
    dialog = _dialog()
    try:
        QTest.mouseClick(dialog.advanced_button, Qt.MouseButton.LeftButton)
        dialog.resize(560, 620)
        dialog.show()
        qapp.processEvents()
        assert dialog.height() <= 620
        assert dialog.create_button.isVisible()
        assert dialog.create_button.mapTo(dialog, dialog.create_button.rect().bottomRight()).y() < dialog.height()
        assert dialog.form_scroll.isVisible()
        assert dialog.form_scroll.verticalScrollBar().maximum() > 0
        assert dialog.easy_button.geometry().right() < dialog.advanced_button.geometry().left()
    finally:
        dialog.close()
        dialog.deleteLater()
