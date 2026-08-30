"""New-character tile shown alongside existing character cards."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.constants import SEMANTIC_COLORS as S
from src.widgets.ui_translation import register_translatable_widget_tree
from src.widgets.ui_translation import (
    set_translatable_accessible_description,
    set_translatable_accessible_name,
    set_translatable_text,
    set_translatable_tooltip,
)


class _PlusGlyph(QWidget):
    """Static Deep Signal reticle used for the create-character action."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(88, 88)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._available = True

    def set_available(self, available: bool) -> None:
        self._available = bool(available)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent = QColor(S["accent"] if self._available else S["text_muted"])
        accent.setAlpha(220 if self._available else 120)
        soft = QColor(S["accent_soft"])
        soft.setAlpha(130 if self._available else 60)

        painter.setPen(QPen(soft, 1.0))
        painter.setBrush(QColor(5, 17, 28, 190))
        painter.drawEllipse(7, 7, 74, 74)
        painter.drawEllipse(15, 15, 58, 58)

        painter.setPen(QPen(accent, 2.0))
        painter.drawLine(44, 27, 44, 61)
        painter.drawLine(27, 44, 61, 44)

        painter.setPen(QPen(accent, 1.0))
        for start, end in (
            ((44, 3), (44, 10)),
            ((44, 78), (44, 85)),
            ((3, 44), (10, 44)),
            ((78, 44), (85, 44)),
        ):
            painter.drawLine(start[0], start[1], end[0], end[1])
        painter.end()


class NewCharacterCard(QFrame):
    """Keyboard-accessible provisioning card aligned with character cards."""

    requested = pyqtSignal()

    CARD_MIN_WIDTH = 148
    CARD_MAX_WIDTH = 196
    CARD_HEIGHT = 252

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._available = True
        self._reason = ""
        self._hovered = False
        self.setObjectName("newCharacterCard")
        self.setProperty("deepSignal", True)
        self.setMinimumWidth(self.CARD_MIN_WIDTH)
        self.setMaximumWidth(self.CARD_MAX_WIDTH)
        self.setFixedHeight(self.CARD_HEIGHT)
        self.resize(self.CARD_MAX_WIDTH, self.CARD_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        set_translatable_accessible_name(self, "Create a new local character")
        self._build_ui()
        register_translatable_widget_tree(self)
        self._restyle()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(5)

        signal_row = QHBoxLayout()
        signal_row.setContentsMargins(0, 0, 0, 0)
        signal_row.setSpacing(4)
        self._signal_dot = QLabel("+")
        self._signal_dot.setFixedWidth(10)
        self._signal_dot.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        signal_row.addWidget(self._signal_dot)
        self._signal_label = QLabel("NEW PILOT")
        self._signal_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        signal_row.addWidget(self._signal_label)
        signal_row.addStretch()
        layout.addLayout(signal_row)

        self._accent_line = QFrame()
        self._accent_line.setFixedHeight(2)
        self._accent_line.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self._accent_line)

        self._glyph = _PlusGlyph(self)
        layout.addWidget(self._glyph, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._title = QLabel("CREATE CHARACTER")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._title)

        self._subtitle = QLabel("Provision a local EveJS account and pilot")
        self._subtitle.setWordWrap(True)
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        layout.addWidget(self._subtitle)
        layout.addStretch(1)

        self._button = QPushButton("CREATE")
        self._button.setFixedHeight(28)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setAccessibleName("Create a new local character")
        self._button.clicked.connect(self.requested.emit)
        layout.addWidget(self._button)

    def set_available(self, enabled: bool, reason: str = "") -> None:
        self._available = bool(enabled)
        self._reason = "" if enabled else reason
        self._button.setEnabled(enabled)
        unavailable_label = "UNAVAILABLE"
        reason_folded = self._reason.casefold()
        if "native" in reason_folded:
            unavailable_label = "NATIVE ONLY"
        elif "managed" in reason_folded or "connect-only" in reason_folded:
            unavailable_label = "MANAGED ONLY"
        set_translatable_text(
            self._button,
            "CREATE" if enabled else unavailable_label,
        )
        set_translatable_tooltip(self._button, self._reason)
        self._glyph.set_available(enabled)
        set_translatable_tooltip(self, self._reason)
        set_translatable_accessible_description(
            self,
            "Character creation is available"
            if enabled
            else self._reason or "Character creation is unavailable",
        )
        self._restyle()

    def retranslate_ui(self) -> None:
        """Refresh retained availability and accessibility copy."""
        set_translatable_accessible_name(self, "Create a new local character")
        set_translatable_accessible_name(
            self._button,
            "Create a new local character",
        )
        self.set_available(self._available, self._reason)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._restyle()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._restyle()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._restyle()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._restyle()
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._available and event.key() in {
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Space,
        }:
            self.requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._available:
            self.requested.emit()
        super().mousePressEvent(event)

    def _restyle(self) -> None:
        active = self._available and (self._hovered or self.hasFocus())
        border = S["accent"] if active else S["border_bright"]
        background = "rgba(10, 31, 44, 232)" if active else "rgba(7, 20, 31, 220)"
        text = S["text_primary"] if self._available else S["text_muted"]
        accent = S["accent"] if self._available else S["text_muted"]
        self.setStyleSheet(
            f"""
            QFrame#newCharacterCard {{
                background-color: {background};
                border: 2px dashed {border};
                border-radius: 8px;
            }}
            QFrame#newCharacterCard QLabel {{
                background: transparent;
                border: none;
            }}
            """
        )
        self._signal_dot.setStyleSheet(
            f"color: {accent}; border: none; background: transparent; "
            "font-size: 10px; font-weight: 700;"
        )
        self._signal_label.setStyleSheet(
            f"color: {S['text_secondary']}; border: none; background: transparent; "
            "font-size: 9px; font-weight: 700; letter-spacing: 1px;"
        )
        self._accent_line.setStyleSheet(
            f"background-color: {accent}; border: none;"
        )
        self._title.setStyleSheet(
            f"color: {text}; border: none; background: transparent; "
            "font-size: 12px; font-weight: 700; letter-spacing: 1px;"
        )
        self._subtitle.setStyleSheet(
            f"color: {S['text_muted']}; border: none; background: transparent; "
            "font-size: 9px;"
        )
        self._button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {accent};
                color: {S['background']};
                border: 1px solid {accent};
                border-radius: 4px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: {S['text_primary']};
                border-color: {S['text_primary']};
            }}
            QPushButton:focus {{ border: 2px solid {S['text_primary']}; }}
            QPushButton:disabled {{
                background-color: {S['surface_elevated']};
                color: {S['text_muted']};
                border-color: {S['border']};
            }}
            """
        )
