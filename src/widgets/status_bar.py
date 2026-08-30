"""Status bar widget for EveJS Launcher V2."""
from __future__ import annotations

from math import cos, pi, sin

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

from src.constants import APP_VERSION, COLORS
from src.core.service_status import ServiceState
from src.i18n import (
    LANGUAGES,
    current_language,
    set_language,
    translate,
    translate_service_action,
    translate_ui_phrase,
)
from src.ui.motion import MotionController
from src.widgets.ui_translation import (
    set_translatable_accessible_name,
)


def _language_flag_icon(code: str) -> QIcon:
    """Paint a small deterministic flag without relying on emoji fonts."""
    width, height = 24, 16
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)

    if code == "en":
        painter.fillRect(rect, QColor("#17365D"))
        painter.setPen(QPen(QColor("#FFFFFF"), 3.0))
        painter.drawLine(QPointF(1, 1), QPointF(width - 1, height - 1))
        painter.drawLine(QPointF(width - 1, 1), QPointF(1, height - 1))
        painter.setPen(QPen(QColor("#C8102E"), 1.2))
        painter.drawLine(QPointF(1, 1), QPointF(width - 1, height - 1))
        painter.drawLine(QPointF(width - 1, 1), QPointF(1, height - 1))
        painter.fillRect(QRectF(0, 5.5, width, 5), QColor("#FFFFFF"))
        painter.fillRect(QRectF(9.5, 0, 5, height), QColor("#FFFFFF"))
        painter.fillRect(QRectF(0, 6.5, width, 3), QColor("#C8102E"))
        painter.fillRect(QRectF(10.5, 0, 3, height), QColor("#C8102E"))
    elif code == "zh_CN":
        painter.fillRect(rect, QColor("#DE2910"))
        star_center = QPointF(5.2, 4.8)
        star_points = []
        for index in range(10):
            radius = 2.4 if index % 2 == 0 else 1.0
            angle = -pi / 2.0 + index * pi / 5.0
            star_points.append(
                QPointF(
                    star_center.x() + radius * cos(angle),
                    star_center.y() + radius * sin(angle),
                )
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFDE00"))
        painter.drawPolygon(*star_points)
    elif code == "ja":
        painter.fillRect(rect, QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#BC002D"))
        painter.drawEllipse(QPointF(width / 2, height / 2), 4.2, 4.2)
    elif code == "ko":
        painter.fillRect(rect, QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#CD2E3A"))
        painter.drawPie(QRectF(7, 3, 10, 10), 0, 180 * 16)
        painter.setBrush(QColor("#0047A0"))
        painter.drawPie(QRectF(7, 3, 10, 10), 180 * 16, 180 * 16)
        painter.setPen(QPen(QColor("#111111"), 1.0))
        painter.drawLine(QPointF(3, 4), QPointF(6, 2))
        painter.drawLine(QPointF(18, 14), QPointF(21, 12))
    elif code == "fr":
        painter.fillRect(QRectF(0.5, 0.5, 8, 15), QColor("#0055A4"))
        painter.fillRect(QRectF(8.5, 0.5, 8, 15), QColor("#FFFFFF"))
        painter.fillRect(QRectF(16.5, 0.5, 7, 15), QColor("#EF4135"))
    elif code == "de":
        painter.fillRect(QRectF(0.5, 0.5, 23, 5), QColor("#111111"))
        painter.fillRect(QRectF(0.5, 5.5, 23, 5), QColor("#DD0000"))
        painter.fillRect(QRectF(0.5, 10.5, 23, 5), QColor("#FFCE00"))
    elif code == "nl":
        painter.fillRect(QRectF(0.5, 0.5, 23, 5), QColor("#AE1C28"))
        painter.fillRect(QRectF(0.5, 5.5, 23, 5), QColor("#FFFFFF"))
        painter.fillRect(QRectF(0.5, 10.5, 23, 5), QColor("#21468B"))
    elif code == "ru":
        painter.fillRect(QRectF(0.5, 0.5, 23, 5), QColor("#FFFFFF"))
        painter.fillRect(QRectF(0.5, 5.5, 23, 5), QColor("#0039A6"))
        painter.fillRect(QRectF(0.5, 10.5, 23, 5), QColor("#D52B1E"))
    else:
        painter.fillRect(rect, QColor("#34495E"))
        painter.setPen(QPen(QColor("#D5E7F0"), 1.0))
        painter.drawEllipse(QRectF(7.5, 2.5, 9, 11))
        painter.drawLine(QPointF(3, 8), QPointF(21, 8))
        painter.drawLine(QPointF(12, 2.5), QPointF(12, 13.5))

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(170, 190, 202, 170), 0.8))
    painter.drawRoundedRect(rect, 1.0, 1.0)
    painter.end()
    return QIcon(pixmap)


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
        self._pid: int | None = None
        self._container: str | None = None
        self._error: str | None = None
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
        set_translatable_accessible_name(
            self,
            f"{self.section_name} telemetry",
            allow_templates=True,
        )

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
        self._pid = pid
        self._container = container
        self._error = error
        if state == ServiceState.ONLINE:
            self._set_visual_state("online", COLORS["green"])
            txt = translate_service_action(f"{self.section_name}: Online")
            if pid:
                txt += f" (PID {pid})"
            elif container:
                txt += f" (Container {container})"
            self._set_label_text(txt)
        elif state == ServiceState.STARTING:
            self._set_visual_state("warning", COLORS["gold"], pulse=True)
            self._set_label_text(
                translate_service_action(f"{self.section_name}: Starting...")
            )
        elif state == ServiceState.STOPPING:
            self._set_visual_state("warning", COLORS["gold"], pulse=True)
            self._set_label_text(
                translate_service_action(f"{self.section_name}: Stopping...")
            )
        elif state == ServiceState.FAILED:
            self._set_visual_state("danger", COLORS["red"])
            self._set_label_text(
                translate_service_action(f"{self.section_name}: Failed")
            )
            if error:
                self.label.setToolTip(error)
        elif state == ServiceState.UNKNOWN:
            self._set_visual_state("warning", COLORS["gold"])
            base = translate_service_action(f"{self.section_name}: Unknown")
            self._set_label_text(base + (f" — {error}" if error else ""))
        else:
            self._set_visual_state("offline", COLORS["grey"])
            self._set_label_text(
                translate_service_action(f"{self.section_name}: Offline")
            )

    def set_count(self, count: int) -> None:
        """Set the client count (only used for Clients section)."""
        self._count = count
        self._set_visual_state(
            "online" if count > 0 else "offline",
            COLORS["green"] if count > 0 else COLORS["grey"],
        )
        source = f"{count} client{'s' if count != 1 else ''} running"
        self._set_label_text(translate_ui_phrase(source))

    def retranslate_ui(self) -> None:
        """Render the current semantic state in the active language."""
        set_translatable_accessible_name(
            self,
            f"{self.section_name} telemetry",
            allow_templates=True,
        )
        if self._count is not None:
            self.set_count(self._count)
            return
        self.set_state(
            self._state,
            pid=self._pid,
            container=self._container,
            error=self._error,
        )

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
    language_changed = pyqtSignal(str)
    HEIGHT = 44

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._motion = MotionController(parent=self)
        self.setObjectName("statusBar")
        set_translatable_accessible_name(self, "Launcher telemetry footer")
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

        # Language belongs to the application-wide footer, not to one page or
        # the system-control block.  Its 190px width keeps the first telemetry
        # section aligned just beyond the 220px navigation rail.
        self.language_combo = QComboBox(self)
        self.language_combo.setObjectName("languageSelector")
        self.language_combo.setAccessibleName(translate("nav.language_tooltip"))
        self.language_combo.setToolTip(translate("nav.language_tooltip"))
        self.language_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.language_combo.setFixedSize(190, 28)
        self.language_combo.setIconSize(QSize(24, 16))
        self.language_combo.setMaxVisibleItems(len(LANGUAGES))
        self.language_combo.setStyleSheet(f"""
            QComboBox#languageSelector {{
                background-color: rgba(7, 15, 26, 0.92);
                color: #D7E5EC;
                border: 1px solid rgba(0, 200, 224, 0.34);
                border-radius: 3px;
                padding: 0 28px 0 12px;
                font-size: 10px;
                font-weight: 600;
            }}
            QComboBox#languageSelector:hover,
            QComboBox#languageSelector:focus {{
                background-color: rgba(19, 35, 49, 0.96);
                border-color: {COLORS['teal']};
            }}
            QComboBox#languageSelector::drop-down {{
                width: 24px;
                border: none;
                border-left: 1px solid rgba(0, 200, 224, 0.18);
            }}
            QComboBox#languageSelector QAbstractItemView {{
                background-color: #0B1725;
                color: #D7E5EC;
                border: 1px solid {COLORS['teal_dim']};
                selection-background-color: #153746;
                selection-color: #FFFFFF;
                outline: 0;
            }}
        """)
        for option in LANGUAGES:
            self.language_combo.addItem(
                _language_flag_icon(option.code),
                option.display_name,
                option.code,
            )
        selected_index = self.language_combo.findData(current_language())
        self.language_combo.setCurrentIndex(max(selected_index, 0))
        self.language_combo.currentIndexChanged.connect(
            self._on_language_selected
        )
        layout.addWidget(
            self.language_combo,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

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
        self.server_section.setMinimumWidth(160)
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
        self.market_section.setMinimumWidth(160)
        self.market_section.setMaximumWidth(230)
        layout.addWidget(self.market_section)

        # Clients section
        self.clients_section = StatusSection(
            "Clients",
            self,
            motion_controller=self._motion,
        )
        self.clients_section.set_count(0)
        self.clients_section.setMinimumWidth(160)
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

    def _on_language_selected(self, index: int) -> None:
        code = self.language_combo.itemData(index)
        if not isinstance(code, str):
            return
        normalized = set_language(code)
        self.retranslate_ui()
        self.language_changed.emit(normalized)

    def retranslate_ui(self) -> None:
        """Refresh the footer-owned language control."""
        language_tooltip = translate("nav.language_tooltip")
        self.language_combo.setAccessibleName(language_tooltip)
        self.language_combo.setToolTip(language_tooltip)
        for section in (
            self.server_section,
            self.market_section,
            self.clients_section,
        ):
            section.retranslate_ui()

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
