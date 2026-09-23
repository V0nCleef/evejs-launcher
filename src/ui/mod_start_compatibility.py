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
    DISABLE_AFFECTED = "disable_affected"
    CANCEL = "cancel"


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
        if item.status is EvejsModCompatibilityStatus.UNSUPPORTED
    )


@dataclass(frozen=True)
class ModCompatibilityConsent:
    """One invocation's explicit consent, bound to its exact inspected modset."""

    root: str
    backend: str
    installed_version: str
    modset_sha256: str
    unsupported: tuple[tuple[str, str, tuple[str, ...]], ...]

    @classmethod
    def capture(
        cls,
        root: str | Path,
        backend: str,
        installed_version: str,
        mods: tuple[Mod, ...],
        compatibility: tuple[EvejsModCompatibility, ...],
    ) -> "ModCompatibilityConsent":
        return cls(
            root=_canonical_root(root),
            backend=_backend_key(backend),
            installed_version=installed_version,
            modset_sha256=modset_fingerprint(mods),
            unsupported=_unsupported_signature(compatibility),
        )

    def matches(
        self,
        root: str | Path,
        backend: str,
        installed_version: str | None,
        mods: tuple[Mod, ...],
    ) -> bool:
        if installed_version is None:
            return False
        try:
            canonical_root = _canonical_root(root)
            fingerprint = modset_fingerprint(mods)
            current = assess_active_runtime_mods(mods, installed_version)
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
        installed_version: str,
        parent: QWidget | None = None,
        *,
        docker_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.choice = ModStartCompatibilityChoice.CANCEL
        self.setWindowTitle("Mod Compatibility")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(620, 380)

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

        heading = QLabel(f"Selected installation: EveJS {installed_version}")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(heading)

        rows = []
        for item in compatibility:
            if item.status is not EvejsModCompatibilityStatus.UNSUPPORTED:
                continue
            version = f" (mod {item.mod_version})" if item.mod_version else ""
            declared = ", ".join(item.declared_versions or ())
            rows.append(f"• {item.mod_name}{version}\n  Declares EveJS: {declared}")
        list_label = QLabel("\n\n".join(rows))
        list_label.setTextFormat(Qt.TextFormat.PlainText)
        list_label.setWordWrap(True)
        list_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(list_label)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.run_anyway_button = QPushButton("Run anyway — unsupported")
        self.run_anyway_button.setObjectName("runAnywayUnsupportedButton")
        self.disable_button = QPushButton("Disable affected mods")
        self.disable_button.setObjectName("disableAffectedModsButton")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancelModCompatibilityButton")
        self.run_anyway_button.setAutoDefault(False)
        self.disable_button.setAutoDefault(False)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        buttons.addWidget(self.run_anyway_button)
        buttons.addWidget(self.disable_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self.run_anyway_button.clicked.connect(self._run_anyway)
        self.disable_button.clicked.connect(self._disable_affected)
        self.cancel_button.clicked.connect(self.reject)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _run_anyway(self) -> None:
        self.choice = ModStartCompatibilityChoice.RUN_ANYWAY
        self.accept()

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
        if not mod.active or not mod.valid or mod.activation_kind not in runtime_kinds:
            continue
        descriptor = mod.api_descriptor
        declared = tuple(descriptor.evejs_versions or ()) if descriptor is not None else ()
        signature = (mod.id, mod.version or "", declared)
        if signatures[signature]:
            selected.append(mod)
            signatures[signature] -= 1
    return tuple(selected)
