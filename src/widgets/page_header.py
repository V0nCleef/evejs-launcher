"""Reusable Deep Signal page heading with an optional action rail."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.constants import SPACING


class PageHeader(QWidget):
    """Consistent eyebrow, title, subtitle, and right-aligned actions."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        eyebrow: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACING["lg"])

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(SPACING["xs"])

        self.eyebrow_label = QLabel()
        self.eyebrow_label.setProperty("class", "pageEyebrow")
        copy.addWidget(self.eyebrow_label)

        self.title_label = QLabel()
        self.title_label.setProperty("class", "pageTitle")
        copy.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setProperty("class", "pageSubtitle")
        self.subtitle_label.setWordWrap(True)
        copy.addWidget(self.subtitle_label)

        root.addLayout(copy, 1)
        self.action_layout = QHBoxLayout()
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(SPACING["sm"])
        self.action_layout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        root.addLayout(self.action_layout)

        self.set_eyebrow(eyebrow)
        self.set_title(title)
        self.set_subtitle(subtitle)

    def set_eyebrow(self, text: str) -> None:
        text = str(text)
        self.eyebrow_label.setText(text.upper())
        self.eyebrow_label.setVisible(bool(text))

    def set_title(self, text: str) -> None:
        text = str(text)
        self.title_label.setText(text)
        self.setAccessibleName(text)

    def set_subtitle(self, text: str) -> None:
        text = str(text)
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))
        self.setAccessibleDescription(text)

    def add_action(self, widget: QWidget) -> None:
        self.action_layout.addWidget(widget)


__all__ = ["PageHeader"]
