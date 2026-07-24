"""Settings page for EveJS Launcher V2.

Sections
--------
* General        — EveJS root, client path, proxy URL
* Launch         — stagger/autologin delays, window title, auto-start toggles
* UI             — animations toggle, hero rotation interval
* Hidden Accounts— list of hidden usernames with a "Show Selected" action
* Danger Zone    — delete all local launcher data

Values load from / save to :mod:`src.config`.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src import config
from src.constants import COLORS, APP_VERSION
from src.widgets.toggle_switch import ToggleSwitch


class SettingsPage(QWidget):
    """Application settings form."""

    settings_saved = pyqtSignal(dict)
    settings_update_check = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.load_settings()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        root = QVBoxLayout(container)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        title = QLabel("SETTINGS")
        title.setProperty("class", "title")
        root.addWidget(title)

        # ── General ──────────────────────────────────────────────────────────
        general_box = QGroupBox("General")
        general_form = QFormLayout(general_box)
        general_form.setSpacing(10)

        self.evejs_root_edit = QLineEdit()
        general_form.addRow("EveJS Root:", self._with_browse(self.evejs_root_edit, directory=True))

        self.client_path_edit = QLineEdit()
        general_form.addRow("EVE Client Path:", self._with_browse(self.client_path_edit, directory=False))

        self.proxy_url_edit = QLineEdit()
        general_form.addRow("Proxy URL:", self.proxy_url_edit)

        root.addWidget(general_box)

        # ── Launch ───────────────────────────────────────────────────────────
        launch_box = QGroupBox("Launch")
        launch_form = QFormLayout(launch_box)
        launch_form.setSpacing(10)

        self.stagger_delay_spin = QSpinBox()
        self.stagger_delay_spin.setRange(0, 30)
        self.stagger_delay_spin.setSuffix(" s")
        launch_form.addRow("Stagger Delay:", self.stagger_delay_spin)

        self.autologin_delay_spin = QSpinBox()
        self.autologin_delay_spin.setRange(0, 10)
        self.autologin_delay_spin.setSuffix(" s")
        launch_form.addRow("Autologin Delay:", self.autologin_delay_spin)

        self.autologin_title_edit = QLineEdit()
        launch_form.addRow("Autologin Window Title:", self.autologin_title_edit)

        self.auto_start_server_toggle = ToggleSwitch()
        launch_form.addRow("Auto-Start Server:", self.auto_start_server_toggle)

        self.auto_start_market_toggle = ToggleSwitch()
        launch_form.addRow("Auto-Start Market:", self.auto_start_market_toggle)

        root.addWidget(launch_box)

        # ── UI ───────────────────────────────────────────────────────────────
        ui_box = QGroupBox("UI")
        ui_form = QFormLayout(ui_box)
        ui_form.setSpacing(10)

        self.animations_toggle = ToggleSwitch()
        ui_form.addRow("Animations:", self.animations_toggle)

        self.hero_interval_spin = QSpinBox()
        self.hero_interval_spin.setRange(3, 30)
        self.hero_interval_spin.setSuffix(" s")
        ui_form.addRow("Hero Rotation Interval:", self.hero_interval_spin)

        root.addWidget(ui_box)

        # ── Updates ─────────────────────────────────────────────────────────
        updates_box = QGroupBox("Updates")
        updates_form = QFormLayout(updates_box)
        updates_form.setSpacing(10)

        self.version_label = QLabel(f"Version {APP_VERSION}")
        self.version_label.setStyleSheet(f"color: {COLORS['grey']}; font-size: 11px;")
        self.version_label.setCursor(Qt.CursorShape.ArrowCursor)
        updates_form.addRow("Current Version:", self.version_label)

        check_btn = QPushButton("Check for Updates")
        check_btn.setProperty("class", "primary")
        check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        check_btn.clicked.connect(self.settings_update_check.emit)
        updates_form.addRow("", check_btn)

        self.update_auto_check_toggle = ToggleSwitch()
        updates_form.addRow("Auto-Check for Updates:", self.update_auto_check_toggle)

        self.update_interval_spin = QSpinBox()
        self.update_interval_spin.setRange(1, 72)
        self.update_interval_spin.setSuffix(" h")
        updates_form.addRow("Check Interval:", self.update_interval_spin)

        self.last_checked_label = QLabel("Never")
        self.last_checked_label.setStyleSheet(f"color: {COLORS['grey']}; font-size: 11px;")
        updates_form.addRow("Last Checked:", self.last_checked_label)

        changelog_btn = QPushButton("View Changelog")
        changelog_btn.setProperty("class", "ghost")
        changelog_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        changelog_btn.clicked.connect(self._open_changelog)
        updates_form.addRow("", changelog_btn)

        root.addWidget(updates_box)

        # ── Hidden Accounts ──────────────────────────────────────────────────
        hidden_box = QGroupBox("Hidden Accounts")
        hidden_layout = QVBoxLayout(hidden_box)
        hidden_layout.setSpacing(8)

        self.hidden_list = QListWidget()
        self.hidden_list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {COLORS['deep_space']};
                border: 1px solid {COLORS['steel']};
                border-radius: 4px;
                color: {COLORS['white']};
            }}
            """
        )
        hidden_layout.addWidget(self.hidden_list)

        show_btn = QPushButton("Show Selected")
        show_btn.setProperty("class", "ghost")
        show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        show_btn.clicked.connect(self._show_selected_accounts)
        hidden_layout.addWidget(show_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addWidget(hidden_box)

        # ── Danger Zone ──────────────────────────────────────────────────────
        danger_box = QGroupBox("Danger Zone")
        danger_box.setStyleSheet(
            f"QGroupBox {{ border: 1px solid {COLORS['red']}; }}"
        )
        danger_layout = QVBoxLayout(danger_box)
        danger_layout.setSpacing(8)

        danger_label = QLabel(
            "Deleting all local data removes launcher settings, caches, and logs."
        )
        danger_label.setProperty("class", "secondary")
        danger_label.setWordWrap(True)
        danger_layout.addWidget(danger_label)

        delete_btn = QPushButton("Delete All Local Data")
        delete_btn.setProperty("class", "danger")
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.clicked.connect(self._delete_all_local_data)
        danger_layout.addWidget(delete_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addWidget(danger_box)

        # ── Save / Cancel ────────────────────────────────────────────────────
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "ghost")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.load_settings)
        buttons.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setProperty("class", "primary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.save_settings)
        buttons.addWidget(save_btn)

        root.addLayout(buttons)
        root.addStretch()

    def _with_browse(self, line_edit: QLineEdit, directory: bool) -> QWidget:
        """Wrap a QLineEdit with a Browse button in an HBox."""
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(line_edit, stretch=1)

        browse = QPushButton("Browse…")
        browse.setProperty("class", "ghost")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        if directory:
            browse.clicked.connect(lambda: self._browse_directory(line_edit))
        else:
            browse.clicked.connect(lambda: self._browse_file(line_edit))
        row.addWidget(browse)
        return wrapper

    # ── Browse helpers ───────────────────────────────────────────────────────
    def _browse_directory(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Folder", target.text())
        if path:
            target.setText(path)

    def _browse_file(self, target: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File", target.text(), "Executables (*.exe);;All Files (*)"
        )
        if path:
            target.setText(path)

    # ── Load / Save ──────────────────────────────────────────────────────────
    def load_settings(self) -> None:
        """Populate the form from the persisted config."""
        cfg = config.load()

        self.evejs_root_edit.setText(str(cfg.get("evejs_root", "")))
        self.client_path_edit.setText(str(cfg.get("client_path", "")))
        self.proxy_url_edit.setText(str(cfg.get("proxy_url", "")))

        self.stagger_delay_spin.setValue(int(cfg.get("stagger_delay_sec", 3)))
        self.autologin_delay_spin.setValue(int(cfg.get("autologin_delay_sec", 2)))
        self.autologin_title_edit.setText(str(cfg.get("autologin_window_title", "EVE")))
        self.auto_start_server_toggle.setChecked(bool(cfg.get("auto_start_server", False)))
        self.auto_start_market_toggle.setChecked(bool(cfg.get("auto_start_market", False)))

        self.animations_toggle.setChecked(bool(cfg.get("animations_enabled", True)))
        self.hero_interval_spin.setValue(int(cfg.get("hero_rotation_interval_sec", 6)))

        self.update_auto_check_toggle.setChecked(bool(cfg.get("update_auto_check", True)))
        self.update_interval_spin.setValue(int(cfg.get("update_check_interval_hours", 6)))

        last_checked = cfg.get("update_last_checked", "")
        self.last_checked_label.setText(last_checked[:16] if last_checked else "Never")

        self.hidden_list.clear()
        for username in cfg.get("hidden_accounts", []):
            self.hidden_list.addItem(str(username))

    def save_settings(self) -> None:
        """Persist the form values to config."""
        cfg = config.load()
        cfg.update(
            {
                "evejs_root": self.evejs_root_edit.text().strip(),
                "client_path": self.client_path_edit.text().strip(),
                "proxy_url": self.proxy_url_edit.text().strip(),
                "stagger_delay_sec": self.stagger_delay_spin.value(),
                "autologin_delay_sec": self.autologin_delay_spin.value(),
                "autologin_window_title": self.autologin_title_edit.text().strip(),
                "auto_start_server": self.auto_start_server_toggle.isChecked(),
                "auto_start_market": self.auto_start_market_toggle.isChecked(),
                "animations_enabled": self.animations_toggle.isChecked(),
                "hero_rotation_interval_sec": self.hero_interval_spin.value(),
                "update_auto_check": self.update_auto_check_toggle.isChecked(),
                "update_check_interval_hours": self.update_interval_spin.value(),
                "hidden_accounts": [
                    self.hidden_list.item(i).text()
                    for i in range(self.hidden_list.count())
                ],
            }
        )
        config.save(cfg)
        self.settings_saved.emit(cfg)

    # ── Update helpers ───────────────────────────────────────────────────────
    def _open_changelog(self) -> None:
        """Open CHANGELOG.md in the default text editor."""
        changelog_path = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"
        if changelog_path.exists():
            subprocess.Popen(["start", str(changelog_path)], shell=True)

    # ── Hidden accounts ──────────────────────────────────────────────────────
    def _show_selected_accounts(self) -> None:
        """Remove the selected usernames from the hidden list."""
        for item in self.hidden_list.selectedItems():
            self.hidden_list.takeItem(self.hidden_list.row(item))

    # ── Danger zone ──────────────────────────────────────────────────────────
    def _delete_all_local_data(self) -> None:
        confirm = QMessageBox.warning(
            self,
            "Delete All Local Data",
            "This will permanently delete launcher settings, caches, and logs.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        data_dir = Path(config.CONFIG_DIR)
        try:
            if data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)
            QMessageBox.information(
                self,
                "Deleted",
                "Local launcher data has been deleted.\n"
                "The application will now use default settings.",
            )
            self.load_settings()
        except OSError as exc:
            QMessageBox.critical(
                self, "Error", f"Failed to delete local data:\n{exc}"
            )
