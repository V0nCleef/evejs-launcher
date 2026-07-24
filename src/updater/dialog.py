"""Dark-themed modal dialog that presents an available update to the user.

Displays the current version, new version, a scrollable markdown changelog,
release date, and three action buttons: Download & Install, Remind Me Later,
and Skip This Version.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS

if TYPE_CHECKING:
    pass


class UpdateDialog(QDialog):
    """Modal dialog informing the user about a newer launcher version.

    Parameters
    ----------
    current_version:
        The version string currently installed (e.g. ``"1.0.0"``).
    new_version:
        The remote tag name (e.g. ``"v1.1.0"`` or ``"1.1.0"``).
    changelog:
        Markdown body from the GitHub release.
    download_url:
        Direct URL of the ``.exe`` asset to download.
    published_at:
        ISO-8601 timestamp string from GitHub.
    parent:
        Optional parent widget.
    """

    def __init__(
        self,
        current_version: str,
        new_version: str,
        changelog: str,
        download_url: str,
        published_at: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._current_version = current_version
        self._new_version = new_version
        self._changelog = changelog
        self._download_url = download_url
        self._published_at = published_at
        self._skip_requested: bool = False

        self._build_ui()
        self._apply_styles()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def skip_requested(self) -> bool:
        """*True* when the user pressed **Skip This Version**."""
        return self._skip_requested

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Assemble the dialog layout top-to-bottom."""
        self.setWindowTitle("Update Available")
        self.setFixedSize(520, 520)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setModal(True)

        # ── Root layout ───────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(0)

        # ── Header row: logo + title ──────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        # Logo placeholder — attempt to load the app icon; fall back to
        # a styled unicode label if the icon file is missing.
        logo_label = QLabel()
        logo_path = (
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
            / "assets"
            / "icon.png"
        )
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path)).scaled(
                40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            logo_label.setPixmap(pixmap)
        else:
            logo_label.setText("\u26a1")  # ⚡ lightning bolt
            logo_label.setFont(QFont("Segoe UI", 22))
            logo_label.setStyleSheet(f"color: {COLORS['teal']};")
        logo_label.setFixedSize(44, 44)
        header_row.addWidget(logo_label)

        title_label = QLabel("Update Available")
        title_label.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title_label.setStyleSheet(f"color: {COLORS['white']};")
        header_row.addWidget(title_label)
        header_row.addStretch()

        root.addLayout(header_row)
        root.addSpacing(18)

        # ── Version row: current → new ────────────────────────────────
        version_row = QHBoxLayout()
        version_row.setSpacing(10)
        version_row.setContentsMargins(0, 0, 0, 0)

        cur_ver = QLabel(self._current_version)
        cur_ver.setFont(QFont("Consolas", 13))
        cur_ver.setStyleSheet(
            f"color: {COLORS['grey']}; "
            f"background: {COLORS['card']}; "
            "border-radius: 6px; "
            "padding: 6px 14px;"
        )
        version_row.addWidget(cur_ver)

        arrow = QLabel("\u2192")  # →
        arrow.setFont(QFont("Segoe UI", 14))
        arrow.setStyleSheet(f"color: {COLORS['teal']};")
        version_row.addWidget(arrow)

        new_ver = QLabel(self._new_version.lstrip("vV"))
        new_ver.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
        new_ver.setStyleSheet(
            f"color: {COLORS['green']}; "
            f"background: {COLORS['card']}; "
            "border-radius: 6px; "
            "padding: 6px 14px;"
        )
        version_row.addWidget(new_ver)
        version_row.addStretch()

        root.addLayout(version_row)
        root.addSpacing(14)

        # ── Release date ──────────────────────────────────────────────
        date_text = self._format_date(self._published_at)
        date_label = QLabel(f"Released: {date_text}")
        date_label.setFont(QFont("Segoe UI", 10))
        date_label.setStyleSheet(f"color: {COLORS['grey']};")
        root.addWidget(date_label)
        root.addSpacing(14)

        # ── Changelog header ──────────────────────────────────────────
        changelog_header = QLabel(
            f"What's new in v{self._new_version.lstrip('vV')}"
        )
        changelog_header.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        changelog_header.setStyleSheet(f"color: {COLORS['white']};")
        root.addWidget(changelog_header)
        root.addSpacing(8)

        # ── Changelog body ────────────────────────────────────────────
        self._changelog_view = QTextEdit()
        self._changelog_view.setReadOnly(True)
        self._changelog_view.setMarkdown(self._changelog or "*No changelog provided.*")
        self._changelog_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._changelog_view, stretch=1)
        root.addSpacing(16)

        # ── Button row ────────────────────────────────────────────────
        button_row = QHBoxLayout()
        button_row.setSpacing(10)

        # Skip This Version (muted / ghost)
        self._skip_btn = QPushButton("Skip This Version")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        button_row.addWidget(self._skip_btn)

        button_row.addStretch()

        # Remind Me Later (ghost)
        self._remind_btn = QPushButton("Remind Me Later")
        self._remind_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remind_btn.clicked.connect(self.reject)
        button_row.addWidget(self._remind_btn)

        # Download & Install (primary / teal)
        self._install_btn = QPushButton("Download && Install")
        self._install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_btn.clicked.connect(self.accept)
        button_row.addWidget(self._install_btn)

        root.addLayout(button_row)

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        """Apply the dark-themed stylesheet to the dialog and its children."""
        self.setStyleSheet(
            f"""
            UpdateDialog {{
                background-color: {COLORS['panel']};
                border: 1px solid {COLORS['steel']};
                border-radius: 12px;
            }}
            """
        )

        self._changelog_view.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {COLORS['card']};
                color: {COLORS['white']};
                border: 1px solid {COLORS['steel']};
                border-radius: 8px;
                padding: 10px;
                font-size: 13px;
                selection-background-color: {COLORS['teal_dim']};
            }}
            QScrollBar:vertical {{
                background: {COLORS['card']};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {COLORS['steel']};
                border-radius: 4px;
                min-height: 30px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """
        )

        # ── Primary button (Download & Install) ───────────────────────
        self._install_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLORS['teal']};
                color: {COLORS['void_black']};
                border: none;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {COLORS['teal_dim']};
            }}
            QPushButton:pressed {{
                background-color: {COLORS['teal_dim']};
            }}
            """
        )

        # ── Ghost / muted buttons ─────────────────────────────────────
        ghost_style = (
            f"""
            QPushButton {{
                background: transparent;
                color: {COLORS['grey']};
                border: 1px solid {COLORS['steel']};
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                color: {COLORS['white']};
                border-color: {COLORS['grey']};
            }}
            QPushButton:pressed {{
                color: {COLORS['white']};
                background-color: {COLORS['steel']};
            }}
            """
        )
        self._remind_btn.setStyleSheet(ghost_style)
        self._skip_btn.setStyleSheet(ghost_style)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_skip(self) -> None:
        """Mark skip as requested and close the dialog."""
        self._skip_requested = True
        self.reject()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_date(iso_string: str) -> str:
        """Turn an ISO-8601 timestamp into a human-readable date string."""
        if not iso_string:
            return "Unknown"
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return dt.strftime("%B %d, %Y")
        except (ValueError, TypeError):
            return iso_string
