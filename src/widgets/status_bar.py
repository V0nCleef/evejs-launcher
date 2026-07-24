"""Status bar widget for EveJS Launcher V2."""
from __future__ import annotations

from enum import Enum, auto

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget, QGraphicsOpacityEffect

from src.constants import COLORS, APP_VERSION


class ServiceState(Enum):
    ONLINE = auto()
    STARTING = auto()
    OFFLINE = auto()


class StatusSection(QFrame):
    """A clickable status section with a colored dot and label."""

    clicked = pyqtSignal(str)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.section_name = name
        self._state = ServiceState.OFFLINE
        self._count: int | None = None
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)

        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet(f"background-color: {COLORS['grey']}; border-radius: 5px;")
        layout.addWidget(self.dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel(f"{self.section_name}: Offline")
        self.label.setStyleSheet(f"color: {COLORS['white']}; font-size: 12px;")
        layout.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Pulsing animation for "Starting..." state
        self._opacity_effect = QGraphicsOpacityEffect(self.dot)
        self.dot.setGraphicsEffect(self._opacity_effect)
        self._pulse = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._pulse.setDuration(1000)
        self._pulse.setStartValue(0.5)
        self._pulse.setEndValue(1.0)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._pulse.setLoopCount(-1)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            StatusSection {{
                background-color: transparent;
            }}
            StatusSection:hover {{
                background-color: {COLORS['steel']};
            }}
        """)

    def set_state(self, state: ServiceState, pid: int | None = None) -> None:
        self._state = state
        if state == ServiceState.ONLINE:
            self._pulse.stop()
            self._opacity_effect.setOpacity(1.0)
            self.dot.setStyleSheet(f"background-color: {COLORS['green']}; border-radius: 5px;")
            txt = f"{self.section_name}: Online"
            if pid:
                txt += f" (PID {pid})"
            self.label.setText(txt)
        elif state == ServiceState.STARTING:
            self.dot.setStyleSheet(f"background-color: {COLORS['gold']}; border-radius: 5px;")
            self.label.setText(f"{self.section_name}: Starting...")
            self._pulse.start()
        else:
            self._pulse.stop()
            self._opacity_effect.setOpacity(1.0)
            self.dot.setStyleSheet(f"background-color: {COLORS['grey']}; border-radius: 5px;")
            self.label.setText(f"{self.section_name}: Offline")

    def set_count(self, count: int) -> None:
        """Set the client count (only used for Clients section)."""
        self._count = count
        self.label.setText(f"{count} client{'s' if count != 1 else ''} running")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.section_name.lower())
        super().mousePressEvent(event)


class VerticalDivider(QFrame):
    """A simple vertical divider line."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(1)
        self.setStyleSheet(f"background-color: {COLORS['steel']};")


class StatusBar(QFrame):
    """Bottom status bar showing server, market, and client status."""

    console_toggled = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setStyleSheet(f"""
            StatusBar {{
                background-color: {COLORS['carbon']};
                border-top: 1px solid {COLORS['steel']};
            }}
        """)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Server section
        self.server_section = StatusSection("Server")
        self.server_section.clicked.connect(self.console_toggled.emit)
        # Install event filter to catch clicks on child labels
        self.server_section.label.installEventFilter(self)
        self.server_section.dot.installEventFilter(self)
        layout.addWidget(self.server_section)
        layout.addWidget(VerticalDivider())

        # Market section
        self.market_section = StatusSection("Market")
        self.market_section.clicked.connect(self.console_toggled.emit)
        self.market_section.label.installEventFilter(self)
        self.market_section.dot.installEventFilter(self)
        layout.addWidget(self.market_section)
        layout.addWidget(VerticalDivider())

        # Clients section
        self.clients_section = StatusSection("Clients")
        self.clients_section.set_count(0)
        layout.addWidget(self.clients_section)

        layout.addStretch()

        # Version label — bottom right
        version_label = QLabel(f"v{APP_VERSION}")
        version_label.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 12px; padding-right: 10px;"
        )
        layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignVCenter)

    # ── Event filter: forward child-label clicks to the parent section ──
    def eventFilter(self, obj: QObject, event) -> bool:  # noqa: N802
        if event.type() == event.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                for section in (self.server_section, self.market_section, self.clients_section):
                    if obj is section.label or obj is section.dot:
                        section.clicked.emit(section.section_name.lower())
                        return True
        return super().eventFilter(obj, event)

    def set_server_state(self, state: ServiceState, pid: int | None = None) -> None:
        self.server_section.set_state(state, pid=pid)

    def set_market_state(self, state: ServiceState, pid: int | None = None) -> None:
        self.market_section.set_state(state, pid=pid)

    def set_client_count(self, count: int) -> None:
        self.clients_section.set_count(count)
