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

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QCheckBox,
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
from src.core.runtime.docker_setup import (
    DockerPreflightRequest,
    DockerPreflightResult,
    DockerSetupDraft,
    create_preflight_request,
    docker_draft_fingerprint,
)
from src.widgets.toggle_switch import ToggleSwitch


NATIVE_RUNTIME_HELP = (
    "Runs the EveJS Game and Market services directly on Windows from the "
    "selected EveJS folder. Docker Desktop is not required."
)
DOCKER_RUNTIME_HELP = (
    "Uses an existing EveJS Compose project through Docker Desktop in "
    "Linux-container mode."
)
RUNTIME_DATA_NOTICE = (
    "Changing runtime does not move characters, market data, or server data."
)
COMPOSE_FILE_HELP = (
    "Recommended: leave this blank. The launcher automatically uses compose.yaml "
    "from EveJS Root. Select a file only when it has a different name or location."
)
PROJECT_NAME_HELP = (
    "Most users should leave this blank. Set a project name only to reconnect to "
    "a stack created with a custom -p name, keep a stable name after moving the "
    "folder, or separate multiple stacks. Changing it may target a different "
    "Docker stack."
)
CONNECT_ONLY_HELP = (
    "Connect only: the launcher shows status and logs but never starts, stops, "
    "or changes the Docker stack."
)
MANAGED_HELP = (
    "Managed: the launcher can start, stop, restart, and maintain this Docker stack."
)
DOCKER_TEST_HELP = (
    "Testing is read-only. It checks Docker and the Compose project without "
    "starting containers or initializing data."
)


