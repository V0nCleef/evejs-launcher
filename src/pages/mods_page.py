"""Mod Manager page for EveJS Launcher V2.

Lists every mod discovered under ``<evejs_root>/mods`` with a toggle switch.
Toggling immediately renames the mod's ``loader.js`` via
:func:`src.core.mod_manager.toggle_mod`; a server restart is required for
changes to take effect.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src import config
from src.constants import COLORS
from src.core.mod_manager import Mod, scan_mods, toggle_mod
from src.widgets.toggle_switch import ToggleSwitch


class ModRow(QFrame):
    """One row in the mod list: toggle + name + path."""

    def __init__(self, mod: Mod, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mod = mod
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.toggle = ToggleSwitch()
        self.toggle.setChecked(mod.active)
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        name_label = QLabel(mod.name)
        name_label.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 15px; font-weight: 600;"
        )
        text_col.addWidget(name_label)

        path_label = QLabel(str(mod.path))
        path_label.setStyleSheet(f"color: {COLORS['grey']}; font-size: 11px;")
        text_col.addWidget(path_label)

        layout.addLayout(text_col, stretch=1)

    def _on_toggled(self, _checked: bool) -> None:
        try:
            new_state = toggle_mod(self.mod)
            self.mod.active = new_state
            # Guard against desync if the filesystem op flipped state oddly.
            if self.toggle.isChecked() != new_state:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(new_state)
                self.toggle.blockSignals(False)
        except Exception as exc:  # pragma: no cover - filesystem errors
            # Revert the switch on failure.
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.mod.active)
            self.toggle.blockSignals(False)
            self.setToolTip(f"Failed to toggle mod: {exc}")


class ModsPage(QWidget):
    """Mod manager: scan, list, toggle mods; apply & restart server."""

    apply_restart_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[ModRow] = []
        self._build_ui()
        self.refresh_mods()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel("MOD MANAGER")
        title.setProperty("class", "title")
        header.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setProperty("class", "secondary")
        header.addWidget(self.count_label)

        header.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("class", "ghost")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_mods)
        header.addWidget(refresh_btn)

        root.addLayout(header)

        # Mod list (scrollable)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        root.addWidget(self._scroll, stretch=1)

        # Warning
        warning = QLabel("Changes take effect on server restart.")
        warning.setStyleSheet(f"color: {COLORS['gold']}; font-size: 12px;")
        root.addWidget(warning)

        # Apply & restart button
        self.apply_btn = QPushButton("Apply && Restart Server")
        self.apply_btn.setProperty("class", "primary")
        self.apply_btn.setFixedHeight(42)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self.apply_restart_clicked.emit)
        root.addWidget(self.apply_btn)

    # ── Data ─────────────────────────────────────────────────────────────────
    def refresh_mods(self) -> None:
        """Rescan the mods directory and rebuild the list."""
        evejs_root = str(config.get_setting("evejs_root") or "")
        mods: list[Mod] = scan_mods(evejs_root) if evejs_root else []

        # Clear existing rows.
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        # Remove all items except the trailing stretch.
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not mods:
            empty = QLabel(
                "No mods found."
                if evejs_root
                else "Set the EveJS root folder in Settings first."
            )
            empty.setProperty("class", "secondary")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_layout.insertWidget(0, empty)
        else:
            for i, mod in enumerate(mods):
                row = ModRow(mod)
                self._rows.append(row)
                self._list_layout.insertWidget(i, row)

        active = sum(1 for m in mods if m.active)
        self.count_label.setText(f"({active} active / {len(mods)} total)")

    def mods(self) -> list[Mod]:
        """Return the currently displayed mods."""
        return [row.mod for row in self._rows]
