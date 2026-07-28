"""Branded, non-dismissable progress window used by both update processes."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS


class UpdateProgressDialog(QDialog):
    """Keep the user informed while an update downloads and installs."""

    _STAGES = {
        "download": ("Downloading update", 0),
        "prepare": ("Preparing update", 1),
        "install": ("Installing update", 2),
    }

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._version = version.lstrip("vV")
        self._allow_close = False
        self._phase_labels: list[QLabel] = []

        self._build_ui()
        self._apply_styles()
        self.set_stage("download", "Preparing the update…")

    def _build_ui(self) -> None:
        """Assemble a compact, launcher-styled update surface."""
        self.setWindowTitle("EveJS Launcher Update")
        self.setFixedSize(520, 432)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(12)

        logo = QLabel(self)
        logo.setFixedSize(40, 40)
        logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png"
        if logo_path.is_file():
            pixmap = QPixmap(str(logo_path)).scaled(
                40,
                40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo.setPixmap(pixmap)
        else:
            logo.setText("E")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
            logo.setStyleSheet(f"color: {COLORS['teal']};")
        header.addWidget(logo)

        heading = QVBoxLayout()
        heading.setSpacing(1)
        eyebrow = QLabel("EVEJS LAUNCHER", self)
        eyebrow.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 10px; font-weight: 700; "
            "letter-spacing: 1.6px;"
        )
        heading.addWidget(eyebrow)

        title = QLabel("Installing update", self)
        title.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 20px; font-weight: 700;"
        )
        heading.addWidget(title)
        header.addLayout(heading)
        header.addStretch()

        version = QLabel(f"v{self._version}", self)
        version.setStyleSheet(
            f"color: {COLORS['gold']}; background: {COLORS['card']}; "
            f"border: 1px solid {COLORS['steel']}; border-radius: 6px; "
            "padding: 5px 9px; font-family: Consolas; font-size: 11px;"
        )
        header.addWidget(version, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)
        root.addSpacing(18)

        self.hero_banner = QLabel(self)
        self.hero_banner.setFixedHeight(82)
        self.hero_banner.setStyleSheet(
            f"background: {COLORS['void_black']}; border: 1px solid {COLORS['steel']}; "
            "border-radius: 8px;"
        )
        hero_path = (
            Path(__file__).resolve().parent.parent.parent
            / "assets"
            / "hero"
            / "hero_nebula.png"
        )
        if hero_path.is_file():
            source = QPixmap(str(hero_path))
            target_width = self.width() - 56
            target_height = self.hero_banner.height()
            hero = source.scaled(
                target_width,
                target_height,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            crop_x = max(0, (hero.width() - target_width) // 2)
            crop_y = max(0, (hero.height() - target_height) // 2)
            self.hero_banner.setPixmap(hero.copy(crop_x, crop_y, target_width, target_height))
            opacity = QGraphicsOpacityEffect(self.hero_banner)
            opacity.setOpacity(0.58)
            self.hero_banner.setGraphicsEffect(opacity)
        else:
            self.hero_banner.setText("UPDATING EVEJS LAUNCHER")
            self.hero_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.hero_banner.setStyleSheet(
                f"background: {COLORS['void_black']}; border: 1px solid {COLORS['steel']}; "
                f"border-radius: 8px; color: {COLORS['teal']}; font-size: 11px; "
                "font-weight: 700; letter-spacing: 2px;"
            )
        root.addWidget(self.hero_banner)
        root.addSpacing(20)

        self.status_label = QLabel(self)
        self.status_label.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 15px; font-weight: 600;"
        )
        root.addWidget(self.status_label)
        root.addSpacing(6)

        self.detail_label = QLabel(self)
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {COLORS['grey']}; font-size: 12px;")
        root.addWidget(self.detail_label)
        root.addSpacing(14)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        root.addWidget(self.progress_bar)
        root.addSpacing(20)

        phases = QFrame(self)
        phases.setObjectName("updatePhases")
        phase_layout = QHBoxLayout(phases)
        phase_layout.setContentsMargins(14, 10, 14, 10)
        phase_layout.setSpacing(10)
        for index, text in enumerate(("Download", "Prepare", "Restart")):
            label = QLabel(text, phases)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._phase_labels.append(label)
            phase_layout.addWidget(label, stretch=1)
            if index < 2:
                divider = QLabel("›", phases)
                divider.setStyleSheet(f"color: {COLORS['steel']}; font-size: 18px;")
                phase_layout.addWidget(divider)
        root.addWidget(phases)
        root.addStretch()

        self._close_button = QPushButton("Close", self)
        self._close_button.setVisible(False)
        self._close_button.clicked.connect(self.accept)
        root.addWidget(self._close_button, alignment=Qt.AlignmentFlag.AlignRight)

    def _apply_styles(self) -> None:
        """Apply the launcher palette locally so the child updater matches exactly."""
        self.setStyleSheet(
            f"""
            UpdateProgressDialog {{
                background: {COLORS['panel']};
                border: 1px solid {COLORS['steel']};
                border-radius: 12px;
            }}
            QFrame#updatePhases {{
                background: {COLORS['card']};
                border: 1px solid {COLORS['steel']};
                border-radius: 8px;
            }}
            QProgressBar {{
                background: {COLORS['deep_space']};
                border: 1px solid {COLORS['steel']};
                border-radius: 8px;
                color: {COLORS['white']};
                font-size: 10px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: {COLORS['teal']};
                border-radius: 7px;
            }}
            QPushButton {{
                background: transparent;
                border: 1px solid {COLORS['steel']};
                border-radius: 6px;
                color: {COLORS['white']};
                padding: 7px 14px;
            }}
            QPushButton:hover {{
                border-color: {COLORS['teal']};
                color: {COLORS['teal']};
            }}
            """
        )

    def set_stage(self, stage: str, detail: str) -> None:
        """Show a named non-download phase with an active progress indicator."""
        title, index = self._STAGES.get(stage, self._STAGES["prepare"])
        self.status_label.setText(title)
        self.detail_label.setText(detail)
        self.progress_bar.setRange(0, 0)
        self._set_active_phase(index)

    def set_download_progress(self, downloaded: int, total: int) -> None:
        """Render real byte progress from the release-asset download."""
        self.status_label.setText(self._STAGES["download"][0])
        self._set_active_phase(self._STAGES["download"][1])

        if total <= 0:
            self.detail_label.setText(f"Downloaded {self._format_bytes(downloaded)}")
            self.progress_bar.setRange(0, 0)
            return

        percent = min(100, max(0, round(downloaded * 100 / total)))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.detail_label.setText(
            f"{self._format_bytes(downloaded)} of {self._format_bytes(total)}"
        )

    def set_copy_progress(self, completed: int, total: int) -> None:
        """Render file-copy progress from the standalone updater process."""
        self.status_label.setText("Installing update")
        self._set_active_phase(self._STAGES["install"][1])
        if total <= 0:
            self.detail_label.setText("Copying launcher files…")
            self.progress_bar.setRange(0, 0)
            return

        percent = min(100, max(0, round(completed * 100 / total)))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.detail_label.setText(f"Copied {completed} of {total} files")

    def show_error(self, detail: str) -> None:
        """Keep a failed update visible and let the user return to the launcher."""
        self._allow_close = True
        self.status_label.setText("Update could not finish")
        self.detail_label.setText(detail)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.progress_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {COLORS['red']}; }}"
        )
        self._close_button.setVisible(True)

    def allow_close(self) -> None:
        """Permit programmatic shutdown after a successful handoff."""
        self._allow_close = True

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._allow_close:
            super().closeEvent(event)
        else:
            event.ignore()

    def _set_active_phase(self, active_index: int) -> None:
        for index, label in enumerate(self._phase_labels):
            if index < active_index:
                color = COLORS["green"]
            elif index == active_index:
                color = COLORS["teal"]
            else:
                color = COLORS["grey"]
            label.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: "
                f"{'700' if index == active_index else '500'};"
            )

    @staticmethod
    def _format_bytes(value: int) -> str:
        """Return a compact binary-size string suitable for the progress label."""
        if value < 1024 * 1024:
            return f"{max(0, value) / 1024:.1f} KB"
        return f"{max(0, value) / (1024 * 1024):.1f} MB"
