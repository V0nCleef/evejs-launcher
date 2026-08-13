"""Character card widget for EveJS Launcher V2."""
from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QKeyEvent,
    QPixmap,
    QPainter,
    QPainterPath,
    QColor,
    QFontMetrics,
    QPen,
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

from src.constants import COLORS as C, SEMANTIC_COLORS as S, Status


# Map status to bar color and button state
_STATUS_CONFIG = {
    Status.READY: {
        "label": "READY",
        "bar_color": C["green"],
        "btn_text": "LAUNCH",
        "btn_enabled": True,
        "btn_color": C["teal"],
        "btn_hover": C["teal_dim"],
    },
    Status.LAUNCHING: {
        "label": "LAUNCHING",
        "bar_color": C["gold"],
        "btn_text": "LAUNCHING...",
        "btn_enabled": False,
        "btn_color": C["gold"],
        "btn_hover": C["gold"],
    },
    Status.RUNNING: {
        "label": "RUNNING",
        "bar_color": C["green"],
        "btn_text": "RUNNING",
        "btn_enabled": False,
        "btn_color": C["green"],
        "btn_hover": C["green"],
    },
    Status.BANNED: {
        "label": "BANNED",
        "bar_color": C["gold"],
        "btn_text": "BANNED",
        "btn_enabled": False,
        "btn_color": C["gold"],
        "btn_hover": C["gold"],
    },
    Status.SAME_ACCOUNT_ONLINE: {
        "label": "WAITING",
        "bar_color": C["grey"],
        "btn_text": "WAITING",
        "btn_enabled": False,
        "btn_color": C["grey"],
        "btn_hover": C["grey"],
    },
    Status.NO_PROFILE: {
        "label": "NO PROFILE",
        "bar_color": C["grey"],
        "btn_text": "+ CREATE PROFILE",
        "btn_enabled": True,
        "btn_color": C["grey"],
        "btn_hover": C["white"],
    },
    Status.ERROR: {
        "label": "ERROR",
        "bar_color": C["red"],
        "btn_text": "RETRY",
        "btn_enabled": True,
        "btn_color": C["red"],
        "btn_hover": C["red"],
    },
}


