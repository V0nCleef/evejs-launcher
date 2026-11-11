"""One-start choices for mods whose manifest omits the selected EveJS release."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.core.mod_evejs_compatibility import (
    EvejsModCompatibility,
    EvejsModCompatibilityStatus,
    assess_active_runtime_mods,
)
from src.core.mod_manifest import ActivationKind, Mod


class ModStartCompatibilityChoice(str, Enum):
    RUN_ANYWAY = "run_anyway"
    REMEMBER = "remember"
    DISABLE_AFFECTED = "disable_affected"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ModStartCompatibilityDecision:
    """An explicit choice and the exact reviewed physical mods to remember."""

    choice: ModStartCompatibilityChoice
    selected_mod_keys: tuple[str, ...] = ()


def _canonical_root(root: str | Path) -> str:
    return os.path.normcase(str(Path(root).resolve(strict=True)))


def _backend_key(backend: str) -> str:
    value = str(backend).casefold()
    return "docker" if value in {"docker", "docker_compose"} else value


def modset_fingerprint(mods: tuple[Mod, ...]) -> str:
    """Fingerprint the ordered applicable contracts and their enabled states."""
    from src.core.mod_runtime_state import mod_contract_sha256, mod_state_key

    entries = []
    for mod in mods:
        entries.append(
            {
                "key": mod_state_key(mod),
                "contract": mod_contract_sha256(mod),
                "active": bool(mod.active),
                "activation": mod.activation_kind.value,
            }
        )
    encoded = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unsupported_signature(
    compatibility: tuple[EvejsModCompatibility, ...],
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    return tuple(
        (item.mod_id, item.mod_version, tuple(item.declared_versions or ()))
        for item in compatibility
        if item.status in {EvejsModCompatibilityStatus.UNSUPPORTED,
                           EvejsModCompatibilityStatus.UNKNOWN_INSTALLED_VERSION}
    )


@dataclass(frozen=True)
class ModCompatibilityConsent:
    """One invocation's explicit consent, bound to its exact inspected modset."""

    root: str
    backend: str
    installed_version: str | None
    modset_sha256: str
    unsupported: tuple[tuple[str, str, tuple[str, ...]], ...]
    local_payloads: tuple[tuple[str, str], ...] = ()

    @classmethod
    def capture(
        cls,
        root: str | Path,
        backend: str,
        installed_version: str | None,
        mods: tuple[Mod, ...],
        compatibility: tuple[EvejsModCompatibility, ...],
        *, local_mods=(), local_payloads=(),
    ) -> "ModCompatibilityConsent":
        from src.core.mod_compatibility_approvals import payload_fingerprint
        from src.core.mod_runtime_state import mod_state_key
        return cls(
            root=_canonical_root(root),
            backend=_backend_key(backend),
            installed_version=installed_version,
            modset_sha256=modset_fingerprint(mods),
            unsupported=_unsupported_signature(compatibility),
            local_payloads=tuple(local_payloads) + tuple((mod_state_key(mod), payload_fingerprint(mod)) for mod in local_mods),
        )

    def matches(
        self,
        root: str | Path,
        backend: str,
        installed_version: str | None,
        mods: tuple[Mod, ...],
    ) -> bool:
        try:
            canonical_root = _canonical_root(root)
            fingerprint = modset_fingerprint(mods)
            current = assess_active_runtime_mods(mods, installed_version)
            from src.core.mod_compatibility_approvals import payload_fingerprint
            from src.core.mod_runtime_state import mod_state_key
            by_key = {mod_state_key(mod): mod for mod in mods}
            if any(key not in by_key or payload_fingerprint(by_key[key]) != digest for key, digest in self.local_payloads):
                return False
        except (OSError, TypeError, ValueError):
            return False
        return (
            canonical_root == self.root
            and _backend_key(backend) == self.backend
            and installed_version == self.installed_version
            and fingerprint == self.modset_sha256
            and _unsupported_signature(current) == self.unsupported
        )


