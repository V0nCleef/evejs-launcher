"""Branded, non-dismissable progress window used by both update processes."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import SEMANTIC_COLORS as S
from src.i18n import translate_ui_phrase
from src.theme import load_fonts
from src.widgets.deep_signal_background import operations_scene_path
from src.widgets.ui_translation import (
    register_translatable_widget_tree,
    set_translatable_text,
    set_translatable_text_template,
)


class UpdateProgressDialog(QDialog):
    """Keep the user informed while an update downloads and installs."""

    _STAGES = {
        "download": ("Downloading update", 0),
        "prepare": ("Preparing update", 1),
        "install": ("Installing update", 2),
        "restart": ("Restarting launcher", 3),
    }

    _STAGE_BADGES = {
        "download": "DOWNLOAD LINK",
        "prepare": "PACKAGE VERIFY",
        "install": "INSTALL HANDOFF",
        "restart": "RESTART SEQUENCE",
    }

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._version = version.lstrip("vV")
        self._allow_close = False
        self._phase_labels: list[QLabel] = []
        self._phase_frames: list[QFrame] = []
        self._active_phase_index = 0

        self._build_ui()
        register_translatable_widget_tree(self)
        self._apply_styles()
        self.set_stage("download", "Preparing the update…")

    def _build_ui(self) -> None:
        """Assemble a compact, launcher-styled update surface."""
        self.setWindowTitle("EveJS Launcher Update")
        self.setObjectName("updateProgressDialog")
        self.setProperty("deepSignal", True)
        # The standalone updater can use different native font fallback
        # metrics, so stay safely above the full header's minimum size hint.
        self.setFixedSize(620, 430)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAccessibleName("Launcher update progress")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)

        logo = QLabel(self)
        logo.setObjectName("updateLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(42, 42)
        logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png"
        if logo_path.is_file():
            pixmap = QPixmap(str(logo_path)).scaled(
                36,
                36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo.setPixmap(pixmap)
        else:
            logo.setText("\u25c9")
            logo.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        header.addWidget(logo)

        heading = QVBoxLayout()
        heading.setSpacing(1)
        self.context_label = QLabel("DEEP SIGNAL // UPDATE LINK", self)
        self.context_label.setObjectName("dialogEyebrow")
        heading.addWidget(self.context_label)

        self.window_heading = QLabel("Installing update", self)
        self.window_heading.setObjectName("dialogTitle")
        heading.addWidget(self.window_heading)
        header.addLayout(heading)
        header.addStretch()

        version = QLabel(f"v{self._version}", self)
        version.setObjectName("versionBadge")
        version.setAccessibleName("Update version")
        header.addWidget(version, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)
        root.addSpacing(16)

        self.hero_banner = QLabel(self)
        self.hero_banner.setObjectName("updateHero")
        self.hero_banner.setFixedHeight(96)
        source = QPixmap(str(operations_scene_path(__file__)))
        if source.isNull():
            source = QPixmap(
                str(
                    Path(__file__).resolve().parent.parent.parent
                    / "assets"
                    / "hero"
                    / "hero_nebula.png"
                )
            )
        if not source.isNull():
            target_width = self.width() - 48
            target_height = self.hero_banner.height()
            hero = source.scaled(
                target_width,
                target_height,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop_x = max(0, int((hero.width() - target_width) * 0.68))
            crop_y = max(0, (hero.height() - target_height) // 2)
            self.hero_banner.setPixmap(hero.copy(crop_x, crop_y, target_width, target_height))

        hero_copy = QVBoxLayout(self.hero_banner)
        hero_copy.setContentsMargins(16, 13, 16, 13)
        hero_copy.setSpacing(3)
        self.hero_state_label = QLabel("DOWNLOAD LINK", self.hero_banner)
        self.hero_state_label.setObjectName("heroState")
        hero_copy.addWidget(self.hero_state_label)
        hero_copy.addStretch()
        hero_detail = QLabel("SECURE RELEASE CHANNEL / VERIFIED PACKAGE", self.hero_banner)
        hero_detail.setObjectName("heroDetail")
        hero_copy.addWidget(hero_detail)
        root.addWidget(self.hero_banner)
        root.addSpacing(16)

        self.status_panel = QFrame(self)
        self.status_panel.setObjectName("updateStatusPanel")
        self.status_panel.setProperty("state", "active")
        status_layout = QVBoxLayout(self.status_panel)
        status_layout.setContentsMargins(15, 12, 15, 13)
        status_layout.setSpacing(5)

        status_header = QHBoxLayout()
        status_header.setSpacing(8)
        self.state_indicator = QLabel("", self.status_panel)
        self.state_indicator.setObjectName("stateIndicator")
        self.state_indicator.setProperty("state", "active")
        self.state_indicator.setFixedSize(9, 9)
        status_header.addWidget(self.state_indicator)
        self.status_label = QLabel(self.status_panel)
        self.status_label.setObjectName("updateStatus")
        self.status_label.setAccessibleName("Update status")
        status_header.addWidget(self.status_label)
        status_header.addStretch()
        self.state_badge = QLabel("ACTIVE LINK", self.status_panel)
        self.state_badge.setObjectName("stateBadge")
        self.state_badge.setProperty("state", "active")
        status_header.addWidget(self.state_badge)
        status_layout.addLayout(status_header)

        self.detail_label = QLabel(self.status_panel)
        self.detail_label.setObjectName("updateDetail")
        self.detail_label.setWordWrap(True)
        status_layout.addWidget(self.detail_label)

        self.progress_bar = QProgressBar(self.status_panel)
        self.progress_bar.setObjectName("updateProgress")
        self.progress_bar.setProperty("state", "active")
        self.progress_bar.setAccessibleName("Update progress")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        status_layout.addWidget(self.progress_bar)
        root.addWidget(self.status_panel)
        root.addSpacing(16)

        phases = QFrame(self)
        phases.setObjectName("updatePhases")
        phase_layout = QHBoxLayout(phases)
        phase_layout.setContentsMargins(10, 9, 10, 9)
        phase_layout.setSpacing(7)
        for index, text in enumerate(
            ("DOWNLOAD", "PREPARE", "INSTALL", "RESTART")
        ):
            phase = QFrame(phases)
            phase.setObjectName("updatePhase")
            phase.setProperty("phaseState", "pending")
            phase_step = QVBoxLayout(phase)
            phase_step.setContentsMargins(6, 5, 6, 5)
            phase_step.setSpacing(1)
            number = QLabel(f"0{index + 1}", phase)
            number.setObjectName("phaseNumber")
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            phase_step.addWidget(number)
            label = QLabel(text, phase)
            label.setObjectName("phaseLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            phase_step.addWidget(label)
            self._phase_frames.append(phase)
            self._phase_labels.append(label)
            phase_layout.addWidget(phase, stretch=1)
            if index < 3:
                divider = QLabel("›", phases)
                divider.setObjectName("phaseDivider")
                phase_layout.addWidget(divider)
        root.addWidget(phases)
        root.addStretch()

        self._close_button = QPushButton("Close", self)
        self._close_button.setObjectName("closeUpdateAction")
        self._close_button.setProperty("class", "secondary")
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setVisible(False)
        self._close_button.clicked.connect(self.accept)
        root.addWidget(self._close_button, alignment=Qt.AlignmentFlag.AlignRight)

    def _apply_styles(self) -> None:
        """Apply the Deep Signal palette locally for the standalone updater."""
        try:
            fonts = load_fonts()
        except Exception:
            fonts = {"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"}
        header = fonts["header"]
        body = fonts["body"]
        mono = fonts["mono"]

        self.setStyleSheet(
            f"""
            QDialog#updateProgressDialog {{
                background-color: {S['background']};
                border: 1px solid {S['border_bright']};
                border-radius: 13px;
            }}
            QDialog#updateProgressDialog QLabel {{
                background: transparent;
                border: none;
                color: {S['text_secondary']};
                font-family: '{body}';
            }}
            QLabel#updateLogo {{
                background-color: rgba(0, 200, 224, 18);
                border: 1px solid {S['accent_dim']};
                border-radius: 9px;
                color: {S['accent']};
            }}
            QLabel#dialogEyebrow {{
                color: {S['accent']};
                font-family: '{header}';
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 2px;
            }}
            QLabel#dialogTitle {{
                color: {S['text_primary']};
                font-family: '{header}';
                font-size: 22px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#versionBadge {{
                color: {S['warning']};
                background-color: rgba(255, 184, 0, 18);
                border: 1px solid rgba(255, 184, 0, 106);
                border-radius: 5px;
                padding: 5px 9px;
                font-family: '{mono}';
                font-size: 10px;
                font-weight: 700;
            }}
            QLabel#updateHero {{
                background-color: rgba(3, 10, 17, 238);
                border: 1px solid {S['border_bright']};
                border-radius: 9px;
            }}
            QLabel#heroState {{
                color: {S['text_primary']};
                background-color: rgba(3, 10, 17, 220);
                border: 1px solid rgba(0, 200, 224, 118);
                border-radius: 4px;
                padding: 5px 8px;
                font-family: '{header}';
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1.4px;
            }}
            QLabel#heroDetail {{
                color: {S['text_muted']};
                font-family: '{mono}';
                font-size: 8px;
                letter-spacing: 0.7px;
            }}
            QFrame#updateStatusPanel {{
                background-color: rgba(8, 20, 31, 232);
                border: 1px solid {S['border']};
                border-radius: 9px;
            }}
            QFrame#updateStatusPanel[state="error"] {{
                background-color: rgba(45, 17, 23, 220);
                border-color: {S['danger']};
            }}
            QLabel#stateIndicator {{
                background-color: {S['accent']};
                border: 1px solid rgba(240, 244, 248, 112);
                border-radius: 4px;
            }}
            QLabel#stateIndicator[state="error"] {{ background-color: {S['danger']}; }}
            QLabel#stateIndicator[state="restart"] {{ background-color: {S['success']}; }}
            QLabel#updateStatus {{
                color: {S['text_primary']};
                font-family: '{header}';
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#updateStatus[state="error"] {{ color: {S['danger']}; }}
            QLabel#updateDetail {{ color: {S['text_muted']}; font-size: 11px; }}
            QLabel#stateBadge {{
                color: {S['accent']};
                background-color: rgba(0, 200, 224, 14);
                border: 1px solid rgba(0, 200, 224, 90);
                border-radius: 4px;
                padding: 3px 6px;
                font-family: '{header}';
                font-size: 7px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#stateBadge[state="error"] {{
                color: {S['danger']};
                background-color: rgba(224, 79, 79, 16);
                border-color: rgba(224, 79, 79, 104);
            }}
            QLabel#stateBadge[state="restart"] {{
                color: {S['success']};
                background-color: rgba(79, 224, 127, 16);
                border-color: rgba(79, 224, 127, 104);
            }}
            QFrame#updatePhases {{
                background-color: rgba(3, 10, 17, 222);
                border: 1px solid {S['border']};
                border-radius: 9px;
            }}
            QFrame#updatePhase {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
            }}
            QFrame#updatePhase[phaseState="active"] {{
                background-color: rgba(0, 200, 224, 16);
                border-color: rgba(0, 200, 224, 92);
            }}
            QFrame#updatePhase[phaseState="complete"] {{
                background-color: rgba(79, 224, 127, 10);
            }}
            QFrame#updatePhase[phaseState="error"] {{
                background-color: rgba(224, 79, 79, 16);
                border-color: rgba(224, 79, 79, 100);
            }}
            QLabel#phaseNumber {{
                color: {S['text_muted']};
                font-family: '{mono}';
                font-size: 7px;
            }}
            QLabel#phaseLabel {{
                color: {S['text_muted']};
                font-family: '{header}';
                font-size: 8px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }}
            QFrame#updatePhase[phaseState="active"] QLabel {{ color: {S['accent']}; }}
            QFrame#updatePhase[phaseState="complete"] QLabel {{ color: {S['success']}; }}
            QFrame#updatePhase[phaseState="error"] QLabel {{ color: {S['danger']}; }}
            QLabel#phaseDivider {{
                color: {S['border_bright']};
                font-size: 16px;
            }}
            QProgressBar#updateProgress {{
                background-color: rgba(3, 10, 17, 232);
                border: 1px solid {S['border_bright']};
                border-radius: 8px;
                color: {S['text_primary']};
                font-family: '{mono}';
                font-size: 9px;
                text-align: center;
            }}
            QProgressBar#updateProgress::chunk {{
                background-color: {S['accent']};
                border-radius: 7px;
            }}
            QProgressBar#updateProgress[state="error"]::chunk {{
                background-color: {S['danger']};
            }}
            QPushButton {{
                min-height: 34px;
                padding: 0 14px;
                border-radius: 5px;
                font-family: '{header}';
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.7px;
            }}
            QPushButton[class="secondary"] {{
                background-color: {S['accent_soft']};
                color: {S['text_primary']};
                border: 1px solid {S['accent_dim']};
            }}
            QPushButton[class="secondary"]:hover {{
                background-color: {S['accent_dim']};
                border-color: {S['accent']};
            }}
            QPushButton:focus {{ border: 2px solid {S['text_primary']}; }}
            """
        )

    def set_stage(self, stage: str, detail: str) -> None:
        """Show one trusted launcher-owned phase and preserve inserted values."""
        stage_key = stage if stage in self._STAGES else "prepare"
        title, index = self._STAGES[stage_key]
        set_translatable_text(self.status_label, title)
        # Stage details come only from the launcher/updater workers.  Opting
        # this explicit boundary into reviewed-template matching lets the
        # lock-settle countdown translate without ever translating arbitrary
        # paths or diagnostics: unknown text still passes through unchanged.
        set_translatable_text_template(
            self.detail_label,
            detail,
            template_min_literal=20,
        )
        self.progress_bar.setRange(0, 0)
        self._set_visual_state(stage_key)
        self._set_active_phase(index)

    def set_download_progress(self, downloaded: int, total: int) -> None:
        """Render real byte progress from the release-asset download."""
        set_translatable_text(self.status_label, self._STAGES["download"][0])
        self._set_visual_state("download")
        self._set_active_phase(self._STAGES["download"][1])

        if total <= 0:
            set_translatable_text_template(
                self.detail_label,
                f"Downloaded {self._format_bytes(downloaded)}",
            )
            self.progress_bar.setRange(0, 0)
            return

        percent = min(100, max(0, round(downloaded * 100 / total)))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        set_translatable_text_template(
            self.detail_label,
            f"{self._format_bytes(downloaded)} of {self._format_bytes(total)}",
        )

    def set_copy_progress(self, completed: int, total: int) -> None:
        """Render file-copy progress from the standalone updater process."""
        set_translatable_text(self.status_label, "Installing update")
        self._set_visual_state("install")
        self._set_active_phase(self._STAGES["install"][1])
        if total <= 0:
            set_translatable_text(self.detail_label, "Copying launcher files…")
            self.progress_bar.setRange(0, 0)
            return

        percent = min(100, max(0, round(completed * 100 / total)))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        set_translatable_text_template(
            self.detail_label,
            f"Copied {completed} of {total} files",
        )

    def show_error(self, detail: str) -> None:
        """Keep a failed update visible and let the user return to the launcher."""
        self._allow_close = True
        set_translatable_text(self.status_label, "Update could not finish")
        # Update workers return launcher-owned framing with paths/exceptions
        # inserted.  Reverse-match only strongly specific reviewed templates;
        # arbitrary diagnostics must remain byte-for-byte unchanged.
        self.detail_label.setText(
            translate_ui_phrase(
                detail,
                allow_templates=True,
                template_min_literal=20,
            )
        )
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        set_translatable_text(self.hero_state_label, "UPDATE LINK FAILED")
        set_translatable_text(self.state_badge, "LINK FAILURE")
        for widget in (
            self.status_panel,
            self.state_indicator,
            self.status_label,
            self.state_badge,
            self.progress_bar,
        ):
            widget.setProperty("state", "error")
            self._refresh_widget(widget)
        self._set_active_phase(self._active_phase_index, failed=True)
        self._close_button.setVisible(True)

    def set_handoff_mode(self) -> None:
        """Identify the standalone updater without altering worker behavior."""
        self.setProperty("handoffMode", True)
        set_translatable_text(self.context_label, "DEEP SIGNAL // UPDATE AGENT")
        set_translatable_text(self.window_heading, "Applying launcher update")
        self._refresh_widget(self)

    def allow_close(self) -> None:
        """Permit programmatic shutdown after a successful handoff."""
        self._allow_close = True

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._allow_close:
            super().closeEvent(event)
        else:
            event.ignore()

    def _set_visual_state(self, stage: str) -> None:
        visual_state = "restart" if stage == "restart" else "active"
        badge = self._STAGE_BADGES.get(stage, self._STAGE_BADGES["prepare"])
        set_translatable_text(self.hero_state_label, badge)
        set_translatable_text(self.state_badge, badge)
        for widget in (
            self.status_panel,
            self.state_indicator,
            self.status_label,
            self.state_badge,
            self.progress_bar,
        ):
            widget.setProperty("state", visual_state)
            self._refresh_widget(widget)

    def _set_active_phase(self, active_index: int, *, failed: bool = False) -> None:
        self._active_phase_index = min(
            max(0, int(active_index)), max(0, len(self._phase_frames) - 1)
        )
        for index, frame in enumerate(self._phase_frames):
            if index < self._active_phase_index:
                state = "complete"
            elif index == self._active_phase_index:
                state = "error" if failed else "active"
            else:
                state = "pending"
            frame.setProperty("phaseState", state)
            self._refresh_widget(frame)

    @staticmethod
    def _refresh_widget(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    @staticmethod
    def _format_bytes(value: int) -> str:
        """Return a compact binary-size string suitable for the progress label."""
        if value < 1024 * 1024:
            return f"{max(0, value) / 1024:.1f} KB"
        return f"{max(0, value) / (1024 * 1024):.1f} MB"