class SettingsPage(QWidget):
    """Application settings form."""

    settings_saved = pyqtSignal(dict)
    settings_update_check = pyqtSignal()
    docker_preflight_requested = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stale_server_preference = ""
        self._docker_preflight_token = 0
        self._pending_docker_request: DockerPreflightRequest | None = None
        self._validated_docker_fingerprint: str | None = None
        self._save_after_docker_preflight = False
        self._build_ui()
        self.load_settings()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self._save_feedback_timer = QTimer(self)
        self._save_feedback_timer.setObjectName("settingsSaveFeedbackTimer")
        self._save_feedback_timer.setSingleShot(True)
        self._save_feedback_timer.setInterval(2_500)
        self._save_feedback_timer.timeout.connect(self._clear_save_feedback)

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
        proxy_help = (
            "Local EveJS client-traffic proxy. Keep http://127.0.0.1:26002 "
            "unless your EveJS proxy runs elsewhere."
        )
        self.proxy_url_edit.setToolTip(proxy_help)
        self.proxy_url_edit.setAccessibleDescription(proxy_help)
        general_form.addRow("Proxy URL:", self.proxy_url_edit)

        root.addWidget(general_box)

        # ── Runtime ─────────────────────────────────────────────────────────
        self.runtime_box = QGroupBox("Runtime")
        runtime_form = QFormLayout(self.runtime_box)
        runtime_form.setSpacing(8)
        self.runtime_backend_combo = QComboBox()
        self.runtime_backend_combo.addItem(
            "Native — run directly on Windows",
            "native",
        )
        self.runtime_backend_combo.addItem(
            "Docker Compose — use Docker Desktop",
            "docker_compose",
        )
        self.runtime_backend_combo.currentIndexChanged.connect(self._update_runtime_visibility)
        runtime_form.addRow("How should EveJS run?", self.runtime_backend_combo)
        self.runtime_backend_help_label = self._make_help_label("")
        self.runtime_backend_help_label.setObjectName("runtimeBackendHelp")
        runtime_form.addRow("", self.runtime_backend_help_label)
        self.runtime_data_notice_label = self._make_help_label(
            RUNTIME_DATA_NOTICE,
            color=COLORS["gold"],
        )
        self.runtime_data_notice_label.setObjectName("runtimeDataNotice")
        runtime_form.addRow("", self.runtime_data_notice_label)

        self.docker_fields = QWidget()
        docker_form = QFormLayout(self.docker_fields)
        docker_form.setContentsMargins(0, 0, 0, 0)
        docker_form.setSpacing(8)

        self.docker_compose_edit = QLineEdit()
        self.docker_compose_edit.setPlaceholderText(
            "Optional — leave blank to use <EveJS Root>\\compose.yaml"
        )
        self.docker_compose_edit.setToolTip(COMPOSE_FILE_HELP)
        self.docker_compose_edit.setAccessibleDescription(COMPOSE_FILE_HELP)
        docker_form.addRow(
            "Compose File (optional):",
            self._with_browse(
                self.docker_compose_edit,
                directory=False,
                compose=True,
            ),
        )
        self.docker_compose_help_label = self._make_help_label(COMPOSE_FILE_HELP)
        self.docker_compose_help_label.setObjectName("dockerComposeHelp")
        docker_form.addRow("", self.docker_compose_help_label)
        self.docker_compose_resolved_label = self._make_help_label("")
        self.docker_compose_resolved_label.setObjectName("dockerComposeResolved")
        docker_form.addRow("", self.docker_compose_resolved_label)

        self.docker_policy_combo = QComboBox()
        self.docker_policy_combo.addItem(
            "Connect only — observe an existing stack",
            "connect_only",
        )
        self.docker_policy_combo.addItem(
            "Managed — launcher controls the stack (recommended)",
            "managed",
        )
        self.docker_policy_combo.currentIndexChanged.connect(self._update_runtime_visibility)
        docker_form.addRow("Control Policy:", self.docker_policy_combo)
        self.docker_policy_help_label = self._make_help_label("")
        self.docker_policy_help_label.setObjectName("dockerPolicyHelp")
        docker_form.addRow("", self.docker_policy_help_label)

        self.docker_keep_running_toggle = ToggleSwitch()
        docker_form.addRow("Keep Stack Running on Exit:", self.docker_keep_running_toggle)

        self.docker_advanced_toggle = QCheckBox(
            "Show advanced Docker options"
        )
        self.docker_advanced_toggle.setObjectName("dockerAdvancedToggle")
        self.docker_advanced_toggle.toggled.connect(
            lambda _checked: self._update_docker_guidance()
        )
        docker_form.addRow("", self.docker_advanced_toggle)

        self.docker_advanced_fields = QWidget()
        advanced_form = QFormLayout(self.docker_advanced_fields)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(8)
        self.docker_project_edit = QLineEdit()
        self.docker_project_edit.setPlaceholderText(
            "Optional — example: evejs-local"
        )
        self.docker_project_edit.setToolTip(PROJECT_NAME_HELP)
        self.docker_project_edit.setAccessibleDescription(PROJECT_NAME_HELP)
        advanced_form.addRow(
            "Compose Project Name (optional):",
            self.docker_project_edit,
        )
        self.docker_project_help_label = self._make_help_label(
            PROJECT_NAME_HELP
        )
        self.docker_project_help_label.setObjectName("dockerProjectHelp")
        advanced_form.addRow("", self.docker_project_help_label)
        docker_form.addRow("", self.docker_advanced_fields)

        self.test_docker_setup_btn = QPushButton("Test Docker setup")
        self.test_docker_setup_btn.setObjectName("testDockerSetupButton")
        self.test_docker_setup_btn.clicked.connect(self.test_docker_setup)
        docker_form.addRow("", self.test_docker_setup_btn)
        self.docker_test_help_label = self._make_help_label(DOCKER_TEST_HELP)
        self.docker_test_help_label.setObjectName("dockerTestHelp")
        docker_form.addRow("", self.docker_test_help_label)
        self.docker_preflight_result_label = QLabel("")
        self.docker_preflight_result_label.setWordWrap(True)
        self.docker_preflight_result_label.hide()
        docker_form.addRow("Setup Status:", self.docker_preflight_result_label)
        runtime_form.addRow("", self.docker_fields)
        root.addWidget(self.runtime_box)

        for edit in (
            self.evejs_root_edit,
            self.client_path_edit,
            self.docker_compose_edit,
            self.docker_project_edit,
        ):
            edit.textChanged.connect(self._invalidate_docker_preflight)
        self.runtime_backend_combo.currentIndexChanged.connect(
            self._invalidate_docker_preflight
        )
        self.docker_policy_combo.currentIndexChanged.connect(
            self._invalidate_docker_preflight
        )
        self.docker_keep_running_toggle.toggled.connect(
            self._invalidate_docker_preflight
        )
        self.evejs_root_edit.textChanged.connect(
            lambda _text: self._update_docker_guidance()
        )
        self.docker_compose_edit.textChanged.connect(
            lambda _text: self._update_docker_guidance()
        )

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
        self.scripts_box = scripts_box
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

        self.save_feedback_label = QLabel()
        self.save_feedback_label.setObjectName("settingsSaveFeedback")
        self.save_feedback_label.setAccessibleName("Settings save feedback")
        self.save_feedback_label.hide()
        buttons.addWidget(self.save_feedback_label)
        buttons.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setProperty("class", "ghost")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.load_settings)
        buttons.addWidget(cancel_btn)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("settingsSaveButton")
        self.save_btn.setProperty("class", "primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save_settings)
        buttons.addWidget(self.save_btn)

        root.addLayout(buttons)
        root.addStretch()

    @staticmethod
    def _make_help_label(
        text: str,
        *,
        color: str | None = None,
    ) -> QLabel:
        """Create one consistent, readable inline explanation label."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {color or COLORS['grey']}; font-size: 11px;"
        )
        label.setAccessibleDescription(text)
        return label

    def _with_browse(self, line_edit: QLineEdit, directory: bool, compose: bool = False) -> QWidget:
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
            browse.clicked.connect(lambda: self._browse_file(line_edit, compose=compose))
        row.addWidget(browse)
        return wrapper

    # ── Browse helpers ───────────────────────────────────────────────────────
    def _browse_directory(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select Folder", target.text())
        if path:
            target.setText(path)
            if target is self.evejs_root_edit:
                self._on_evejs_root_edited()

    def _browse_file(self, target: QLineEdit, *, compose: bool = False) -> None:
        """Select an executable or Compose YAML file as appropriate."""
        if compose:
            file_filter = "Compose files (*.yaml *.yml);;YAML files (*.yaml *.yml);;All Files (*)"
        else:
            from src.core.platform import get_exe_file_filter
            file_filter = get_exe_file_filter()
        path, _ = QFileDialog.getOpenFileName(self, "Select File", target.text(), file_filter)
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
        self._set_combo_data(self.runtime_backend_combo, cfg.get("runtime_backend", "native"))
        self.docker_compose_edit.setText(str(cfg.get("docker_compose_file", "")))
        self._set_combo_data(self.docker_policy_combo, cfg.get("docker_control_policy", "connect_only"))
        self.docker_project_edit.setText(str(cfg.get("docker_project_name", "")))
        self.docker_advanced_toggle.setChecked(
            bool(self.docker_project_edit.text().strip())
        )
        self.docker_keep_running_toggle.setChecked(bool(cfg.get("docker_keep_running_on_exit", True)))
        self._pending_docker_request = None
        self._validated_docker_fingerprint = None
        self._save_after_docker_preflight = False
        self._update_runtime_visibility()

    def save_settings(self) -> None:
        """Persist Native immediately or preflight the exact Docker draft."""
        if self.runtime_backend_combo.currentData() == "docker_compose":
            draft = self._collect_docker_draft()
            if (
                self._validated_docker_fingerprint
                == docker_draft_fingerprint(draft)
            ):
                self._persist_settings(self._collect_settings())
            else:
                self._request_docker_preflight(save_after=True)
            return
        self._persist_settings(self._collect_settings())

    def _collect_settings(self) -> dict:
        """Return a complete settings draft without writing configuration."""
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
                "runtime_backend": self.runtime_backend_combo.currentData() or "native",
                "docker_compose_file": self.docker_compose_edit.text().strip(),
                "docker_control_policy": self.docker_policy_combo.currentData() or "connect_only",
                "docker_project_name": self.docker_project_edit.text().strip(),
                "docker_keep_running_on_exit": self.docker_keep_running_toggle.isChecked(),
            }
        )
        return cfg

    def _persist_settings(self, cfg: dict) -> None:
        """Write one already-validated settings draft exactly once."""
        try:
            config.save(cfg)
        except OSError:
            self._show_save_feedback("Save failed — please try again.", success=False)
            return

        self._stale_server_preference = ""
        self._update_script_info()
        self._show_save_feedback("Saved ✓", success=True)
        self.settings_saved.emit(cfg)

    def _collect_docker_draft(self) -> DockerSetupDraft:
        return DockerSetupDraft(
            evejs_root=self.evejs_root_edit.text(),
            compose_file=self.docker_compose_edit.text(),
            project_name=self.docker_project_edit.text(),
            control_policy=(
                self.docker_policy_combo.currentData() or "connect_only"
            ),
            keep_running_on_exit=self.docker_keep_running_toggle.isChecked(),
            client_path=self.client_path_edit.text(),
        )

    def test_docker_setup(self) -> None:
        """Request read-only preflight without persisting any field."""
        self._request_docker_preflight(save_after=False)

    def _request_docker_preflight(self, *, save_after: bool) -> None:
        self._docker_preflight_token += 1
        request = create_preflight_request(
            self._collect_docker_draft(),
            token=self._docker_preflight_token,
        )
        self._pending_docker_request = request
        self._save_after_docker_preflight = save_after
        self.save_btn.setEnabled(False)
        self.test_docker_setup_btn.setEnabled(False)
        self.docker_preflight_result_label.setText(
            "Checking Docker CLI, engine, Compose, services, endpoints, and data state..."
        )
        self.docker_preflight_result_label.show()
        self.docker_preflight_requested.emit(request)

    def apply_docker_preflight_result(
        self,
        result: DockerPreflightResult,
    ) -> None:
        """Accept only the active result for the exact current draft."""
        pending = self._pending_docker_request
        if (
            pending is None
            or not isinstance(result, DockerPreflightResult)
            or result.request_token != pending.token
            or result.draft_fingerprint != pending.draft_fingerprint
        ):
            return

        self._pending_docker_request = None
        self.save_btn.setEnabled(True)
        self.test_docker_setup_btn.setEnabled(True)
        current_fingerprint = docker_draft_fingerprint(
            self._collect_docker_draft()
        )
        if current_fingerprint != result.draft_fingerprint:
            self._save_after_docker_preflight = False
            self._show_docker_preflight_message(
                "Docker setup fields changed during validation. Test the current values again.",
                success=False,
            )
            return

        if not result.report.ok:
            self._validated_docker_fingerprint = None
            self._save_after_docker_preflight = False
            diagnostic = (
                result.report.diagnostics[0]
                if result.report.diagnostics
                else "Docker setup validation failed."
            )
            self._show_docker_preflight_message(diagnostic, success=False)
            return

        self._validated_docker_fingerprint = result.draft_fingerprint
        self._show_docker_preflight_message(
            self._format_docker_preflight_success(result),
            success=True,
        )
        save_after = self._save_after_docker_preflight
        self._save_after_docker_preflight = False
        if save_after:
            self._persist_settings(self._collect_settings())

    def _invalidate_docker_preflight(self, *_args: object) -> None:
        self._validated_docker_fingerprint = None

    def _show_docker_preflight_message(
        self,
        message: str,
        *,
        success: bool,
    ) -> None:
        color = COLORS["green"] if success else COLORS["red"]
        self.docker_preflight_result_label.setStyleSheet(
            f"color: {color}; font-size: 12px;"
        )
        self.docker_preflight_result_label.setText(message)
        self.docker_preflight_result_label.show()

    @staticmethod
    def _format_docker_preflight_success(
        result: DockerPreflightResult,
    ) -> str:
        report = result.report
        if report.config is None or report.records is None:
            return "Docker setup is valid."
        records = report.records
        initialized = bool(
            records.get("init")
            and records["init"].exists
            and records["init"].exit_code == 0
        )
        running = any(
            record.exists and record.raw_state == "running"
            for record in records.values()
        )
        return "\n".join(
            (
                "Docker CLI / Engine / Compose: Ready",
                "Required services and loopback endpoints: Valid",
                f"Data initialization: {'Initialized' if initialized else 'Not initialized yet'}",
                f"Runtime readiness: {'Containers observed' if running else 'Valid but stopped/pristine'}",
            )
        )

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _update_runtime_visibility(self) -> None:
        """Keep Native controls intact but unavailable while Docker is selected."""
        docker = self.runtime_backend_combo.currentData() == "docker_compose"
        self.docker_fields.setVisible(docker)
        self.scripts_box.setVisible(not docker)
        self.runtime_backend_help_label.setText(
            DOCKER_RUNTIME_HELP if docker else NATIVE_RUNTIME_HELP
        )
        self.runtime_backend_combo.setToolTip(
            DOCKER_RUNTIME_HELP if docker else NATIVE_RUNTIME_HELP
        )
        self.runtime_backend_combo.setAccessibleDescription(
            DOCKER_RUNTIME_HELP if docker else NATIVE_RUNTIME_HELP
        )
        self._update_docker_guidance()

    def _update_docker_guidance(self) -> None:
        """Explain the selected Docker target without changing its saved values."""
        docker = self.runtime_backend_combo.currentData() == "docker_compose"
        managed = docker and self.docker_policy_combo.currentData() == "managed"
        self.docker_keep_running_toggle.setEnabled(managed)
        self.docker_keep_running_toggle.setToolTip(
            "Leave managed Compose services running when the launcher closes."
            if managed else "Available only for Docker Compose with Managed control."
        )
        policy_help = MANAGED_HELP if managed else CONNECT_ONLY_HELP
        self.docker_policy_help_label.setText(policy_help)
        self.docker_policy_combo.setToolTip(policy_help)
        self.docker_policy_combo.setAccessibleDescription(policy_help)
        self.docker_advanced_fields.setVisible(
            docker and self.docker_advanced_toggle.isChecked()
        )

        explicit = self.docker_compose_edit.text().strip()
        root = self.evejs_root_edit.text().strip()
        if explicit:
            resolved = f"Using: {explicit}"
        elif root:
            resolved = f"Using: {Path(root) / 'compose.yaml'} (automatic)"
        else:
            resolved = (
                "Using: <EveJS Root>\\compose.yaml after a root is selected "
                "(automatic)"
            )
        self.docker_compose_resolved_label.setText(resolved)
        self.docker_compose_resolved_label.setToolTip(resolved)

    def _show_save_feedback(self, message: str, *, success: bool) -> None:
        """Render a truthful inline result without interrupting form editing."""
        self._save_feedback_timer.stop()
        color = COLORS["green"] if success else COLORS["red"]
        self.save_feedback_label.setStyleSheet(
            f"color: {color}; font-size: 12px; font-weight: 600;"
        )
        self.save_feedback_label.setText(message)
        self.save_feedback_label.setToolTip(message)
        self.save_feedback_label.show()
        if success:
            self._save_feedback_timer.start()

    def _clear_save_feedback(self) -> None:
        """Return the save area to its neutral state after a successful save."""
        self.save_feedback_label.clear()
        self.save_feedback_label.setToolTip("")
        self.save_feedback_label.hide()

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
