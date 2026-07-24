"""Custom frameless-window title bar for EveJS Launcher V2.

A 36px bar with app logo, title text and min/max/close buttons rendered
with universally-available Unicode glyphs. Supports drag-to-move and double-click
to toggle maximize. Emits no signals — it invokes methods on the
top-level window directly.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QMouseEvent, QFont
from PyQt6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)

from src.constants import COLORS, APP_TITLE
from src.widgets.update_button import UpdateButton

# Simple universally-available glyphs for window controls
_GLYPH_MIN = "—"       # em dash for minimize
_GLYPH_MAX = "□"       # white square for maximize
_GLYPH_RESTORE = "❐"   # lower right drop-shadowed white square for restore
_GLYPH_CLOSE = "✕"     # multiplication X for close

_WIN_BTN_FONT = "Segoe UI"

_ASSETS_LOGO = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png"


class _TitleButton(QPushButton):
    """Small square glyph button used on the title bar."""

    def __init__(self, glyph: str, parent: QWidget | None = None, hover_bg: str | None = None):
        super().__init__(glyph, parent)
        self.setFixedSize(46, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        font = QFont(_WIN_BTN_FONT)
        font.setPixelSize(14)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)
        hover = hover_bg or COLORS["steel"]
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {COLORS["white"]};
            }}
            QPushButton:hover {{
                background: {hover};
            }}
            QPushButton:pressed {{
                background: {COLORS["carbon"]};
            }}
            """
        )


class TitleBar(QWidget):
    """Frameless window title bar (36px).

    Calls ``window().showMinimized()``, ``window().showMaximized()`` /
    ``window().showNormal()`` and ``window().close()`` directly — no
    signals are emitted.
    """

    HEIGHT = 36

    update_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, title: str = APP_TITLE):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("TitleBar")
        self.setStyleSheet(
            f"""
            #TitleBar {{
                background: {COLORS["deep_space"]};
                border-bottom: 1px solid {COLORS["steel"]};
            }}
            """
        )

        self._drag_pos: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(6)

        # ── Logo ─────────────────────────────────────────────────────────
        self._logo = QLabel(self)
        self._logo.setFixedSize(20, 20)
        if _ASSETS_LOGO.exists():
            pix = QPixmap(str(_ASSETS_LOGO)).scaled(
                20, 20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._logo.setPixmap(pix)
        layout.addWidget(self._logo)

        # ── Title ────────────────────────────────────────────────────────
        self._title = QLabel(title, self)
        self._title.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 11px; font-weight: 600;"
            " letter-spacing: 2px; background: transparent;"
        )
        layout.addWidget(self._title)

        # Spacer
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(spacer)

        # ── Update pill ──────────────────────────────────────────────────
        self.update_btn = UpdateButton(self)
        self.update_btn.clicked.connect(self.update_clicked.emit)
        layout.addWidget(self.update_btn)

        # ── Window controls ──────────────────────────────────────────────
        self.btn_min = _TitleButton(_GLYPH_MIN, self)
        self.btn_min.setToolTip("Minimize")
        self.btn_min.clicked.connect(self._on_minimize)
        layout.addWidget(self.btn_min)

        self.btn_max = _TitleButton(_GLYPH_MAX, self)
        self.btn_max.setToolTip("Maximize")
        self.btn_max.clicked.connect(self._on_maximize_restore)
        layout.addWidget(self.btn_max)

        self.btn_close = _TitleButton(_GLYPH_CLOSE, self, hover_bg=COLORS["red"])
        self.btn_close.setToolTip("Close")
        self.btn_close.clicked.connect(self._on_close)
        layout.addWidget(self.btn_close)

    # ── Public API ───────────────────────────────────────────────────────
    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def show_update_available(self, version: str) -> None:
        """Display the gold update pill with *version* and pulse animation."""
        self.update_btn.set_update_available(version)

    def set_update_checking(self) -> None:
        """Show the checking-for-updates spinner."""
        self.update_btn.set_checking()

    def set_update_up_to_date(self) -> None:
        """Hide the update button (no update available)."""
        self.update_btn.set_up_to_date()

    # ── Window control slots (call parent window directly) ───────────────
    def _on_minimize(self) -> None:
        win = self.window()
        if win is not None:
            win.showMinimized()

    def _on_maximize_restore(self) -> None:
        self._toggle_max_restore()

    def _on_close(self) -> None:
        win = self.window()
        if win is not None:
            win.close()

    def _toggle_max_restore(self) -> None:
        win = self.window()
        if win is None:
            return
        if win.isMaximized():
            win.showNormal()
            self.btn_max.setText(_GLYPH_MAX)
            self.btn_max.setToolTip("Maximize")
        else:
            win.showMaximized()
            self.btn_max.setText(_GLYPH_RESTORE)
            self.btn_max.setToolTip("Restore")

    # ── Drag-to-move / double-click maximize ─────────────────────────────
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win is not None:
                self._drag_pos = (
                    event.globalPosition().toPoint() - win.frameGeometry().topLeft()
                )
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win is not None:
                if win.isMaximized():
                    # Dragging from a maximized state restores first so the
                    # window follows the cursor naturally.
                    win.showNormal()
                    self.btn_max.setText(_GLYPH_MAX)
                win.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(super().sizeHint().width(), self.HEIGHT)
