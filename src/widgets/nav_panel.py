"""Navigation panel widget for EveJS Launcher V2."""
from __future__ import annotations

from math import cos, pi, sin
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRect, QRectF, QPointF
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QButtonGroup,
    QWidget,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
)

from src.constants import COLORS, Page


def logo_asset_path(module_file: str | Path | None = None) -> Path:
    """Resolve the bundled logo independently of the process working directory."""
    source_file = Path(module_file) if module_file is not None else Path(__file__)
    return source_file.resolve().parent.parent.parent / "assets" / "logo.png"


def _nav_icon(kind: str, size: int = 24) -> QIcon:
    """Create a crisp original line icon without relying on external assets."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#B7C9D6"), 1.45)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    center = QPointF(size / 2.0, size / 2.0)
    r = size * 0.32

    if kind == "home":
        roof = QPainterPath()
        roof.moveTo(size * 0.18, size * 0.48)
        roof.lineTo(size * 0.5, size * 0.19)
        roof.lineTo(size * 0.82, size * 0.48)
        painter.drawPath(roof)
        painter.drawPolyline(
            QPointF(size * 0.27, size * 0.42),
            QPointF(size * 0.27, size * 0.79),
            QPointF(size * 0.73, size * 0.79),
            QPointF(size * 0.73, size * 0.42),
        )
        painter.drawLine(
            QPointF(size * 0.44, size * 0.79),
            QPointF(size * 0.44, size * 0.59),
        )
    elif kind == "characters":
        painter.drawEllipse(
            QRectF(size * 0.38, size * 0.17, size * 0.24, size * 0.24)
        )
        arc = QPainterPath()
        arc.moveTo(size * 0.19, size * 0.79)
        arc.cubicTo(
            size * 0.24, size * 0.49,
            size * 0.76, size * 0.49,
            size * 0.81, size * 0.79,
        )
        painter.drawPath(arc)
    elif kind == "mods":
        points = [
            QPointF(
                center.x() + r * 1.06 * cos(i * pi / 3.0),
                center.y() + r * 1.06 * sin(i * pi / 3.0),
            )
            for i in range(6)
        ]
        painter.drawPolygon(*points)
        painter.drawEllipse(center, size * 0.08, size * 0.08)
        painter.drawLine(points[0], center)
        painter.drawLine(points[2], center)
        painter.drawLine(points[4], center)
    elif kind == "tools":
        painter.drawLine(
            QPointF(size * 0.22, size * 0.75),
            QPointF(size * 0.72, size * 0.25),
        )
        painter.drawEllipse(
            QRectF(size * 0.16, size * 0.69, size * 0.19, size * 0.19)
        )
        painter.drawArc(
            QRectF(size * 0.56, size * 0.10, size * 0.31, size * 0.31),
            35 * 16,
            180 * 16,
        )
        painter.drawLine(
            QPointF(size * 0.29, size * 0.24),
            QPointF(size * 0.76, size * 0.72),
        )
    else:
        painter.drawEllipse(center, r, r)
        for angle in range(0, 360, 45):
            radians = angle * 3.141592653589793 / 180.0
            inner = QPointF(
                center.x() + r * 0.78 * cos(radians),
                center.y() + r * 0.78 * sin(radians),
            )
            outer = QPointF(
                center.x() + r * 1.18 * cos(radians),
                center.y() + r * 1.18 * sin(radians),
            )
            painter.drawLine(inner, outer)
        painter.drawEllipse(center, size * 0.08, size * 0.08)

    painter.end()
    return QIcon(pixmap)


def _paint_telemetry_button(button: QPushButton, badge_count: int = 0) -> None:
    """Paint compact uppercase telemetry text without changing ``text()``.

    Several runtime tests and controller paths use the button text as a logical
    contract.  Deep Signal presents that same text in uppercase while retaining
    the original value for accessibility, tooltips, and callers.
    """
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.text = button.text().upper()

    painter = QStylePainter(button)
    painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
    if badge_count <= 0:
        painter.end()
        return

    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    badge_radius = 8
    margin = 8
    x = button.width() - badge_radius * 2 - margin
    y = (button.height() - badge_radius * 2) // 2
    rect = QRect(x, y, badge_radius * 2, badge_radius * 2)
    painter.setBrush(QColor(COLORS["red"]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(rect)
    painter.setPen(QColor(COLORS["white"]))
    font = QFont(button.font())
    font.setPixelSize(9)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(badge_count))
    painter.end()


class NavButton(QPushButton):
    """A navigation button with icon, label, and optional badge."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(24, 24))
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._badge_count: int = 0
        self._setup_style()

    def _setup_style(self) -> None:
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(8, 17, 29, 0.28);
                color: {COLORS['white']};
                border: 1px solid transparent;
                border-left: 3px solid transparent;
                text-align: left;
                padding-left: 22px;
                padding-right: 30px;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: rgba(30, 42, 56, 0.82);
                border-top-color: rgba(0, 200, 224, 0.18);
                border-bottom-color: rgba(0, 200, 224, 0.18);
            }}
            QPushButton:checked {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(0, 200, 224, 0.18),
                    stop:0.72 rgba(19, 35, 49, 0.86),
                    stop:1 rgba(8, 17, 29, 0.34)
                );
                border-left: 3px solid {COLORS['teal']};
                border-top-color: rgba(0, 200, 224, 0.32);
                border-bottom-color: rgba(0, 200, 224, 0.32);
                color: #FFFFFF;
            }}
            QPushButton:focus {{
                border-top-color: {COLORS['teal_dim']};
                border-right-color: {COLORS['teal_dim']};
                border-bottom-color: {COLORS['teal_dim']};
            }}
            QPushButton:disabled {{
                color: #667786;
                background-color: transparent;
            }}
        """)

    def set_badge_count(self, count: int) -> None:
        """Set the badge count; 0 hides the badge."""
        self._badge_count = count
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _paint_telemetry_button(self, self._badge_count)


class ToggleButton(QPushButton):
    """A non-checkable toggle button styled like NavButton."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(20, 20))
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setProperty("telemetryState", "idle")
        self._setup_style()
        self._update_telemetry_state(text)

    def _setup_style(self) -> None:
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(7, 15, 26, 0.28);
                color: #B7C5D1;
                border: none;
                border-left: 2px solid {COLORS['teal_dim']};
                text-align: left;
                padding-left: 18px;
                padding-right: 8px;
                font-size: 9px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: rgba(30, 42, 56, 0.88);
                border-left-color: {COLORS['teal']};
            }}
            QPushButton:focus {{
                border-color: {COLORS['teal_dim']};
            }}
            QPushButton[telemetryState="online"] {{
                color: {COLORS['green']};
                border-left-color: {COLORS['green']};
            }}
            QPushButton[telemetryState="warning"] {{
                color: {COLORS['gold']};
                border-left-color: {COLORS['gold']};
            }}
            QPushButton[telemetryState="danger"] {{
                color: {COLORS['red']};
                border-left-color: {COLORS['red']};
            }}
            QPushButton:disabled {{
                background-color: rgba(7, 15, 26, 0.36);
                color: #62727F;
                border-color: rgba(30, 42, 56, 0.54);
            }}
        """)

    def setText(self, text: str) -> None:  # noqa: N802
        """Retain logical text while deriving its semantic telemetry colour."""
        super().setText(text)
        self._update_telemetry_state(text)

    def _update_telemetry_state(self, text: str) -> None:
        normalized = text.casefold()
        if any(
            word in normalized
            for word in ("failed", "error", "unavailable", "unsupported", "retry")
        ):
            state = "danger"
        elif any(word in normalized for word in ("starting", "stopping", "unknown")):
            state = "warning"
        elif any(word in normalized for word in ("stop ", "external", "online", "running")):
            state = "online"
        else:
            state = "idle"
        if self.property("telemetryState") == state:
            return
        self.setProperty("telemetryState", state)
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        _paint_telemetry_button(self)


class OrbitalEmblem(QWidget):
    """Original Deep Signal orbital mark painted from simple geometry."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(88, 88)
        self.setAccessibleName("Deep Signal orbital emblem")

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2.0, self.height() / 2.0)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 200, 224, 32), 7.0))
        painter.drawEllipse(center, 28.0, 28.0)

        fine_pen = QPen(QColor(0, 200, 224, 158), 1.0)
        fine_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(fine_pen)
        painter.drawEllipse(center, 31.0, 31.0)
        painter.drawEllipse(center, 21.0, 21.0)
        painter.drawEllipse(center, 11.0, 11.0)
        painter.drawArc(QRectF(6.0, 6.0, 76.0, 76.0), 18 * 16, 82 * 16)
        painter.drawArc(QRectF(6.0, 6.0, 76.0, 76.0), 198 * 16, 82 * 16)

        painter.setPen(QPen(QColor(183, 213, 225, 158), 1.0))
        for angle in range(0, 360, 45):
            radians = angle * pi / 180.0
            inner = QPointF(
                center.x() + 13.0 * cos(radians),
                center.y() + 13.0 * sin(radians),
            )
            outer = QPointF(
                center.x() + 37.0 * cos(radians),
                center.y() + 37.0 * sin(radians),
            )
            painter.drawLine(inner, outer)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#EAF5F8"))
        painter.drawEllipse(center, 5.5, 5.5)
        painter.setBrush(QColor(COLORS["teal"]))
        for angle in (25, 115, 205, 295):
            radians = angle * pi / 180.0
            painter.drawEllipse(
                QPointF(
                    center.x() + 38.0 * cos(radians),
                    center.y() + 38.0 * sin(radians),
                ),
                1.7,
                1.7,
            )


