"""Detail panel widget for EveJS Launcher V2."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QPainterPath, QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
    QStackedWidget,
)

from src.constants import COLORS as C


class BorderedPortraitLabel(QLabel):
    """256×256 label with a teal border."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(256, 256)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap: Optional[QPixmap] = None
        self._update_style()

    def _update_style(self) -> None:
        if self._pixmap is None:
            self.setStyleSheet(
                f"background-color: {C['steel']}; border: 2px solid {C['grey']}; border-radius: 4px;"
            )
        else:
            self.setStyleSheet(
                f"background-color: transparent; border: 2px solid {C['teal']}; border-radius: 4px;"
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
                252, 252,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (256 - scaled.width()) // 2
            y = (256 - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()


class DetailPanel(QFrame):
    """280px fixed-width detail panel."""

    launch_clicked = pyqtSignal()
    hide_clicked = pyqtSignal()
    log_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setMaximumWidth(280)
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {C['panel']};
                border: 1px solid {C['steel']};
                border-radius: 6px;
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

        self._setup_ui()
        self.show_empty()

    def _setup_ui(self) -> None:
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

    def _apply_launch_button_state(self) -> None:
        if self._launch_pending:
            self._launch_btn.setEnabled(False)
            self._launch_btn.setText("LAUNCHING...")
            self._launch_btn.setToolTip("Preparing the profile and starting EVE")
            return
        self._launch_btn.setEnabled(self._launch_available)
        self._launch_btn.setText("LAUNCH" if self._launch_available else "VIEW ONLY")
        self._launch_btn.setToolTip(
            "" if self._launch_available else self._launch_unavailable_reason
        )
