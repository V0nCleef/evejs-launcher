"""Deep Signal presentation for the EveJS mod preload manifest.

The page keeps the original filesystem and backend capability contracts:
Native toggles loader files directly, Managed Docker exposes the reviewed
override/recreate flow, and Connect-only remains observable but read-only.
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
from src.constants import SPACING
from src.core.mod_manager import Mod, scan_mods, toggle_mod
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.widgets.page_header import PageHeader
from src.widgets.toggle_switch import ToggleSwitch


class ModRow(QFrame):
    """One keyboard-accessible mod instrument in preload order."""

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
        self._disabled_reason = disabled_reason
        self.setObjectName(f"modRow-{mod.name.casefold().replace(' ', '-')}")
        self.setProperty("class", "modInstrument")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(64)
        self.setAccessibleName(f"Mod {mod.name}")
        self.setAccessibleDescription(
            disabled_reason
            or "Toggle this loader in the server mod preload manifest."
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(SPACING["md"])

        icon_plate = QLabel("MOD")
        icon_plate.setProperty("class", "modIconPlate")
        icon_plate.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_plate.setFixedSize(40, 40)
        icon_plate.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(icon_plate)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        self.name_label = QLabel(mod.name)
        self.name_label.setProperty("class", "modName")
        text_col.addWidget(self.name_label)

        # Keep normal presentation privacy-safe while retaining the absolute
        # path for local troubleshooting in the tooltip.
        folder_name = mod.path.name or mod.name
        self.path_label = QLabel(f"mods / {folder_name} / loader.js")
        self.path_label.setProperty("class", "modPath")
        self.path_label.setToolTip(str(mod.path))
        text_col.addWidget(self.path_label)

        layout.addLayout(text_col, stretch=1)

        self.state_label = QLabel()
        self.state_label.setProperty("class", "modState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setMinimumWidth(74)
        layout.addWidget(self.state_label)

        self.toggle = ToggleSwitch(self)
        self.toggle.setChecked(mod.active)
        self.toggle.setEnabled(can_toggle)
        self.toggle.setToolTip(disabled_reason if not can_toggle else "")
        self.toggle.setAccessibleName(f"Enable {mod.name}")
        self.toggle.setAccessibleDescription(
            disabled_reason
            or "Rename the reviewed loader file to enable or disable this mod."
        )
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)
        self._update_state_presentation()

    def _update_state_presentation(self, *, failed: bool = False) -> None:
        if failed:
            text, state = "ERROR", "error"
        elif not self._can_toggle:
            text, state = "READ ONLY", "readonly"
        elif self.mod.active:
            text, state = "ENABLED", "enabled"
        else:
            text, state = "DISABLED", "disabled"
        self.state_label.setText(text)
        self.state_label.setProperty("state", state)
        self.setProperty("state", state)
        for widget in (self, self.state_label):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

    def _on_toggled(self, _checked: bool) -> None:
        if not self._can_toggle:
            return
        try:
            new_state = toggle_mod(self.mod)
            self.mod.active = new_state
            # Guard against desync if the filesystem operation changed state
            # differently from the optimistic control value.
            if self.toggle.isChecked() != new_state:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(new_state)
                self.toggle.blockSignals(False)
            self.setToolTip("")
            self._update_state_presentation()
        except Exception:  # pragma: no cover - filesystem errors
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.mod.active)
            self.toggle.blockSignals(False)
            self.setToolTip("Failed to change this mod's loader state.")
            self._update_state_presentation(failed=True)


class ModsPage(QWidget):
    """Mod manager: scan, list, toggle mods; apply & restart server."""

    apply_restart_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        self.setAccessibleName("Mods")
        self.setAccessibleDescription(
            "Inspect and apply reviewed EveJS mod loader state."
        )
        self._evejs_root = str(config.get_setting("evejs_root") or "")
        self._rows: list[ModRow] = []
        self._runtime_backend = RuntimeBackend.NATIVE
        self._docker_policy = DockerControlPolicy.CONNECT_ONLY
        self._build_ui()
        self.refresh_mods()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            SPACING["xl"],
            SPACING["lg"],
            SPACING["xl"],
            SPACING["lg"],
        )
        root.setSpacing(SPACING["md"])

        self.page_header = PageHeader(
            "MODS",
            "Review the deterministic loader manifest before changing server state.",
            "EXTENSION CONTROL",
            self,
        )
        self.count_label = QLabel("0 ACTIVE / 0 INSTALLED")
        self.count_label.setProperty("class", "signalPill")
        self.count_label.setAccessibleName("Mod availability count")
        self.page_header.add_action(self.count_label)

        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setProperty("class", "signalSecondary")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setAccessibleName("Refresh mods")
        self.refresh_btn.setAccessibleDescription(
            "Rescan the configured EveJS mods directory."
        )
        self.refresh_btn.clicked.connect(
            lambda _checked=False: self.refresh_mods()
        )
        self.page_header.add_action(self.refresh_btn)
        root.addWidget(self.page_header)

        self.runtime_panel = QFrame(self)
        self.runtime_panel.setProperty("class", "modsRuntimePanel")
        runtime_layout = QHBoxLayout(self.runtime_panel)
        runtime_layout.setContentsMargins(16, 11, 16, 11)
        runtime_layout.setSpacing(SPACING["md"])

        runtime_mark = QLabel("RT")
        runtime_mark.setProperty("class", "modsRuntimeMark")
        runtime_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        runtime_mark.setFixedSize(38, 38)
        runtime_layout.addWidget(runtime_mark)

        runtime_copy = QVBoxLayout()
        runtime_copy.setSpacing(2)
        runtime_eyebrow = QLabel("RUNTIME POLICY")
        runtime_eyebrow.setProperty("class", "modsRuntimeEyebrow")
        runtime_copy.addWidget(runtime_eyebrow)
        self.lbl_backend = QLabel()
        self.lbl_backend.setWordWrap(True)
        self.lbl_backend.setProperty("class", "modsRuntimeDescription")
        self.lbl_backend.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        runtime_copy.addWidget(self.lbl_backend)
        runtime_layout.addLayout(runtime_copy, stretch=1)

        self.runtime_state_label = QLabel()
        self.runtime_state_label.setProperty("class", "signalPill")
        self.runtime_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.runtime_state_label.setAccessibleName("Current mod runtime policy")
        runtime_layout.addWidget(self.runtime_state_label)
        root.addWidget(self.runtime_panel)

        self.manifest_panel = QFrame(self)
        self.manifest_panel.setProperty("class", "modsManifestPanel")
        manifest_layout = QVBoxLayout(self.manifest_panel)
        manifest_layout.setContentsMargins(14, 12, 14, 12)
        manifest_layout.setSpacing(SPACING["sm"])

        manifest_header = QHBoxLayout()
        manifest_title = QLabel("PRELOAD MANIFEST")
        manifest_title.setProperty("class", "modsManifestTitle")
        manifest_header.addWidget(manifest_title)
        manifest_header.addStretch()
        self.manifest_meta_label = QLabel("SCAN ORDER IS PRELOAD ORDER")
        self.manifest_meta_label.setProperty("class", "modsManifestMeta")
        manifest_header.addWidget(self.manifest_meta_label)
        manifest_layout.addLayout(manifest_header)

        divider = QFrame()
        divider.setProperty("modsDivider", True)
        divider.setFixedHeight(1)
        manifest_layout.addWidget(divider)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("modsManifestScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setAccessibleName("Mod preload manifest")

        self._list_container = QWidget()
        self._list_container.setProperty("deepSignal", True)
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 2, 0)
        self._list_layout.setSpacing(SPACING["sm"])
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_container)
        manifest_layout.addWidget(self._scroll, stretch=1)
        root.addWidget(self.manifest_panel, stretch=1)

        self.action_rail = QFrame(self)
        self.action_rail.setProperty("class", "modsActionRail")
        action_layout = QHBoxLayout(self.action_rail)
        action_layout.setContentsMargins(16, 10, 12, 10)
        action_layout.setSpacing(SPACING["md"])

        action_copy = QVBoxLayout()
        action_copy.setSpacing(1)
        action_title = QLabel("COMMIT MANIFEST")
        action_title.setProperty("class", "modsActionTitle")
        action_copy.addWidget(action_title)
        self.warning_label = QLabel("Changes take effect on server restart.")
        self.warning_label.setProperty("class", "modsActionDescription")
        self.warning_label.setWordWrap(True)
        action_copy.addWidget(self.warning_label)
        action_layout.addLayout(action_copy, stretch=1)

        self.apply_btn = QPushButton("APPLY & RESTART SERVER")
        self.apply_btn.setProperty("class", "modsApply")
        self.apply_btn.setMinimumHeight(42)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setAccessibleName("Apply mod manifest and restart server")
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        action_layout.addWidget(self.apply_btn)
        root.addWidget(self.action_rail)
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

    def _set_runtime_state(self, text: str, state: str) -> None:
        self.runtime_state_label.setText(text)
        self.runtime_state_label.setProperty("state", state)
        style = self.runtime_state_label.style()
        style.unpolish(self.runtime_state_label)
        style.polish(self.runtime_state_label)
        self.runtime_state_label.update()

    def _apply_runtime_presentation(self) -> None:
        if self._runtime_backend is RuntimeBackend.NATIVE:
            self.lbl_backend.setText(
                "Native: loader.js changes take effect after a server restart."
            )
            self.warning_label.setText("Changes take effect on server restart.")
            self.apply_btn.setText("Apply && Restart Server")
            self.apply_btn.setToolTip("")
            self.apply_btn.setAccessibleDescription(
                "Apply the selected loader state and restart the Native server stack."
            )
            self._set_runtime_state("NATIVE HOST", "ready")
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
            self.apply_btn.setAccessibleDescription(self.apply_btn.toolTip())
            self._set_runtime_state("DOCKER · MANAGED", "online")
            return
        reason = self._disabled_reason()
        self.lbl_backend.setText(
            "Connect-only Docker: mods are visible, but the launcher cannot "
            "change mod or Compose state."
        )
        self.warning_label.setText("Connect-only mode is observational and read-only.")
        self.apply_btn.setText("Connect-only — Read Only")
        self.apply_btn.setToolTip(reason)
        self.apply_btn.setAccessibleDescription(reason)
        self._set_runtime_state("DOCKER · CONNECT ONLY", "idle")

    def _on_apply_clicked(self) -> None:
        if self._can_mutate():
            self.apply_restart_clicked.emit()

    # ── Data ─────────────────────────────────────────────────────────────────
    def refresh_mods(self) -> None:
        """Rescan the mods directory and rebuild the list."""
        evejs_root = self._evejs_root
        mods: list[Mod] = scan_mods(evejs_root) if evejs_root else []

        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        # Preserve the trailing stretch so short manifests remain top-aligned.
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not mods:
            empty = QFrame()
            empty.setProperty("class", "modsEmptyState")
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(24, 32, 24, 32)
            empty_layout.setSpacing(SPACING["sm"])
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_mark = QLabel("NO LOADERS")
            empty_mark.setProperty("class", "modsEmptyTitle")
            empty_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(empty_mark)
            empty_message = QLabel(
                "No reviewed mod loaders were found."
                if evejs_root
                else "Set the EveJS root folder in Settings first."
            )
            empty_message.setProperty("class", "modsEmptyDescription")
            empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_message.setWordWrap(True)
            empty_layout.addWidget(empty_message)
            self._list_layout.insertWidget(0, empty)
        else:
            can_mutate = self._can_mutate()
            disabled_reason = self._disabled_reason()
            for index, mod in enumerate(mods):
                row = ModRow(
                    mod,
                    can_toggle=can_mutate,
                    disabled_reason=disabled_reason,
                    parent=self._list_container,
                )
                self._rows.append(row)
                self._list_layout.insertWidget(index, row)

        active = sum(1 for mod in mods if mod.active)
        self.count_label.setText(f"{active} ACTIVE / {len(mods)} INSTALLED")
        self.count_label.setToolTip(
            f"{active} active mod loaders out of {len(mods)} installed"
        )
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
