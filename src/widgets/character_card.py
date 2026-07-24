"""Character card widget for EveJS Launcher V2."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QPixmap,
    QPainter,
    QPainterPath,
    QColor,
    QFontMetrics,
)
from PyQt6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
    QMenu,
)

from src.constants import COLORS as C, Status


# Map status to bar color and button state
_STATUS_CONFIG = {
    Status.READY: {
        "bar_color": C["teal"],
        "btn_text": "LAUNCH",
        "btn_enabled": True,
        "btn_color": C["teal"],
        "btn_hover": C["teal_dim"],
    },
    Status.RUNNING: {
        "bar_color": C["green"],
        "btn_text": "RUNNING",
        "btn_enabled": False,
        "btn_color": C["green"],
        "btn_hover": C["green"],
    },
    Status.BANNED: {
        "bar_color": C["gold"],
        "btn_text": "BANNED",
        "btn_enabled": False,
        "btn_color": C["gold"],
        "btn_hover": C["gold"],
    },
    Status.SAME_ACCOUNT_ONLINE: {
        "bar_color": C["grey"],
        "btn_text": "WAITING",
        "btn_enabled": False,
        "btn_color": C["grey"],
        "btn_hover": C["grey"],
    },
    Status.NO_PROFILE: {
        "bar_color": C["grey"],
        "btn_text": "+ CREATE PROFILE",
        "btn_enabled": True,
        "btn_color": C["grey"],
        "btn_hover": C["white"],
    },
    Status.ERROR: {
        "bar_color": C["red"],
        "btn_text": "RETRY",
        "btn_enabled": True,
        "btn_color": C["red"],
        "btn_hover": C["red"],
    },
}


class HexPortraitLabel(QLabel):
    """128×128 label displaying a hexagon-masked portrait."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(128, 128)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap: Optional[QPixmap] = None
        self._show_skeleton = True
        self._update_style()

    def _update_style(self) -> None:
        if self._show_skeleton:
            self.setStyleSheet(
                f"background-color: {C['steel']}; border-radius: 4px;"
            )
        else:
            self.setStyleSheet("background-color: transparent;")

    def set_portrait(self, pixmap: Optional[QPixmap]) -> None:
        self._pixmap = pixmap
        self._show_skeleton = pixmap is None
        self._update_style()
        self.update()

    def set_skeleton(self) -> None:
        self._pixmap = None
        self._show_skeleton = True
        self._update_style()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._show_skeleton or self._pixmap is None:
            # Skeleton: draw a simple hex outline
            path = self._hex_path()
            painter.fillPath(path, QColor(C["steel"]))
            painter.setPen(QColor(C["grey"]))
            painter.drawPath(path)
        else:
            path = self._hex_path()
            painter.setClipPath(path)
            scaled = self._pixmap.scaled(
                128, 128,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (128 - scaled.width()) // 2
            y = (128 - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)

        painter.end()

    def _hex_path(self) -> QPainterPath:
        """Create a hexagon path centered in 128×128."""
        path = QPainterPath()
        cx, cy = 64.0, 64.0
        r = 60.0
        # Pointy-top hexagon
        import math
        for i in range(6):
            angle = math.radians(60 * i - 30)
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        return path


class HamburgerButton(QPushButton):
    """28×28 button that draws a three-line hamburger icon with QPainter.

    Font-independent — works regardless of which font family is active.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background
        bg = QColor(C["hover"]) if self._hovered else QColor(C["steel"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(self.rect(), 4, 4)

        # Three horizontal bars
        bar_color = QColor(C["white"]) if self._hovered else QColor(C["grey"])
        painter.setBrush(bar_color)
        bar_w = 14
        bar_h = 2
        spacing = 5
        center_x = self.width() / 2
        center_y = self.height() / 2

        for i in (-1, 0, 1):
            y = center_y + i * spacing - bar_h / 2
            x = center_x - bar_w / 2
            painter.drawRoundedRect(int(x), int(y), bar_w, bar_h, 1, 1)

        painter.end()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)


class CharacterCard(QFrame):
    """220×280px character card widget."""

    launched = pyqtSignal(str, str)  # username, char_name
    selected = pyqtSignal(str, str, int)  # username, char_name, char_id
    hide_requested = pyqtSignal(str)  # username

    def __init__(
        self,
        username: str,
        char_name: str,
        char_id: int,
        isk: str = "0",
        ship: str = "—",
        sp: str = "—",
        location: str = "—",
        sec_status: str = "—",
        status: Status = Status.READY,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.username = username
        self.char_name = char_name
        self.char_id = char_id
        self.isk = isk
        self.ship = ship
        self.sp = sp
        self.location = location
        self.sec_status = sec_status
        self._status = status
        self._portrait_pixmap: Optional[QPixmap] = None

        self.setFixedSize(220, 280)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = False
        self._mouse_inside = False
        self._setup_ui()
        self._apply_status(status)

    def _setup_ui(self) -> None:
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {C['card']};
                border: 1px solid {C['steel']};
                border-radius: 6px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(0)

        # 3px status bar top
        self._status_bar = QFrame()
        self._status_bar.setFixedSize(220, 3)
        layout.addWidget(self._status_bar)

        # Content wrapper with padding
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 0)
        content_layout.setSpacing(8)
        layout.addWidget(content)

        # Hex portrait
        self._portrait = HexPortraitLabel()
        content_layout.addWidget(self._portrait, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Character name
        self._name_label = QLabel(self.char_name)
        self._name_label.setStyleSheet(
            f"color: {C['white']}; font-size: 15px; font-weight: bold;"
        )
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setFixedWidth(196)
        self._elide_name()
        content_layout.addWidget(self._name_label)

        # Account
        self._account_label = QLabel(self.username)
        self._account_label.setStyleSheet(
            f"color: {C['grey']}; font-size: 11px;"
        )
        self._account_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content_layout.addWidget(self._account_label)

        # Stats row (ISK + ship)
        stats_row = QWidget()
        stats_layout = QHBoxLayout(stats_row)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(12)

        self._isk_label = QLabel(self.isk)
        self._isk_label.setStyleSheet(
            f"color: {C['white']}; font-size: 12px; font-family: 'Consolas', monospace;"
        )
        stats_layout.addWidget(self._isk_label)

        stats_layout.addStretch()

        self._ship_label = QLabel(self.ship)
        self._ship_label.setStyleSheet(
            f"color: {C['grey']}; font-size: 12px; font-family: 'Consolas', monospace;"
        )
        stats_layout.addWidget(self._ship_label)

        content_layout.addWidget(stats_row)

        content_layout.addStretch()

        # Button row
        btn_row = QWidget()
        btn_layout = QHBoxLayout(btn_row)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)

        self._launch_btn = QPushButton("LAUNCH")
        self._launch_btn.setFixedHeight(32)
        self._launch_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._launch_btn.clicked.connect(self._on_launch_clicked)
        btn_layout.addWidget(self._launch_btn)

        self._overflow_btn = HamburgerButton()
        self._overflow_btn.clicked.connect(self._on_overflow_clicked)
        btn_layout.addWidget(self._overflow_btn)

        content_layout.addWidget(btn_row)

    def _elide_name(self) -> None:
        metrics = QFontMetrics(self._name_label.font())
        elided = metrics.elidedText(
            self.char_name, Qt.TextElideMode.ElideRight, 190
        )
        self._name_label.setText(elided)
        self._name_label.setToolTip(self.char_name)

    def set_selected(self, selected: bool) -> None:
        """Update the card's visual selection state.

        Uses Qt property selectors for clean styling — no layout jumps,
        no glow artefacts, no dynamic setStyleSheet() calls.
        """
        self._selected = selected
        self.setProperty("selected", selected)
        self._restyle()

    # ═════════════════════════════════════════════════════════════════════
    #  Mouse tracking — enter / leave drive hover styling centrally
    # ═════════════════════════════════════════════════════════════════════
    def enterEvent(self, event) -> None:
        self._mouse_inside = True
        if not self._selected:
            self._restyle()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._mouse_inside = False
        if not self._selected:
            self._restyle()
        super().leaveEvent(event)

    # ── Central style dispatch ─────────────────────────────────────────────
    def _restyle(self) -> None:
        """Apply the correct stylesheet based on current (selected, hover) state.

        All states keep a 1 px border so there is never a layout jump.
        """
        if self._selected:
            border_color = C["teal"]
            bg = C["carbon"]  # keep the default card bg
        elif self._mouse_inside:
            border_color = C["teal_dim"]
            bg = C["carbon"]
        else:
            border_color = C["steel"]
            bg = C["card"]

        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg};
                border: 1px solid {border_color};
                border-radius: 6px;
            }}
            """
        )

    def _apply_status(self, status: Status) -> None:
        self._status = status
        cfg = _STATUS_CONFIG.get(status, _STATUS_CONFIG[Status.READY])
        self._status_bar.setStyleSheet(
            f"background-color: {cfg['bar_color']}; border: none;"
        )
        self._launch_btn.setText(cfg["btn_text"])
        self._launch_btn.setEnabled(cfg["btn_enabled"])
        self._launch_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {cfg['btn_color']};
                color: {C['void_black']};
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover:enabled {{
                background-color: {cfg['btn_hover']};
            }}
            QPushButton:disabled {{
                background-color: {cfg['btn_color']};
                color: {C['void_black']};
            }}
            """
        )

    def set_status(self, status: Status) -> None:
        """Update card status."""
        self._apply_status(status)

    def set_portrait(self, pixmap: Optional[QPixmap]) -> None:
        """Set portrait pixmap (called from async loader)."""
        self._portrait_pixmap = pixmap
        self._portrait.set_portrait(pixmap)

    def set_skeleton(self) -> None:
        """Show skeleton loading state on portrait."""
        self._portrait.set_skeleton()

    def _on_launch_clicked(self) -> None:
        self.launched.emit(self.username, self.char_name)

    def _on_overflow_clicked(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: {C['panel']};
                color: {C['white']};
                border: 1px solid {C['steel']};
            }}
            QMenu::item:selected {{
                background-color: {C['hover']};
            }}
            """
        )
        menu.addAction("View Details", self._on_view_details)
        menu.addAction("Hide Character", lambda: self.hide_requested.emit(self.username))
        menu.addAction("View Log", lambda: None)
        menu.exec(self._overflow_btn.mapToGlobal(self._overflow_btn.rect().bottomLeft()))

    def _on_view_details(self) -> None:
        self.selected.emit(self.username, self.char_name, self.char_id)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.username, self.char_name, self.char_id)
        super().mousePressEvent(event)
