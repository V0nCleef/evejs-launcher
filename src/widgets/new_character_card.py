"""New-character tile shown alongside existing character cards."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from src.constants import COLORS as C


class _PlusGlyph(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(128, 128)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(C["teal"]), 3))
        painter.setBrush(QColor(C["deep_space"]))
        painter.drawEllipse(18, 18, 92, 92)
        painter.drawLine(64, 42, 64, 86)
        painter.drawLine(42, 64, 86, 64)
        painter.end()


class NewCharacterCard(QFrame):
    requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._available = True
        self._reason = ""
        self._hovered = False
        self.setFixedSize(220, 280)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_ui()
        self._restyle()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 12)
        layout.setSpacing(8)
        glyph = _PlusGlyph()
        layout.addWidget(glyph, alignment=Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("NEW CHARACTER")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color: {C['white']}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(title)

        subtitle = QLabel("Create a local EveJS account and pilot")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {C['grey']}; font-size: 11px;")
        layout.addWidget(subtitle)
        layout.addStretch()

        self._button = QPushButton("CREATE")
        self._button.setFixedHeight(32)
        self._button.setProperty("class", "primary")
        self._button.clicked.connect(self.requested.emit)
        layout.addWidget(self._button)

    def set_available(self, enabled: bool, reason: str = "") -> None:
        self._available = bool(enabled)
        self._reason = "" if enabled else reason
        self._button.setEnabled(enabled)
        self._button.setText("CREATE" if enabled else "NATIVE ONLY")
        self._button.setToolTip(self._reason)
        self.setToolTip(self._reason)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._restyle()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._restyle()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._available:
            self.requested.emit()
        super().mousePressEvent(event)

    def _restyle(self) -> None:
        border = C["teal_dim"] if self._hovered else C["steel"]
        background = C["carbon"] if self._hovered else C["card"]
        self.setStyleSheet(
            f"QFrame {{ background-color: {background}; border: 1px dashed {border}; "
            "border-radius: 6px; }}"
        )
