"""Detail panel widget for EveJS Launcher V2."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath, QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
    QStackedWidget,
)

from src.constants import COLORS as C, SEMANTIC_COLORS as S, Status
from src.widgets.ui_translation import (
    set_translatable_accessible_description,
    set_translatable_accessible_name,
    set_translatable_text,
    set_translatable_tooltip,
)


class BorderedPortraitLabel(QLabel):
    """256×256 label with a teal border."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(104, 104)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap: Optional[QPixmap] = None
        self._update_style()

    def _update_style(self) -> None:
        if self._pixmap is None:
            self.setStyleSheet(
                f"background-color: rgba(9, 24, 36, 235); border: 1px solid {S['border_bright']}; border-radius: 5px;"
            )
        else:
            self.setStyleSheet(
                f"background-color: transparent; border: 1px solid {C['teal']}; border-radius: 5px;"
            )

    def set_portrait(self, pixmap: Optional[QPixmap]) -> None:
        self._pixmap = pixmap
        self._update_style()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._pixmap is not None:
            scaled = self._pixmap.scaled(
                102, 102,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (104 - scaled.width()) // 2
            y = (104 - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()


class DetailPanel(QFrame):
    """280px fixed-width detail panel."""

    launch_clicked = pyqtSignal()
    hide_clicked = pyqtSignal()
    log_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("characterDetailPanel")
        self.setProperty("deepSignal", True)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16_777_215)
        self.setMinimumHeight(0)
        self.setMaximumHeight(150)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setStyleSheet(
            f"""
            QFrame#characterDetailPanel {{
                background-color: rgba(6, 17, 28, 238);
                border: 1px solid {S['border_bright']};
                border-radius: 7px;
            }}
            """
        )

        self._username: str = ""
        self._char_name: str = ""
        self._char_id: int = 0
        self._portrait_pixmap: Optional[QPixmap] = None
        self._launch_available = True
        self._launch_unavailable_reason = ""
        self._launch_pending = False
        self._character_status = Status.READY

        self._setup_ui()
        self.show_empty()

    def _setup_ui(self) -> None:
        """Build the compact horizontal selected-character command pane."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        empty_widget = QWidget()
        empty_widget.setProperty("deepSignal", True)
        empty_layout = QHBoxLayout(empty_widget)
        empty_layout.setContentsMargins(18, 12, 18, 12)
        empty_layout.addStretch()
        empty_label = QLabel("Select a character to inspect capsule telemetry")
        empty_label.setProperty("class", "pageSubtitle")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch()
        self._stack.addWidget(empty_widget)

        populated_widget = QWidget()
        populated_widget.setProperty("deepSignal", True)
        pop_layout = QHBoxLayout(populated_widget)
        pop_layout.setContentsMargins(14, 12, 14, 12)
        pop_layout.setSpacing(14)

        self._portrait = BorderedPortraitLabel()
        pop_layout.addWidget(
            self._portrait,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )

        identity = QWidget()
        identity.setProperty("deepSignal", True)
        identity.setMinimumWidth(124)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 2, 0, 2)
        identity_layout.setSpacing(3)
        eyebrow = QLabel("SELECTED CAPSULEER")
        eyebrow.setProperty("class", "pageEyebrow")
        identity_layout.addWidget(eyebrow)
        self._name_label = QLabel()
        self._name_label.setStyleSheet(
            f"color: {S['text_primary']}; font-size: 18px; font-weight: 700;"
        )
        self._name_label.setWordWrap(True)
        identity_layout.addWidget(self._name_label)
        self._account_label = QLabel()
        self._account_label.setStyleSheet(
            f"color: {S['accent']}; font-size: 11px;"
        )
        identity_layout.addWidget(self._account_label)
        identity_layout.addStretch()
        pop_layout.addWidget(identity, 2)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet(f"color: {S['border']};")
        pop_layout.addWidget(divider)

        stats_widget = QWidget()
        stats_widget.setProperty("deepSignal", True)
        stats_widget.setMinimumWidth(190)
        stats_layout = QGridLayout(stats_widget)
        stats_layout.setContentsMargins(0, 1, 0, 1)
        stats_layout.setHorizontalSpacing(12)
        stats_layout.setVerticalSpacing(5)
        self._stat_rows: dict[str, tuple[QLabel, QLabel]] = {}
        for row_index, label_text in enumerate(
            ("ISK", "SP", "Ship", "Location", "Sec Status")
        ):
            label = QLabel()
            set_translatable_text(label, label_text.upper())
            label.setStyleSheet(
                f"color: {S['text_muted']}; font-size: 9px;"
            )
            value = QLabel("—")
            value.setStyleSheet(
                f"color: {S['text_primary']}; font-size: 11px; "
                "font-family: 'Consolas', monospace;"
            )
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._stat_rows[label_text] = (label, value)
            stats_layout.addWidget(label, row_index, 0)
            stats_layout.addWidget(value, row_index, 1)
        stats_layout.setColumnStretch(1, 1)
        pop_layout.addWidget(stats_widget, 3)

        actions = QWidget()
        actions.setProperty("deepSignal", True)
        actions.setMinimumWidth(154)
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(0, 1, 0, 1)
        actions_layout.setSpacing(7)
        self._launch_btn = QPushButton("LAUNCH")
        self._launch_btn.setFixedHeight(44)
        self._launch_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgba(105, 72, 0, 232); "
            f"color: #FFE39A; border: 1px solid {C['gold']}; border-radius: 4px; "
            "font-size: 15px; font-weight: 700; }}"
            f"QPushButton:hover:enabled {{ background-color: {C['gold']}; "
            f"color: {C['void_black']}; }}"
            "QPushButton:focus { border: 2px solid #FFF2C3; }"
            f"QPushButton:disabled {{ background-color: rgba(10, 22, 32, 210); "
            f"color: {S['text_muted']}; border-color: {S['border']}; }}"
        )
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        actions_layout.addWidget(self._launch_btn)

        secondary_row = QHBoxLayout()
        secondary_row.setContentsMargins(0, 0, 0, 0)
        secondary_row.setSpacing(6)
        secondary_style = (
            f"QPushButton {{ background-color: rgba(7, 17, 29, 190); "
            f"color: {S['text_secondary']}; border: 1px solid {S['border_bright']}; "
            "border-radius: 4px; font-size: 9px; font-weight: 600; }}"
            f"QPushButton:hover {{ color: {S['text_primary']}; border-color: {S['accent']}; }}"
            f"QPushButton:focus {{ border: 2px solid {S['accent']}; }}"
        )
        self._hide_btn = QPushButton("HIDE")
        self._hide_btn.setFixedHeight(30)
        self._hide_btn.setStyleSheet(secondary_style)
        self._hide_btn.setAccessibleName("Hide selected character")
        self._hide_btn.clicked.connect(self.hide_clicked.emit)
        secondary_row.addWidget(self._hide_btn)
        self._log_btn = QPushButton("VIEW LOG")
        self._log_btn.setFixedHeight(30)
        self._log_btn.setStyleSheet(secondary_style)
        self._log_btn.setAccessibleName("View selected character log")
        self._log_btn.clicked.connect(self.log_clicked.emit)
        secondary_row.addWidget(self._log_btn)
        actions_layout.addLayout(secondary_row)
        actions_layout.addStretch()
        pop_layout.addWidget(actions, 2)

        self._stack.addWidget(populated_widget)

    def _setup_legacy_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        # ── Empty state ──
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setContentsMargins(20, 40, 20, 20)
        empty_layout.setSpacing(16)

        empty_layout.addStretch()

        # Dimmed portrait placeholder
        placeholder = QLabel()
        placeholder.setFixedSize(128, 128)
        placeholder.setStyleSheet(
            f"background-color: {C['steel']}; border: 1px solid {C['grey']}; border-radius: 4px;"
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(placeholder, alignment=Qt.AlignmentFlag.AlignHCenter)

        select_label = QLabel("Select a character")
        select_label.setStyleSheet(
            f"color: {C['grey']}; font-size: 14px;"
        )
        select_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(select_label)

        empty_layout.addStretch()

        self._stack.addWidget(empty_widget)

        # ── Populated state ──
        populated_widget = QWidget()
        pop_layout = QVBoxLayout(populated_widget)
        pop_layout.setContentsMargins(16, 16, 16, 16)
        pop_layout.setSpacing(12)

        # Portrait
        self._portrait = BorderedPortraitLabel()
        pop_layout.addWidget(self._portrait, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Character name
        self._name_label = QLabel()
        self._name_label.setStyleSheet(
            f"color: {C['white']}; font-size: 20px; font-weight: bold;"
        )
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pop_layout.addWidget(self._name_label)

        # Account
        self._account_label = QLabel()
        self._account_label.setStyleSheet(
            f"color: {C['teal']}; font-size: 12px;"
        )
        self._account_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pop_layout.addWidget(self._account_label)

        pop_layout.addSpacing(8)

        # Stats grid
        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(6)

        self._stat_rows: dict[str, tuple[QLabel, QLabel]] = {}
        for label_text in ("ISK", "SP", "Ship", "Location", "Sec Status"):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"color: {C['grey']}; font-size: 11px;"
            )
            lbl.setFixedWidth(70)
            row_layout.addWidget(lbl)

            val = QLabel("—")
            val.setStyleSheet(
                f"color: {C['white']}; font-size: 13px; font-family: 'Consolas', monospace;"
            )
            row_layout.addWidget(val)
            row_layout.addStretch()

            self._stat_rows[label_text] = (lbl, val)
            stats_layout.addWidget(row)

        pop_layout.addWidget(stats_widget)

        pop_layout.addStretch()

        # Action buttons
        self._launch_btn = QPushButton("LAUNCH")
        self._launch_btn.setFixedHeight(36)
        self._launch_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._launch_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {C['teal']};
                color: {C['void_black']};
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {C['teal_dim']};
            }}
            """
        )
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        pop_layout.addWidget(self._launch_btn)

        self._hide_btn = QPushButton("HIDE CHARACTER")
        self._hide_btn.setFixedHeight(32)
        self._hide_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hide_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {C['grey']};
                border: 1px solid {C['grey']};
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {C['white']};
                border-color: {C['white']};
            }}
            """
        )
        self._hide_btn.clicked.connect(self.hide_clicked.emit)
        pop_layout.addWidget(self._hide_btn)

        self._log_btn = QPushButton("VIEW LOG")
        self._log_btn.setFixedHeight(32)
        self._log_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._log_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {C['grey']};
                border: 1px solid {C['grey']};
                border-radius: 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {C['white']};
                border-color: {C['white']};
            }}
            """
        )
        self._log_btn.clicked.connect(self.log_clicked.emit)
        pop_layout.addWidget(self._log_btn)

        self._stack.addWidget(populated_widget)

    def show_empty(self) -> None:
        """Show the empty state."""
        self._stack.setCurrentIndex(0)
        set_translatable_accessible_name(self, "Character details")
        set_translatable_accessible_description(self, "No character selected")

    def show_character(
        self,
        username: str,
        char_name: str,
        char_id: int,
        portrait_pixmap: Optional[QPixmap],
        detail_dict: dict[str, str],
    ) -> None:
        """Populate panel with character data."""
        self._username = username
        self._char_name = char_name
        self._char_id = char_id
        self._portrait_pixmap = portrait_pixmap

        self._name_label.setText(char_name)
        self._account_label.setText(username)
        self._portrait.set_portrait(portrait_pixmap)
        set_translatable_accessible_name(
            self._launch_btn,
            f"Launch {char_name}",
            allow_templates=True,
        )
        set_translatable_accessible_name(
            self,
            f"Details for {char_name}",
            allow_templates=True,
        )
        set_translatable_accessible_description(
            self,
            f"Selected character on account {username}.",
            allow_templates=True,
        )

        # Populate stats
        for key, (_, val_label) in self._stat_rows.items():
            val_label.setText(detail_dict.get(key, "—"))

        self._apply_launch_button_state()
        self._stack.setCurrentIndex(1)

    def get_character(self) -> tuple[str, str, int]:
        """Return current character info."""
        return self._username, self._char_name, self._char_id

    def set_portrait(self, pixmap: Optional[QPixmap]) -> None:
        """Update only the portrait for the currently displayed character."""
        self._portrait_pixmap = pixmap
        self._portrait.set_portrait(pixmap)

    def set_launch_available(self, enabled: bool, reason: str = "") -> None:
        """Enable launch or clearly present the detail panel as view-only."""
        self._launch_available = bool(enabled)
        self._launch_unavailable_reason = "" if enabled else reason
        self._apply_launch_button_state()

    def set_launch_pending(self, pending: bool) -> None:
        """Show an immediate, non-clickable state while this client is prepared."""
        self._launch_pending = bool(pending)
        self._apply_launch_button_state()

    def set_character_status(self, status: Status) -> None:
        """Mirror the selected card's per-account launch state."""
        self._character_status = status
        self._apply_launch_button_state()

    def _apply_launch_button_state(self) -> None:
        if self._launch_pending or self._character_status is Status.LAUNCHING:
            self._launch_btn.setEnabled(False)
            set_translatable_text(self._launch_btn, "LAUNCHING...")
            set_translatable_tooltip(
                self._launch_btn,
                "Preparing the profile and starting EVE",
            )
            return
        if not self._launch_available:
            self._launch_btn.setEnabled(False)
            set_translatable_text(self._launch_btn, "VIEW ONLY")
            set_translatable_tooltip(
                self._launch_btn,
                self._launch_unavailable_reason,
            )
            return
        blocked = {
            Status.RUNNING: (
                "RUNNING",
                "This character's EVE client is already running.",
            ),
            Status.SAME_ACCOUNT_ONLINE: (
                "WAITING",
                "Another character on this account is already running.",
            ),
            Status.BANNED: (
                "BANNED",
                "This account is banned and cannot launch a character.",
            ),
        }
        blocked_state = blocked.get(self._character_status)
        if blocked_state is not None:
            text, tooltip = blocked_state
            self._launch_btn.setEnabled(False)
            set_translatable_text(self._launch_btn, text)
            set_translatable_tooltip(self._launch_btn, tooltip)
            return
        self._launch_btn.setEnabled(True)
        set_translatable_text(self._launch_btn, "LAUNCH")
        self._launch_btn.setToolTip("")

    def retranslate_ui(self) -> None:
        """Refresh retained launch state and selected-character framing."""
        self._apply_launch_button_state()
        if self._stack.currentIndex() == 0 or not self._char_name:
            set_translatable_accessible_name(self, "Character details")
            set_translatable_accessible_description(self, "No character selected")
            return
        set_translatable_accessible_name(
            self._launch_btn,
            f"Launch {self._char_name}",
            allow_templates=True,
        )
        set_translatable_accessible_name(
            self,
            f"Details for {self._char_name}",
            allow_templates=True,
        )
        set_translatable_accessible_description(
            self,
            f"Selected character on account {self._username}.",
            allow_templates=True,
        )
