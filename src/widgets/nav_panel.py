"""Navigation panel widget for EveJS Launcher V2."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRect
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QButtonGroup,
    QWidget,
    QSizePolicy,
)

from src.constants import COLORS, CONTROL_HEIGHTS, Page


def logo_asset_path(module_file: str | Path | None = None) -> Path:
    """Resolve the bundled logo independently of the process working directory."""
    source_file = Path(module_file) if module_file is not None else Path(__file__)
    return source_file.resolve().parent.parent.parent / "assets" / "logo.png"


class NavButton(QPushButton):
    """A navigation button with icon, label, and optional badge."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(20, 20))
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._badge_count: int = 0
        self._setup_style()

    def _setup_style(self) -> None:
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS['white']};
                border: none;
                border-left: 3px solid transparent;
                text-align: left;
                padding-left: 12px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['steel']};
            }}
            QPushButton:checked {{
                background-color: {COLORS['carbon']};
                border-left: 3px solid {COLORS['teal']};
            }}
        """)

    def set_badge_count(self, count: int) -> None:
        """Set the badge count; 0 hides the badge."""
        self._badge_count = count
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self._badge_count > 0:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            badge_radius = 8
            margin = 6
            x = self.width() - badge_radius * 2 - margin
            y = (self.height() - badge_radius * 2) // 2
            rect = QRect(x, y, badge_radius * 2, badge_radius * 2)
            painter.setBrush(QColor(COLORS['red']))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect)
            painter.setPen(QColor(COLORS['white']))
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self._badge_count))
            painter.end()


class ToggleButton(QPushButton):
    """A non-checkable toggle button styled like NavButton."""

    def __init__(self, text: str, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(20, 20))
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._setup_style()

    def _setup_style(self) -> None:
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {COLORS['white']};
                border: none;
                border-left: 3px solid transparent;
                text-align: left;
                padding-left: 12px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {COLORS['steel']};
            }}
        """)


class NavPanel(QFrame):
    """Left navigation panel with logo, page buttons, service toggles, and kill button."""

    page_changed = pyqtSignal(int)
    server_toggled = pyqtSignal()
    market_toggled = pyqtSignal()
    kill_all_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(244)
        self.setStyleSheet(f"""
            NavPanel {{
                background-color: {COLORS['deep_space']};
                border-right: 1px solid {COLORS['steel']};
            }}
        """)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)

        # Logo area
        logo_area = QFrame()
        logo_area.setFixedHeight(60)
        logo_area.setStyleSheet(f"background-color: {COLORS['carbon']};")
        logo_layout = QHBoxLayout(logo_area)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_label = QLabel()
        logo_pixmap = QPixmap(str(logo_asset_path()))
        if not logo_pixmap.isNull():
            logo_label.setPixmap(logo_pixmap.scaled(
                40, 40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
        else:
            logo_label.setText("EVEJS")
            logo_label.setStyleSheet(f"""
                color: {COLORS['teal']};
                font-size: 18px;
                font-weight: bold;
                letter-spacing: 2px;
            """)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_layout.addWidget(logo_label)
        layout.addWidget(logo_area)

        # Navigation buttons
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.btn_home = NavButton("Home")
        self.btn_characters = NavButton("Characters")
        self.btn_mods = NavButton("Mods")
        self.btn_tools = NavButton("Tools")
        self.btn_settings = NavButton("Settings")

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
        self.btn_server = ToggleButton("Server")
        self.btn_market = ToggleButton("Market")
        self.btn_server.clicked.connect(self.server_toggled.emit)
        self.btn_market.clicked.connect(self.market_toggled.emit)
        layout.addWidget(self.btn_server)
        layout.addWidget(self.btn_market)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {COLORS['steel']};")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # Kill All Clients
        self.btn_kill_all = QPushButton("Kill All Clients")
        self.btn_kill_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kill_all.setFixedHeight(CONTROL_HEIGHTS["compact"])
        self.btn_kill_all.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLORS['red']};
                color: {COLORS['white']};
                border: none;
                border-radius: 4px;
                padding: 0 12px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {COLORS['red']};
                color: {COLORS['white']};
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
