"""Dialog for creating an EveJS account and starter character."""
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

from src.constants import COLORS as C, SEMANTIC_COLORS as S
from src.core.character_creation import normalize_character_name
from src.core.db import Account
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus
from src.i18n import translate_ui_phrase
from src.widgets.ui_translation import (
    register_translatable_widget_tree,
    set_translatable_text,
    set_translatable_text_template,
)


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
        *,
        runtime_label: str = "NATIVE RUNTIME",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New Character")
        self.setModal(True)
        self.setObjectName("newCharacterDialog")
        self.setProperty("deepSignal", True)
        self.resize(680, 600)
        self.setMinimumSize(560, 520)
        self.setMaximumWidth(760)
        self.setAccessibleName("Create new local character")
        self._accounts = list(accounts)
        self._snapshot_ready_ids = set(snapshot_ready_ids)
        self._patch_status = patch_status
        self._runtime_label = str(runtime_label).strip().upper() or "EVEJS RUNTIME"
        self._busy = False
        self._build_ui()
        register_translatable_widget_tree(self)
        self._apply_deep_signal_style()
        self.set_patch_status(patch_status)
        self._validate()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        eyebrow = QLabel()
        set_translatable_text_template(
            eyebrow,
            "CHARACTER PROVISIONING  /  "
            f"{translate_ui_phrase(self._runtime_label)}",
        )
        eyebrow.setObjectName("dialogEyebrow")
        root.addWidget(eyebrow)

        title = QLabel("CREATE NEW CHARACTER")
        title.setObjectName("dialogTitle")
        title.setProperty("class", "title")
        root.addWidget(title)
        intro = QLabel(
            "Creates a new local EveJS account and its starter character. "
            "The game and market services are stopped briefly so the database "
            "can be backed up and updated safely."
        )
        intro.setWordWrap(True)
        intro.setObjectName("dialogIntro")
        intro.setProperty("class", "secondary")
        root.addWidget(intro)

        identity_card = QFrame()
        identity_card.setObjectName("identityCard")
        identity_card.setProperty("class", "card")
        identity_layout = QVBoxLayout(identity_card)
        identity_layout.setContentsMargins(16, 14, 16, 14)
        identity_layout.setSpacing(10)
        identity_title = QLabel("ACCOUNT & CHARACTER")
        identity_title.setObjectName("identityTitle")
        identity_title.setProperty("class", "sectionTitle")
        identity_layout.addWidget(identity_title)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.account_edit = QLineEdit()
        self.account_edit.setAccessibleName("Local account name")
        self.account_edit.setMaxLength(32)
        self.account_edit.setPlaceholderText("Local account name")
        self.character_edit = QLineEdit()
        self.character_edit.setAccessibleName("Character name")
        self.character_edit.setMaxLength(37)
        self.character_edit.setPlaceholderText("Character name")
        self.gm_check = QCheckBox("Grant GM/admin permissions to this account")
        self.gm_check.setAccessibleName("Grant GM or administrator permissions")
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
        password_note.setObjectName("credentialNote")
        password_note.setProperty("class", "muted")
        identity_layout.addWidget(password_note)
        root.addWidget(identity_card)

        overview_card = QFrame()
        overview_card.setObjectName("overviewCard")
        overview_card.setProperty("class", "card")
        overview_layout = QVBoxLayout(overview_card)
        overview_layout.setContentsMargins(16, 14, 16, 14)
        overview_layout.setSpacing(10)
        overview_title = QLabel("OVERVIEW COPY (OPTIONAL)")
        overview_title.setObjectName("overviewTitle")
        overview_title.setProperty("class", "sectionTitle")
        overview_layout.addWidget(overview_title)
        self.overview_combo = QComboBox()
        self.overview_combo.setAccessibleName("Overview source character")
        self.overview_combo.addItem("Do not copy an overview", None)
        for account in self._accounts:
            for character in account.characters:
                readiness = (
                    "snapshot ready"
                    if character.char_id in self._snapshot_ready_ids
                    else "launch once to capture"
                )
                self.overview_combo.addItem(
                    f"{character.name} ({account.username}) — "
                    f"{translate_ui_phrase(readiness)}",
                    character.char_id,
                )
        overview_layout.addWidget(self.overview_combo)
        self.overview_hint = QLabel()
        self.overview_hint.setObjectName("overviewHint")
        self.overview_hint.setWordWrap(True)
        self.overview_hint.setProperty("class", "muted")
        overview_layout.addWidget(self.overview_hint)

        patch_row = QHBoxLayout()
        patch_row.setSpacing(10)
        self.patch_status_label = QLabel()
        self.patch_status_label.setObjectName("patchStatus")
        self.patch_status_label.setWordWrap(True)
        patch_row.addWidget(self.patch_status_label, stretch=1)
        self.patch_button = QPushButton("PATCH CLIENT")
        self.patch_button.setObjectName("secondaryAction")
        self.patch_button.setProperty("class", "secondary")
        self.patch_button.clicked.connect(self.patch_requested.emit)
        patch_row.addWidget(self.patch_button)
        self.restore_button = QPushButton("RESTORE ORIGINAL")
        self.restore_button.setObjectName("ghostAction")
        self.restore_button.setProperty("class", "ghost")
        self.restore_button.clicked.connect(self.restore_requested.emit)
        patch_row.addWidget(self.restore_button)
        overview_layout.addLayout(patch_row)
        root.addWidget(overview_card)

        self.error_label = QLabel()
        self.error_label.setObjectName("dialogError")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {C['red']};")
        self.error_label.hide()
        root.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.setObjectName("ghostAction")
        self.cancel_button.setProperty("class", "ghost")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.create_button = QPushButton("CREATE CHARACTER")
        self.create_button.setObjectName("primaryAction")
        self.create_button.setProperty("class", "primary")
        self.create_button.clicked.connect(self._submit)
        buttons.addWidget(self.create_button)
        root.addLayout(buttons)

        self.account_edit.textChanged.connect(self._validate)
        self.character_edit.textChanged.connect(self._validate)
        self.overview_combo.currentIndexChanged.connect(self._validate)

    def _apply_deep_signal_style(self) -> None:
        """Give the native dialog the same static glass-console treatment."""
        self.setStyleSheet(
            f"""
            QDialog#newCharacterDialog {{
                background-color: {S['background']};
                color: {S['text_primary']};
            }}
            QLabel {{
                background: transparent;
                color: {S['text_secondary']};
            }}
            QLabel#dialogEyebrow {{
                color: {S['accent']};
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 2px;
            }}
            QLabel#dialogTitle {{
                color: {S['text_primary']};
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 2px;
            }}
            QLabel#dialogIntro {{ color: {S['text_secondary']}; font-size: 11px; }}
            QFrame#identityCard, QFrame#overviewCard {{
                background-color: rgba(10, 24, 36, 232);
                border: 1px solid {S['border']};
                border-radius: 9px;
            }}
            QLabel#identityTitle, QLabel#overviewTitle {{
                color: {S['accent']};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QLabel#credentialNote, QLabel#overviewHint {{
                color: {S['text_muted']};
                font-size: 10px;
            }}
            QLineEdit, QComboBox {{
                min-height: 32px;
                padding: 0 10px;
                color: {S['text_primary']};
                background-color: rgba(4, 12, 20, 238);
                border: 1px solid {S['border_bright']};
                border-radius: 5px;
                selection-background-color: {S['accent_soft']};
            }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {S['accent']}; }}
            QLineEdit:disabled, QComboBox:disabled {{
                color: {S['text_muted']};
                border-color: {S['border']};
            }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QCheckBox {{ color: {S['text_secondary']}; spacing: 8px; }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                background-color: {S['background']};
                border: 1px solid {S['border_bright']};
                border-radius: 3px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {S['accent']};
                border-color: {S['accent']};
            }}
            """
        )
        self._style_action_button(self.create_button, "primary")
        self._style_action_button(self.cancel_button, "ghost")
        self._style_action_button(self.patch_button, "secondary")
        self._style_action_button(self.restore_button, "ghost")

    @staticmethod
    def _style_action_button(button: QPushButton, variant: str) -> None:
        if variant == "primary":
            background, color, border = S["accent"], S["background"], S["accent"]
            hover_background, hover_color = S["text_primary"], S["background"]
        elif variant == "secondary":
            background, color, border = S["accent_soft"], S["text_primary"], S["accent_dim"]
            hover_background, hover_color = S["accent_dim"], S["text_primary"]
        else:
            background, color, border = "transparent", S["text_secondary"], S["border_bright"]
            hover_background, hover_color = S["surface_hover"], S["text_primary"]
        button.setMinimumHeight(34)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {background};
                color: {color};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 0 14px;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ background-color: {hover_background}; color: {hover_color}; }}
            QPushButton:focus {{ border: 2px solid {S['text_primary']}; }}
            QPushButton:disabled {{
                background-color: {S['surface']};
                color: {S['text_muted']};
                border-color: {S['border']};
            }}
            """
        )

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
        set_translatable_text(self.patch_status_label, status.reason)
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
                        f"{character.name} ({account.username}) — "
                        f"{translate_ui_phrase(readiness)}",
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
        set_translatable_text(self.create_button, button_text)
        self._validate()

    def show_error(self, message: str) -> None:
        set_translatable_text(self.error_label, message)
        self.error_label.setVisible(bool(message))

    def _selected_source(self) -> int | None:
        value = self.overview_combo.currentData()
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _validate(self) -> None:
        username = self.account_edit.text().strip()
        character_name = normalize_character_name(self.character_edit.text())
        source_id = self._selected_source()
        valid = bool(
            _ACCOUNT_PATTERN.fullmatch(username)
            and character_name is not None
        )
        action_text = "CREATE CHARACTER"
        if source_id is not None:
            if self._patch_status.state is not OverviewPatchState.PATCHED:
                valid = False
                action_text = "PATCH CLIENT FIRST"
                set_translatable_text(
                    self.overview_hint,
                    "Install the optional overview bridge before selecting a source."
                )
            elif source_id not in self._snapshot_ready_ids:
                set_translatable_text(
                    self.overview_hint,
                    "You can create the character now. Afterwards, launch this source "
                    "character once through the launcher to capture its overview, then "
                    "launch the new character to apply the queued copy."
                )
            else:
                set_translatable_text(
                    self.overview_hint,
                    "The captured source overview will be imported on the new "
                    "character's first launcher login."
                )
        else:
            set_translatable_text(
                self.overview_hint,
                "Choose a captured character only if you want the same overview "
                "tabs, presets, columns, colors, and filters."
            )
        if not self._busy:
            set_translatable_text(self.create_button, action_text)
        self.create_button.setEnabled(valid and not self._busy)

    def _submit(self) -> None:
        if not self.create_button.isEnabled():
            return
        character_name = normalize_character_name(self.character_edit.text())
        if character_name is None:
            return
        self.show_error("")
        self.create_requested.emit(
            NewCharacterDraft(
                username=self.account_edit.text().strip(),
                character_name=character_name,
                is_gm=self.gm_check.isChecked(),
                overview_source_character_id=self._selected_source(),
            )
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)