class HexPortraitLabel(QLabel):
    """128×128 label displaying a hexagon-masked portrait."""

    _SIZE = 112

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap: Optional[QPixmap] = None
        self._show_skeleton = True
        self._update_style()

    def _update_style(self) -> None:
        self.setStyleSheet("background-color: transparent; border: none;")

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
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._show_skeleton or self._pixmap is None:
            path = self._hex_path()
            painter.fillPath(path, QColor(12, 28, 41, 235))
            painter.setPen(QPen(QColor(S["border_bright"]), 1.0))
            painter.drawPath(path)
            center = self._SIZE // 2
            painter.setPen(QPen(QColor(0, 200, 224, 54), 1.0))
            painter.drawEllipse(center - 21, center - 21, 42, 42)
            painter.drawLine(center, 24, center, self._SIZE - 24)
            painter.drawLine(24, center, self._SIZE - 24, center)
        else:
            path = self._hex_path()
            painter.setClipPath(path)
            scaled = self._pixmap.scaled(
                self._SIZE, self._SIZE,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self._SIZE - scaled.width()) // 2
            y = (self._SIZE - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
            painter.setClipping(False)
            painter.setPen(QPen(QColor(S["border_bright"]), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        painter.end()

    def _hex_path(self) -> QPainterPath:
        """Create a hexagon path centered in 128×128."""
        path = QPainterPath()
        cx = cy = self._SIZE / 2.0
        r = cx - 3.0
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
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Character actions")
        self.setToolTip("Character actions")
        self._hovered = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        border = QColor(S["accent"] if self.hasFocus() else S["border_bright"])
        fill = QColor(18, 39, 54, 238 if self._hovered else 190)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)

        dot_color = QColor(
            S["text_primary"] if self._hovered else S["text_secondary"]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot_color)
        for x in (9, 14, 19):
            painter.drawEllipse(x - 1, 13, 2, 2)

        painter.end()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().focusOutEvent(event)


class CharacterCard(QFrame):
    CARD_MIN_WIDTH = 148
    CARD_MAX_WIDTH = 196
    CARD_HEIGHT = 252
    """220×280px character card widget."""

    launched = pyqtSignal(str, str, int)  # username, char_name, char_id
    selected = pyqtSignal(str, str, int)  # username, char_name, char_id
    hide_requested = pyqtSignal(str)  # character_name
    manage_groups_requested = pyqtSignal(str, str, int)
    delete_character_requested = pyqtSignal(str, str, int)
    delete_account_requested = pyqtSignal(str, str, int)

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
        self._launch_available = True
        self._launch_unavailable_reason = ""

        self.setObjectName("characterCard")
        self.setProperty("deepSignal", True)
        self.setProperty("selected", False)
        self.setProperty("status", status.value)
        self.setMinimumWidth(self.CARD_MIN_WIDTH)
        self.setMaximumWidth(self.CARD_MAX_WIDTH)
        self.setFixedHeight(self.CARD_HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._selected = False
        self._mouse_inside = False
        self._setup_ui()
        self._apply_status(status)
        self._restyle()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(5)

        signal_row = QHBoxLayout()
        signal_row.setContentsMargins(0, 0, 0, 0)
        signal_row.setSpacing(4)
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(10)
        signal_row.addWidget(self._status_dot)
        self._status_label = QLabel("READY")
        self._status_label.setStyleSheet(
            f"color: {S['text_secondary']}; border: none; background: transparent; "
            "font-size: 9px; font-weight: 700;"
        )
        signal_row.addWidget(self._status_label)
        signal_row.addStretch()
        self._selection_marker = QLabel("★")
        self._selection_marker.setStyleSheet(
            f"color: {S['accent']}; border: none; background: transparent; font-size: 13px;"
        )
        self._selection_marker.setToolTip("Selected character")
        self._selection_marker.hide()
        signal_row.addWidget(self._selection_marker)
        layout.addLayout(signal_row)

        self._status_bar = QFrame()
        self._status_bar.setFixedHeight(2)
        self._status_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self._status_bar)

        self._portrait = HexPortraitLabel()
        layout.addWidget(self._portrait, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._name_label = QLabel(self.char_name)
        self._name_label.setStyleSheet(
            f"color: {S['text_primary']}; border: none; background: transparent; "
            "font-size: 14px; font-weight: 700;"
        )
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._name_label)

        self._account_label = QLabel(self.username)
        self._account_label.setStyleSheet(
            f"color: {S['accent']}; border: none; background: transparent; font-size: 9px;"
        )
        self._account_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._account_label)

        # Compatibility labels remain available to integrations that inspect
        # them, but the compact card presents their values in one summary line.
        self._isk_label = QLabel(self.isk, self)
        self._isk_label.hide()
        self._ship_label = QLabel(self.ship, self)
        self._ship_label.hide()
        self._summary_label = QLabel()
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._summary_label.setStyleSheet(
            f"color: {S['text_muted']}; border: none; background: transparent; font-size: 9px;"
        )
        layout.addWidget(self._summary_label)
        layout.addStretch(1)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(6)

        self._launch_btn = QPushButton("LAUNCH")
        self._launch_btn.setFixedHeight(28)
        self._launch_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_btn.setAccessibleName(f"Launch {self.char_name}")
        self._launch_btn.clicked.connect(self._on_launch_clicked)
        btn_layout.addWidget(self._launch_btn)

        self._overflow_btn = HamburgerButton()
        self._overflow_btn.clicked.connect(self._on_overflow_clicked)
        btn_layout.addWidget(self._overflow_btn)
        layout.addLayout(btn_layout)
        self._elide_name()

    def _elide_name(self) -> None:
        available = max(92, self.width() - 24)
        metrics = QFontMetrics(self._name_label.font())
        elided = metrics.elidedText(
            self.char_name, Qt.TextElideMode.ElideRight, available
        )
        self._name_label.setText(elided)
        self._name_label.setToolTip(self.char_name)
        summary = f"{self.ship}  ·  {self.isk} ISK"
        summary_metrics = QFontMetrics(self._summary_label.font())
        self._summary_label.setText(
            summary_metrics.elidedText(
                summary,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )
        self._summary_label.setToolTip(summary)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide_name()

    def set_selected(self, selected: bool) -> None:
        """Update the card's visual selection state.

        Uses Qt property selectors for clean styling — no layout jumps,
        no glow artefacts, no dynamic setStyleSheet() calls.
        """
        self._selected = bool(selected)
        self.setProperty("selected", self._selected)
        self._selection_marker.setVisible(self._selected)
        self._restyle()
        self._update_accessibility()

    # ═════════════════════════════════════════════════════════════════════
    #  Mouse tracking — enter / leave drive hover styling centrally
    # ═════════════════════════════════════════════════════════════════════
    def enterEvent(self, event) -> None:
        self._mouse_inside = True
        self._restyle()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._mouse_inside = False
        self._restyle()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802
        self._restyle()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self._restyle()
        super().focusOutEvent(event)

    # ── Central style dispatch ─────────────────────────────────────────────
    def _restyle(self) -> None:
        """Apply the correct stylesheet based on current (selected, hover) state.

        All states keep a 2 px border so there is never a layout jump.
        """
        if self._selected:
            border_color = S["accent"]
            bg = "rgba(8, 31, 43, 238)"
        elif self.hasFocus():
            border_color = S["text_primary"]
            bg = "rgba(10, 27, 39, 232)"
        elif self._mouse_inside:
            border_color = S["accent_dim"]
            bg = "rgba(13, 31, 44, 232)"
        else:
            border_color = S["border"]
            bg = "rgba(7, 17, 29, 222)"

        self.setStyleSheet(
            f"""
            QFrame#characterCard {{
                background-color: {bg};
                border: 2px solid {border_color};
                border-radius: 7px;
            }}
            """
        )

    def _apply_status(self, status: Status) -> None:
        self._status = status
        self.setProperty("status", status.value)
        cfg = _STATUS_CONFIG.get(status, _STATUS_CONFIG[Status.READY])
        self._status_bar.setStyleSheet(
            f"background-color: {cfg['bar_color']}; border: none; border-radius: 1px;"
        )
        self._status_dot.setStyleSheet(
            f"color: {cfg['bar_color']}; border: none; background: transparent; font-size: 9px;"
        )
        self._status_label.setText(str(cfg["label"]))
        if self._launch_available:
            self._launch_btn.setText(cfg["btn_text"])
            self._launch_btn.setEnabled(cfg["btn_enabled"])
            self._launch_btn.setToolTip("")
        else:
            self._launch_btn.setText("VIEW ONLY")
            self._launch_btn.setEnabled(False)
            self._launch_btn.setToolTip(self._launch_unavailable_reason)
        self._launch_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(105, 72, 0, 224);
                color: #FFE39A;
                border: 1px solid {C['gold']};
                border-radius: 4px;
                font-weight: bold;
                font-size: 10px;
            }}
            QPushButton:hover:enabled {{
                background-color: {C['gold']};
                color: {C['void_black']};
            }}
            QPushButton:focus {{
                border: 2px solid #FFF2C3;
            }}
            QPushButton:disabled {{
                background-color: rgba(10, 22, 32, 210);
                color: {S['text_muted']};
                border: 1px solid {S['border']};
            }}
            """
        )
        self._update_accessibility()

    def _update_accessibility(self) -> None:
        cfg = _STATUS_CONFIG.get(self._status, _STATUS_CONFIG[Status.READY])
        self.setAccessibleName(self.char_name)
        description = (
            f"Character on account {self.username}. {cfg['label']}. "
            f"Ship {self.ship}. Balance {self.isk} ISK."
        )
        if self._selected:
            description += " Selected."
        if not self._launch_available and self._launch_unavailable_reason:
            description += f" {self._launch_unavailable_reason}"
        self.setAccessibleDescription(description)

    def set_status(self, status: Status) -> None:
        """Update card status."""
        self._apply_status(status)

    def set_launch_available(self, enabled: bool, reason: str = "") -> None:
        """Enable Native launch controls or present a read-only card."""
        self._launch_available = bool(enabled)
        self._launch_unavailable_reason = "" if enabled else reason
        self._apply_status(self._status)

    def set_portrait(self, pixmap: Optional[QPixmap]) -> None:
        """Set portrait pixmap (called from async loader)."""
        self._portrait_pixmap = pixmap
        self._portrait.set_portrait(pixmap)

    def set_skeleton(self) -> None:
        """Show skeleton loading state on portrait."""
        self._portrait.set_skeleton()

    def _on_launch_clicked(self) -> None:
        self.launched.emit(self.username, self.char_name, self.char_id)

    def _on_overflow_clicked(self) -> None:
        menu = QMenu(self)
        menu.setAccessibleName(f"Actions for {self.char_name}")
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: {C['panel']};
                color: {C['white']};
                border: 1px solid {S['border_bright']};
                padding: 4px;
            }}
            QMenu::item {{
                padding: 7px 20px;
            }}
            QMenu::item:selected {{
                background-color: {C['hover']};
            }}
            """
        )
        menu.addAction("View Details", self._on_view_details)
        menu.addAction(
            "Manage Groups...",
            lambda: self.manage_groups_requested.emit(
                self.username,
                self.char_name,
                self.char_id,
            ),
        )
        menu.addAction("Hide Character", lambda: self.hide_requested.emit(self.char_name))
        menu.addAction("View Log", lambda: None)
        menu.addSeparator()
        menu.addAction(
            "Delete Character...",
            lambda: self.delete_character_requested.emit(
                self.username,
                self.char_name,
                self.char_id,
            ),
        )
        menu.addAction(
            "Delete Account...",
            lambda: self.delete_account_requested.emit(
                self.username,
                self.char_name,
                self.char_id,
            ),
        )
        menu.exec(self._overflow_btn.mapToGlobal(self._overflow_btn.rect().bottomLeft()))

    def _on_view_details(self) -> None:
        self.selected.emit(self.username, self.char_name, self.char_id)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Space,
        }:
            self.selected.emit(self.username, self.char_name, self.char_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.selected.emit(self.username, self.char_name, self.char_id)
        super().mousePressEvent(event)
