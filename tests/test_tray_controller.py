"""Close, ownership, stale-menu and Settings regressions without desktop input."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QSystemTrayIcon

from src import config
from src.app import MainWindow
from src.core.service_status import DockerControlPolicy, RuntimeBackend, RuntimeSnapshot, ServiceState
from src.i18n import set_language, translate_ui_phrase
from src.pages.settings_page import SettingsPage
from src.translations_tray import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE
from src.ui.tray_controller import ACTIONS, LauncherTray, action_states

OFF = ServiceState.OFFLINE
ON = ServiceState.ONLINE


class FakeIcon(QObject):
    activated = pyqtSignal(object)
    messageClicked = pyqtSignal()

    def __init__(self, icon, parent):
        super().__init__(parent)
        self._icon, self.visible = icon, False

    def setContextMenu(self, menu):
        self.menu = menu

    def setToolTip(self, value):
        self.tooltip = value

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def isVisible(self):
        return self.visible

    def icon(self):
        return self._icon


@pytest.fixture
def tray(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.load()
    cfg.update(evejs_root=str(tmp_path), close_behavior="ask", update_auto_check=False)
    config.save(cfg)
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = cfg
    window._close_in_progress = False
    window._schedule_service_monitor_start = lambda: None
    window._console_panel = SimpleNamespace(isVisible=lambda: False)
    window._runtime_snapshot = RuntimeSnapshot(OFF, OFF, 0)
    calls = []
    window._close_launcher = lambda event: (calls.append("exit"), event.accept())
    for key, (_, method) in ACTIONS.items():
        setattr(window, method, lambda name=key: calls.append(name))
    window._open_settings_page = lambda: calls.append("settings")
    controller = LauncherTray(window, available=lambda: True, icon_factory=FakeIcon)
    window._tray_controller = controller
    window.show()
    yield window, controller, calls
    controller.shutdown()
    window.deleteLater()
    qapp.sendPostedEvents(window, QEvent.Type.DeferredDelete)
    set_language("en")


def test_first_close_remembers_tray_without_entering_shutdown(tray, monkeypatch):
    window, controller, calls = tray
    answers = []
    monkeypatch.setattr(controller, "_ask_behavior", lambda: answers.append(True) or "tray")
    assert not window.close()
    assert not window.isVisible() and controller.icon.visible
    assert calls == [] and not window._close_in_progress
    assert config.load()["close_behavior"] == "tray"
    controller.restore()
    assert window.isVisible()
    window.close()
    assert answers == [True] and calls == []


@pytest.mark.parametrize("answer", [None, "exit"])
def test_first_close_cancel_or_explicit_exit(tray, monkeypatch, answer):
    window, controller, calls = tray
    monkeypatch.setattr(controller, "_ask_behavior", lambda: answer)
    window.close()
    if answer is None:
        assert window.isVisible() and calls == []
        assert config.load()["close_behavior"] == "ask"
    else:
        assert calls == ["exit"] and not controller.icon.visible
        assert config.load()["close_behavior"] == "exit"


def test_explicit_quit_bypasses_minimize_preference(tray):
    window, controller, calls = tray
    window._cfg["close_behavior"] = "tray"
    window.close()
    controller.request_quit()
    assert calls == ["exit"] and not controller.icon.visible


def test_cancelled_shutdown_allows_later_minimize(tray):
    window, controller, calls = tray
    window._cfg["close_behavior"] = "tray"
    window._close_launcher = lambda event: event.ignore()
    controller.request_quit()
    assert not controller._force_exit and window.isVisible()
    window.close()
    assert not window.isVisible() and calls == []


def test_deferred_real_close_does_not_hide_instead(tray):
    window, controller, calls = tray
    window._cfg["close_behavior"] = "tray"
    def pending(event):
        window._close_in_progress = True
        event.ignore()
    window._close_launcher = pending
    controller.request_quit()
    assert window._close_in_progress
    window._close_launcher = lambda event: (calls.append("finished"), event.accept())
    window.close()
    assert calls == ["finished"] and not controller.icon.visible


def test_no_tray_fallback_never_strands_hidden_window(tray, monkeypatch):
    window, controller, calls = tray
    controller._available = lambda: False
    window.close()  # Unavailable tray: first-close choice remains unanswered.
    assert calls == ["exit"] and config.load()["close_behavior"] == "ask"
    controller.restore()
    window._cfg["close_behavior"] = "tray"
    warnings = []
    monkeypatch.setattr("src.ui.tray_controller.QMessageBox.information", lambda *args: warnings.append(args))
    window.close()
    assert window.isVisible() and len(warnings) == 1 and calls == ["exit"]


def test_preference_write_failure_preserves_window_and_old_setting(tray, monkeypatch):
    window, controller, calls = tray
    monkeypatch.setattr(controller, "_ask_behavior", lambda: "tray")
    def failed(_cfg):
        raise OSError("synthetic failure")
    monkeypatch.setattr(config, "save", failed)
    monkeypatch.setattr("src.ui.tray_controller.QMessageBox.warning", lambda *args: None)
    window.close()
    assert window.isVisible() and calls == [] and window._cfg["close_behavior"] == "ask"


def test_update_handoff_and_session_exit_bypass_no_operations(tray, qapp, monkeypatch):
    window, controller, calls = tray
    window._cfg["close_behavior"] = "tray"
    window._update_handoff_pending = True
    window.close()
    controller.request_quit()
    assert window.isVisible() and calls == []
    window._update_handoff_pending = False
    monkeypatch.setattr(qapp, "isSavingSession", lambda: True)
    window.close()
    assert calls == ["exit"]


def test_menu_routing_rechecks_busy_state_and_selected_world(tray):
    window, controller, calls = tray
    controller.actions["start_server"].trigger()
    assert calls == ["start_server"]
    window._lifecycle_thread = object()
    controller.actions["start_market"].trigger()
    assert calls == ["start_server"]
    window._lifecycle_thread = None
    window._cfg["evejs_root"] += "-changed"
    controller.actions["start_stack"].trigger()
    assert calls == ["start_server"]
    controller.refresh()
    controller.actions["start_stack"].trigger()
    assert calls[-1] == "start_stack"
    controller.open_settings()
    assert calls[-1] == "settings"


@pytest.mark.parametrize("state", [ServiceState.UNKNOWN, ServiceState.STARTING, ServiceState.STOPPING])
def test_uncertain_or_changing_runtime_disables_tray_commands(state):
    assert not any(action_states(RuntimeSnapshot(state, OFF, 0), busy=False, configured=True).values())


@pytest.mark.parametrize("busy,configured", [(True, True), (False, False)])
def test_mutations_or_missing_configuration_disable_lifecycle(busy, configured):
    assert not any(action_states(RuntimeSnapshot(OFF, OFF, 0), busy=busy, configured=configured).values())


def test_external_processes_and_market_dependencies_are_respected():
    snapshot = RuntimeSnapshot(ON, ON, 2, market_owned=True)
    states = action_states(snapshot, busy=False, configured=True)
    assert not states["stop_server"] and not states["stop_market"] and not states["stop_stack"]
    states = action_states(replace(snapshot, game_owned=True), busy=False, configured=True)
    assert states["stop_server"] and states["stop_stack"] and not states["stop_market"]
    states = action_states(replace(snapshot, game=OFF), busy=False, configured=True)
    assert states["stop_market"] and states["stop_stack"]


@pytest.mark.parametrize("policy", list(DockerControlPolicy))
def test_docker_policy_and_mod_drift_preserve_explicit_stop(policy):
    snapshot = RuntimeSnapshot(ON, OFF, 0, backend=RuntimeBackend.DOCKER_COMPOSE,
                               docker_control_policy=policy, docker_mod_error="stale mods")
    states = action_states(snapshot, busy=False, configured=True)
    if policy is DockerControlPolicy.CONNECT_ONLY:
        assert not any(states.values())
    else:
        assert states["stop_server"] and states["stop_stack"]
        assert not states["start_market"] and not states["start_stack"]


def test_first_close_updates_settings_without_erasing_draft(tray, monkeypatch):
    window, controller, calls = tray
    page = SettingsPage(window)
    window._settings_page = page
    page.proxy_url_edit.setText("http://127.0.0.1:39999")
    monkeypatch.setattr(controller, "_ask_behavior", lambda: "tray")
    window.close()
    assert page.close_behavior_combo.currentData() == "tray"
    assert page.proxy_url_edit.text().endswith(":39999") and page.is_dirty()
    page.save_settings()
    assert config.load()["close_behavior"] == "tray"
    assert config.load()["proxy_url"].endswith(":39999")


@pytest.mark.parametrize("choice", ["ask", "exit", "tray"])
def test_settings_saves_and_reloads_launcher_wide_choice(tray, choice):
    page = SettingsPage(tray[0])
    page.close_behavior_combo.setCurrentIndex(page.close_behavior_combo.findData(choice))
    page.save_settings()
    page.load_settings()
    assert config.load()["close_behavior"] == page.close_behavior_combo.currentData() == choice
    assert "close_behavior" not in config.load()["installation_profiles"][0]["settings"]


@pytest.mark.parametrize("language", UI_PHRASES_BY_LANGUAGE)
def test_all_tray_phrases_are_localized_and_format_safe(language):
    set_language(language)
    assert set(UI_PHRASES_BY_LANGUAGE[language]) == set(SOURCE_PHRASES)
    for source in SOURCE_PHRASES:
        translated = translate_ui_phrase(source)
        assert translated == UI_PHRASES_BY_LANGUAGE[language][source]
        assert translated.format(name="Fixture World", state="Fixture state")
    set_language("en")


@pytest.mark.parametrize("choice", ["exit", "tray", None])
def test_real_first_close_dialog_buttons_and_escape(tray, choice):
    controller = tray[1]
    def click():
        box = tray[0].findChild(QMessageBox)
        assert box is not None
        if choice is None:
            box.reject()
        else:
            expected = "Exit launcher" if choice == "exit" else "Minimize to system tray"
            next(button for button in box.buttons() if button.text() == expected).click()
    QTimer.singleShot(0, click)
    assert controller._ask_behavior() == choice


def test_restore_preserves_maximized_window_and_icon_activation(tray):
    window, controller, _ = tray
    window._cfg["close_behavior"] = "tray"
    window.showMaximized()
    window.close()
    controller._activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert window.isVisible() and window.isMaximized()


def test_failed_deferred_settings_save_does_not_latch_quit(tray):
    window, controller, calls = tray
    window._cfg["close_behavior"] = "tray"
    window._pending_settings_intent = ("close", None)
    window._close_launcher = lambda event: event.ignore()
    controller.request_quit()
    window._on_settings_save_finished(False)
    window.close()
    assert not window.isVisible() and calls == []


def test_successful_deferred_settings_save_resumes_real_exit(tray, qapp):
    window, controller, calls = tray
    window._cfg["close_behavior"] = "tray"
    window._pending_settings_intent = ("close", None)
    window._on_settings_save_finished(True)
    qapp.processEvents()
    assert calls == ["exit"] and not controller.icon.visible


def test_full_window_hides_without_teardown_then_quits_cleanly(qapp, tmp_path, monkeypatch, close_main_window):
    from src import app as app_module
    import src.ui.tray_controller as tray_module
    from src.ui.mod_update_controller import ModUpdateController
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.load()
    cfg.update(evejs_root=str(tmp_path), update_auto_check=False, audio_master_muted=True, close_behavior="ask")
    config.save(cfg)
    monkeypatch.setattr(ModUpdateController, "check", lambda self, **kwargs: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **kwargs: False)
    monkeypatch.setattr(app_module, "load_accounts", lambda root: [])
    monkeypatch.setattr(MainWindow, "_schedule_service_monitor_start", lambda self: None)
    monkeypatch.setattr(tray_module, "LauncherTray", lambda window: LauncherTray(window, available=lambda: True, icon_factory=FakeIcon))
    window = MainWindow()
    window.show()
    controller = window._tray_controller
    monkeypatch.setattr(controller, "_ask_behavior", lambda: "tray")
    window.close()
    assert not window.isVisible() and not window._close_in_progress
    assert window._status_timer.isActive() and window._prune_timer.isActive()
    controller.restore()
    window._cfg["close_behavior"] = "exit"
    close_main_window(window)


@pytest.mark.parametrize("bad", [None, True, [], {}, "unknown"])
def test_invalid_saved_close_preference_is_read_only_normalized(tray, bad):
    raw = config.CONFIG_FILE.read_text(encoding="utf-8")
    import json
    payload = json.loads(raw)
    payload["close_behavior"] = bad
    config.CONFIG_FILE.write_text(json.dumps(payload), encoding="utf-8")
    before = config.CONFIG_FILE.read_bytes()
    assert config.load()["close_behavior"] == "ask"
    assert config.CONFIG_FILE.read_bytes() == before


def test_first_close_choice_defers_automatic_update_until_dialog_finishes(tray, monkeypatch):
    from src.core.application_operations import active_mutations, update_blocked
    window, controller, _ = tray
    def choose():
        assert update_blocked(window)
        assert "close_preference" in active_mutations(window)
        return None
    monkeypatch.setattr(controller, "_ask_behavior", choose)
    event = QCloseEvent()
    assert controller.intercept_close(event)
    assert not update_blocked(window) and window._close_preference_request is None


def test_stale_backend_or_changed_connect_only_policy_cannot_enable_actions(tray):
    window, controller, calls = tray
    window._cfg.update(runtime_backend="docker_compose", docker_control_policy="managed")
    controller.refresh()
    assert not any(action.isEnabled() for action in controller.actions.values())
    window._runtime_snapshot = replace(window._runtime_snapshot, backend=RuntimeBackend.DOCKER_COMPOSE,
                                        docker_control_policy=DockerControlPolicy.MANAGED)
    controller.refresh()
    assert controller.actions["start_stack"].isEnabled()
    window._cfg["docker_control_policy"] = "connect_only"
    controller.actions["start_stack"].trigger()
    controller.refresh()
    assert not any(action.isEnabled() for action in controller.actions.values()) and calls == []


@pytest.mark.parametrize("registry", [None, "invalid", [None, "invalid"]])
def test_invalid_registry_retains_access_to_launcher_and_exit(tray, registry):
    window, controller, _ = tray
    window._cfg.update(installation_profiles=registry, installation_profile_error="invalid registry")
    controller.refresh()
    assert not any(action.isEnabled() for action in controller.actions.values())
    assert controller.open_action.isEnabled() and controller.quit_action.isEnabled()
