"""Image-backed, keyboard-accessible character setup choices."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from src.widgets.ui_translation import (
    register_translatable_widget_tree,
    set_translatable_accessible_name,
)


class CharacterSetupButton(QPushButton):
    def __init__(
        self, title: str, subtitle: str, status: str, icon_name: str,
        *, accent: str, parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("characterSetupButton")
        self.setCheckable(True)
        self.setMinimumHeight(176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        set_translatable_accessible_name(self, title)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(5)
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        self.status_label = QLabel(status)
        self.status_label.setObjectName("setupStatus")
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        self.selection_label = QLabel("")
        self.selection_label.setObjectName("setupSelected")
        self.selection_label.setFixedSize(18, 18)
        self.selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_row.addWidget(self.selection_label)
        layout.addLayout(status_row)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(68, 68)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "character_creation" / icon_name
        self.icon_label.setPixmap(QPixmap(str(icon_path)).scaled(
            68, 68, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("setupTitle")
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("setupSubtitle")
        for label in (self.title_label, self.subtitle_label):
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.setStyleSheet(f"""
            QPushButton#characterSetupButton {{
                background: #0d1926; border: 1px solid #294153; border-radius: 9px;
                padding: 0;
            }}
            QPushButton#characterSetupButton:hover {{ border-color: #64869a; }}
            QPushButton#characterSetupButton:checked {{ border: 1px solid {accent}; }}
            QPushButton#characterSetupButton:focus {{ border: 2px solid {accent}; }}
            QPushButton#characterSetupButton:disabled {{ border-color: #223443; }}
            QLabel {{ background: transparent; border: none; color: #e6f0f8; }}
            QLabel#setupStatus {{ color: {accent}; font-size: 9px; font-weight: 600; }}
            QLabel#setupSelected {{ color: {accent}; font-size: 13px; }}
            QLabel#setupTitle {{ font-size: 15px; font-weight: 600; }}
            QLabel#setupSubtitle {{ color: #91a7ba; font-size: 11px; }}
        """)
        self.toggled.connect(self._show_selection)
        register_translatable_widget_tree(self)

    def _show_selection(self, selected: bool) -> None:
        self.selection_label.setText("✓" if selected else "")
