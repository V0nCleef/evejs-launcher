"""Deep Signal dialog that presents an available launcher update.

The updater remains deliberately small and modal, but shares the command-deck
hierarchy used by the rest of the launcher: a release uplink header, a clear
current-to-available version route, glass release notes, and explicit actions.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QDate, QLocale, Qt
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.constants import SEMANTIC_COLORS as S
from src.i18n import current_language, translate_ui_phrase
from src.theme import load_fonts
from src.widgets.deep_signal_background import operations_scene_path
from src.widgets.ui_translation import (
    register_translatable_widget_tree,
    set_translatable_text_template,
)


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
        Direct URL of the launcher release asset to download.
    published_at:
        ISO-8601 timestamp string from GitHub.
    parent:
        Optional parent widget.
    """

    _DESIGN_WIDTH = 680
    _DESIGN_HEIGHT = 650
    _SCREEN_MARGIN = 32

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
        register_translatable_widget_tree(self)
        self._apply_styles()
        self._fit_to_available_screen()

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
        self.setObjectName("updateAvailableDialog")
        self.setProperty("deepSignal", True)
        # Leave native-font headroom for the release-uplink heading and badge.
        # Height is capped against the active screen after styling so the
        # scrollable notes yield space before the bottom action rail can move
        # off-screen on scaled or remote desktops.
        self.setFixedSize(self._DESIGN_WIDTH, self._DESIGN_HEIGHT)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setModal(True)
        self.setAccessibleName("Launcher update available")

        # ── Root layout ───────────────────────────────────────────────
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(0)

        # ── Header row: logo + title ──────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        logo_label = QLabel(self)
        logo_label.setObjectName("updateLogo")
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png"
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path)).scaled(
                36,
                36,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pixmap)
        else:
            logo_label.setText("\u25c9")
            logo_label.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        logo_label.setFixedSize(42, 42)
        header_row.addWidget(logo_label)

        heading = QVBoxLayout()
        heading.setSpacing(1)
        eyebrow = QLabel("DEEP SIGNAL // RELEASE UPLINK", self)
        eyebrow.setObjectName("dialogEyebrow")
        heading.addWidget(eyebrow)
        title_label = QLabel("Update Available", self)
        title_label.setObjectName("dialogTitle")
        heading.addWidget(title_label)
        header_row.addLayout(heading)
        header_row.addStretch()

        channel = QLabel("STABLE CHANNEL", self)
        channel.setObjectName("channelBadge")
        channel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        channel.setAccessibleName("Stable update channel")
        header_row.addWidget(channel, alignment=Qt.AlignmentFlag.AlignTop)

        root.addLayout(header_row)
        root.addSpacing(16)

        self.hero_banner = QLabel(self)
        self.hero_banner.setObjectName("releaseHero")
        self.hero_banner.setFixedHeight(118)
        self.hero_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_hero_scene()

        version_row = QHBoxLayout(self.hero_banner)
        version_row.setSpacing(14)
        version_row.setContentsMargins(18, 15, 18, 15)

        current_block = QVBoxLayout()
        current_block.setSpacing(3)
        current_caption = QLabel("CURRENT BUILD", self.hero_banner)
        current_caption.setObjectName("versionCaption")
        current_block.addWidget(current_caption)
        self.current_version_label = QLabel(self._current_version, self.hero_banner)
        self.current_version_label.setObjectName("currentVersion")
        self.current_version_label.setAccessibleName("Current launcher version")
        current_block.addWidget(self.current_version_label)
        current_block.addStretch()
        version_row.addLayout(current_block, stretch=1)

        arrow = QLabel("\u2192", self.hero_banner)
        arrow.setObjectName("versionArrow")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_row.addWidget(arrow)

        available_block = QVBoxLayout()
        available_block.setSpacing(3)
        available_caption = QLabel("AVAILABLE BUILD", self.hero_banner)
        available_caption.setObjectName("versionCaption")
        available_block.addWidget(available_caption)
        self.new_version_label = QLabel(
            self._new_version.lstrip("vV"), self.hero_banner
        )
        self.new_version_label.setObjectName("availableVersion")
        self.new_version_label.setAccessibleName("Available launcher version")
        available_block.addWidget(self.new_version_label)
        available_block.addStretch()
        version_row.addLayout(available_block, stretch=1)

        root.addWidget(self.hero_banner)
        root.addSpacing(14)

        # ── Release notes ─────────────────────────────────────────────
        notes_header = QHBoxLayout()
        notes_header.setSpacing(10)
        self.changelog_header = QLabel()
        set_translatable_text_template(
            self.changelog_header,
            f"What's new in v{self._new_version.lstrip('vV')}",
        )
        self.changelog_header.setObjectName("releaseNotesTitle")
        notes_header.addWidget(self.changelog_header)
        notes_header.addStretch()
        self.date_label = QLabel()
        set_translatable_text_template(
            self.date_label,
            f"Released: {self._format_date(self._published_at)}",
        )
        self.date_label.setObjectName("releaseDate")
        notes_header.addWidget(self.date_label)
        root.addLayout(notes_header)
        root.addSpacing(8)

        # ── Changelog body ────────────────────────────────────────────
        self._changelog_view = QTextEdit(self)
        self._changelog_view.setObjectName("releaseNotes")
        self._changelog_view.setReadOnly(True)
        fallback_changelog = f"*{translate_ui_phrase('No changelog provided.')}*"
        self._changelog_view.setMarkdown(self._changelog or fallback_changelog)
        self._changelog_view.setAccessibleName("Update release notes")
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
        self._skip_btn.setObjectName("skipUpdateAction")
        self._skip_btn.setProperty("class", "ghost")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        button_row.addWidget(self._skip_btn)

        button_row.addStretch()

        # Remind Me Later (ghost)
        self._remind_btn = QPushButton("Remind Me Later")
        self._remind_btn.setObjectName("remindUpdateAction")
        self._remind_btn.setProperty("class", "secondary")
        self._remind_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._remind_btn.clicked.connect(self.reject)
        button_row.addWidget(self._remind_btn)

        # Download & Install (primary / teal)
        self._install_btn = QPushButton("Download && Install")
        self._install_btn.setObjectName("installUpdateAction")
        self._install_btn.setProperty("class", "primary")
        self._install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_btn.setDefault(True)
        self._install_btn.clicked.connect(self.accept)
        button_row.addWidget(self._install_btn)

        root.addLayout(button_row)

    def _set_hero_scene(self) -> None:
        """Use the approved Deep Signal orbital scene with a safe fallback."""
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
        if source.isNull():
            return

        target_width = self.width() - 52
        target_height = self.hero_banner.height()
        scaled = source.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        crop_x = max(0, int((scaled.width() - target_width) * 0.68))
        crop_y = max(0, (scaled.height() - target_height) // 2)
        self.hero_banner.setPixmap(
            scaled.copy(crop_x, crop_y, target_width, target_height)
        )

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------

    def _apply_styles(self) -> None:
        """Apply self-contained Deep Signal styling for normal and handoff apps."""
        try:
            fonts = load_fonts()
        except Exception:
            fonts = {"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"}
        header = fonts["header"]
        body = fonts["body"]
        mono = fonts["mono"]

        self.setStyleSheet(
            f"""
            QDialog#updateAvailableDialog {{
                background-color: {S['background']};
                border: 1px solid {S['border_bright']};
                border-radius: 13px;
            }}
            QDialog#updateAvailableDialog QLabel {{
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
                font-size: 23px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#channelBadge {{
                color: {S['success']};
                background-color: rgba(79, 224, 127, 20);
                border: 1px solid rgba(79, 224, 127, 112);
                border-radius: 5px;
                padding: 5px 9px;
                font-family: '{header}';
                font-size: 8px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#releaseHero {{
                background-color: rgba(3, 10, 17, 238);
                border: 1px solid {S['border_bright']};
                border-radius: 9px;
            }}
            QLabel#versionCaption {{
                color: {S['text_muted']};
                font-family: '{header}';
                font-size: 8px;
                font-weight: 700;
                letter-spacing: 1.5px;
            }}
            QLabel#currentVersion, QLabel#availableVersion {{
                background-color: rgba(3, 10, 17, 222);
                border: 1px solid {S['border']};
                border-radius: 5px;
                padding: 6px 10px;
                font-family: '{mono}';
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#currentVersion {{ color: {S['text_secondary']}; }}
            QLabel#availableVersion {{
                color: {S['success']};
                border-color: rgba(79, 224, 127, 115);
            }}
            QLabel#versionArrow {{
                color: {S['accent']};
                font-family: '{header}';
                font-size: 24px;
                font-weight: 500;
            }}
            QLabel#releaseNotesTitle {{
                color: {S['text_primary']};
                font-family: '{header}';
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.5px;
            }}
            QLabel#releaseDate {{
                color: {S['text_muted']};
                font-size: 10px;
            }}
            QTextEdit#releaseNotes {{
                background-color: rgba(8, 20, 31, 232);
                color: {S['text_secondary']};
                border: 1px solid {S['border']};
                border-radius: 9px;
                padding: 11px;
                font-family: '{body}';
                font-size: 12px;
                selection-background-color: {S['accent_soft']};
            }}
            QTextEdit#releaseNotes:focus {{ border-color: {S['accent_dim']}; }}
            QScrollBar:vertical {{
                background: rgba(3, 10, 17, 190);
                width: 8px;
                margin: 2px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {S['border_bright']};
                border-radius: 3px;
                min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {S['accent_dim']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QPushButton {{
                min-height: 36px;
                padding: 0 15px;
                border-radius: 5px;
                font-family: '{header}';
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.7px;
            }}
            QPushButton[class="primary"] {{
                background-color: {S['accent']};
                color: {S['background']};
                border: 1px solid {S['accent']};
            }}
            QPushButton[class="primary"]:hover {{
                background-color: {S['text_primary']};
                border-color: {S['text_primary']};
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
            QPushButton[class="ghost"] {{
                background: transparent;
                color: {S['text_muted']};
                border: 1px solid {S['border']};
            }}
            QPushButton[class="ghost"]:hover {{
                background-color: {S['surface_hover']};
                color: {S['text_primary']};
                border-color: {S['border_bright']};
            }}
            QPushButton:focus {{ border: 2px solid {S['text_primary']}; }}
            QPushButton:pressed {{ padding-top: 1px; }}
            """
        )

    def _fit_to_available_screen(self, available_height: int | None = None) -> None:
        """Keep the frameless action rail inside the active screen.

        ``available_height`` is injectable for deterministic geometry tests;
        production uses the screen selected by Qt for this dialog.
        """
        if available_height is None:
            screen = self.screen()
            if screen is None:
                return
            available_height = screen.availableGeometry().height()

        usable_height = max(1, int(available_height) - self._SCREEN_MARGIN)
        minimum_layout_height = self.minimumSizeHint().height()
        target_height = min(
            self._DESIGN_HEIGHT,
            max(minimum_layout_height, usable_height),
        )
        self.setFixedSize(self._DESIGN_WIDTH, target_height)

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
            return translate_ui_phrase("Unknown")
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            locale_names = {
                "en": "en_GB",
                "zh_CN": "zh_CN",
                "ja": "ja_JP",
                "ko": "ko_KR",
                "fr": "fr_FR",
                "de": "de_DE",
                "nl": "nl_NL",
                "ru": "ru_RU",
            }
            locale = QLocale(locale_names.get(current_language(), "en_GB"))
            return locale.toString(
                QDate(dt.year, dt.month, dt.day),
                QLocale.FormatType.LongFormat,
            )
        except (ValueError, TypeError):
            return iso_string
