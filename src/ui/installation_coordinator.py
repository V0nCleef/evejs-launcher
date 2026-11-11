"""Named world selection, with no implicit service stop or data migration."""
from __future__ import annotations

from pathlib import Path
from PyQt6.QtCore import QUrl, QSize, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton, QLabel, QFileDialog, QSizePolicy, QToolButton, QMenu

from src import config
from src.core.installation_profiles import (
    ensure_profiles, select_profile, add_profile, rename_profile, remove_profile,
    InstallationProfileError,
)
from src.widgets.localized_dialogs import LocalizedInputDialog as QInputDialog, LocalizedMessageBox as QMessageBox
from src.widgets.ui_translation import register_translatable_widget_tree
from src.i18n import translate_ui_phrase


class InstallationBar(QWidget):
    presentation_changed = pyqtSignal()

    def __init__(self, window):
        super().__init__(window)
        self._launcher = window
        self.setObjectName("installationBar")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setStyleSheet("""
            QWidget#installationBar QPushButton { padding: 3px 8px; font-size: 12px; }
            QWidget#installationBar QComboBox { padding: 3px 22px 3px 8px; font-size: 12px; }
            QWidget#installationBar QLabel { font-size: 12px; }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(7)
        self.caption = QLabel("Installation")
        layout.addWidget(self.caption)
        self.selector = QComboBox()
        self.selector.setAccessibleName("Installation")
        self.selector.setMinimumWidth(120)
        self.selector.setMaximumWidth(180)
        layout.addWidget(self.selector)
        self.management_buttons = []
        for text, action in (("Add", self.add), ("Edit", window._open_settings_page), ("Rename", self.rename), ("Remove", self.remove)):
            button = QPushButton(text)
            button.clicked.connect(action)
            layout.addWidget(button)
            self.management_buttons.append(button)
        self.menu_button = QToolButton(self)
        self.menu_button.setText("⋯")
        self.menu_button.setFixedSize(28, 26)
        self.menu_button.setStyleSheet("background: #0d1823; color: #d7e5ec; border: 1px solid #233645; border-radius: 3px; font-size: 16px;")
        self.management_menu = QMenu(self.menu_button)
        for button in self.management_buttons:
            self.management_menu.addAction(button.text()).triggered.connect(button.click)
        self.menu_button.setMenu(self.management_menu)
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.menu_button)
        self.menu_button.hide()
        self._compact = False
        self.retry = QPushButton("Retry verification")
        self.retry.clicked.connect(self.retry_verification)
        self.stop = QPushButton("Stop Server")
        self.stop.clicked.connect(self.stop_server)
        layout.addWidget(self.retry)
        layout.addWidget(self.stop)
        self.logs = QPushButton("Open launcher logs")
        self.logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.CONFIG_DIR / "logs"))))
        layout.addWidget(self.logs)
        self.refresh()
        self.selector.currentIndexChanged.connect(self.select)
        register_translatable_widget_tree(self)

    def minimumSizeHint(self):
        # The footer can choose the compact representation before growing the window.
        return QSize(0, super().minimumSizeHint().height())

    def set_compact(self, compact):
        self._compact = bool(compact)
        self.caption.setVisible(not compact)
        for button, action in zip(self.management_buttons, self.management_menu.actions()):
            action.setText(button.text())
            action.setEnabled(button.isEnabled())
            button.setVisible(not compact)
        self.menu_button.setVisible(compact)
        self.menu_button.setAccessibleName(translate_ui_phrase("Installation"))
        self.menu_button.setToolTip(translate_ui_phrase("Installation"))

    def refresh(self):
        self.selector.blockSignals(True)
        try:
            cfg = ensure_profiles(self._launcher._cfg)
            if "installation_profiles" not in self._launcher._cfg:
                self._launcher._cfg.update(cfg)
            self.selector.setEnabled(True)
            entries = tuple((entry["name"],entry["id"]) for entry in cfg["installation_profiles"])
            if entries != getattr(self,"_displayed_entries",None):
                self.selector.clear()
                for name,identity in entries:
                    self.selector.addItem(name,identity)
                self._displayed_entries = entries
            self.selector.setCurrentIndex(self.selector.findData(cfg["active_installation_profile"]))
            self.selector.setToolTip(self.selector.currentText())
        except InstallationProfileError:
            self.selector.clear()
            self._displayed_entries = None
            self.selector.addItem("Invalid installation registry")
            self.selector.setEnabled(False)
        self.selector.blockSignals(False)
        target = getattr(getattr(self._launcher, "_runtime_snapshot", None), "target_identity", None)
        pending = target in getattr(self._launcher, "_deferred_docker_mod_verification", {})
        self.retry.setVisible(pending)
        self.stop.setVisible(pending)
        self.presentation_changed.emit()

    def rejection(self):
        w = self._launcher
        if callable(getattr(w, "_lifecycle_active", None)) and w._lifecycle_active():
            return "Wait for the current server operation before changing installations."
        if any(getattr(w, name, None) is not None for name in (
            "_mod_operation_request", "_client_launch_thread", "_launch_queue", "_character_creation_thread",
            "_character_deletion_thread", "_overview_patch_thread", "_mod_lifecycle_lease",
            "_docker_character_token", "_docker_tool_token")):
            return "Wait for the current launcher operation before changing installations."
        if getattr(getattr(w, "_tracker", None), "running_count", 0):
            return "Close the tracked clients before switching installations."
        for name in ("_server_proc", "_market_proc"):
            process = getattr(w, name, None)
            if process is not None and process.poll() is None:
                return "Stop the managed Native services before switching installations. No service was stopped automatically."
        settings = getattr(w, "_settings_page", None)
        if settings is not None and settings.is_dirty():
            return "Save or discard the Settings draft before switching installations."
        return None

    def select(self, index):
        identity = self.selector.itemData(index)
        if not identity or identity == self._launcher._cfg.get("active_installation_profile"):
            return
        reason = self.rejection()
        if reason:
            self.refresh()
            QMessageBox.information(self._launcher, "Installation", reason)
            return
        self._apply(select_profile(self._launcher._cfg, identity))

    def _apply(self, cfg):
        config.save(cfg)
        self._launcher._on_settings_saved(cfg)
        settings = getattr(self._launcher, "_settings_page", None)
        if settings is not None:
            settings.load_settings()
        self.refresh()

    def add(self):
        name, accepted = QInputDialog.getText(self._launcher, "Installation", "Name")
        if not accepted:
            return
        root = QFileDialog.getExistingDirectory(self._launcher, "Choose EveJS installation")
        if not root:
            return
        try:
            cfg = add_profile(self._launcher._cfg, name, {
                "evejs_root": root, "client_path": self._launcher._cfg.get("client_path", ""),
            })
            config.save(cfg)
            self._launcher._cfg.update(cfg)
            self.refresh()
        except (InstallationProfileError, OSError) as exc:
            QMessageBox.warning(self._launcher, "Installation", str(exc))

    def rename(self):
        identity = self._launcher._cfg.get("active_installation_profile", "default")
        name, accepted = QInputDialog.getText(self._launcher, "Installation", "Name")
        if accepted:
            try:
                self._apply(rename_profile(self._launcher._cfg, identity, name))
            except (InstallationProfileError, OSError) as exc:
                QMessageBox.warning(self._launcher, "Installation", str(exc))

    def remove(self):
        entries = self._launcher._cfg.get("installation_profiles", [])
        names = [entry["name"] for entry in entries if entry["id"] != self._launcher._cfg.get("active_installation_profile")]
        if not names:
            QMessageBox.information(self._launcher, "Installation", "Select another installation before removing this profile.")
            return
        name, accepted = QInputDialog.getItem(self._launcher, "Remove installation profile", "Remove registration only; files and saves are preserved.", names, 0, False)
        if accepted:
            identity = next(entry["id"] for entry in entries if entry["name"] == name and entry["id"] != self._launcher._cfg.get("active_installation_profile"))
            self._apply(remove_profile(self._launcher._cfg, identity))

    def retry_verification(self):
        target = getattr(getattr(self._launcher, "_runtime_snapshot", None), "target_identity", None)
        entry = getattr(self._launcher, "_deferred_docker_mod_verification", {}).get(target)
        if entry:
            import time
            entry.update(auto_retry=True, floor=time.monotonic_ns())
            self._launcher._docker_observe_requested.emit()

    def stop_server(self):
        from src.core.runtime.docker_controller import DockerLifecycleAction
        self._launcher._begin_docker_lifecycle(DockerLifecycleAction.STOP_GAME)
