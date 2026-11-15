"""Owned tray UI; lifecycle commands remain in the existing launcher coordinators."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from src import config
from src.core.application_operations import active_mutations, update_active
from src.core.service_status import DockerControlPolicy, RuntimeBackend, ServiceState
from src.i18n import format_ui_phrase, translate_ui_phrase
from src.widgets.localized_dialogs import LocalizedMessageBox as QMessageBox

log = logging.getLogger(__name__)

CLOSE_BEHAVIORS = ("ask", "exit", "tray")
ACTIONS = {
    "start_stack": ("Start stack", "_start_all_servers"),
    "stop_stack": ("Shut down stack", "_stop_all_servers"),
    "start_server": ("Start server", "_start_server"),
    "stop_server": ("Stop server", "_stop_server"),
    "start_market": ("Start market", "_start_market"),
    "stop_market": ("Stop market", "_stop_market"),
}


def action_states(snapshot, *, busy: bool, configured: bool) -> dict[str, bool]:
    """Use cached observations only; opening the menu never probes a service."""
    disabled = dict.fromkeys(ACTIONS, False)
    if snapshot is None or busy or not configured:
        return disabled
    docker = snapshot.backend is RuntimeBackend.DOCKER_COMPOSE
    if docker and snapshot.docker_control_policy is not DockerControlPolicy.MANAGED:
        return disabled
    off = {ServiceState.OFFLINE, ServiceState.FAILED}
    if snapshot.game not in off | {ServiceState.ONLINE} or snapshot.market not in off | {ServiceState.ONLINE}:
        return disabled
    game_off, market_off = snapshot.game in off, snapshot.market in off
    game_owned = snapshot.game is ServiceState.ONLINE and (docker or snapshot.game_owned)
    market_owned = snapshot.market is ServiceState.ONLINE and (docker or snapshot.market_owned)
    can_start = not getattr(snapshot, "docker_mod_error", None)
    return {
        "start_stack": can_start and (game_off or market_off),
        "stop_stack": game_owned or (market_owned and game_off),
        "start_server": can_start and game_off,
        "stop_server": game_owned,
        "start_market": can_start and market_off,
        "stop_market": market_owned and game_off,
    }


class LauncherTray(QObject):
    """One icon/menu owned by one MainWindow, with an explicit real-exit path."""

    def __init__(self, window, *, available=None, icon_factory=QSystemTrayIcon):
        super().__init__(window)
        self.window = window
        self._available = available or QSystemTrayIcon.isSystemTrayAvailable
        self._force_exit = False
        self._asking = False
        self._restore_maximized = False
        self.icon = None
        self._icon_factory = icon_factory
        self.menu = QMenu(window)
        self.installation = self.menu.addAction("")
        self.installation.setEnabled(False)
        self.status = [self.menu.addAction(""), self.menu.addAction("")]
        for action in self.status:
            action.setEnabled(False)
        self.menu.addSeparator()
        self.open_action = self.menu.addAction("")
        self.open_action.triggered.connect(self.restore)
        self.menu.addSeparator()
        self.actions = {}
        for key in ACTIONS:
            action = self.menu.addAction("")
            action.triggered.connect(lambda _checked=False, name=key: self.dispatch(name))
            self.actions[key] = action
            if key in {"stop_stack", "stop_server"}:
                self.menu.addSeparator()
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction("")
        self.settings_action.triggered.connect(self.open_settings)
        self.quit_action = self.menu.addAction("")
        self.quit_action.triggered.connect(self.request_quit)
        self.menu.aboutToShow.connect(self.refresh)
        app = QApplication.instance()
        app.aboutToQuit.connect(self.shutdown)
        self.refresh()
        self._ensure_icon()

    def _ensure_icon(self):
        # Do not allocate native tray resources on a platform without a tray.
        # In particular, offscreen Qt must not own an unusable native icon.
        if not self._available():
            return False
        if self.icon is None:
            icon = self.window.windowIcon()
            if icon.isNull():
                icon = self.window.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            self.icon = self._icon_factory(icon, self)
            self.icon.setContextMenu(self.menu)
            self.icon.activated.connect(self._activated)
            self.icon.messageClicked.connect(self.restore)
            self.icon.setToolTip("EveJS Launcher")
        self.icon.show()
        return self.icon.isVisible() and not self.icon.icon().isNull()

    def _stamp(self):
        cfg = self.window._cfg
        return tuple(cfg.get(key) for key in (
            "active_installation_profile", "evejs_root", "runtime_backend",
            "docker_compose_file", "docker_project_name", "docker_control_policy", "game_port",
        ))

    def _busy(self):
        return bool(self.window.__dict__.get("_close_in_progress") or active_mutations(self.window)
                    or QApplication.activeModalWidget() is not None)

    def _states(self):
        snapshot = self.window.__dict__.get("_runtime_snapshot")
        cfg = self.window._cfg
        backend = cfg.get("runtime_backend", "native")
        if (cfg.get("installation_profile_error")
                or snapshot is None or snapshot.backend.value != backend
                or (backend == "docker_compose" and cfg.get("docker_control_policy") != "managed")):
            return dict.fromkeys(ACTIONS, False)
        return action_states(snapshot, busy=self._busy(), configured=bool(cfg.get("evejs_root")))

    def refresh(self):
        self._menu_stamp = self._stamp()
        cfg = self.window._cfg
        identity = cfg.get("active_installation_profile")
        entries = cfg.get("installation_profiles")
        entries = entries if isinstance(entries, list) else ()
        name = next((entry["name"] for entry in entries if isinstance(entry, dict)
                     and entry.get("id") == identity and isinstance(entry.get("name"), str)), "EveJS Launcher")
        self.installation.setText(format_ui_phrase("Installation: {name}", name=name))
        if self.icon is not None:
            self.icon.setToolTip("EveJS Launcher\n" + name)
        snapshot = self.window.__dict__.get("_runtime_snapshot")
        for action, role, label in zip(self.status, ("game", "market"), ("Server", "Market")):
            state = getattr(snapshot, role, ServiceState.UNKNOWN)
            action.setText(format_ui_phrase(label + ": {state}", state=translate_ui_phrase(state.value.title())))
        states = self._states()
        for key, action in self.actions.items():
            action.setText(translate_ui_phrase(ACTIONS[key][0]))
            action.setEnabled(states[key])
        self.open_action.setText(translate_ui_phrase("Open launcher"))
        self.settings_action.setText(translate_ui_phrase("Settings"))
        self.settings_action.setEnabled(not self._busy())
        self.quit_action.setText(translate_ui_phrase("Exit launcher"))
        self.quit_action.setEnabled(not update_active(self.window) and not self._asking)

    def restore(self):
        if self._restore_maximized or self.window.isMaximized():
            self.window.showMaximized()
        else:
            self.window.showNormal()
        self._restore_maximized = False
        self.window.raise_()
        self.window.activateWindow()

    def _activated(self, reason):
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.restore()

    def dispatch(self, key):
        # A menu opened for one world must never act on a newly selected one.
        if self._menu_stamp != self._stamp() or not self._states().get(key):
            return
        self.restore()
        getattr(self.window, ACTIONS[key][1])()

    def open_settings(self):
        if not self._busy():
            self.restore()
            self.window._open_settings_page()

    def request_quit(self):
        if self._asking or update_active(self.window):
            return
        self.restore()
        self._force_exit = True
        self.window.close()

    def _ask_behavior(self):
        box = QMessageBox(self.window)
        box.setWindowTitle("Close launcher")
        box.setText("What should the close button do?")
        box.setInformativeText("Remember this choice. You can change it in Settings. Minimizing keeps your setup running; exiting follows your existing shutdown settings.")
        exit_button = box.addButton("Exit launcher", QMessageBox.ButtonRole.AcceptRole)
        tray_button = box.addButton("Minimize to system tray", QMessageBox.ButtonRole.ActionRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return "exit" if box.clickedButton() is exit_button else "tray" if box.clickedButton() is tray_button else None

    def intercept_close(self, event):
        """Handle hiding before any of MainWindow's real teardown can execute."""
        app = QApplication.instance()
        explicit_exit = self._force_exit
        self._force_exit = False
        if explicit_exit or self.window.__dict__.get("_close_in_progress") or app.isSavingSession():
            return False
        if self._asking or update_active(self.window) or QApplication.activeModalWidget() is not None:
            event.ignore()
            return True
        behavior = self.window._cfg.get("close_behavior", "ask")
        if behavior not in CLOSE_BEHAVIORS:
            behavior = "ask"
        available = self._available()
        # Without a tray there is no choice to make, and offscreen/native tests
        # retain the established normal-close path without a surprise dialog.
        if behavior == "ask" and not available:
            return False
        if behavior == "ask":
            self._asking = True
            self.window._close_preference_request = object()
            try:
                behavior = self._ask_behavior()
                if behavior is None:
                    event.ignore()
                    return True
                cfg = config.load()
                cfg["close_behavior"] = behavior
                config.save(cfg)
                self.window._cfg["close_behavior"] = behavior
                settings = self.window.__dict__.get("_settings_page")
                if settings is not None:
                    settings.set_close_behavior(behavior)
            except (OSError, ValueError):
                log.exception("Unable to save launcher close preference")
                QMessageBox.warning(self.window, "Close preference not saved",
                                    "Could not save the close preference. Check the launcher logs and try again.")
                event.ignore()
                return True
            finally:
                self._asking = False
                self.window._close_preference_request = None
        if behavior == "exit":
            return False
        event.ignore()
        if not self._available():
            self.restore()
            QMessageBox.information(self.window, "System tray unavailable",
                                    "The system tray is unavailable. The launcher will stay open. Use Exit launcher in Settings to close normally.")
            return True
        if not self._ensure_icon():
            self.restore()
            return True
        self._restore_maximized = self.window.isMaximized()
        self.window.hide()
        return True

    def close_finished(self, event):
        if event.isAccepted():
            self.shutdown()

    def shutdown(self):
        if self.icon is not None:
            self.icon.hide()
