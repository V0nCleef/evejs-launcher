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
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.widgets.toggle_switch import ToggleSwitch


class ModRow(QFrame):
    """One row in the mod list: toggle + name + path."""

    def __init__(
        self,
        mod: Mod,
        *,
        can_toggle: bool = True,
        disabled_reason: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mod = mod
        self._can_toggle = can_toggle
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self.toggle = ToggleSwitch()
        self.toggle.setChecked(mod.active)
        self.toggle.setEnabled(can_toggle)
        self.toggle.setToolTip(disabled_reason if not can_toggle else "")
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
        if not self._can_toggle:
            return
        try:
            new_state = toggle_mod(self.mod)
            self.mod.active = new_state
            # Guard against desync if the filesystem op flipped state oddly.
            if self.toggle.isChecked() != new_state:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(new_state)
                self.toggle.blockSignals(False)
        except Exception:  # pragma: no cover - filesystem errors
            # Revert the switch on failure.
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.mod.active)
            self.toggle.blockSignals(False)
            self.setToolTip("Failed to change this mod's loader state.")


class ModsPage(QWidget):
    """Mod manager: scan, list, toggle mods; apply & restart server."""

    apply_restart_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._evejs_root = str(config.get_setting("evejs_root") or "")
        self._rows: list[ModRow] = []
        self._runtime_backend = RuntimeBackend.NATIVE
        self._docker_policy = DockerControlPolicy.CONNECT_ONLY
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

        self.lbl_backend = QLabel()
        self.lbl_backend.setWordWrap(True)
        self.lbl_backend.setProperty("class", "secondary")
        root.addWidget(self.lbl_backend)

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
        self.warning_label = QLabel("Changes take effect on server restart.")
        self.warning_label.setStyleSheet(
            f"color: {COLORS['gold']}; font-size: 12px;"
        )
        root.addWidget(self.warning_label)

        # Apply & restart button
        self.apply_btn = QPushButton("Apply && Restart Server")
        self.apply_btn.setProperty("class", "primary")
        self.apply_btn.setFixedHeight(42)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        root.addWidget(self.apply_btn)
        self._apply_runtime_presentation()

    def set_evejs_root(self, evejs_root: str) -> None:
        """Select the root scanned by both Native and Docker mod views."""
        self._evejs_root = evejs_root
        self.refresh_mods()

    def set_runtime_context(
        self,
        backend: RuntimeBackend,
        docker_policy: DockerControlPolicy,
    ) -> None:
        """Expose only controls the selected backend can actually perform."""
        if (
            backend is self._runtime_backend
            and docker_policy is self._docker_policy
        ):
            return
        self._runtime_backend = backend
        self._docker_policy = docker_policy
        self._apply_runtime_presentation()
        self.refresh_mods()

    def selected_mod_names(self) -> tuple[str, ...]:
        """Return active loaders in deterministic scan/preload order."""
        return tuple(row.mod.name for row in self._rows if row.mod.active)

    def _can_mutate(self) -> bool:
        return (
            self._runtime_backend is RuntimeBackend.NATIVE
            or self._docker_policy is DockerControlPolicy.MANAGED
        )

    def _disabled_reason(self) -> str:
        if self._runtime_backend is RuntimeBackend.DOCKER_COMPOSE:
            return "Connect-only Docker mode cannot change mod or Compose state."
        return ""

    def _apply_runtime_presentation(self) -> None:
        if self._runtime_backend is RuntimeBackend.NATIVE:
            self.lbl_backend.setText(
                "Native: loader.js changes take effect after a server restart."
            )
            self.warning_label.setText("Changes take effect on server restart.")
            self.apply_btn.setText("Apply && Restart Server")
            self.apply_btn.setToolTip("")
            return
        if self._docker_policy is DockerControlPolicy.MANAGED:
            self.lbl_backend.setText(
                "Managed Docker: supported loader.js preloads are bind-mounted "
                "and applied by recreating the server container."
            )
            self.warning_label.setText(
                "Applying disconnects clients while the server container is recreated."
            )
            self.apply_btn.setText("Apply && Recreate Server")
            self.apply_btn.setToolTip(
                "Write the launcher-owned override and recreate the server container."
            )
            return
        reason = self._disabled_reason()
        self.lbl_backend.setText(
            "Connect-only Docker: mods are visible, but the launcher cannot "
            "change mod or Compose state."
        )
        self.warning_label.setText("Connect-only mode is observational and read-only.")
        self.apply_btn.setText("Connect-only — Read Only")
        self.apply_btn.setToolTip(reason)

    def _on_apply_clicked(self) -> None:
        if self._can_mutate():
            self.apply_restart_clicked.emit()

    # ── Data ─────────────────────────────────────────────────────────────────
    def refresh_mods(self) -> None:
        """Rescan the mods directory and rebuild the list."""
        evejs_root = self._evejs_root
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
            can_mutate = self._can_mutate()
            disabled_reason = self._disabled_reason()
            for i, mod in enumerate(mods):
                row = ModRow(
                    mod,
                    can_toggle=can_mutate,
                    disabled_reason=disabled_reason,
                )
                self._rows.append(row)
                self._list_layout.insertWidget(i, row)

        active = sum(1 for m in mods if m.active)
        self.count_label.setText(f"({active} active / {len(mods)} total)")
        can_apply_empty_docker = (
            self._runtime_backend is RuntimeBackend.DOCKER_COMPOSE
            and self._docker_policy is DockerControlPolicy.MANAGED
            and bool(evejs_root)
        )
        self.apply_btn.setEnabled(
            self._can_mutate() and (bool(mods) or can_apply_empty_docker)
        )

    def mods(self) -> list[Mod]:
        """Return the currently displayed mods."""
        return [row.mod for row in self._rows]
