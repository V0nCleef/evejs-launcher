"""Deep Signal presentation for installed EveJS mods.

The page presents both legacy ``loader.js`` preloads and reviewed,
manifest-declared source integrations.  Filesystem mutation stays in the mod
manager; the widgets only request an explicit desired state and report it.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src import config
from src.constants import SPACING
from src.core.mod_activation_state import (
    ModActivationProjection,
    ModActivationStateError,
    ModActivationStatus,
    project_mod_activation,
    read_mod_activation_state,
)
from src.core.mod_manager import (
    ActivationKind,
    Mod,
    active_loader_names,
    request_mod_activation,
    scan_mods,
)
from src.core.mod_management import (
    ManagedModRegistration,
    ModManagementError,
    ModNotManagedError,
    managed_mod_registry_path,
    read_managed_mod_registration,
)
from src.core.mod_runtime_state import ModRuntimeSnapshot
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.widgets.page_header import PageHeader
from src.widgets.toggle_switch import ToggleSwitch


class ModRow(QFrame):
    """One keyboard-accessible installed-mod instrument."""

    state_changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(
        self,
        mod: Mod,
        *,
        projection: ModActivationProjection,
        projection_resolver: Callable[[Mod], ModActivationProjection],
        can_toggle: bool = True,
        disabled_reason: str = "",
        disabled_state: str = "READ ONLY",
        management: ManagedModRegistration | None = None,
        management_error: str = "",
        can_remove: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mod = mod
        self._can_toggle = can_toggle
        self._disabled_reason = disabled_reason
        self._disabled_state = disabled_state
        self._projection = projection
        self._projection_resolver = projection_resolver
        self._management = management
        self._management_error = management_error
        self._can_remove = can_remove
        self._can_show_repair = management is None and bool(management_error)
        self._operation_error = ""
        self._lifecycle_busy = False
        is_loader = mod.activation_kind is ActivationKind.LOADER_RENAME
        self.setObjectName(f"modRow-{mod.name.casefold().replace(' ', '-')}")
        self.setProperty("class", "modInstrument")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(64)
        self.setAccessibleName(f"Mod {mod.name}")
        self.setAccessibleDescription(
            disabled_reason
            or (
                "Toggle this JavaScript preload mod. The configured state "
                "takes effect after the server restarts."
                if is_loader
                else "Toggle this source-integrated mod through its declared "
                "configuration. The configured state takes effect after the "
                "Game server restarts."
            )
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(SPACING["md"])

        self.kind_badge = QLabel("JS" if is_loader else "CFG")
        self.kind_badge.setProperty("class", "modIconPlate")
        self.kind_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.kind_badge.setFixedSize(40, 40)
        self.kind_badge.setToolTip(
            "PRELOAD MOD" if is_loader else "SOURCE-INTEGRATED MOD"
        )
        self.kind_badge.setAccessibleName(self.kind_badge.toolTip())
        self.kind_badge.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        layout.addWidget(self.kind_badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        self.name_label = QLabel(mod.name)
        self.name_label.setProperty("class", "modName")
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        text_col.addWidget(self.name_label)

        # Keep normal presentation privacy-safe while retaining the absolute
        # path for local troubleshooting in the tooltip.
        if is_loader:
            folder_name = mod.path.name or mod.name
            display_path = f"mods / {folder_name} / loader.js"
            troubleshooting_path = mod.path
        else:
            config_path = mod.config_path
            config_name = config_path.name if config_path else "configuration.json"
            config_key = mod.config_key or "enabled"
            display_path = f"config / mods / {config_name} → {config_key}"
            troubleshooting_path = config_path or mod.manifest_path or mod.path
        self.path_label = QLabel(display_path)
        self.path_label.setProperty("class", "modPath")
        self.path_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.path_label.setToolTip(str(troubleshooting_path))
        text_col.addWidget(self.path_label)

        layout.addLayout(text_col, stretch=1)

        self.state_label = QLabel()
        self.state_label.setProperty("class", "modState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setMinimumWidth(124)
        layout.addWidget(self.state_label)

        self.remove_btn = QPushButton()
        self.remove_btn.setProperty("class", "modManagementAction")
        self.remove_btn.setFixedSize(94, 32)
        self.remove_btn.setAccessibleName(f"Remove {mod.name}")
        if management is not None:
            self.remove_btn.setProperty("managementRole", "remove")
            self.remove_btn.setText("REMOVE")
            self.remove_btn.setAccessibleDescription(
                "Remove this mod through its verified launcher-compatible installer."
            )
            self.remove_btn.setToolTip(
                "Remove this mod from EveJS. Saved data handling is chosen next."
                if can_remove
                else management_error
            )
        elif management_error:
            self.remove_btn.setProperty("managementRole", "repair")
            self.remove_btn.setText("REPAIR")
            self.remove_btn.setAccessibleName(
                f"Explain removal repair for {mod.name}"
            )
            self.remove_btn.setAccessibleDescription(management_error)
            self.remove_btn.setToolTip(management_error)
        else:
            self.remove_btn.setProperty("managementRole", "external")
            self.remove_btn.setText("EXTERNAL")
            unmanaged_reason = (
                "This mod was installed outside a launcher-compatible Setup. "
                "Run its matching Setup once to add launcher removal support."
            )
            self.remove_btn.setAccessibleDescription(unmanaged_reason)
            self.remove_btn.setToolTip(unmanaged_reason)
        self.remove_btn.setEnabled(can_remove or self._can_show_repair)
        self._sync_remove_cursor()
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.remove_btn)

        self.toggle = ToggleSwitch(self)
        self.toggle.setChecked(mod.active)
        self.toggle.setEnabled(can_toggle)
        self.toggle.setToolTip(disabled_reason if not can_toggle else "")
        self.toggle.setAccessibleName(f"Enable {mod.name}")
        self.toggle.setAccessibleDescription(
            disabled_reason
            or (
                "Set the reviewed loader mod to the selected state."
                if is_loader
                else "Set the source-integrated mod's declared enabled flag "
                "to the selected state."
            )
        )
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)
        self._update_state_presentation()

    def _update_state_presentation(self) -> None:
        if not self.mod.valid:
            text, state = "INVALID", "error"
        elif self._operation_error:
            text, state = "VERIFICATION FAILED", "error"
        elif self._lifecycle_busy and self._can_toggle:
            text, state = "SERVER BUSY", "readonly"
        elif not self._can_toggle:
            text, state = self._disabled_state, "readonly"
        else:
            status = self._projection.status
            if status is ModActivationStatus.VERIFIED:
                if self._projection.effective:
                    text, state = "ENABLED · VERIFIED", "enabled"
                else:
                    text, state = "DISABLED · VERIFIED", "disabled"
            elif status is ModActivationStatus.RESTART_REQUIRED:
                text, state = "RESTART REQUIRED", "pending"
            elif status is ModActivationStatus.RUNTIME_UNVERIFIED:
                text, state = "RUNTIME UNVERIFIED", "readonly"
            elif status is ModActivationStatus.VERIFICATION_FAILED:
                text, state = "VERIFICATION FAILED", "error"
            else:
                text, state = "CONTRACT CHANGED", "error"
        self.state_label.setText(text)
        self.state_label.setProperty("state", state)
        self.setProperty("state", state)
        for widget in (self, self.state_label):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

    def _on_toggled(self, checked: bool) -> None:
        if not self._can_toggle or self._lifecycle_busy:
            if self.toggle.isChecked() != self.mod.active:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(self.mod.active)
                self.toggle.blockSignals(False)
            return
        try:
            # Pass the control's explicit desired value.  Inverting the model
            # here would race a refresh or external config edit and could apply
            # the exact opposite of what the user selected.  Delightful.
            new_state = request_mod_activation(self.mod, bool(checked))
            self.mod.active = new_state
            # Guard against desync if the filesystem operation changed state
            # differently from the optimistic control value.
            if self.toggle.isChecked() != new_state:
                self.toggle.blockSignals(True)
                self.toggle.setChecked(new_state)
                self.toggle.blockSignals(False)
            self._operation_error = ""
            self.setToolTip("")
            self.toggle.setToolTip("")
            self._projection = self._projection_resolver(self.mod)
            self._update_state_presentation()
            self.state_changed.emit()
        except Exception as exc:  # filesystem and validation errors
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.mod.active)
            self.toggle.blockSignals(False)
            self._operation_error = str(exc) or "Unknown mod activation error."
            try:
                self._projection = self._projection_resolver(self.mod)
            except Exception:
                pass
            failure_message = (
                f"Failed to change this mod's state: {self._operation_error}"
            )
            self.setToolTip(failure_message)
            self.toggle.setToolTip(failure_message)
            self._update_state_presentation()
            self.state_changed.emit()

    def _on_remove_clicked(self) -> None:
        if self._lifecycle_busy:
            return
        if self._management is None:
            if self._can_show_repair:
                QMessageBox.warning(
                    self,
                    "Mod Removal Needs Repair",
                    self._management_error
                    + "\n\nRun this mod's matching launcher-compatible Setup "
                    "for the selected EveJS root, then refresh Mods. Nothing "
                    "was removed.",
                )
            return
        if (
            not self._can_remove
        ):
            return
        self.remove_requested.emit(self.mod)

    def set_lifecycle_busy(self, busy: bool) -> None:
        """Temporarily lock mutation without changing backend capability."""
        self._lifecycle_busy = bool(busy)
        self.toggle.setEnabled(self._can_toggle and not self._lifecycle_busy)
        self.remove_btn.setEnabled(
            (self._can_remove or self._can_show_repair)
            and not self._lifecycle_busy
        )
        self._sync_remove_cursor()
        if self._lifecycle_busy and self._can_toggle:
            reason = "A Game server lifecycle operation is currently running."
        else:
            reason = self._disabled_reason if not self._can_toggle else ""
        self.toggle.setToolTip(reason)
        self._update_state_presentation()

    def _sync_remove_cursor(self) -> None:
        cursor = (
            Qt.CursorShape.PointingHandCursor
            if self.remove_btn.isEnabled()
            else Qt.CursorShape.ArrowCursor
        )
        self.remove_btn.setCursor(cursor)


class ModsPage(QWidget):
    """Scan, present and configure mods, then request their restart scope."""

    apply_restart_clicked = pyqtSignal()
    remove_mod_requested = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        self.setAccessibleName("Mods")
        self.setAccessibleDescription(
            "Inspect and apply reviewed EveJS mod activation state."
        )
        self._evejs_root = str(config.get_setting("evejs_root") or "")
        self._rows: list[ModRow] = []
        self._runtime_backend = RuntimeBackend.NATIVE
        self._docker_policy = DockerControlPolicy.CONNECT_ONLY
        self._mod_runtime_snapshot: ModRuntimeSnapshot | None = None
        self._activation_state_error = ""
        self._lifecycle_busy = False
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
            "Review installed loaders and source integrations before changing server state.",
            "EXTENSION CONTROL",
            self,
        )
        self.count_label = QLabel("0 CONFIGURED ON / 0 INSTALLED")
        self.count_label.setProperty("class", "signalPill")
        self.count_label.setAccessibleName("Mod availability count")
        self.page_header.add_action(self.count_label)

        self.refresh_btn = QPushButton("REFRESH")
        self.refresh_btn.setProperty("class", "signalSecondary")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setAccessibleName("Refresh mods")
        self.refresh_btn.setAccessibleDescription(
            "Rescan supported mod locations in the configured EveJS root."
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
        self.manifest_title = QLabel("MOD MANIFEST")
        self.manifest_title.setProperty("class", "modsManifestTitle")
        manifest_header.addWidget(self.manifest_title)
        manifest_header.addStretch()
        self.manifest_meta_label = QLabel("LOADERS AND SOURCE INTEGRATIONS")
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
        self._scroll.setAccessibleName("Installed mod manifest")

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
        action_title = QLabel("APPLY MOD STATE")
        action_title.setProperty("class", "modsActionTitle")
        action_copy.addWidget(action_title)
        self.warning_label = QLabel(
            "Changes are confirmed only after a verified Game restart."
        )
        self.warning_label.setProperty("class", "modsActionDescription")
        self.warning_label.setWordWrap(True)
        action_copy.addWidget(self.warning_label)
        action_layout.addLayout(action_copy, stretch=1)

        self.apply_btn = QPushButton("APPLY & RESTART SERVER")
        self.apply_btn.setProperty("class", "modsApply")
        self.apply_btn.setMinimumHeight(42)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.setAccessibleName("Apply configured mods and restart server")
        self.apply_btn.clicked.connect(self._on_apply_clicked)
        action_layout.addWidget(self.apply_btn)
        root.addWidget(self.action_rail)
        self._apply_runtime_presentation()

    def set_evejs_root(self, evejs_root: str) -> None:
        """Select the root scanned by both Native and Docker mod views."""
        if evejs_root != self._evejs_root:
            self._mod_runtime_snapshot = None
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
        if backend is not self._runtime_backend:
            self._mod_runtime_snapshot = None
        self._runtime_backend = backend
        self._docker_policy = docker_policy
        self._apply_runtime_presentation()
        self.refresh_mods()

    def set_mod_runtime_snapshot(
        self,
        snapshot: ModRuntimeSnapshot | None,
    ) -> None:
        """Publish current attested mod evidence, or clear it as unverified."""

        if snapshot is not None and not isinstance(snapshot, ModRuntimeSnapshot):
            raise TypeError("snapshot must be a ModRuntimeSnapshot or None.")
        self._mod_runtime_snapshot = snapshot
        self.refresh_mods()

    def set_lifecycle_busy(self, busy: bool) -> None:
        """Lock all mod mutation while a server lifecycle action owns state."""
        busy = bool(busy)
        if busy == self._lifecycle_busy:
            return
        self._lifecycle_busy = busy
        for row in self._rows:
            row.set_lifecycle_busy(busy)
        self.refresh_btn.setEnabled(not busy)
        self._update_summary_and_actions()

    def selected_loader_names(self) -> tuple[str, ...]:
        """Return only active loader preloads in deterministic scan order."""
        return active_loader_names(self.mods())

    def selected_mod_names(self) -> tuple[str, ...]:
        """Compatibility alias for older application call sites.

        Docker must never receive config-backed mod IDs as ``NODE_OPTIONS``
        preloads, so even this legacy spelling deliberately returns loaders
        only.
        """
        return self.selected_loader_names()

    def _can_mutate(self) -> bool:
        return (
            self._runtime_backend is RuntimeBackend.NATIVE
            or self._docker_policy is DockerControlPolicy.MANAGED
        )

    def _disabled_reason(self) -> str:
        if self._runtime_backend is RuntimeBackend.DOCKER_COMPOSE:
            return "Connect-only Docker mode cannot change mod or Compose state."
        return ""

    def _row_capability(self, mod: Mod) -> tuple[bool, str, str]:
        """Return whether this backend may mutate ``mod`` and why not."""
        if not mod.valid:
            return False, mod.error or "This mod manifest is invalid.", "INVALID"
        if self._activation_state_error:
            return (
                False,
                "The launcher activation journal is invalid or unavailable: "
                + self._activation_state_error,
                "STATE ERROR",
            )

        if self._runtime_backend is RuntimeBackend.NATIVE:
            if "native" not in mod.supported_backends:
                return (
                    False,
                    "This mod does not declare support for the Native backend.",
                    "UNSUPPORTED",
                )
            return True, "", ""

        if mod.activation_kind is ActivationKind.JSON_BOOLEAN:
            return (
                False,
                "Source-integrated mods are available on Native servers only.",
                "NATIVE ONLY",
            )

        if "docker" not in mod.supported_backends:
            return (
                False,
                "This mod does not declare support for managed Docker.",
                "UNSUPPORTED",
            )

        if self._docker_policy is DockerControlPolicy.CONNECT_ONLY:
            return False, self._disabled_reason(), "READ ONLY"
        return True, "", ""

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
                "Native: configured mod changes take effect after a Game server restart."
            )
            self.warning_label.setText(
                "Configured changes are confirmed only after a verified Game restart."
            )
            self.apply_btn.setText("Apply && Restart Server")
            self.apply_btn.setToolTip("")
            self.apply_btn.setAccessibleDescription(
                "Apply the configured mod state and restart the Native Game server."
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
        if self._can_mutate() and self.apply_btn.isEnabled():
            self.apply_restart_clicked.emit()

    # ── Data ─────────────────────────────────────────────────────────────────
    def _current_mod_snapshot(self) -> ModRuntimeSnapshot | None:
        snapshot = self._mod_runtime_snapshot
        if snapshot is None or not self._evejs_root:
            return None
        try:
            snapshot_root = snapshot.root.resolve(strict=True)
            selected_root = Path(self._evejs_root).resolve(strict=True)
        except (OSError, TypeError, ValueError):
            return None
        expected_backend = self._runtime_backend.value
        if snapshot_root != selected_root or snapshot.backend != expected_backend:
            return None
        return snapshot

    def _resolve_projection(self, mod: Mod) -> ModActivationProjection:
        state = read_mod_activation_state(mod.evejs_root or self._evejs_root)
        return project_mod_activation(
            mod,
            self._current_mod_snapshot(),
            state.for_mod(mod.id),
        )

    def refresh_mods(self) -> None:
        """Rescan supported mod locations and rebuild the list."""
        evejs_root = self._evejs_root
        mods: list[Mod] = scan_mods(evejs_root) if evejs_root else []
        activation_state = None
        self._activation_state_error = ""
        if evejs_root:
            try:
                activation_state = read_mod_activation_state(evejs_root)
            except ModActivationStateError as exc:
                self._activation_state_error = str(exc) or "Unknown state error."
        runtime_snapshot = self._current_mod_snapshot()

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
            empty_mark = QLabel("NO MODS")
            empty_mark.setProperty("class", "modsEmptyTitle")
            empty_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(empty_mark)
            empty_message = QLabel(
                "No supported loader mods or source integrations were found."
                if evejs_root
                else "Set the EveJS root folder in Settings first."
            )
            empty_message.setProperty("class", "modsEmptyDescription")
            empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_message.setWordWrap(True)
            empty_layout.addWidget(empty_message)
            self._list_layout.insertWidget(0, empty)
        else:
            for index, mod in enumerate(mods):
                if activation_state is None:
                    projection = project_mod_activation(mod, runtime_snapshot)
                else:
                    projection = project_mod_activation(
                        mod,
                        runtime_snapshot,
                        activation_state.for_mod(mod.id),
                    )
                can_toggle, disabled_reason, disabled_state = (
                    self._row_capability(mod)
                )
                management = None
                management_error = ""
                try:
                    # Legacy loader IDs come from folder names and are not
                    # necessarily registry-safe. They are simply external,
                    # not broken launcher enrollments.
                    managed_mod_registry_path(mod.id)
                except ModManagementError:
                    eligible_for_management = False
                else:
                    eligible_for_management = True
                if eligible_for_management:
                    try:
                        management = read_managed_mod_registration(mod)
                    except ModNotManagedError:
                        pass
                    except ModManagementError as exc:
                        management_error = (
                            "Launcher removal support needs repair: "
                            + (str(exc) or "unknown registration error")
                        )
                can_remove = (
                    management is not None
                    and mod.valid
                    and self._runtime_backend is RuntimeBackend.NATIVE
                )
                if management is not None and not can_remove:
                    if self._runtime_backend is not RuntimeBackend.NATIVE:
                        management_error = (
                            "Managed mod removal is available from the Native backend only."
                        )
                row = ModRow(
                    mod,
                    projection=projection,
                    projection_resolver=self._resolve_projection,
                    can_toggle=can_toggle,
                    disabled_reason=disabled_reason,
                    disabled_state=disabled_state,
                    management=management,
                    management_error=management_error,
                    can_remove=can_remove,
                    parent=self._list_container,
                )
                row.state_changed.connect(self._update_summary_and_actions)
                row.remove_requested.connect(self.remove_mod_requested.emit)
                row.set_lifecycle_busy(self._lifecycle_busy)
                self._rows.append(row)
                self._list_layout.insertWidget(index, row)

        self._update_summary_and_actions()

    def _update_summary_and_actions(self) -> None:
        """Refresh count and Apply capability after an in-row mutation."""
        mods = self.mods()
        active = sum(1 for mod in mods if mod.valid and mod.active)
        self.count_label.setText(
            f"{active} CONFIGURED ON / {len(mods)} INSTALLED"
        )
        self.count_label.setToolTip(
            f"{active} configured enabled mods out of {len(mods)} installed"
        )

        native_capable = any(
            mod.valid and "native" in mod.supported_backends for mod in mods
        )
        loader_capable = any(
            mod.valid
            and mod.activation_kind is ActivationKind.LOADER_RENAME
            and "docker" in mod.supported_backends
            for mod in mods
        )
        can_apply_empty_docker = (
            self._runtime_backend is RuntimeBackend.DOCKER_COMPOSE
            and self._docker_policy is DockerControlPolicy.MANAGED
            and bool(self._evejs_root)
            and not mods
        )
        if self._runtime_backend is RuntimeBackend.NATIVE:
            can_apply = native_capable
        elif self._docker_policy is DockerControlPolicy.MANAGED:
            can_apply = loader_capable or can_apply_empty_docker
        else:
            can_apply = False
        if self._lifecycle_busy:
            can_apply = False
        if self._activation_state_error:
            can_apply = False
        self.apply_btn.setEnabled(can_apply)

    def mods(self) -> list[Mod]:
        """Return the currently displayed mods."""
        return [row.mod for row in self._rows]
