"""Status bar widget for EveJS Launcher V2."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QColor, QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from src.constants import APP_VERSION, COLORS, CONTROL_HEIGHTS
from src.core.service_status import ServiceState


class StatusSection(QFrame):
    """A clickable status section with a colored dot and label."""

    clicked = pyqtSignal(str)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.section_name = name
        self._state = ServiceState.OFFLINE
        self._count: int | None = None
        self._full_text = ""
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.dot.setStyleSheet(f"background-color: {COLORS['grey']}; border-radius: 4px;")
        layout.addWidget(self.dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.label = QLabel()
        self.label.setStyleSheet(f"color: {COLORS['white']}; font-size: 12px;")
        self.label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._set_label_text(f"{self.section_name}: Offline")
        layout.addWidget(self.label, stretch=1)

        # The footer has a deliberately compact fixed height, but each section
        # must still receive that full vertical allocation.  A fixed section
        # height collapses its label to its bare font metrics and clips the
        # baseline under the application stylesheet.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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

    def _set_label_text(self, text: str) -> None:
        """Keep the full status available while eliding only when shown narrow."""
        self._full_text = text
        self.label.setToolTip(text)
        self.label.setText(text)
        if self.isVisible():
            self._render_label_text()

    def _render_label_text(self) -> None:
        """Fit the visible label deliberately instead of allowing raw clipping."""
        available_width = self.label.contentsRect().width()
        if available_width <= 0:
            return
        self.label.setText(
            self.label.fontMetrics().elidedText(
                self._full_text,
                Qt.TextElideMode.ElideRight,
                available_width,
            )
        )

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            StatusSection {{
                background-color: transparent;
            }}
            StatusSection:hover {{
                background-color: {COLORS['steel']};
            }}
        """)

    def set_state(self, state: ServiceState, pid: int | None = None, container: str | None = None,
                  error: str | None = None) -> None:
        self._state = state
        if state == ServiceState.ONLINE:
            self._pulse.stop()
            self._opacity_effect.setOpacity(1.0)
            self.dot.setStyleSheet(f"background-color: {COLORS['green']}; border-radius: 5px;")
            txt = f"{self.section_name}: Online"
            if pid:
                txt += f" (PID {pid})"
            elif container:
                txt += f" (Container {container})"
            self._set_label_text(txt)
        elif state == ServiceState.STARTING:
            self.dot.setStyleSheet(f"background-color: {COLORS['gold']}; border-radius: 5px;")
            self._set_label_text(f"{self.section_name}: Starting...")
            self._pulse.start()
        elif state == ServiceState.STOPPING:
            self.dot.setStyleSheet(f"background-color: {COLORS['gold']}; border-radius: 5px;")
            self._set_label_text(f"{self.section_name}: Stopping...")
            self._pulse.start()
        elif state == ServiceState.FAILED:
            self._pulse.stop()
            self._opacity_effect.setOpacity(1.0)
            self.dot.setStyleSheet(f"background-color: {COLORS['red']}; border-radius: 5px;")
            self._set_label_text(f"{self.section_name}: Failed")
            if error:
                self.label.setToolTip(error)
        elif state == ServiceState.UNKNOWN:
            self._pulse.stop()
            self._opacity_effect.setOpacity(1.0)
            self.dot.setStyleSheet(f"background-color: {COLORS['gold']}; border-radius: 5px;")
            self._set_label_text(f"{self.section_name}: Unknown" + (f" — {error}" if error else ""))
        else:
            self._pulse.stop()
            self._opacity_effect.setOpacity(1.0)
            self.dot.setStyleSheet(f"background-color: {COLORS['grey']}; border-radius: 5px;")
            self._set_label_text(f"{self.section_name}: Offline")

    def set_count(self, count: int) -> None:
        """Set the client count (only used for Clients section)."""
        self._count = count
        self._set_label_text(f"{count} client{'s' if count != 1 else ''} running")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render_label_text()

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
        self.setFixedHeight(CONTROL_HEIGHTS["compact"])
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
        layout.addWidget(self.server_section, stretch=1)
        layout.addWidget(VerticalDivider())

        # Market section
        self.market_section = StatusSection("Market")
        self.market_section.clicked.connect(self.console_toggled.emit)
        self.market_section.label.installEventFilter(self)
        self.market_section.dot.installEventFilter(self)
        layout.addWidget(self.market_section, stretch=1)
        layout.addWidget(VerticalDivider())

        # Clients section
        self.clients_section = StatusSection("Clients")
        self.clients_section.set_count(0)
        layout.addWidget(self.clients_section, stretch=1)

        layout.addStretch()

        # Version label — bottom right
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Preferred,
        )
        self.version_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.version_label.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 12px; padding-right: 6px;"
        )
        layout.addWidget(self.version_label)

    # ── Event filter: forward child-label clicks to the parent section ──
    def eventFilter(self, obj: QObject, event) -> bool:  # noqa: N802
        if event.type() == event.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                for section in (self.server_section, self.market_section, self.clients_section):
                    if obj is section.label or obj is section.dot:
                        section.clicked.emit(section.section_name.lower())
                        return True
        return super().eventFilter(obj, event)

    def set_server_state(self, state: ServiceState, pid: int | None = None, container: str | None = None,
                         error: str | None = None) -> None:
        self.server_section.set_state(state, pid=pid, container=container, error=error)

    def set_market_state(self, state: ServiceState, pid: int | None = None, container: str | None = None,
                         error: str | None = None) -> None:
        self.market_section.set_state(state, pid=pid, container=container, error=error)

    def set_client_count(self, count: int) -> None:
        self.clients_section.set_count(count)
