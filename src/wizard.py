"""First-run setup wizard — prompts for EveJS installation path on initial launch."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QStackedWidget, QProgressBar, QWidget,
)

from .config import load, save
from .constants import COLORS as C
from .core.discovery import validate_evejs_root, find_client_path


class _WizardPage(QWidget):
    """A single page in the setup wizard."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        tl = QLabel(title)
        tl.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {C['white']};")
        layout.addWidget(tl)

        if subtitle:
            sl = QLabel(subtitle)
            sl.setStyleSheet(f"color: {C['grey']}; font-size: 13px;")
            sl.setWordWrap(True)
            layout.addWidget(sl)

        layout.addSpacing(8)
        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        layout.addLayout(self.content)
        layout.addStretch()


class SetupWizard(QDialog):
    """Modal wizard shown when no EveJS root is configured."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EveJS Launcher — Setup")
        self.setFixedSize(560, 440)
        self.setWindowFlags(Qt.WindowType.Dialog)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['bg']}; }}
            QLabel {{ color: {C['white']}; }}
            QLineEdit {{
                background-color: {C['card']};
                border: 1px solid {C['steel']};
                padding: 8px;
                color: {C['white']};
                font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {C['teal']}; }}
            QPushButton {{
                background-color: {C['card']};
                border: 1px solid {C['steel']};
                padding: 8px 20px;
                color: {C['white']};
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['steel']}; }}
            QProgressBar {{
                border: 1px solid {C['steel']};
                background: {C['card']};
                height: 6px;
                text-align: center;
            }}
            QProgressBar::chunk {{ background: {C['teal']}; }}
        """)

        self._evejs_root = ""
        self._client_path = ""
        self._build()

    # ── UI ────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Stacked pages
        self._stack = QStackedWidget()

        # Page 0: Welcome
        p0 = _WizardPage(
            "Welcome to EveJS Launcher",
            "This tool manages and launches multiple EVE clients for your local EveJS server.\n\n"
            "Let's locate your EveJS installation to get started."
        )
        self._stack.addWidget(p0)

        # Page 1: Path selection
        p1 = _WizardPage(
            "Locate Your EveJS Installation",
            "Select the root folder containing StartServerWithMods.bat and the server directory."
        )
        ph = QHBoxLayout()
        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("e.g. G:\\Eve Local\\EveJS-v0.12.2")
        self._path_input.textChanged.connect(self._on_path_changed)
        ph.addWidget(self._path_input)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        ph.addWidget(browse)
        p1.content.addLayout(ph)

        self._path_status = QLabel("Enter the path to your EveJS folder")
        self._path_status.setStyleSheet(f"font-size: 12px; color: {C['grey']};")
        p1.content.addWidget(self._path_status)
        self._stack.addWidget(p1)

        # Page 2: Validation
        p2 = _WizardPage(
            "Installation Verified",
            "We found a working EveJS installation. Here's what was detected:"
        )
        self._results = QLabel("")
        self._results.setStyleSheet(
            f"color: {C['grey']}; font-size: 12px; background-color: {C['card']};"
            "padding: 12px; border-radius: 4px;"
        )
        self._results.setWordWrap(True)
        p2.content.addWidget(self._results)
        self._stack.addWidget(p2)

        # Page 3: Done
        p3 = _WizardPage(
            "Ready!",
            "Setup is complete. The launcher will scan your accounts and show your characters."
        )
        for line in ("✓ EveJS installation validated", "✓ CLIENT path detected", "✓ Accounts will be loaded"):
            check = QLabel(line)
            check.setStyleSheet(f"color: {C['green']}; font-size: 13px;")
            p3.content.addWidget(check)
        self._stack.addWidget(p3)

        root.addWidget(self._stack, 1)

        # Bottom nav bar
        nav = QWidget()
        nav.setFixedHeight(56)
        nav.setStyleSheet(f"background-color: {C['card']};")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(16, 8, 16, 8)

        self._progress = QProgressBar()
        self._progress.setMaximum(3)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        nl.addWidget(self._progress)
        nl.addStretch()

        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setVisible(False)
        nl.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['teal']};
                border: none;
                color: {C['bg']};
                font-weight: bold;
                padding: 8px 20px;
            }}
            QPushButton:hover {{ background-color: #00D8F0; }}
            QPushButton:disabled {{ background-color: {C['steel']}; color: {C['grey']}; }}
        """)
        self._next_btn.clicked.connect(self._go_next)
        nl.addWidget(self._next_btn)

        root.addWidget(nav)

    # ── Navigation ────────────────────────────────────────────────────

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select EveJS Root Folder", "G:\\")
        if path:
            self._path_input.setText(path)

    def _on_path_changed(self, text: str) -> None:
        valid, msg = validate_evejs_root(text)
        if valid:
            self._path_status.setText("✓ Valid EveJS installation")
            self._path_status.setStyleSheet(f"color: {C['green']}; font-size: 12px;")
            self._next_btn.setEnabled(True)
        elif text:
            self._path_status.setText(f"✗ {msg}")
            self._path_status.setStyleSheet(f"color: {C['red']}; font-size: 12px;")
            self._next_btn.setEnabled(False)
        else:
            self._path_status.setText("Enter the path to your EveJS folder")
            self._path_status.setStyleSheet(f"color: {C['grey']}; font-size: 12px;")
            self._next_btn.setEnabled(False)

    def _go_back(self) -> None:
        cur = self._stack.currentIndex()
        if cur <= 0:
            return
        prev = cur - 1
        self._stack.setCurrentIndex(prev)
        self._progress.setValue(prev)
        self._next_btn.setText("Next →")
        self._next_btn.setEnabled(True)
        self._back_btn.setVisible(prev > 0)

    def _go_next(self) -> None:
        cur = self._stack.currentIndex()

        if cur == 1:  # Path → Validation
            self._evejs_root = self._path_input.text().strip()
            self._client_path = find_client_path(self._evejs_root) or "(not detected)"
            self._results.setText(
                f"EveJS Root: {self._evejs_root}\n"
                f"CLIENT Path: {self._client_path}\n\n"
                "Click Next to save these settings."
            )

        nxt = cur + 1
        if nxt < self._stack.count():
            self._stack.setCurrentIndex(nxt)
            self._progress.setValue(nxt)
            self._back_btn.setVisible(True)

            if nxt == self._stack.count() - 1:
                self._next_btn.setText("✓ Finish")
            elif nxt == 1:
                self._next_btn.setText("Next →")
                valid, _ = validate_evejs_root(self._path_input.text())
                self._next_btn.setEnabled(valid)
            else:
                self._next_btn.setText("Next →")
                self._next_btn.setEnabled(True)
        else:
            self._save_and_accept()

    def _save_and_accept(self) -> None:
        cfg = load()
        cfg["evejs_root"] = self._evejs_root
        cfg["client_path"] = self._client_path
        save(cfg)
        self.accept()
