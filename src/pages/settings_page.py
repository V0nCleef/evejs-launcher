"""Settings page for EveJS Launcher V2.

Sections
--------
* General        — EveJS root, client path, proxy URL
* Launch         — stagger delay, auto-start toggles
* UI             — animations toggle, hero rotation interval
* Hidden Characters— list of hidden character names with a "Show Selected" action
* Danger Zone    — delete all local launcher data

Values load from / save to :mod:`src.config`.
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QComboBox,
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


class FocusWheelSpinBox(QSpinBox):
    """A QSpinBox that only responds to the mouse wheel when it has focus.

    Standard QSpinBox consumes wheel events even when the cursor merely
    passes over it while the user is scrolling a parent QScrollArea,
    which hijacks the scroll and changes the spinbox value instead.
    When unfocused, this subclass ignores wheel events and lets the
    parent scroll area handle them normally.
    """

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()  # propagate to parent scroll area

from src import config
from src.constants import COLORS, APP_VERSION
from src.core.server_selection import (
    ASK_EVERY_TIME,
    discover_server_scripts,
    mode_for_script,
)
from src.widgets.toggle_switch import ToggleSwitch


class SettingsPage(QWidget):
    """Application settings form."""

    settings_saved = pyqtSignal(dict)
    settings_update_check = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stale_server_preference = ""
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
        self.evejs_root_edit.editingFinished.connect(self._on_evejs_root_edited)
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

        self.stagger_delay_spin = FocusWheelSpinBox()
        self.stagger_delay_spin.setRange(0, 30)
        self.stagger_delay_spin.setSuffix(" s")
        launch_form.addRow("Stagger Delay:", self.stagger_delay_spin)

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

        self.hero_interval_spin = FocusWheelSpinBox()
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

        self.check_btn = QPushButton("Check for Updates")
        self.check_btn.setProperty("class", "primary")
        self.check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_btn.clicked.connect(self._on_check_clicked)
        updates_form.addRow("", self.check_btn)

        self.update_auto_check_toggle = ToggleSwitch()
        updates_form.addRow("Auto-Check for Updates:", self.update_auto_check_toggle)

        self.update_interval_spin = FocusWheelSpinBox()
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

        # ── Server Start Scripts ─────────────────────────────────────────────
        scripts_box = QGroupBox("Server Start Scripts")
        scripts_box.setToolTip(
            "Select which server mode to use when starting the game server.\n"
            "The launcher detects StartServer*.bat files to determine the mode.\n"
            "StartServerWithMods.bat → modded (mods enabled)\n"
            "StartServer.bat → vanilla (no mods)\n"
            "The server is always launched via Node.js directly — the .bat is\n"
            "only used as a mode indicator, not executed."
        )
        scripts_layout = QFormLayout(scripts_box)
        scripts_layout.setSpacing(10)

        self.server_script_combo = QComboBox()
        self.server_script_combo.setMinimumWidth(300)
        self.server_script_combo.currentIndexChanged.connect(self._update_script_info)
        scripts_layout.addRow("Default:", self.server_script_combo)

        scripts_btn_row = QHBoxLayout()
        scripts_btn_row.setSpacing(8)

        rescan_btn = QPushButton("Rescan")
        rescan_btn.setProperty("class", "ghost")
        rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan_btn.clicked.connect(self._rescan_server_scripts)
        scripts_btn_row.addWidget(rescan_btn)

        scripts_btn_row.addStretch()
        scripts_layout.addRow("", scripts_btn_row)

        self.server_script_info = QLabel("")
        self.server_script_info.setStyleSheet(f"color: {COLORS['grey']}; font-size: 11px;")
        self.server_script_info.setWordWrap(True)
        scripts_layout.addRow("", self.server_script_info)

        root.addWidget(scripts_box)

        # ── Hidden Characters ────────────────────────────────────────────────
        hidden_box = QGroupBox("Hidden Characters")
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
        show_btn.clicked.connect(self._show_selected_characters)
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
            if target is self.evejs_root_edit:
                self._on_evejs_root_edited()

    def _browse_file(self, target: QLineEdit) -> None:
        from src.core.platform import get_exe_file_filter
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File", target.text(), get_exe_file_filter()
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
        self.auto_start_server_toggle.setChecked(bool(cfg.get("auto_start_server", False)))
        self.auto_start_market_toggle.setChecked(bool(cfg.get("auto_start_market", False)))

        self.animations_toggle.setChecked(bool(cfg.get("animations_enabled", True)))
        self.hero_interval_spin.setValue(int(cfg.get("hero_rotation_interval_sec", 6)))

        self.update_auto_check_toggle.setChecked(bool(cfg.get("update_auto_check", True)))
        self.update_interval_spin.setValue(int(cfg.get("update_check_interval_hours", 6)))

        last_checked = cfg.get("update_last_checked", "")
        self.last_checked_label.setText(last_checked[:16] if last_checked else "Never")

        self.hidden_list.clear()
        for name in cfg.get("hidden_characters", []):
            self.hidden_list.addItem(str(name))

        self._populate_server_scripts(
            str(cfg.get("server_start_preference", ASK_EVERY_TIME))
        )

    def save_settings(self) -> None:
        """Persist the form values to config."""
        cfg = config.load()
        cfg.update(
            {
                "evejs_root": self.evejs_root_edit.text().strip(),
                "client_path": self.client_path_edit.text().strip(),
                "proxy_url": self.proxy_url_edit.text().strip(),
                "stagger_delay_sec": self.stagger_delay_spin.value(),
                "auto_start_server": self.auto_start_server_toggle.isChecked(),
                "auto_start_market": self.auto_start_market_toggle.isChecked(),
                "animations_enabled": self.animations_toggle.isChecked(),
                "hero_rotation_interval_sec": self.hero_interval_spin.value(),
                "update_auto_check": self.update_auto_check_toggle.isChecked(),
                "update_check_interval_hours": self.update_interval_spin.value(),
                "hidden_characters": [
                    self.hidden_list.item(i).text()
                    for i in range(self.hidden_list.count())
                ],
                "server_start_preference": (
                    self.server_script_combo.currentData() or ASK_EVERY_TIME
                ),
            }
        )
        config.save(cfg)
        self._stale_server_preference = ""
        self._update_script_info()
        self.settings_saved.emit(cfg)

    # ── Update helpers ───────────────────────────────────────────────────────
    def _on_check_clicked(self) -> None:
        """User clicked Check for Updates — disable button and emit signal."""
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking...")
        self.settings_update_check.emit()

    def set_update_checking(self) -> None:
        """Called externally — show checking state on the button."""
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking...")

    def set_update_check_done(self, success: bool) -> None:
        """Called externally after check completes — reset button + update label."""
        self.check_btn.setEnabled(True)
        self.check_btn.setText("Check for Updates")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if success:
            self.last_checked_label.setText(now)
        else:
            self.last_checked_label.setText(f"Failed — {now}")
            self.last_checked_label.setStyleSheet(f"color: {COLORS['red']}; font-size: 11px;")

    def _open_changelog(self) -> None:
        """Open CHANGELOG.md in the default text editor."""
        changelog_path = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"
        if changelog_path.exists():
            subprocess.Popen(["start", str(changelog_path)], shell=True)

    # ── Hidden characters ────────────────────────────────────────────────────
    def _show_selected_characters(self) -> None:
        """Remove the selected character names from the hidden list and persist.

        Writes ``never_hide_characters`` *before* calling ``save_settings()``
        so the ``settings_saved → _refresh_characters`` chain sees the
        exemptions and doesn't immediately re-hide the character.
        """
        removed = [self.hidden_list.item(i).text()
                   for i in range(self.hidden_list.count())
                   if self.hidden_list.item(i).isSelected()]
        for item in self.hidden_list.selectedItems():
            self.hidden_list.takeItem(self.hidden_list.row(item))
        if removed:
            # ── Write never_hide_characters FIRST so _refresh_characters sees it ──
            cfg = config.load()
            never = set(cfg.get("never_hide_characters", []))
            for name in removed:
                never.add(name)
            cfg["never_hide_characters"] = sorted(never)
            config.save(cfg)
            # Now persist the updated hidden_characters (triggers refresh)
            self.save_settings()

    # ── Server start scripts ─────────────────────────────────────────────────
    def _populate_server_scripts(self, preference: str | None = None) -> None:
        """Populate the preference combo from the currently entered EveJS root."""
        evejs_root = self.evejs_root_edit.text().strip()
        if preference is None:
            preference = str(
                self.server_script_combo.currentData() or ASK_EVERY_TIME
            )
        scripts = discover_server_scripts(evejs_root)

        self.server_script_combo.blockSignals(True)
        self.server_script_combo.clear()
        self.server_script_combo.addItem("Always ask (default)", ASK_EVERY_TIME)
        selected_index = 0
        matched = preference.casefold() == ASK_EVERY_TIME
        for script in scripts:
            self.server_script_combo.addItem(script.name, script.name)
            if script.name.casefold() == preference.casefold():
                selected_index = self.server_script_combo.count() - 1
                matched = True
        self.server_script_combo.setCurrentIndex(selected_index)
        self.server_script_combo.blockSignals(False)
        self._stale_server_preference = "" if matched else preference

        self._update_script_info()

    def _update_script_info(self, _index: int = -1) -> None:
        """Explain the effective launch behavior for the current selection."""
        if _index >= 0:
            self._stale_server_preference = ""
        scripts = discover_server_scripts(self.evejs_root_edit.text().strip())
        one_script_note = ""
        if len(scripts) == 1:
            try:
                mode_for_script(scripts[0])
            except ValueError:
                one_script_note = (
                    f" {scripts[0].name} was found but is unsupported as a mode indicator."
                )
            else:
                one_script_note = (
                    f" Only {scripts[0].name} was found, so it will be used "
                    "automatically and no prompt will appear."
                )

        if self._stale_server_preference:
            self.server_script_info.setText(
                f"Saved script {self._stale_server_preference} is unavailable in this "
                f"EveJS root. The preference was reset to Always ask.{one_script_note}"
            )
            return

        selected = str(self.server_script_combo.currentData() or ASK_EVERY_TIME)
        if not scripts:
            self.server_script_info.setText(
                "No StartServer*.bat files detected. The legacy direct-Node mode "
                "fallback will be used."
            )
            return
        if len(scripts) == 1:
            self.server_script_info.setText(one_script_note.strip())
            return
        if selected.casefold() == ASK_EVERY_TIME:
            self.server_script_info.setText(
                "A script chooser will appear whenever the game server is started."
            )
            return

        try:
            mode = mode_for_script(Path(selected))
        except ValueError:
            self.server_script_info.setText(
                f"{selected} is detected but unsupported as a mode indicator."
            )
            return
        detail = "mods enabled" if mode == "modded" else "no mods"
        self.server_script_info.setText(f"Mode: {mode} ({detail}) — {selected}")

    def _on_evejs_root_edited(self) -> None:
        """Rescan immediately when the root field finishes changing."""
        self._populate_server_scripts()

    def _rescan_server_scripts(self) -> None:
        """Re-scan the EveJS root for server start scripts."""
        self._populate_server_scripts()

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
