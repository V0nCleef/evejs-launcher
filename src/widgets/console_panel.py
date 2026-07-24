"""Overlay console panel for tailing log files inside EveJS Launcher V2."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QSize
from PyQt6.QtGui import QFont, QFontDatabase, QTextCursor, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS


class ConsolePanel(QFrame):
    """Floating bottom panel that displays and tails a log file.

    Designed to be placed as an absolute-position child of a central widget.
    The parent is responsible for calling ``stop()`` when the user clicks
    outside the panel.
    """

    MAX_LINES = 2000
    DEFAULT_HEIGHT = 200
    MIN_AUTO_CLOSE_HEIGHT = 50
    STATUS_BAR_OFFSET = 24
    HEADER_HEIGHT = 28
    MAX_HEIGHT_RATIO = 0.85

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("consolePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._log_path: Path | None = None
        self._log_offset: int = 0
        self._drag_start_y: int = 0
        self._drag_start_height: int = 0
        self._resizing: bool = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_log)

        self._build_ui()
        self._apply_style()
        self.hide()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar
        self._header = QFrame(self)
        self._header.setObjectName("consoleHeader")
        self._header.setFixedHeight(self.HEADER_HEIGHT)
        self._header.setCursor(Qt.CursorShape.SizeVerCursor)
        self._header.mousePressEvent = self._header_mouse_press
        self._header.mouseMoveEvent = self._header_mouse_move
        self._header.mouseReleaseEvent = self._header_mouse_release

        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(8, 0, 4, 0)
        header_layout.setSpacing(4)

        self._title_label = QLabel("Console", self._header)
        self._title_label.setObjectName("consoleTitle")
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()

        self._copy_btn = QPushButton("Copy", self._header)
        self._copy_btn.setObjectName("consoleCopyBtn")
        self._copy_btn.setFixedHeight(20)
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        header_layout.addWidget(self._copy_btn)

        self._notepad_btn = QPushButton("Open in Notepad", self._header)
        self._notepad_btn.setObjectName("consoleNotepadBtn")
        self._notepad_btn.setFixedHeight(20)
        self._notepad_btn.clicked.connect(self._open_in_notepad)
        header_layout.addWidget(self._notepad_btn)

        self._close_btn = QPushButton("✕", self._header)
        self._close_btn.setObjectName("consoleCloseBtn")
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.clicked.connect(self.stop)
        header_layout.addWidget(self._close_btn)

        layout.addWidget(self._header)

        # Log area
        self._log = QTextEdit(self)
        self._log.setObjectName("consoleLog")
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._log)

        self._set_monospace_font(10)

    def _apply_style(self) -> None:
        c = COLORS
        self.setStyleSheet(f"""
            QFrame#consolePanel {{
                background-color: {c['deep_space']};
                border: 1px solid {c['steel']};
                border-top: 2px solid {c['teal_dim']};
            }}
            QFrame#consoleHeader {{
                background-color: {c['carbon']};
                border-bottom: 1px solid {c['steel']};
            }}
            QLabel#consoleTitle {{
                color: {c['teal']};
                font-weight: bold;
                font-size: 11px;
            }}
            QPushButton#consoleCopyBtn,
            QPushButton#consoleNotepadBtn {{
                background-color: {c['steel']};
                color: {c['white']};
                border: 1px solid {c['teal_dim']};
                border-radius: 3px;
                padding: 0 6px;
                font-size: 10px;
            }}
            QPushButton#consoleCopyBtn:hover,
            QPushButton#consoleNotepadBtn:hover {{
                background-color: {c['teal_dim']};
            }}
            QPushButton#consoleCloseBtn {{
                background-color: transparent;
                color: {c['grey']};
                border: none;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#consoleCloseBtn:hover {{
                color: {c['red']};
            }}
            QTextEdit#consoleLog {{
                background-color: {c['void_black']};
                color: {c['white']};
                border: none;
                selection-background-color: {c['teal_dim']};
            }}
            QScrollBar:vertical {{
                background-color: {c['carbon']};
                width: 10px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background-color: {c['steel']};
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {c['teal_dim']};
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                background-color: {c['carbon']};
                height: 10px;
                margin: 0;
            }}
            QScrollBar::handle:horizontal {{
                background-color: {c['steel']};
                min-width: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background-color: {c['teal_dim']};
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
        """)

    # ── Font scaling ─────────────────────────────────────────────────────────
    def _set_monospace_font(self, size_px: int) -> None:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPixelSize(max(9, min(14, size_px)))
        self._log.setFont(font)

    def _update_font_from_height(self) -> None:
        h = self.height()
        if h <= 60:
            size = 9
        elif h >= 400:
            size = 14
        else:
            # Linear interpolation between 9px @60 and 14px @400
            size = int(9 + (h - 60) * (14 - 9) / (400 - 60))
        self._set_monospace_font(size)

    # ── Geometry / positioning ───────────────────────────────────────────────
    def _reposition(self) -> None:
        if not self.parentWidget():
            return
        parent = self.parentWidget()
        max_h = int(parent.height() * self.MAX_HEIGHT_RATIO)
        new_h = min(self.height(), max_h)
        if new_h != self.height():
            self.resize(self.width(), new_h)
        x = 0
        y = parent.height() - self.height() - self.STATUS_BAR_OFFSET
        self.setGeometry(0, max(0, y), parent.width(), self.height())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._reposition()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_font_from_height()

    # ── Drag-resize via header ───────────────────────────────────────────────
    def _header_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._resizing = True
            self._drag_start_y = event.globalPosition().toPoint().y()
            self._drag_start_height = self.height()
            event.accept()

    def _header_mouse_move(self, event: QMouseEvent) -> None:
        if not self._resizing:
            return
        dy = self._drag_start_y - event.globalPosition().toPoint().y()
        new_h = self._drag_start_height + dy
        if new_h < self.MIN_AUTO_CLOSE_HEIGHT:
            self.stop()
            return
        parent = self.parentWidget()
        if parent:
            max_h = int(parent.height() * self.MAX_HEIGHT_RATIO)
            new_h = min(new_h, max_h)
            new_h = max(new_h, self.HEADER_HEIGHT + 20)
            self.resize(self.width(), new_h)
            self._reposition()
        event.accept()

    def _header_mouse_release(self, event: QMouseEvent) -> None:
        self._resizing = False
        event.accept()

    # ── Log tailing ──────────────────────────────────────────────────────────
    def tail(self, log_path: str | Path) -> None:
        """Start tailing *log_path* and show the panel."""
        self.stop_tailing()
        self._log_path = Path(log_path)
        self._log.clear()
        self._log_offset = 0

        if self._log_path.exists():
            file_size = self._log_path.stat().st_size
            # Only load the last ~100KB for large files
            if file_size > 100_000:
                with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(file_size - 100_000)
                    f.readline()  # discard partial line
                    self._append_lines(f.read().splitlines())
            else:
                self._append_lines(
                    self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                )
            self._log_offset = file_size

        self._title_label.setText(f"Console — {self._log_path.name}")
        self._poll_timer.start()
        self._reposition()
        self.show()
        self.raise_()

    def stop_tailing(self) -> None:
        """Stop the polling timer without hiding the panel."""
        self._poll_timer.stop()

    def stop(self) -> None:
        """Stop tailing and hide the panel."""
        self.stop_tailing()
        self.hide()
        self.closed.emit()

    def _poll_log(self) -> None:
        if not self._log_path or not self._log_path.exists():
            return
        try:
            size = self._log_path.stat().st_size
            if size < self._log_offset:
                # File was truncated / rotated
                self._log.clear()
                self._log_offset = 0
            if size > self._log_offset:
                with self._log_path.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(self._log_offset)
                    new_text = fh.read()
                self._log_offset = size
                self._append_lines(new_text.splitlines())
        except OSError:
            pass

    def _append_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in lines:
            cursor.insertText(line + "\n")
        # Enforce ring-buffer line limit
        doc = self._log.document()
        while doc.blockCount() > self.MAX_LINES:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # remove the newline
        self._log.ensureCursorVisible()

    # ── Header actions ───────────────────────────────────────────────────────
    def _copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self._log.toPlainText())

    def _open_in_notepad(self) -> None:
        if self._log_path and self._log_path.exists():
            subprocess.Popen(["notepad.exe", str(self._log_path)])

    # ── Size hints ───────────────────────────────────────────────────────────
    def sizeHint(self) -> QSize:  # noqa: N802
        parent = self.parentWidget()
        width = parent.width() if parent else 600
        return QSize(width, self.DEFAULT_HEIGHT)
