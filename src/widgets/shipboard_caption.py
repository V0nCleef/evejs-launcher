"""Transient, accessible captions for local LYRA announcements."""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from src.constants import COLORS
from src.i18n import format_ui_phrase, translate_ui_phrase
from src.widgets.ui_translation import register_translatable_widget_tree


class ShipboardCaption(QFrame):
    """A compact overlay that mirrors speech without blocking launcher actions."""

    DISPLAY_MS = 5_000
    HORIZONTAL_MARGIN = 28
    BOTTOM_MARGIN = 58

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shipboardCaption")
        self.setProperty("deepSignal", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("LYRA shipboard caption")
        self.setStyleSheet(
            f"""
            QFrame#shipboardCaption {{
                background: rgba(5, 13, 22, 0.96);
                border: 1px solid rgba(0, 200, 224, 0.62);
                border-radius: 7px;
            }}
            QLabel#shipboardCaptionIdentity {{
                color: {COLORS['teal']};
                background: rgba(0, 200, 224, 0.10);
                border: 1px solid rgba(0, 200, 224, 0.38);
                border-radius: 3px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 3px 5px;
            }}
            QLabel#shipboardCaptionMessage {{
                color: {COLORS['white']};
                background: transparent;
                font-size: 12px;
                font-weight: 500;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 16, 9)
        layout.setSpacing(10)

        identity = QLabel("LYRA", self)
        identity.setObjectName("shipboardCaptionIdentity")
        identity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity.setFixedWidth(48)
        layout.addWidget(identity)

        self.message_label = QLabel("", self)
        self.message_label.setObjectName("shipboardCaptionMessage")
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.message_label, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DISPLAY_MS)
        self._timer.timeout.connect(self.hide)
        self._source_message = ""
        register_translatable_widget_tree(self)
        self.hide()

    def show_caption(self, text: str) -> None:
        """Show one normalized local caption and restart its dwell timer."""
        message = " ".join(str(text).split())
        if not message:
            return
        self._source_message = message
        self._render_message()
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start()

    def _render_message(self) -> None:
        message = translate_ui_phrase(self._source_message)
        self.message_label.setText(message)
        self.message_label.setAccessibleDescription(message)
        self.setAccessibleDescription(
            format_ui_phrase("LYRA says: {message}", message=message)
        )

    def retranslate_ui(self) -> None:
        """Refresh the visible fixed caption without replaying its audio."""
        if self._source_message:
            self._render_message()

    def reposition(self) -> None:
        """Keep the caption centered above the footer across window resizes."""
        parent = self.parentWidget()
        if parent is None:
            return
        maximum_width = max(280, min(660, parent.width() - 2 * self.HORIZONTAL_MARGIN))
        self.setMaximumWidth(maximum_width)
        self.message_label.setMaximumWidth(maximum_width - 102)
        self.adjustSize()
        x = max(self.HORIZONTAL_MARGIN, (parent.width() - self.width()) // 2)
        y = max(0, parent.height() - self.height() - self.BOTTOM_MARGIN)
        self.move(x, y)
