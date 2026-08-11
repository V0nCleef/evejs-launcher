"""Dialog for creating a Native EveJS account and starter character."""
from __future__ import annotations

from dataclasses import dataclass
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS as C
from src.core.db import Account
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus


_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")


@dataclass(frozen=True)
class NewCharacterDraft:
    username: str
    character_name: str
    is_gm: bool
    overview_source_character_id: int | None


class NewCharacterDialog(QDialog):
    create_requested = pyqtSignal(object)
    patch_requested = pyqtSignal()
    restore_requested = pyqtSignal()

    def __init__(
        self,
        accounts: list[Account],
        patch_status: OverviewPatchStatus,
        snapshot_ready_ids: set[int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New Character")
        self.setModal(True)
        self.setMinimumWidth(620)
        self.setMaximumWidth(720)
        self._accounts = list(accounts)
        self._snapshot_ready_ids = set(snapshot_ready_ids)
        self._patch_status = patch_status
        self._busy = False
        self._build_ui()
        self.set_patch_status(patch_status)
        self._validate()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        title = QLabel("CREATE NEW CHARACTER")
        title.setProperty("class", "title")
        root.addWidget(title)
        intro = QLabel(
            "Creates a new local EveJS account and its starter character. "
            "The game and market services are stopped briefly so the database "
            "can be backed up and updated safely."
        )
        intro.setWordWrap(True)
        intro.setProperty("class", "secondary")
        root.addWidget(intro)

        identity_card = QFrame()
        identity_card.setProperty("class", "card")
        identity_layout = QVBoxLayout(identity_card)
        identity_layout.setContentsMargins(16, 14, 16, 14)
        identity_layout.setSpacing(10)
        identity_title = QLabel("ACCOUNT & CHARACTER")
        identity_title.setProperty("class", "sectionTitle")
        identity_layout.addWidget(identity_title)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.account_edit = QLineEdit()
        self.account_edit.setMaxLength(32)
        self.account_edit.setPlaceholderText("Local account name")
        self.character_edit = QLineEdit()
        self.character_edit.setMaxLength(37)
        self.character_edit.setPlaceholderText("Character name")
        self.gm_check = QCheckBox("Grant GM/admin permissions to this account")
        self.gm_check.setChecked(False)
        form.addRow("Account name:", self.account_edit)
        form.addRow("Character name:", self.character_edit)
        form.addRow("GM account:", self.gm_check)
        identity_layout.addLayout(form)
        password_note = QLabel(
            "Local login uses the launcher's EveJS development credential; no "
            "real EVE Online credentials are created or stored."
        )
        password_note.setWordWrap(True)
        password_note.setProperty("class", "muted")
        identity_layout.addWidget(password_note)
        root.addWidget(identity_card)

        overview_card = QFrame()
        overview_card.setProperty("class", "card")
        overview_layout = QVBoxLayout(overview_card)
        overview_layout.setContentsMargins(16, 14, 16, 14)
        overview_layout.setSpacing(10)
        overview_title = QLabel("OVERVIEW COPY (OPTIONAL)")
        overview_title.setProperty("class", "sectionTitle")
        overview_layout.addWidget(overview_title)
        self.overview_combo = QComboBox()
        self.overview_combo.addItem("Do not copy an overview", None)
        for account in self._accounts:
            for character in account.characters:
                readiness = (
                    "snapshot ready"
                    if character.char_id in self._snapshot_ready_ids
                    else "launch once to capture"
                )
                self.overview_combo.addItem(
                    f"{character.name} ({account.username}) — {readiness}",
                    character.char_id,
                )
        overview_layout.addWidget(self.overview_combo)
        self.overview_hint = QLabel()
        self.overview_hint.setWordWrap(True)
        self.overview_hint.setProperty("class", "muted")
        overview_layout.addWidget(self.overview_hint)

        patch_row = QHBoxLayout()
        patch_row.setSpacing(10)
        self.patch_status_label = QLabel()
        self.patch_status_label.setWordWrap(True)
        patch_row.addWidget(self.patch_status_label, stretch=1)
        self.patch_button = QPushButton("PATCH CLIENT")
        self.patch_button.setProperty("class", "secondary")
        self.patch_button.clicked.connect(self.patch_requested.emit)
        patch_row.addWidget(self.patch_button)
        self.restore_button = QPushButton("RESTORE ORIGINAL")
        self.restore_button.setProperty("class", "ghost")
        self.restore_button.clicked.connect(self.restore_requested.emit)
        patch_row.addWidget(self.restore_button)
        overview_layout.addLayout(patch_row)
        root.addWidget(overview_card)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {C['red']};")
        self.error_label.hide()
        root.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.setProperty("class", "ghost")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.create_button = QPushButton("CREATE CHARACTER")
        self.create_button.setProperty("class", "primary")
        self.create_button.clicked.connect(self._submit)
        buttons.addWidget(self.create_button)
        root.addLayout(buttons)

        self.account_edit.textChanged.connect(self._validate)
        self.character_edit.textChanged.connect(self._validate)
        self.overview_combo.currentIndexChanged.connect(self._validate)

    def set_patch_status(self, status: OverviewPatchStatus) -> None:
        self._patch_status = status
        colors = {
            OverviewPatchState.PATCHED: C["green"],
            OverviewPatchState.LEGACY: C["gold"],
            OverviewPatchState.READY: C["teal"],
            OverviewPatchState.CORRUPT: C["red"],
            OverviewPatchState.UNSUPPORTED: C["gold"],
            OverviewPatchState.MISSING: C["grey"],
        }
        self.patch_status_label.setText(status.reason)
        self.patch_status_label.setStyleSheet(
            f"color: {colors.get(status.state, C['grey'])};"
        )
        self.patch_button.setVisible(status.can_patch)
        self.restore_button.setVisible(status.can_restore)
        self._validate()

    def set_snapshot_ready_ids(self, character_ids: set[int]) -> None:
        self._snapshot_ready_ids = set(character_ids)
        for index in range(1, self.overview_combo.count()):
            character_id = self.overview_combo.itemData(index)
            for account in self._accounts:
                character = next(
                    (entry for entry in account.characters if entry.char_id == character_id),
                    None,
                )
                if character is not None:
                    readiness = (
                        "snapshot ready"
                        if character.char_id in self._snapshot_ready_ids
                        else "launch once to capture"
                    )
                    self.overview_combo.setItemText(
                        index,
                        f"{character.name} ({account.username}) — {readiness}",
                    )
                    break
        self._validate()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = bool(busy)
        for widget in (
            self.account_edit,
            self.character_edit,
            self.gm_check,
            self.overview_combo,
            self.patch_button,
            self.restore_button,
            self.cancel_button,
        ):
            widget.setEnabled(not busy)
        button_text = (message or "WORKING…") if busy else "CREATE CHARACTER"
        self.create_button.setText(button_text)
        self._validate()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _selected_source(self) -> int | None:
        value = self.overview_combo.currentData()
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _validate(self) -> None:
        username = self.account_edit.text().strip()
        character_name = " ".join(self.character_edit.text().strip().split())
        source_id = self._selected_source()
        valid = bool(
            _ACCOUNT_PATTERN.fullmatch(username)
            and 3 <= len(character_name) <= 37
        )
        action_text = "CREATE CHARACTER"
        if source_id is not None:
            if self._patch_status.state is not OverviewPatchState.PATCHED:
                valid = False
                action_text = "PATCH CLIENT FIRST"
                self.overview_hint.setText(
                    "Install the optional overview bridge before selecting a source."
                )
            elif source_id not in self._snapshot_ready_ids:
                self.overview_hint.setText(
                    "You can create the character now. Afterwards, launch this source "
                    "character once through the launcher to capture its overview, then "
                    "launch the new character to apply the queued copy."
                )
            else:
                self.overview_hint.setText(
                    "The captured source overview will be imported on the new "
                    "character's first launcher login."
                )
        else:
            self.overview_hint.setText(
                "Choose a captured character only if you want the same overview "
                "tabs, presets, columns, colors, and filters."
            )
        if not self._busy:
            self.create_button.setText(action_text)
        self.create_button.setEnabled(valid and not self._busy)

    def _submit(self) -> None:
        if not self.create_button.isEnabled():
            return
        self.show_error("")
        self.create_requested.emit(
            NewCharacterDraft(
                username=self.account_edit.text().strip(),
                character_name=" ".join(self.character_edit.text().strip().split()),
                is_gm=self.gm_check.isChecked(),
                overview_source_character_id=self._selected_source(),
            )
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
