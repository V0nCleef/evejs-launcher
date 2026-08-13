"""User-facing manager for configurable character launch groups."""
from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS as C, SEMANTIC_COLORS as S
from src.core.db import Account, Character
from src.core.groups import (
    CharacterGroup,
    DEFAULT_GROUP_COLOR,
    GROUP_COLORS,
    GroupMember,
    GroupValidationError,
    TargetGroupState,
    create_group,
    duplicate_group,
    resolve_group,
    validate_state,
)


_GROUP_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_MEMBER_ROLE = _GROUP_ID_ROLE + 1
_COLOR_LABELS = {
    "teal": "Teal",
    "gold": "Gold",
    "green": "Green",
    "red": "Red",
    "steel": "Steel",
}
_COLOR_VALUES = {
    "teal": C["teal"],
    "gold": C["gold"],
    "green": C["green"],
    "red": C["red"],
    "steel": C["grey"],
}


class CharacterGroupsDialog(QDialog):
    """Create groups and assign exact characters with one-per-account safety."""

    def __init__(
        self,
        accounts: list[Account],
        hidden_characters: set[str],
        state: TargetGroupState,
        *,
        focus_character_id: int | None = None,
        relink_candidates: tuple[TargetGroupState, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Character Groups")
        self.setModal(True)
        self.setObjectName("characterGroupsDialog")
        self.setProperty("deepSignal", True)
        self.resize(960, 660)
        self.setMinimumSize(760, 520)
        self.setAccessibleName("Manage character launch groups")
        self._accounts = list(accounts)
        self._hidden_characters = set(hidden_characters)
        self._groups = list(state.groups)
        self._selected_group_id = state.selected_group_id
        self._result_state = state
        self._focus_character_id = focus_character_id
        self._relink_candidates = tuple(relink_candidates)
        self._updating_editor = False
        self._member_items: dict[GroupMember, QTreeWidgetItem] = {}
        self._build_ui()
        self._apply_deep_signal_style()
        self._refresh_group_list(select_id=self._initial_group_id())

    @property
    def group_state(self) -> TargetGroupState:
        """Return the validated state after an accepted dialog."""
        return self._result_state

    def _initial_group_id(self) -> str | None:
        if self._focus_character_id is not None:
            for group in self._groups:
                if any(
                    member.character_id == self._focus_character_id
                    for member in group.members
                ):
                    return group.group_id
        if any(group.group_id == self._selected_group_id for group in self._groups):
            return self._selected_group_id
        return self._groups[0].group_id if self._groups else None

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        eyebrow = QLabel("LAUNCH ORCHESTRATION  /  PILOT GROUPS")
        eyebrow.setObjectName("dialogEyebrow")
        root.addWidget(eyebrow)

        title = QLabel("MANAGE CHARACTER GROUPS")
        title.setObjectName("dialogTitle")
        title.setProperty("class", "title")
        root.addWidget(title)

        subtitle = QLabel(
            "Build reusable launch groups. A group can contain one character "
            "from each account; characters may belong to multiple groups."
        )
        subtitle.setWordWrap(True)
        subtitle.setObjectName("dialogIntro")
        subtitle.setProperty("class", "secondary")
        root.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("groupsSplitter")
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, stretch=1)

        left = QFrame()
        left.setObjectName("groupsRail")
        left.setProperty("class", "card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)

        groups_title = QLabel("GROUPS")
        groups_title.setObjectName("groupsTitle")
        groups_title.setProperty("class", "sectionTitle")
        left_layout.addWidget(groups_title)

        self.group_list = QListWidget()
        self.group_list.setAccessibleName("Character groups")
        self.group_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.group_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.group_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.group_list.currentRowChanged.connect(self._on_group_selected)
        left_layout.addWidget(self.group_list, stretch=1)

        create_row = QHBoxLayout()
        self.new_button = QPushButton("+ NEW")
        self.new_button.setAccessibleName("Create new character group")
        self.new_button.setProperty("class", "secondary")
        self.new_button.clicked.connect(self._new_group)
        create_row.addWidget(self.new_button)
        self.duplicate_button = QPushButton("DUPLICATE")
        self.duplicate_button.setAccessibleName("Duplicate selected character group")
        self.duplicate_button.setProperty("class", "ghost")
        self.duplicate_button.clicked.connect(self._duplicate_group)
        create_row.addWidget(self.duplicate_button)
        left_layout.addLayout(create_row)

        self.restore_button = QPushButton("RESTORE PREVIOUS")
        self.restore_button.setAccessibleName("Restore verified previous groups")
        self.restore_button.setProperty("class", "ghost")
        self.restore_button.setToolTip(
            "Restore groups from a moved or upgraded EveJS installation "
            "after every character ID has been verified"
        )
        self.restore_button.setEnabled(bool(self._relink_candidates))
        self.restore_button.clicked.connect(self._restore_previous_groups)
        left_layout.addWidget(self.restore_button)

        order_row = QHBoxLayout()
        self.up_button = QPushButton("MOVE UP")
        self.up_button.setAccessibleName("Move selected group up")
        self.up_button.setProperty("class", "compactGhost")
        self.up_button.clicked.connect(lambda: self._move_group(-1))
        order_row.addWidget(self.up_button)
        self.down_button = QPushButton("MOVE DOWN")
        self.down_button.setAccessibleName("Move selected group down")
        self.down_button.setProperty("class", "compactGhost")
        self.down_button.clicked.connect(lambda: self._move_group(1))
        order_row.addWidget(self.down_button)
        self.delete_button = QPushButton("DELETE")
        self.delete_button.setAccessibleName("Delete selected launcher group")
        self.delete_button.setProperty("class", "dangerOutline")
        self.delete_button.clicked.connect(self._delete_group)
        order_row.addWidget(self.delete_button)
        left_layout.addLayout(order_row)
        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("groupEditor")
        right.setProperty("class", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(10)

        self.editor_title = QLabel("GROUP DETAILS")
        self.editor_title.setObjectName("editorTitle")
        self.editor_title.setProperty("class", "sectionTitle")
        right_layout.addWidget(self.editor_title)

        identity_row = QHBoxLayout()
        name_box = QVBoxLayout()
        name_label = QLabel("GROUP NAME")
        name_label.setProperty("class", "muted")
        name_box.addWidget(name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setAccessibleName("Group name")
        self.name_edit.setMaxLength(40)
        self.name_edit.setPlaceholderText("Example: Miners")
        self.name_edit.textChanged.connect(self._on_name_changed)
        name_box.addWidget(self.name_edit)
        identity_row.addLayout(name_box, stretch=3)

        color_box = QVBoxLayout()
        color_label = QLabel("COLOR")
        color_label.setProperty("class", "muted")
        color_box.addWidget(color_label)
        self.color_combo = QComboBox()
        self.color_combo.setAccessibleName("Group color")
        for key in GROUP_COLORS:
            self.color_combo.addItem(_COLOR_LABELS[key], key)
        self.color_combo.currentIndexChanged.connect(self._on_color_changed)
        color_box.addWidget(self.color_combo)
        identity_row.addLayout(color_box, stretch=1)
        right_layout.addLayout(identity_row)

        members_header = QHBoxLayout()
        members_label = QLabel("CHARACTERS")
        members_label.setProperty("class", "muted")
        members_header.addWidget(members_label)
        members_header.addStretch()
        self.member_count_label = QLabel("")
        self.member_count_label.setProperty("class", "secondary")
        members_header.addWidget(self.member_count_label)
        right_layout.addLayout(members_header)

        self.search_edit = QLineEdit()
        self.search_edit.setAccessibleName("Search characters or accounts")
        self.search_edit.setPlaceholderText("Search character or account...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter_characters)
        right_layout.addWidget(self.search_edit)

        self.character_tree = QTreeWidget()
        self.character_tree.setAccessibleName("Characters assigned to selected group")
        self.character_tree.setHeaderHidden(True)
        self.character_tree.setRootIsDecorated(True)
        self.character_tree.setAlternatingRowColors(False)
        self.character_tree.itemChanged.connect(self._on_member_changed)
        right_layout.addWidget(self.character_tree, stretch=1)

        self.hint_label = QLabel(
            "Hidden and banned characters remain visible here but are skipped "
            "when the group launches."
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setProperty("class", "muted")
        right_layout.addWidget(self.hint_label)
        splitter.addWidget(right)
        splitter.setSizes([300, 620])

        actions = QHBoxLayout()
        self.error_label = QLabel("")
        self.error_label.setObjectName("dialogMessage")
        self.error_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.error_label.setAccessibleName("Group validation message")
        self.error_label.setStyleSheet(f"color: {C['red']};")
        self.error_label.setWordWrap(True)
        actions.addWidget(self.error_label, stretch=1)
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.setProperty("class", "ghost")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton("SAVE GROUPS")
        self.save_button.setAccessibleName("Save character groups")
        self.save_button.setProperty("class", "primary")
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

    def _apply_deep_signal_style(self) -> None:
        """Polish the native dialog without changing group-edit semantics."""
        self.setStyleSheet(
            f"""
            QDialog#characterGroupsDialog {{
                background-color: {S['background']};
                color: {S['text_primary']};
            }}
            QLabel {{ background: transparent; color: {S['text_secondary']}; }}
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
            QFrame#groupsRail, QFrame#groupEditor {{
                background-color: rgba(10, 24, 36, 232);
                border: 1px solid {S['border']};
                border-radius: 9px;
            }}
            QLabel#groupsTitle, QLabel#editorTitle {{
                color: {S['accent']};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QSplitter#groupsSplitter::handle {{
                background-color: transparent;
                width: 10px;
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
            """
        )
        self.group_list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: rgba(3, 10, 17, 235);
                border: 1px solid {S['border']};
                border-radius: 6px;
                color: {S['text_primary']};
                padding: 5px;
                outline: none;
            }}
            QListWidget::item {{
                min-height: 26px;
                padding: 5px 7px;
                border: 1px solid transparent;
                border-radius: 4px;
            }}
            QListWidget::item:hover {{ background-color: {S['surface_hover']}; }}
            QListWidget::item:selected {{
                background-color: {S['accent_soft']};
                border-color: {S['accent_dim']};
                color: {S['text_primary']};
            }}
            """
        )
        self.character_tree.setStyleSheet(
            f"""
            QTreeWidget {{
                background-color: rgba(3, 10, 17, 235);
                border: 1px solid {S['border']};
                border-radius: 6px;
                color: {S['text_primary']};
                padding: 4px;
                outline: none;
            }}
            QTreeWidget::item {{
                min-height: 24px;
                padding: 3px 5px;
                border-radius: 3px;
            }}
            QTreeWidget::item:hover {{ background-color: {S['surface_hover']}; }}
            QTreeWidget::item:selected {{ background-color: {S['accent_soft']}; }}
            QTreeWidget::indicator {{
                width: 15px;
                height: 15px;
                background-color: {S['background']};
                border: 1px solid {S['border_bright']};
                border-radius: 3px;
            }}
            QTreeWidget::indicator:checked {{
                background-color: {S['accent']};
                border-color: {S['accent']};
            }}
            QTreeWidget:disabled {{ color: {S['text_muted']}; }}
            """
        )
        for button, variant in (
            (self.save_button, "primary"),
            (self.new_button, "secondary"),
            (self.delete_button, "danger"),
            (self.cancel_button, "ghost"),
            (self.duplicate_button, "ghost"),
            (self.restore_button, "ghost"),
            (self.up_button, "ghost"),
            (self.down_button, "ghost"),
        ):
            self._style_action_button(button, variant)

    @staticmethod
    def _style_action_button(button: QPushButton, variant: str) -> None:
        if variant == "primary":
            background, color, border = S["accent"], S["background"], S["accent"]
            hover_background = S["text_primary"]
        elif variant == "secondary":
            background, color, border = S["accent_soft"], S["text_primary"], S["accent_dim"]
            hover_background = S["accent_dim"]
        elif variant == "danger":
            background, color, border = "transparent", S["danger"], S["danger"]
            hover_background = "#3B1B22"
        else:
            background, color, border = "transparent", S["text_secondary"], S["border_bright"]
            hover_background = S["surface_hover"]
        button.setMinimumHeight(32)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {background};
                color: {color};
                border: 1px solid {border};
                border-radius: 5px;
                padding: 0 10px;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{ background-color: {hover_background}; color: {S['text_primary']}; }}
            QPushButton:focus {{ border: 2px solid {S['text_primary']}; }}
            QPushButton:disabled {{
                background-color: {S['surface']};
                color: {S['text_muted']};
                border-color: {S['border']};
            }}
            """
        )

    def _current_group(self) -> CharacterGroup | None:
        row = self.group_list.currentRow()
        if 0 <= row < len(self._groups):
            return self._groups[row]
        return None

    def _refresh_group_list(self, *, select_id: str | None = None) -> None:
        self.group_list.blockSignals(True)
        self.group_list.clear()
        selected_row = -1
        for row, group in enumerate(self._groups):
            item = QListWidgetItem(self._group_item_text(group))
            item.setData(_GROUP_ID_ROLE, group.group_id)
            item.setForeground(QColor(_COLOR_VALUES.get(group.color, C["teal"])))
            self.group_list.addItem(item)
            if group.group_id == select_id:
                selected_row = row
        self.group_list.blockSignals(False)
        if selected_row < 0 and self._groups:
            selected_row = 0
        self.group_list.setCurrentRow(selected_row)
        if selected_row < 0:
            self._populate_editor(None)

    @staticmethod
    def _group_item_text(group: CharacterGroup) -> str:
        return f"●  {group.name or 'Untitled Group'}   ({len(group.members)})"

    def _on_group_selected(self, row: int) -> None:
        group = self._groups[row] if 0 <= row < len(self._groups) else None
        self._populate_editor(group)

    def _populate_editor(self, group: CharacterGroup | None) -> None:
        self._updating_editor = True
        enabled = group is not None
        self.name_edit.setEnabled(enabled)
        self.color_combo.setEnabled(enabled)
        self.search_edit.setEnabled(enabled)
        self.character_tree.setEnabled(enabled)
        for button in (
            self.duplicate_button,
            self.delete_button,
            self.up_button,
            self.down_button,
        ):
            button.setEnabled(enabled)
        if group is None:
            self.name_edit.clear()
            self.color_combo.setCurrentIndex(0)
            self.character_tree.clear()
            self.member_count_label.setText("No group selected")
            self.editor_title.setText("CREATE YOUR FIRST GROUP")
            self._member_items.clear()
            self._updating_editor = False
            return

        self.editor_title.setText(f"EDIT {group.name.upper()}")
        self.name_edit.setText(group.name)
        color_index = self.color_combo.findData(group.color)
        self.color_combo.setCurrentIndex(max(0, color_index))
        self._populate_character_tree(group)
        row = self.group_list.currentRow()
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < len(self._groups) - 1)
        self._updating_editor = False
        self._filter_characters(self.search_edit.text())
        if self._focus_character_id is not None:
            for member, item in self._member_items.items():
                if member.character_id == self._focus_character_id:
                    self.character_tree.scrollToItem(item)
                    self.character_tree.setCurrentItem(item)
                    break

    def _populate_character_tree(self, group: CharacterGroup) -> None:
        self.character_tree.clear()
        self._member_items.clear()
        selected = set(group.members)
        known: set[GroupMember] = set()

        def character_is_unavailable(account: Account, character: Character) -> bool:
            return bool(
                account.banned
                or getattr(account, "hidden", False)
                or character.name in self._hidden_characters
            )

        def account_sort_key(account: Account) -> tuple[bool, str, int]:
            has_launchable_character = any(
                not character_is_unavailable(account, character)
                for character in account.characters
            )
            return (
                not has_launchable_character,
                account.username.casefold(),
                account.account_id,
            )

        for account in sorted(self._accounts, key=account_sort_key):
            parent = QTreeWidgetItem([account.username])
            parent.setForeground(0, QColor(C["grey"]))
            self.character_tree.addTopLevelItem(parent)
            characters = sorted(
                account.characters,
                key=lambda character: (
                    character_is_unavailable(account, character),
                    character.name.casefold(),
                    character.char_id,
                ),
            )
            for character in characters:
                member = GroupMember(account.account_id, character.char_id)
                known.add(member)
                statuses: list[str] = []
                if account.banned:
                    statuses.append("banned — will not launch")
                if getattr(account, "hidden", False) or character.name in self._hidden_characters:
                    statuses.append("hidden — will not launch")
                suffix = f"  [{'; '.join(statuses)}]" if statuses else ""
                child = QTreeWidgetItem([f"{character.name}{suffix}"])
                child.setData(0, _MEMBER_ROLE, member)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if member in selected
                    else Qt.CheckState.Unchecked,
                )
                if statuses:
                    child.setForeground(0, QColor(C["gold"]))
                parent.addChild(child)
                self._member_items[member] = child
            parent.setExpanded(True)

        missing = [member for member in group.members if member not in known]
        if missing:
            parent = QTreeWidgetItem(["Unavailable characters"])
            parent.setForeground(0, QColor(C["red"]))
            self.character_tree.addTopLevelItem(parent)
            for member in missing:
                child = QTreeWidgetItem(
                    [
                        f"Character ID {member.character_id} "
                        f"(account ID {member.account_id}) — missing"
                    ]
                )
                child.setData(0, _MEMBER_ROLE, member)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                child.setForeground(0, QColor(C["red"]))
                parent.addChild(child)
                self._member_items[member] = child
            parent.setExpanded(True)
        self._update_member_count()

    def _new_group(self) -> None:
        folded = {group.name.casefold() for group in self._groups}
        name = "New Group"
        suffix = 2
        while name.casefold() in folded:
            name = f"New Group {suffix}"
            suffix += 1
        state, group = create_group(
            TargetGroupState(tuple(self._groups), self._selected_group_id),
            name,
            color=DEFAULT_GROUP_COLOR,
        )
        self._groups = list(state.groups)
        self._refresh_group_list(select_id=group.group_id)
        self.name_edit.selectAll()
        self.name_edit.setFocus()

    def _duplicate_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        state = TargetGroupState(tuple(self._groups), self._selected_group_id)
        selected_group_id = self._selected_group_id
        try:
            state, copy = duplicate_group(state, group.group_id)
        except GroupValidationError as exc:
            self._show_error(str(exc))
            return
        self._groups = list(state.groups)
        self._selected_group_id = selected_group_id
        self._refresh_group_list(select_id=copy.group_id)

    def _restore_previous_groups(self) -> None:
        if not self._relink_candidates:
            return
        candidate = self._relink_candidates[0]
        if len(self._relink_candidates) > 1:
            labels = [
                (
                    f"Previous set {index}: {len(state.groups)} group(s), "
                    f"{sum(len(group.members) for group in state.groups)} character(s)"
                )
                for index, state in enumerate(self._relink_candidates, start=1)
            ]
            selected, accepted = QInputDialog.getItem(
                self,
                "Restore Previous Groups",
                "Several fully matching group sets were found:",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            candidate = self._relink_candidates[labels.index(selected)]
        if self._groups:
            answer = QMessageBox.question(
                self,
                "Replace Current Groups",
                "Replace the current draft with the verified previous group set?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._groups = list(candidate.groups)
        self._selected_group_id = candidate.selected_group_id
        self._refresh_group_list(select_id=candidate.selected_group_id)
        self.error_label.setText(
            "Previous groups restored. Review them, then choose SAVE GROUPS."
        )
        self.error_label.setStyleSheet(f"color: {C['green']};")

    def _delete_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete Group",
            f"Delete the launcher group '{group.name}'?\n\n"
            "No characters or accounts will be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        row = self.group_list.currentRow()
        self._groups.pop(row)
        if self._selected_group_id == group.group_id:
            self._selected_group_id = None
        next_id = (
            self._groups[min(row, len(self._groups) - 1)].group_id
            if self._groups
            else None
        )
        self._refresh_group_list(select_id=next_id)

    def _move_group(self, offset: int) -> None:
        row = self.group_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= len(self._groups):
            return
        group = self._groups.pop(row)
        self._groups.insert(target, group)
        self._refresh_group_list(select_id=group.group_id)

    def _on_name_changed(self, value: str) -> None:
        if self._updating_editor:
            return
        row = self.group_list.currentRow()
        if not 0 <= row < len(self._groups):
            return
        self._groups[row] = replace(self._groups[row], name=value)
        item = self.group_list.item(row)
        if item is not None:
            item.setText(self._group_item_text(self._groups[row]))
        self.editor_title.setText(f"EDIT {(value or 'GROUP').upper()}")
        self.error_label.clear()

    def _on_color_changed(self, _index: int) -> None:
        if self._updating_editor:
            return
        row = self.group_list.currentRow()
        if not 0 <= row < len(self._groups):
            return
        color = self.color_combo.currentData() or DEFAULT_GROUP_COLOR
        self._groups[row] = replace(self._groups[row], color=color)
        item = self.group_list.item(row)
        if item is not None:
            item.setForeground(QColor(_COLOR_VALUES.get(color, C["teal"])))

    def _on_member_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._updating_editor:
            return
        member = item.data(0, _MEMBER_ROLE)
        if not isinstance(member, GroupMember):
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self._updating_editor = True
            for candidate, candidate_item in self._member_items.items():
                if (
                    candidate != member
                    and candidate.account_id == member.account_id
                    and candidate_item.checkState(0) == Qt.CheckState.Checked
                ):
                    candidate_item.setCheckState(0, Qt.CheckState.Unchecked)
            self._updating_editor = False
        row = self.group_list.currentRow()
        if not 0 <= row < len(self._groups):
            return
        members = tuple(
            candidate
            for candidate, candidate_item in self._member_items.items()
            if candidate_item.checkState(0) == Qt.CheckState.Checked
        )
        self._groups[row] = replace(self._groups[row], members=members)
        list_item = self.group_list.item(row)
        if list_item is not None:
            list_item.setText(self._group_item_text(self._groups[row]))
        self._update_member_count()
        self.error_label.clear()

    def _update_member_count(self) -> None:
        group = self._current_group()
        count = len(group.members) if group is not None else 0
        self.member_count_label.setText(
            f"{count} character{'s' if count != 1 else ''}"
        )

    def _filter_characters(self, text: str) -> None:
        needle = text.strip().casefold()
        for index in range(self.character_tree.topLevelItemCount()):
            parent = self.character_tree.topLevelItem(index)
            parent_match = needle in parent.text(0).casefold()
            visible_child = False
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                match = not needle or parent_match or needle in child.text(0).casefold()
                child.setHidden(not match)
                visible_child = visible_child or match
            parent.setHidden(bool(needle) and not parent_match and not visible_child)

    def _save(self) -> None:
        try:
            state = validate_state(
                TargetGroupState(tuple(self._groups), self._selected_group_id)
            )
            for group in state.groups:
                resolution = resolve_group(group, self._accounts)
                if resolution.conflicting_account_ids:
                    raise GroupValidationError(
                        f"'{group.name}' contains more than one character from "
                        "the same account. Keep only one per account."
                    )
        except GroupValidationError as exc:
            self._show_error(str(exc))
            return
        self._result_state = state
        self.accept()

    def _show_error(self, message: str) -> None:
        self.error_label.setStyleSheet(f"color: {C['red']};")
        self.error_label.setText(message)
        self.error_label.setFocus()