class NavPanel(QFrame):
    """Left navigation panel with logo, page buttons, service toggles, and kill button."""

    page_changed = pyqtSignal(int)
    server_toggled = pyqtSignal()
    market_toggled = pyqtSignal()
    kill_all_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navPanel")
        self.setAccessibleName("Primary launcher navigation")
        self.setFixedWidth(220)
        self.setStyleSheet(f"""
            NavPanel#navPanel {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(10, 21, 35, 0.98),
                    stop:0.55 rgba(8, 16, 28, 0.97),
                    stop:1 rgba(5, 10, 18, 0.99)
                );
                border-right: 1px solid rgba(0, 200, 224, 0.28);
            }}
            QFrame#navLogoPlate {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(19, 35, 49, 0.92),
                    stop:0.65 rgba(11, 27, 42, 0.82),
                    stop:1 rgba(0, 153, 184, 0.16)
                );
                border-bottom: 1px solid rgba(0, 200, 224, 0.30);
            }}
            QLabel#navBrandPrimary {{
                color: {COLORS['white']};
                background: transparent;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 2px;
            }}
            QLabel#navBrandSecondary,
            QLabel#navCommandLabel,
            QLabel#navSystemsLabel {{
                color: #698091;
                background: transparent;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QFrame#navDivider {{
                background-color: rgba(0, 200, 224, 0.20);
                border: none;
            }}
        """)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)

        # Concept-scale orbital identity.  This is an original QPainter motif,
        # deliberately independent from game or publisher logos.
        logo_area = QFrame()
        logo_area.setObjectName("navLogoPlate")
        logo_area.setFixedHeight(132)
        logo_layout = QVBoxLayout(logo_area)
        logo_layout.setContentsMargins(0, 9, 0, 10)
        logo_layout.setSpacing(3)
        self.orbital_emblem = OrbitalEmblem(logo_area)
        logo_layout.addWidget(
            self.orbital_emblem,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        brand_primary = QLabel("DEEP SIGNAL")
        brand_primary.setObjectName("navBrandPrimary")
        brand_primary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_secondary = QLabel("COMMAND NODE // 01")
        brand_secondary.setObjectName("navBrandSecondary")
        brand_secondary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_layout.addWidget(brand_primary)
        logo_layout.addWidget(brand_secondary)
        layout.addWidget(logo_area)

        self.command_label = QLabel("COMMAND DECK")
        self.command_label.setObjectName("navCommandLabel")
        self.command_label.setFixedHeight(24)
        self.command_label.setContentsMargins(16, 4, 0, 0)
        layout.addWidget(self.command_label)

        # Navigation buttons
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.btn_home = NavButton("Home", _nav_icon("home"))
        self.btn_characters = NavButton("Characters", _nav_icon("characters"))
        self.btn_mods = NavButton("Mods", _nav_icon("mods"))
        self.btn_tools = NavButton("Tools", _nav_icon("tools"))
        self.btn_settings = NavButton("Settings", _nav_icon("settings"))

        nav_buttons = [
            (self.btn_home, Page.HOME),
            (self.btn_characters, Page.CHARACTERS),
            (self.btn_mods, Page.MODS),
            (self.btn_tools, Page.TOOLS),
            (self.btn_settings, Page.SETTINGS),
        ]
        for btn, idx in nav_buttons:
            self.nav_group.addButton(btn, int(idx))
            layout.addWidget(btn)

        self.nav_group.idClicked.connect(self.page_changed.emit)

        # Spacer
        layout.addStretch()

        # Server / Market toggles
        self.systems_label = QLabel("SYSTEM CONTROL")
        self.systems_label.setObjectName("navSystemsLabel")
        self.systems_label.setFixedHeight(20)
        self.systems_label.setContentsMargins(16, 2, 0, 0)
        layout.addWidget(self.systems_label)
        self.btn_server = ToggleButton("Server")
        self.btn_market = ToggleButton("Market")
        self.btn_server.clicked.connect(self.server_toggled.emit)
        self.btn_market.clicked.connect(self.market_toggled.emit)
        layout.addWidget(self.btn_server)
        layout.addWidget(self.btn_market)

        # Divider
        divider = QFrame()
        divider.setObjectName("navDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # Kill All Clients
        self.btn_kill_all = QPushButton("Kill All Clients")
        self.btn_kill_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kill_all.setFixedHeight(28)
        self.btn_kill_all.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(224, 79, 79, 0.10);
                color: {COLORS['red']};
                border: 1px solid rgba(224, 79, 79, 0.42);
                border-radius: 3px;
                padding: 0 12px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: rgba(224, 79, 79, 0.24);
                color: {COLORS['white']};
                border-color: #FF8A8A;
            }}
            QPushButton:focus {{
                border: 2px solid #FFB0B0;
            }}
            QPushButton:disabled {{
                background-color: #512A31;
                color: #9B7E84;
                border-color: #5D333A;
            }}
        """)
        self.btn_kill_all.clicked.connect(self.kill_all_clicked.emit)
        layout.addWidget(self.btn_kill_all)

    def set_badge_count(self, page: int, count: int) -> None:
        """Set badge count for a specific nav page."""
        btn = self.nav_group.button(page)
        if isinstance(btn, NavButton):
            btn.set_badge_count(count)

    def set_active_page(self, page: int) -> None:
        """Programmatically set the active nav button."""
        btn = self.nav_group.button(page)
        if btn:
            btn.setChecked(True)
