"""Status bar widget for EveJS Launcher V2."""
from __future__ import annotations

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSpacerItem,
    QSizePolicy,
    QWidget,
)

from src.constants import APP_VERSION, COLORS
from src.core.service_status import ServiceState
from src.ui.motion import MotionController


class TelemetryLabel(QLabel):
    """Render compact uppercase telemetry while preserving logical text."""

    def display_text(self) -> str:
        """Return the visual form without changing ``QLabel.text()``."""
        text = self.text()
        for technical_marker in (" (PID ", " (Container "):
            if technical_marker in text:
                text = text.split(technical_marker, maxsplit=1)[0]
                break
        if ": " in text:
            subject, state = text.split(": ", maxsplit=1)
            text = f"{subject}  •  {state}"
        return text.upper()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(
            self.contentsRect(),
            self.alignment() | Qt.AlignmentFlag.AlignVCenter,
            self.display_text(),
        )


class StatusSection(QFrame):
    """A clickable status section with a colored dot and label."""

    clicked = pyqtSignal(str)

    def __init__(
        self,
        name: str,
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
    ) -> None:
        super().__init__(parent)
        self.section_name = name
        self._state = ServiceState.OFFLINE
        self._count: int | None = None
        self._full_text = ""
        self._pulse_requested = False
        self._motion = motion_controller or MotionController(parent=self)
        self._motion.reduced_motion_changed.connect(
            self._on_motion_policy_changed
        )
        self.setProperty("telemetryState", "offline")
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(7)

        self.dot = QLabel()
        self.dot.setFixedSize(7, 7)
        self.dot.setStyleSheet(
            f"background-color: {COLORS['grey']}; border-radius: 3px;"
        )
        layout.addWidget(self.dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.label = TelemetryLabel()
        self.label.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 10px; font-weight: 600;"
            " letter-spacing: 0.8px; background: transparent;"
        )
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
        self.setAccessibleName(f"{self.section_name} telemetry")

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
        self.label.setAccessibleDescription(text)
        self.setAccessibleDescription(text)
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
                border: none;
            }}
            StatusSection:hover {{
                background-color: rgba(30, 42, 56, 0.42);
                border-radius: 4px;
            }}
        """)

    @property
    def animations_enabled(self) -> bool:
        """Whether this telemetry section may pulse for transient states."""
        return self._motion.animations_enabled

    def set_animations_enabled(self, enabled: bool) -> None:
        """Apply reduced motion immediately without changing semantic state."""
        self._motion.set_reduced_motion(not bool(enabled))
        self._sync_pulse()

    def is_animating(self) -> bool:
        return self._pulse.state() == QAbstractAnimation.State.Running

    def _on_motion_policy_changed(self, _reduced: bool) -> None:
        self._sync_pulse()

    def _sync_pulse(self) -> None:
        should_run = self._pulse_requested and self._motion.animations_enabled
        if should_run:
            if not self.is_animating():
                self._pulse.start()
            return
        self._pulse.stop()
        self._opacity_effect.setOpacity(1.0)

    def _set_visual_state(self, state: str, color: str, *, pulse: bool = False) -> None:
        """Apply one semantic colour to the dot, rail, and activity pulse."""
        self._pulse_requested = bool(pulse)
        self._sync_pulse()
        self.dot.setStyleSheet(
            f"background-color: {color}; border: 1px solid rgba(255, 255, 255, 0.24);"
            " border-radius: 3px;"
        )
        if self.property("telemetryState") == state:
            return
        self.setProperty("telemetryState", state)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def set_state(self, state: ServiceState, pid: int | None = None, container: str | None = None,
                  error: str | None = None) -> None:
        self._state = state
        if state == ServiceState.ONLINE:
            self._set_visual_state("online", COLORS["green"])
            txt = f"{self.section_name}: Online"
            if pid:
                txt += f" (PID {pid})"
            elif container:
                txt += f" (Container {container})"
            self._set_label_text(txt)
        elif state == ServiceState.STARTING:
            self._set_visual_state("warning", COLORS["gold"], pulse=True)
            self._set_label_text(f"{self.section_name}: Starting...")
        elif state == ServiceState.STOPPING:
            self._set_visual_state("warning", COLORS["gold"], pulse=True)
            self._set_label_text(f"{self.section_name}: Stopping...")
        elif state == ServiceState.FAILED:
            self._set_visual_state("danger", COLORS["red"])
            self._set_label_text(f"{self.section_name}: Failed")
            if error:
                self.label.setToolTip(error)
        elif state == ServiceState.UNKNOWN:
            self._set_visual_state("warning", COLORS["gold"])
            self._set_label_text(f"{self.section_name}: Unknown" + (f" — {error}" if error else ""))
        else:
            self._set_visual_state("offline", COLORS["grey"])
            self._set_label_text(f"{self.section_name}: Offline")

    def set_count(self, count: int) -> None:
        """Set the client count (only used for Clients section)."""
        self._count = count
        self._set_visual_state(
            "online" if count > 0 else "offline",
            COLORS["green"] if count > 0 else COLORS["grey"],
        )
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
        self.setStyleSheet("background-color: rgba(0, 200, 224, 0.16);")


class StatusBar(QFrame):
    """Bottom status bar showing server, market, and client status."""

    console_toggled = pyqtSignal(str)
    HEIGHT = 44

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._motion = MotionController(parent=self)
        self.setObjectName("statusBar")
        self.setAccessibleName("Launcher telemetry footer")
        self.setFixedHeight(self.HEIGHT)
        self.setStyleSheet("""
            StatusBar#statusBar {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(5, 11, 20, 0.98),
                    stop:0.58 rgba(7, 16, 27, 0.98),
                    stop:1 rgba(6, 19, 29, 0.98)
                );
                border-top: 1px solid rgba(0, 200, 224, 0.25);
            }
        """)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(18)

        # Flexible shoulders keep the three telemetry sections visually calm
        # and centred.  This must be a non-painting layout item: a plain
        # QWidget inherits the application's opaque base background and masks
        # the footer gradient with a darker rectangle.
        left_shoulder = QSpacerItem(
            76,
            0,
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        layout.addSpacerItem(left_shoulder)
        layout.setStretch(layout.count() - 1, 1)
        # QBoxLayout does not insert its widget-to-widget spacing beside a
        # spacer item, so retain the established telemetry position explicitly.
        layout.addSpacing(layout.spacing())

        # Server section
        self.server_section = StatusSection(
            "Server",
            self,
            motion_controller=self._motion,
        )
        self.server_section.clicked.connect(self.console_toggled.emit)
        # Install event filter to catch clicks on child labels
        self.server_section.label.installEventFilter(self)
        self.server_section.dot.installEventFilter(self)
        self.server_section.setMinimumWidth(180)
        self.server_section.setMaximumWidth(230)
        layout.addWidget(self.server_section)

        # Market section
        self.market_section = StatusSection(
            "Market",
            self,
            motion_controller=self._motion,
        )
        self.market_section.clicked.connect(self.console_toggled.emit)
        self.market_section.label.installEventFilter(self)
        self.market_section.dot.installEventFilter(self)
        self.market_section.setMinimumWidth(180)
        self.market_section.setMaximumWidth(230)
        layout.addWidget(self.market_section)

        # Clients section
        self.clients_section = StatusSection(
            "Clients",
            self,
            motion_controller=self._motion,
        )
        self.clients_section.set_count(0)
        self.clients_section.setMinimumWidth(170)
        self.clients_section.setMaximumWidth(220)
        layout.addWidget(self.clients_section)

        layout.addStretch(1)

        # Technical build identifier -- bottom right
        self.build_label = QLabel("BUILD //")
        self.build_label.setObjectName("statusBuildLabel")
        self.build_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.build_label.setStyleSheet(
            "color: #637A8A; font-family: Consolas; font-size: 9px;"
            " font-weight: 700; letter-spacing: 1px; padding-left: 8px;"
        )
        layout.addWidget(self.build_label)

        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Preferred,
        )
        self.version_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.version_label.setStyleSheet(
            f"color: {COLORS['teal_dim']}; font-family: Consolas; font-size: 10px;"
            " font-weight: 700; letter-spacing: 1px; padding: 0 8px 0 4px;"
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

    @property
    def animations_enabled(self) -> bool:
        """Whether transient footer telemetry may animate."""
        return self._motion.animations_enabled

    def set_animations_enabled(self, enabled: bool) -> None:
        """Apply one footer motion policy and settle active pulses immediately."""
        self._motion.set_reduced_motion(not bool(enabled))
        # Keep this a complete seam even if the effective value did not change.
        for section in (
            self.server_section,
            self.market_section,
            self.clients_section,
        ):
            section._sync_pulse()