class ModStartCompatibilityDialog(QDialog):
    """Ask whether to accept undeclared support, disable affected mods, or stop."""

    def __init__(
        self,
        compatibility: tuple[EvejsModCompatibility, ...],
        installed_version: str | None,
        parent: QWidget | None = None,
        *,
        docker_mode: bool = False,
        mod_keys: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice = ModStartCompatibilityChoice.CANCEL
        self.selected_mod_keys: tuple[str, ...] = ()
        from src.core.mod_evejs_compatibility import normalize_evejs_version
        from src.i18n import format_ui_phrase, translate_ui_phrase
        try:
            normalize_evejs_version(installed_version)
            self._known_version = True
        except (TypeError, ValueError):
            self._known_version = False
        # The owner supplies root-bound physical keys. Standalone dialogs use
        # opaque row keys; they cannot become production approval authority.
        keys = tuple(f"review:{index}" for index in range(len(compatibility))) if mod_keys is None else mod_keys
        if (len(keys) != len(compatibility) or len(set(keys)) != len(keys)
                or any(type(key) is not str or not key for key in keys)):
            raise ValueError("Compatibility rows need unique review keys.")
        self.mod_checkboxes: dict[str, QCheckBox] = {}
        self.setWindowTitle("Mod Compatibility")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(760, 460)

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "The mod authors have not declared support for this EveJS version. "
            "These mods may still work, but compatibility is unconfirmed. "
            "Choose how to handle this start."
            + (
                " Disabling these mods requires recreating the Docker Game "
                "container, which disconnects connected clients."
                if docker_mode
                else ""
            )
        )
        explanation.setTextFormat(Qt.TextFormat.PlainText)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        heading = QLabel(f"Selected installation: EveJS {installed_version or 'unknown'}")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)

        instruction = QLabel(translate_ui_phrase(
            "Select the mods to remember. Unselected mods will be reviewed next."
        ))
        instruction.setTextFormat(Qt.TextFormat.PlainText)
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        list_widget = QWidget()
        rows = QVBoxLayout(list_widget)
        for key, item in zip(keys, compatibility):
            if item.status not in {EvejsModCompatibilityStatus.UNSUPPORTED,
                                   EvejsModCompatibilityStatus.UNKNOWN_INSTALLED_VERSION}:
                continue
            version = f" (mod {item.mod_version})" if item.mod_version else ""
            declared = ", ".join(item.declared_versions or ())
            check = QCheckBox(f"{item.mod_name}{version}".replace("&", "&&"))
            check.setAccessibleName(format_ui_phrase("Remember {mod}", mod=f"{item.mod_name}{version}"))
            check.setAccessibleDescription(instruction.text())
            check.setProperty("modKey", key)
            check.toggled.connect(self._update_remember_enabled)
            self.mod_checkboxes[key] = check
            rows.addWidget(check)
            declaration = QLabel(format_ui_phrase("Declares EveJS: {versions}", versions=declared))
            declaration.setTextFormat(Qt.TextFormat.PlainText)
            declaration.setWordWrap(True)
            declaration.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            rows.addWidget(declaration)
        rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_widget)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.run_anyway_button = QPushButton(translate_ui_phrase("Run once"))
        self.run_anyway_button.setObjectName("runAnywayUnsupportedButton")
        self.remember_button = QPushButton(format_ui_phrase("Remember selected for EveJS {version}", version=installed_version or "?"))
        self.remember_button.setObjectName("rememberLocalCompatibilityButton")
        self.remember_button.setEnabled(False)
        self.remember_button.setAutoDefault(False)
        self.remember_button.setAccessibleName(self.remember_button.text())
        self.remember_button.setToolTip(translate_ui_phrase("Local user approval for selected mods, this installation and backend. Changes to mod files or EveJS require approval again. Author support is unchanged."))
        self.remember_button.setAccessibleDescription(self.remember_button.toolTip())
        self.disable_button = QPushButton(translate_ui_phrase("Disable remaining mods"))
        self.disable_button.setObjectName("disableAffectedModsButton")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelModCompatibilityButton")
        self.run_anyway_button.setAutoDefault(False)
        self.disable_button.setAutoDefault(False)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        buttons.addWidget(self.run_anyway_button)
        buttons.addWidget(self.remember_button)
        buttons.addWidget(self.disable_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self.run_anyway_button.clicked.connect(self._run_anyway)
        self.remember_button.clicked.connect(self._remember)
        self.disable_button.clicked.connect(self._disable_affected)
        self.cancel_button.clicked.connect(self.reject)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _run_anyway(self) -> None:
        self.choice = ModStartCompatibilityChoice.RUN_ANYWAY
        self.accept()

    def _remember(self) -> None:
        if not self.remember_button.isEnabled():
            return
        self.selected_mod_keys = tuple(key for key, check in self.mod_checkboxes.items() if check.isChecked())
        if not self.selected_mod_keys:
            return
        self.choice = ModStartCompatibilityChoice.REMEMBER
        self.accept()

    def _update_remember_enabled(self) -> None:
        self.remember_button.setEnabled(
            self._known_version and any(check.isChecked() for check in self.mod_checkboxes.values())
        )

    def _disable_affected(self) -> None:
        self.choice = ModStartCompatibilityChoice.DISABLE_AFFECTED
        self.accept()


def affected_mods(
    mods: tuple[Mod, ...],
    compatibility: tuple[EvejsModCompatibility, ...],
) -> tuple[Mod, ...]:
    """Return only the active runtime mods represented by unsupported results."""
    signatures = Counter(_unsupported_signature(compatibility))
    selected = []
    runtime_kinds = {ActivationKind.LOADER_RENAME, ActivationKind.JSON_BOOLEAN}
    for mod in mods:
        if not mod.active or not mod.valid or (mod.activation_kind not in runtime_kinds and not (
                mod.api_descriptor is not None and mod.api_descriptor.docker is not None and mod.api_descriptor.docker.image is not None)):
            continue
        descriptor = mod.api_descriptor
        declared = tuple(descriptor.evejs_versions or ()) if descriptor is not None else ()
        signature = (mod.id, mod.version or "", declared)
        if signatures[signature]:
            selected.append(mod)
            signatures[signature] -= 1
    return tuple(selected)
