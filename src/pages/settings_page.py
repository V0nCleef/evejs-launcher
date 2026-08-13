"""Settings page for EveJS Launcher V2.

Sections
--------
* General        — EveJS root, client path, proxy URL
* Launch         — stagger delay, auto-start toggles
* Audio & Voice  — ambience, local shipboard voice, events, accessibility
* UI             — motion preference and hero rotation interval
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
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
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


class FocusWheelSlider(QSlider):
    """A slider that leaves page scrolling alone until it has focus."""

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

from src import config
from src.constants import COLORS, APP_VERSION
from src.core.client_autologin import inspect_auto_login_capability
from src.core.discovery import resolve_client_tq_path
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
AUTO_LOGIN_HELP = (
    "Uses the copied EVE.js client's built-in local-login switches to sign in "
    "and enter the selected character. No client files are modified and no "
    "real password is stored. The fixed dummy value can be visible in local "
    "Windows process details."
)


class SettingsPage(QWidget):
    """Application settings form."""

    settings_saved = pyqtSignal(dict)
    save_finished = pyqtSignal(bool)
    settings_update_check = pyqtSignal()
    docker_preflight_requested = pyqtSignal(object)
    voice_preview_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stale_server_preference = ""
        self._docker_preflight_token = 0
        self._pending_docker_request: DockerPreflightRequest | None = None
        self._validated_docker_fingerprint: str | None = None
        self._save_after_docker_preflight = False
        self._settings_baseline: dict[str, object] | None = None
        self._voice_preview_available = False
        self._voice_preview_reason = (
            "Bundled LYRA voice pack has not been verified yet."
        )
        self._syncing_motion_toggles = False
        self._audio_layout_mode = ""
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

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("settingsScrollArea")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer.addWidget(self.settings_scroll)

        self.settings_container = QWidget()
        self.settings_container.setObjectName("settingsContainer")
        self.settings_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.settings_scroll.setWidget(self.settings_container)

        root = QVBoxLayout(self.settings_container)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # ── General ──────────────────────────────────────────────────────────
        general_box = QGroupBox("General")
        general_form = QFormLayout(general_box)
        general_form.setSpacing(10)

        self.evejs_root_edit = QLineEdit()
        self.evejs_root_edit.editingFinished.connect(self._on_evejs_root_edited)
        general_form.addRow("EveJS Root:", self._with_browse(self.evejs_root_edit, directory=True))

        self.client_path_edit = QLineEdit()
        self.client_path_edit.setPlaceholderText("Copied EVE client tq folder")
        general_form.addRow(
            "EVE Client Path:",
            self._with_browse(
                self.client_path_edit,
                directory=True,
                client=True,
            ),
        )

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
        self.runtime_backend_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
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
        self.docker_policy_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
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

        self.auto_login_toggle = ToggleSwitch()
        self.auto_login_toggle.setToolTip(AUTO_LOGIN_HELP)
        self.auto_login_toggle.setAccessibleDescription(AUTO_LOGIN_HELP)
        launch_form.addRow("Auto-Login Character:", self.auto_login_toggle)
        self.auto_login_help_label = self._make_help_label(AUTO_LOGIN_HELP)
        self.auto_login_help_label.setObjectName("autoLoginHelp")
        launch_form.addRow("", self.auto_login_help_label)
        self.auto_login_status_label = QLabel("")
        self.auto_login_status_label.setObjectName("autoLoginStatus")
        self.auto_login_status_label.setWordWrap(True)
        launch_form.addRow("Auto-Login Status:", self.auto_login_status_label)

        self.evejs_root_edit.textChanged.connect(
            lambda _text: self._update_auto_login_status()
        )
        self.client_path_edit.textChanged.connect(
            lambda _text: self._update_auto_login_status()
        )
        self.runtime_backend_combo.currentIndexChanged.connect(
            lambda _index: self._update_auto_login_status()
        )

        root.addWidget(launch_box)

        # ── UI ───────────────────────────────────────────────────────────────
        audio_heading = QWidget()
        audio_heading.setProperty("deepSignal", True)
        audio_heading_layout = QVBoxLayout(audio_heading)
        audio_heading_layout.setContentsMargins(0, 4, 0, 0)
        audio_heading_layout.setSpacing(3)
        audio_eyebrow = QLabel("SHIPBOARD SYSTEMS")
        audio_eyebrow.setProperty("class", "pageEyebrow")
        audio_heading_layout.addWidget(audio_eyebrow)
        audio_title = QLabel("AUDIO & VOICE")
        audio_title.setObjectName("audioVoiceSectionTitle")
        audio_title.setProperty("class", "pageTitle")
        audio_heading_layout.addWidget(audio_title)
        audio_subtitle = QLabel(
            "Balance the bundled soundtrack and prerecorded LYRA announcements."
        )
        audio_subtitle.setProperty("class", "pageSubtitle")
        audio_subtitle.setWordWrap(True)
        audio_heading_layout.addWidget(audio_subtitle)
        root.addWidget(audio_heading)

        self.audio_panel_host = QWidget()
        self.audio_panel_host.setObjectName("audioVoicePanelHost")
        self.audio_panel_host.setProperty("deepSignal", True)
        self.audio_panel_host.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.audio_panel_grid = QGridLayout(self.audio_panel_host)
        self.audio_panel_grid.setContentsMargins(0, 0, 0, 0)
        self.audio_panel_grid.setHorizontalSpacing(12)
        self.audio_panel_grid.setVerticalSpacing(12)
        self.audio_mix_panel = self._build_audio_mix_panel()
        self.audio_events_panel = self._build_audio_events_panel()
        self.audio_identity_panel = self._build_audio_identity_panel()
        self._sync_audio_panel_layout(1_100)
        root.addWidget(self.audio_panel_host)

        ui_box = QGroupBox("Visual Timing")
        ui_form = QFormLayout(ui_box)
        ui_form.setSpacing(10)

        self.hero_interval_spin = FocusWheelSpinBox()
        self.hero_interval_spin.setRange(3, 30)
        self.hero_interval_spin.setSuffix(" s")
        self.hero_interval_spin.setAccessibleName("Hero rotation interval")
        ui_form.addRow("Hero Rotation Interval:", self.hero_interval_spin)
        self.hero_interval_help = self._make_help_label(
            "Used by rotating hero content. Reduce Motion pauses optional UI motion."
        )
        ui_form.addRow("", self.hero_interval_help)

        root.addWidget(ui_box)
        # Settings opens on the approved Audio & Voice composition.  Existing
        # operational settings remain below it in their original relative order.
        root.removeWidget(audio_heading)
        root.removeWidget(self.audio_panel_host)
        root.removeWidget(ui_box)
        root.insertWidget(0, audio_heading)
        root.insertWidget(1, self.audio_panel_host)
        root.insertWidget(2, ui_box)

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
        cancel_btn.clicked.connect(self.discard_changes)
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

    def _new_audio_panel(
        self,
        title: str,
        description: str,
        *,
        identity: bool = False,
    ) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setProperty("class", "audioSettingsPanel")
        panel.setProperty("identity", identity)
        panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        panel.setMinimumWidth(0)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading = QLabel(title.upper())
        heading.setProperty("class", "panelTitle")
        layout.addWidget(heading)
        copy = QLabel(description)
        copy.setProperty("class", "panelMeta")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        return panel, layout

    @staticmethod
    def _audio_divider() -> QFrame:
        divider = QFrame()
        divider.setProperty("audioDivider", True)
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        return divider

    @staticmethod
    def _new_audio_slider(
        accessible_name: str,
        minimum: int = 0,
        maximum: int = 100,
    ) -> FocusWheelSlider:
        slider = FocusWheelSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setSingleStep(1)
        slider.setPageStep(5)
        slider.setProperty("audioControl", True)
        slider.setAccessibleName(accessible_name)
        slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return slider

    def _make_audio_slider_control(
        self,
        title: str,
        description: str,
        slider: FocusWheelSlider,
        value_label: QLabel,
        toggle: ToggleSwitch | None,
    ) -> QWidget:
        control = QWidget()
        control.setProperty("audioControlRow", True)
        control.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(control)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        name = QLabel(title.upper())
        name.setProperty("class", "audioControlTitle")
        heading_row.addWidget(name)
        heading_row.addStretch()
        if toggle is not None:
            toggle.setAccessibleName(f"Enable {title.casefold()}")
            toggle.setAccessibleDescription(description)
            heading_row.addWidget(toggle)
        layout.addLayout(heading_row)

        help_label = QLabel(description)
        help_label.setProperty("class", "audioControlHelp")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        slider_row = QHBoxLayout()
        slider_row.setContentsMargins(0, 0, 0, 0)
        slider_row.setSpacing(9)
        slider.setAccessibleDescription(description)
        slider_row.addWidget(slider, 1)
        value_label.setProperty("class", "audioControlValue")
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        value_label.setFixedWidth(44)
        slider_row.addWidget(value_label)
        layout.addLayout(slider_row)
        return control

    def _make_audio_toggle_row(
        self,
        title: str,
        description: str,
        toggle: ToggleSwitch,
    ) -> QWidget:
        row = QWidget()
        row.setProperty("audioToggleRow", True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(2)
        name = QLabel(title.upper())
        name.setProperty("class", "audioControlTitle")
        copy.addWidget(name)
        detail = QLabel(description)
        detail.setProperty("class", "audioControlHelp")
        detail.setWordWrap(True)
        copy.addWidget(detail)
        layout.addLayout(copy, 1)
        toggle.setAccessibleName(title)
        toggle.setAccessibleDescription(description)
        layout.addWidget(toggle)
        return row

    def _build_audio_mix_panel(self) -> QFrame:
        panel, layout = self._new_audio_panel(
            "Mix & Access",
            "Independent levels for music and the LYRA voice pack.",
        )
        panel.setObjectName("audioMixPanel")

        self.music_enabled_toggle = ToggleSwitch()
        self.music_volume_slider = self._new_audio_slider("Music volume")
        self.music_volume_value = QLabel("50%")
        layout.addWidget(
            self._make_audio_slider_control(
                "Background music",
                "Volume for the bundled launcher soundtrack rotation.",
                self.music_volume_slider,
                self.music_volume_value,
                self.music_enabled_toggle,
            )
        )

        self.voice_enabled_toggle = ToggleSwitch()
        self.voice_volume_slider = self._new_audio_slider("Voice volume")
        self.voice_volume_value = QLabel("100%")
        layout.addWidget(
            self._make_audio_slider_control(
                "Voice",
                "Volume for bundled prerecorded LYRA announcements.",
                self.voice_volume_slider,
                self.voice_volume_value,
                self.voice_enabled_toggle,
            )
        )

        self.ducking_enabled_toggle = ToggleSwitch()
        self.ducking_level_slider = self._new_audio_slider(
            "Music level while voice is speaking"
        )
        self.ducking_level_value = QLabel("100%")
        layout.addWidget(
            self._make_audio_slider_control(
                "Music while\nLYRA speaks",
                "Defaults to 100%. Lower it only if you want LYRA to soften the music.",
                self.ducking_level_slider,
                self.ducking_level_value,
                self.ducking_enabled_toggle,
            )
        )

        layout.addWidget(self._audio_divider())
        self.animations_toggle = ToggleSwitch(self)
        self.animations_toggle.setObjectName("animationsEnabledCompatibilityToggle")
        self.animations_toggle.hide()
        self.reduce_motion_toggle = ToggleSwitch()
        layout.addWidget(
            self._make_audio_toggle_row(
                "Reduce motion",
                "Pauses optional interface motion and rotating hero content.",
                self.reduce_motion_toggle,
            )
        )

        for slider, label in (
            (self.music_volume_slider, self.music_volume_value),
            (self.voice_volume_slider, self.voice_volume_value),
            (self.ducking_level_slider, self.ducking_level_value),
        ):
            slider.valueChanged.connect(
                lambda value, target=label: target.setText(f"{value}%")
            )
        for toggle in (
            self.music_enabled_toggle,
            self.voice_enabled_toggle,
            self.ducking_enabled_toggle,
        ):
            toggle.toggled.connect(self._sync_audio_control_states)
        self.reduce_motion_toggle.toggled.connect(
            self._on_reduce_motion_toggled
        )
        self.animations_toggle.toggled.connect(
            self._on_animations_enabled_toggled
        )
        return panel

    def _make_voice_event_row(
        self,
        title: str,
        sample: str,
    ) -> tuple[QWidget, QLabel]:
        row = QWidget()
        row.setProperty("audioEventRow", True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.setSpacing(10)
        indicator = QLabel("✓")
        indicator.setProperty("class", "audioEventIndicator")
        indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        indicator.setFixedSize(28, 28)
        layout.addWidget(indicator)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(2)
        name = QLabel(title.upper())
        name.setProperty("class", "audioEventName")
        copy.addWidget(name)
        description = QLabel(sample)
        description.setProperty("class", "audioEventDescription")
        description.setWordWrap(True)
        copy.addWidget(description)
        layout.addLayout(copy, 1)
        return row, indicator

    def _build_audio_events_panel(self) -> QFrame:
        panel, layout = self._new_audio_panel(
            "Voice Events",
            "Fixed prerecorded announcements used for launcher actions.",
        )
        panel.setObjectName("audioEventsPanel")
        self.voice_event_status_label = QLabel("VOICE EVENTS ENABLED")
        self.voice_event_status_label.setProperty("class", "audioAvailability")
        self.voice_event_status_label.setProperty("state", "available")
        layout.addWidget(self.voice_event_status_label)

        self._voice_event_indicators: list[QLabel] = []
        for title, sample in (
            ("Stack launch", "“Launching server stack.”"),
            ("Service ready", "“Server stack online.”"),
            ("Character launch", "“Launching selected character.”"),
            ("Failure & completion", "Optional launch result announcements."),
        ):
            row, indicator = self._make_voice_event_row(title, sample)
            self._voice_event_indicators.append(indicator)
            layout.addWidget(row)

        layout.addWidget(self._audio_divider())
        self.announce_results_toggle = ToggleSwitch()
        layout.addWidget(
            self._make_audio_toggle_row(
                "Announce results",
                "Speak completion and failure outcomes.",
                self.announce_results_toggle,
            )
        )
        layout.addStretch()
        return panel

    def _build_audio_identity_panel(self) -> QFrame:
        panel, layout = self._new_audio_panel(
            "Bundled LYRA Voice",
            "A fixed, prerecorded launcher voice pack, distinct from EVE's Aura.",
            identity=True,
        )
        panel.setObjectName("audioIdentityPanel")

        identity_badge = QLabel("BUNDLED VOICE PACK")
        identity_badge.setProperty("class", "signalPill")
        identity_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_badge.setMaximumWidth(156)
        layout.addWidget(identity_badge)
        identity_name = QLabel("LYRA")
        identity_name.setObjectName("voiceIdentityName")
        identity_name.setProperty("class", "audioIdentityName")
        layout.addWidget(identity_name)
        identity_line = QLabel(
            "Natural prerecorded shipboard announcements · played locally"
        )
        identity_line.setProperty("class", "audioIdentityTagline")
        identity_line.setWordWrap(True)
        layout.addWidget(identity_line)

        facts = QGridLayout()
        facts.setContentsMargins(0, 3, 0, 3)
        facts.setHorizontalSpacing(12)
        facts.setVerticalSpacing(7)
        fact_values: list[tuple[str, QLabel]] = []
        self.voice_pack_source_value = QLabel("LYRA PRERECORDED VOICE")
        self.voice_pack_language_value = QLabel("English (UK)")
        self.voice_pack_profile_value = QLabel("Balanced Lift")
        fact_values.extend(
            (
                ("Voice source", self.voice_pack_source_value),
                ("Language", self.voice_pack_language_value),
                ("Profile", self.voice_pack_profile_value),
            )
        )
        for row, (name, value) in enumerate(fact_values):
            key = QLabel(name)
            key.setProperty("class", "audioIdentityKey")
            value.setProperty("class", "audioIdentityValue")
            value.setWordWrap(True)
            facts.addWidget(key, row, 0)
            facts.addWidget(value, row, 1)
        facts.setColumnStretch(1, 1)
        layout.addLayout(facts)

        layout.addStretch()
        self.preview_voice_btn = QPushButton("▶  PREVIEW LYRA")
        self.preview_voice_btn.setObjectName("previewVoiceButton")
        self.preview_voice_btn.setProperty("class", "signalSecondary")
        self.preview_voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.preview_voice_btn.setAccessibleName("Preview LYRA voice")
        self.preview_voice_btn.clicked.connect(self._request_voice_preview)
        layout.addWidget(self.preview_voice_btn)
        self.voice_preview_status_label = QLabel(self._voice_preview_reason)
        self.voice_preview_status_label.setObjectName("voicePreviewStatus")
        self.voice_preview_status_label.setProperty("class", "audioAvailability")
        self.voice_preview_status_label.setProperty("state", "unavailable")
        self.voice_preview_status_label.setWordWrap(True)
        layout.addWidget(self.voice_preview_status_label)
        return panel

    def set_voice_preview_available(
        self,
        available: bool,
        reason: str = "",
    ) -> None:
        """Publish whether the bundled prerecorded voice pack can be played.

        The page owns presentation only. The audio controller remains the
        authority for asset decoding and connects ``voice_preview_requested``.
        """
        self._voice_preview_available = bool(available)
        if reason.strip():
            self._voice_preview_reason = reason.strip()
        elif available:
            self._voice_preview_reason = "Bundled LYRA voice pack is ready."
        else:
            self._voice_preview_reason = "Bundled LYRA voice pack is unavailable."
        self._sync_audio_control_states()

    def audio_preview_settings(self) -> dict[str, object]:
        """Return the visible unsaved audio draft without mutating persistence."""
        state = self._form_state()
        keys = (
            "audio_voice_enabled",
            "audio_voice_volume",
        )
        return {key: state[key] for key in keys}

    def _request_voice_preview(self) -> None:
        if (
            self._voice_preview_available
            and self.voice_enabled_toggle.isChecked()
        ):
            self.voice_preview_requested.emit()

    @staticmethod
    def _refresh_dynamic_style(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _sync_audio_control_states(self, *_args: object) -> None:
        music_enabled = self.music_enabled_toggle.isChecked()
        voice_enabled = self.voice_enabled_toggle.isChecked()

        self.music_volume_slider.setEnabled(music_enabled)
        self.voice_volume_slider.setEnabled(voice_enabled)
        ducking_available = music_enabled and voice_enabled
        self.ducking_enabled_toggle.setEnabled(ducking_available)
        self.ducking_level_slider.setEnabled(
            ducking_available and self.ducking_enabled_toggle.isChecked()
        )
        self.announce_results_toggle.setEnabled(voice_enabled)

        preview_enabled = voice_enabled and self._voice_preview_available
        self.preview_voice_btn.setEnabled(preview_enabled)
        if not self.voice_enabled_toggle.isChecked():
            preview_status = "Voice announcements are disabled."
            preview_state = "unavailable"
        else:
            preview_status = self._voice_preview_reason
            preview_state = (
                "available" if self._voice_preview_available else "unavailable"
            )
        self.voice_preview_status_label.setText(preview_status)
        self.voice_preview_status_label.setToolTip(preview_status)
        self.voice_preview_status_label.setAccessibleDescription(preview_status)
        self.voice_preview_status_label.setProperty("state", preview_state)
        self.preview_voice_btn.setToolTip(preview_status)
        self.preview_voice_btn.setAccessibleDescription(preview_status)
        self._refresh_dynamic_style(self.voice_preview_status_label)

        self.voice_event_status_label.setText(
            "VOICE EVENTS ENABLED" if voice_enabled else "VOICE EVENTS DISABLED"
        )
        self.voice_event_status_label.setProperty(
            "state", "available" if voice_enabled else "unavailable"
        )
        self._refresh_dynamic_style(self.voice_event_status_label)
        for indicator in self._voice_event_indicators:
            indicator.setText("✓" if voice_enabled else "—")
            indicator.setProperty("state", "available" if voice_enabled else "off")
            self._refresh_dynamic_style(indicator)

    def _set_animations_enabled(self, enabled: bool) -> None:
        self._syncing_motion_toggles = True
        try:
            self.animations_toggle.setChecked(bool(enabled))
            self.reduce_motion_toggle.setChecked(not bool(enabled))
        finally:
            self._syncing_motion_toggles = False
        self.hero_interval_spin.setEnabled(bool(enabled))
        self.hero_interval_help.setText(
            "Used by rotating hero content. Reduce Motion pauses optional UI motion."
            if enabled
            else "Optional interface motion and rotating hero content are paused."
        )

    def _on_reduce_motion_toggled(self, reduced: bool) -> None:
        if self._syncing_motion_toggles:
            return
        self._set_animations_enabled(not reduced)

    def _on_animations_enabled_toggled(self, enabled: bool) -> None:
        if self._syncing_motion_toggles:
            return
        self._set_animations_enabled(enabled)

    def _sync_audio_panel_layout(self, width: int) -> None:
        width = max(0, int(width))
        if width >= 980:
            mode = "wide"
        elif width >= 700:
            mode = "compact"
        else:
            mode = "single"
        if mode == self._audio_layout_mode:
            return

        for panel in (
            self.audio_mix_panel,
            self.audio_events_panel,
            self.audio_identity_panel,
        ):
            self.audio_panel_grid.removeWidget(panel)
        for column in range(3):
            self.audio_panel_grid.setColumnStretch(column, 0)

        if mode == "wide":
            self.audio_panel_grid.addWidget(self.audio_mix_panel, 0, 0)
            self.audio_panel_grid.addWidget(self.audio_events_panel, 0, 1)
            self.audio_panel_grid.addWidget(self.audio_identity_panel, 0, 2)
            self.audio_panel_grid.setColumnStretch(0, 5)
            self.audio_panel_grid.setColumnStretch(1, 4)
            self.audio_panel_grid.setColumnStretch(2, 5)
        elif mode == "compact":
            self.audio_panel_grid.addWidget(self.audio_mix_panel, 0, 0)
            self.audio_panel_grid.addWidget(self.audio_events_panel, 0, 1)
            self.audio_panel_grid.addWidget(
                self.audio_identity_panel, 1, 0, 1, 2
            )
            self.audio_panel_grid.setColumnStretch(0, 1)
            self.audio_panel_grid.setColumnStretch(1, 1)
        else:
            self.audio_panel_grid.addWidget(self.audio_mix_panel, 0, 0)
            self.audio_panel_grid.addWidget(self.audio_events_panel, 1, 0)
            self.audio_panel_grid.addWidget(self.audio_identity_panel, 2, 0)
            self.audio_panel_grid.setColumnStretch(0, 1)
        self._audio_layout_mode = mode
        self.audio_panel_host.updateGeometry()

    def audio_layout_mode(self) -> str:
        """Return the current responsive audio-panel arrangement."""
        return self._audio_layout_mode

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_audio_panel_layout(max(0, event.size().width() - 32))

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

    def _with_browse(
        self,
        line_edit: QLineEdit,
        directory: bool,
        compose: bool = False,
        client: bool = False,
    ) -> QWidget:
        """Wrap a QLineEdit with a Browse button in an HBox."""
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(line_edit, stretch=1)

        browse = QPushButton("Browse…")
        browse.setProperty("class", "ghost")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        if client:
            browse.clicked.connect(self._browse_client_directory)
        elif directory:
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

    def _browse_client_directory(self) -> None:
        """Select any recognizable copied-client folder and store its tq root."""
        start = self.client_path_edit.text().strip()
        resolved_start = resolve_client_tq_path(
            start,
            self.evejs_root_edit.text().strip(),
        )
        if resolved_start is not None:
            start = str(resolved_start)
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Copied EVE Client Folder",
            start,
        )
        if not path:
            return
        resolved = resolve_client_tq_path(
            path,
            self.evejs_root_edit.text().strip(),
        )
        self.client_path_edit.setText(str(resolved) if resolved else path)
        if resolved is None:
            self._show_save_feedback(
                "Select the copied EVE client tq folder (or its bin64 folder).",
                success=False,
            )

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
        raw_client_path = str(cfg.get("client_path", ""))
        resolved_client_path = resolve_client_tq_path(
            raw_client_path,
            str(cfg.get("evejs_root", "")),
        )
        self.client_path_edit.setText(
            str(resolved_client_path) if resolved_client_path else raw_client_path
        )
        self.proxy_url_edit.setText(str(cfg.get("proxy_url", "")))

        self.stagger_delay_spin.setValue(int(cfg.get("stagger_delay_sec", 3)))
        self.auto_start_server_toggle.setChecked(bool(cfg.get("auto_start_server", False)))
        self.auto_start_market_toggle.setChecked(bool(cfg.get("auto_start_market", False)))
        self.auto_login_toggle.setChecked(bool(cfg.get("auto_login_enabled", False)))

        self.music_enabled_toggle.setChecked(
            bool(cfg.get("audio_music_enabled", True))
        )
        self.music_volume_slider.setValue(
            int(cfg.get("audio_music_volume", 50))
        )
        self.voice_enabled_toggle.setChecked(
            bool(cfg.get("audio_voice_enabled", True))
        )
        self.voice_volume_slider.setValue(
            int(cfg.get("audio_voice_volume", 100))
        )
        self.ducking_enabled_toggle.setChecked(
            bool(cfg.get("audio_ducking_enabled", True))
        )
        self.ducking_level_slider.setValue(
            int(cfg.get("audio_ducking_level", 100))
        )
        self.announce_results_toggle.setChecked(
            bool(cfg.get("audio_announce_results", True))
        )

        self._set_animations_enabled(
            bool(cfg.get("animations_enabled", True))
        )
        self.hero_interval_spin.setValue(int(cfg.get("hero_rotation_interval_sec", 6)))
        self._sync_audio_control_states()

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
        self._update_auto_login_status()
        self.save_btn.setEnabled(True)
        self.test_docker_setup_btn.setEnabled(True)
        self._settings_baseline = self._form_state()

    def refresh_if_clean(self) -> bool:
        """Reload current config only when doing so cannot erase a draft."""
        if self.is_dirty() or self._pending_docker_request is not None:
            return False
        self.load_settings()
        return True

    def discard_changes(self) -> bool:
        """Abandon the current draft and any pending Docker-save intent."""
        cancelled_save = (
            self._pending_docker_request is not None
            and self._save_after_docker_preflight
        )
        self._pending_docker_request = None
        self._save_after_docker_preflight = False
        self._validated_docker_fingerprint = None
        self.save_btn.setEnabled(True)
        self.test_docker_setup_btn.setEnabled(True)
        self.load_settings()
        if cancelled_save:
            self.save_finished.emit(False)
        return True

    def is_dirty(self) -> bool:
        """Return whether the visible form differs from its last clean baseline."""
        return (
            self._settings_baseline is not None
            and self._form_state() != self._settings_baseline
        )

    def _update_auto_login_status(self) -> None:
        """Show whether the selected copied client can use local auto-login."""
        if self.runtime_backend_combo.currentData() != "native":
            supported = False
            reason = "Automatic login is available for Native EveJS runtime only."
        else:
            capability = inspect_auto_login_capability(
                self.evejs_root_edit.text().strip(),
                self.client_path_edit.text().strip(),
            )
            supported = capability.supported
            reason = capability.reason
        self.auto_login_toggle.setEnabled(supported)
        self.auto_login_status_label.setText(reason)
        color = COLORS["green"] if supported else COLORS["gold"]
        self.auto_login_status_label.setStyleSheet(f"color: {color};")
        self.auto_login_status_label.setToolTip(reason)

    def save_settings(self) -> None:
        """Persist Native immediately or preflight the exact Docker draft."""
        if not self._normalize_client_path_for_save():
            self.save_finished.emit(False)
            return
        if self.runtime_backend_combo.currentData() == "docker_compose":
            draft = self._collect_docker_draft()
            draft_fingerprint = docker_draft_fingerprint(draft)
            pending = self._pending_docker_request
            if pending is not None:
                if pending.draft_fingerprint == draft_fingerprint:
                    self._save_after_docker_preflight = True
                else:
                    self._show_save_feedback(
                        "Docker setup is already being checked. Try Save again when it finishes.",
                        success=False,
                    )
                    self.save_finished.emit(False)
                return
            if (
                self._validated_docker_fingerprint
                == draft_fingerprint
            ):
                self._persist_settings(self._collect_settings())
            else:
                self._request_docker_preflight(save_after=True)
            return
        self._persist_settings(self._collect_settings())

    def _normalize_client_path_for_save(self) -> bool:
        raw = self.client_path_edit.text().strip()
        if not raw:
            return True
        resolved = resolve_client_tq_path(
            raw,
            self.evejs_root_edit.text().strip(),
        )
        baseline_client = (
            str(self._settings_baseline.get("client_path", ""))
            if self._settings_baseline is not None
            else ""
        )
        if resolved is None:
            # Keep unrelated settings editable when a legacy path is currently
            # unavailable (for example, a disconnected external drive). New or
            # changed invalid values are still rejected.
            if raw == baseline_client:
                return True
            self._show_save_feedback(
                "EVE Client Path must be the copied client's tq folder containing "
                "start.ini and bin64\\exefile.exe.",
                success=False,
            )
            return False
        self.client_path_edit.setText(str(resolved))
        return True

    def _form_state(self) -> dict[str, object]:
        """Project only persisted form values for dirty-state comparison."""
        return {
            "evejs_root": self.evejs_root_edit.text().strip(),
            "client_path": self.client_path_edit.text().strip(),
            "proxy_url": self.proxy_url_edit.text().strip(),
            "stagger_delay_sec": self.stagger_delay_spin.value(),
            "auto_start_server": self.auto_start_server_toggle.isChecked(),
            "auto_start_market": self.auto_start_market_toggle.isChecked(),
            "auto_login_enabled": (
                self.auto_login_toggle.isEnabled()
                and self.auto_login_toggle.isChecked()
            ),
            "audio_music_enabled": self.music_enabled_toggle.isChecked(),
            "audio_music_volume": self.music_volume_slider.value(),
            "audio_voice_enabled": self.voice_enabled_toggle.isChecked(),
            "audio_voice_volume": self.voice_volume_slider.value(),
            "audio_announce_results": self.announce_results_toggle.isChecked(),
            "audio_ducking_enabled": self.ducking_enabled_toggle.isChecked(),
            "audio_ducking_level": self.ducking_level_slider.value(),
            "animations_enabled": self.animations_toggle.isChecked(),
            "hero_rotation_interval_sec": self.hero_interval_spin.value(),
            "update_auto_check": self.update_auto_check_toggle.isChecked(),
            "update_check_interval_hours": self.update_interval_spin.value(),
            "hidden_characters": tuple(
                self.hidden_list.item(i).text()
                for i in range(self.hidden_list.count())
            ),
            "server_start_preference": (
                self.server_script_combo.currentData() or ASK_EVERY_TIME
            ),
            "runtime_backend": self.runtime_backend_combo.currentData() or "native",
            "docker_compose_file": self.docker_compose_edit.text().strip(),
            "docker_control_policy": (
                self.docker_policy_combo.currentData() or "connect_only"
            ),
            "docker_project_name": self.docker_project_edit.text().strip(),
            "docker_keep_running_on_exit": self.docker_keep_running_toggle.isChecked(),
        }

    def _collect_settings(self) -> dict:
        """Return a complete settings draft without writing configuration."""
        cfg = config.load()
        state = self._form_state()
        state["hidden_characters"] = list(state["hidden_characters"])
        cfg.update(state)
        return cfg

    def _persist_settings(self, cfg: dict) -> None:
        """Write one already-validated settings draft exactly once."""
        try:
            config.save(cfg)
        except OSError:
            self._show_save_feedback("Save failed — please try again.", success=False)
            self.save_finished.emit(False)
            return

        self._stale_server_preference = ""
        self._update_script_info()
        self._show_save_feedback("Saved ✓", success=True)
        self._settings_baseline = self._form_state()
        self.settings_saved.emit(cfg)
        self.save_finished.emit(True)

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

    def reject_docker_preflight_request(
        self,
        request: DockerPreflightRequest,
        message: str,
    ) -> None:
        """Finish a request that the window could not start without hanging Save."""
        pending = self._pending_docker_request
        if pending is None or pending.token != request.token:
            return
        save_after = self._save_after_docker_preflight
        self._pending_docker_request = None
        self._save_after_docker_preflight = False
        self.save_btn.setEnabled(True)
        self.test_docker_setup_btn.setEnabled(True)
        self._show_docker_preflight_message(message, success=False)
        if save_after:
            self.save_finished.emit(False)

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

        save_after = self._save_after_docker_preflight
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
            if save_after:
                self.save_finished.emit(False)
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
            if save_after:
                self.save_finished.emit(False)
            return

        self._validated_docker_fingerprint = result.draft_fingerprint
        self._show_docker_preflight_message(
            self._format_docker_preflight_success(result),
            success=True,
        )
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
