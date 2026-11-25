"""Schema-driven mod settings editor. The host owns every persistent write."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Iterator, Mapping, Sequence, TYPE_CHECKING

from PyQt6.QtCore import QEvent, QSignalBlocker, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from src.constants import SEMANTIC_COLORS as S
from src.core.mod_settings_schema import ModSetting, localized_text, validate_values
from src.i18n import format_ui_phrase, translate_ui_phrase
from src.widgets.localized_dialogs import LocalizedMessageBox as QMessageBox
from src.widgets.scroll_safe_controls import (
    ScrollSafeComboBox, ScrollSafeDoubleSpinBox, ScrollSafeSpinBox,
)
from src.widgets.toggle_switch import ToggleSwitch

if TYPE_CHECKING:
    from src.core.mod_settings_layout import SettingsLayoutSection


@dataclass
class _FormSection:
    title: str
    card: QWidget
    grid: QGridLayout
    rows: tuple[tuple[ModSetting, ...], ...]
    authored: bool
    visible: tuple[str, ...] = ()
    positions: tuple[tuple[str, int, int, int], ...] = ()
    columns: int = 0


class ModSettingsDialog(QDialog):
    """Edit a draft; emit Save requests without assuming persistence succeeded.

    The host calls ``mark_saved`` after successful persistence, or ``show_error``
    on failure. ``set_saving`` protects the draft while a worker owns the request.
    Author labels and values are never implicitly translated as launcher text.
    """

    save_requested = pyqtSignal(dict)

    def __init__(
        self, mod_name: str, fields: Sequence[ModSetting], values: Mapping[str, object],
        *, scope_label: str = "", layout: tuple[SettingsLayoutSection, ...] = (),
        layout_error: bool = False, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modSettingsDialog")
        self.setProperty("deepSignal", True)
        self.setWindowTitle(format_ui_phrase("Configure {mod}", mod=mod_name))
        self.setModal(True)
        self.resize(720, 660)
        self.setMinimumSize(420, 400)
        self._fields = tuple(fields)
        if len({field.id for field in fields}) != len(fields):
            raise ValueError("Duplicate mod setting IDs")
        self._baseline = {
            field.id: deepcopy(values.get(field.id, field.default)) for field in fields
        }
        self._edited: set[str] = set()
        self._controls: dict[str, QWidget] = {}
        self._rows: dict[str, QWidget] = {}
        self._errors: dict[str, QLabel] = {}
        self._labels: dict[str, tuple[QLabel, QLabel | None]] = {}
        self._sections: list[_FormSection] = []
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.timeout.connect(self._reflow)
        self._saving = False
        self._loading = True
        self._pending: dict[str, object] | None = None
        self._saved_restart: set[str] = set()
        self._build_ui(mod_name, scope_label, layout, layout_error)
        self._loading = False
        self._refresh()

    @staticmethod
    def _label(text: str, *, muted: bool = False) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        policy = label.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        label.setSizePolicy(policy)
        label.setProperty("class", "muted" if muted else "secondary")
        return label

    def _build_ui(
        self, mod_name: str, scope_label: str, layout: tuple[SettingsLayoutSection, ...],
        layout_error: bool,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        title = self._label(str(mod_name))
        title.setObjectName("modSettingsTitle")
        root.addWidget(title)
        if scope_label:
            root.addWidget(self._label(scope_label, muted=True))
        if layout_error:
            root.addWidget(self._label(translate_ui_phrase(
                "The mod's layout hints could not be loaded. Using automatic layout."
            ), muted=True))

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(translate_ui_phrase("Search settings..."))
        self.search_edit.setAccessibleName(translate_ui_phrase("Search settings..."))
        self.search_edit.setVisible(len(self._fields) >= 8)
        self.search_edit.textChanged.connect(self._filter_rows)
        filters.addWidget(self.search_edit, 1)
        self.advanced_toggle = QCheckBox(translate_ui_phrase("Show advanced settings"))
        self.advanced_toggle.setVisible(any(field.advanced for field in self._fields))
        self.advanced_toggle.toggled.connect(self._filter_rows)
        filters.addWidget(self.advanced_toggle)
        self.section_jump = ScrollSafeComboBox()
        jump_text = translate_ui_phrase("Jump to section")
        self.section_jump.setAccessibleName(jump_text)
        self.section_jump.setToolTip(jump_text)
        self.section_jump.setPlaceholderText(jump_text)
        self.section_jump.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.section_jump.setMinimumContentsLength(len(jump_text))
        self.section_jump.activated.connect(self._jump_to_section)
        self.section_jump.hide()
        filters.addWidget(self.section_jump)
        root.addLayout(filters)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(12)
        for section in self._build_sections(layout):
            self._sections.append(section)
            body.addWidget(section.card)
        self.empty_label = self._label(translate_ui_phrase("No settings are available."))
        body.addWidget(self.empty_label)
        body.addStretch()
        self.scroll_area.setWidget(content)
        self.scroll_area.viewport().installEventFilter(self)
        root.addWidget(self.scroll_area, 1)
        self.restart_label = self._label("", muted=True)
        root.addWidget(self.restart_label)
        self.status_label = self._label("")
        self.status_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        root.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.defaults_button = QPushButton(translate_ui_phrase("Restore Defaults"))
        self.defaults_button.setProperty("class", "ghost")
        self.defaults_button.clicked.connect(self._restore_defaults)
        actions.addWidget(self.defaults_button)
        actions.addStretch()
        self.cancel_button = QPushButton(translate_ui_phrase("Cancel"))
        self.cancel_button.setProperty("class", "ghost")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton(translate_ui_phrase("Save"))
        self.save_button.setProperty("class", "primary")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        root.addLayout(actions)
        self.setStyleSheet(f"""
            QDialog#modSettingsDialog {{ background: {S['background']}; color: {S['text_primary']}; }}
            QLabel {{ background: transparent; color: {S['text_secondary']}; }}
            QLabel#modSettingsTitle {{ color: {S['text_primary']}; font-size: 24px; font-weight: 700; }}
            QLabel#modSettingsGroup {{ color: {S['accent']}; font-weight: 700; }}
            QLabel[class="muted"] {{ color: {S['text_muted']}; }}
            QLabel[validationError="true"] {{ color: {S['danger']}; }}
            QWidget[class="card"] {{ background: {S['surface']}; border: 1px solid {S['border']}; border-radius: 8px; }}
            QScrollArea {{ border: none; background: transparent; }}
        """)

    def _build_sections(self, layout: tuple[SettingsLayoutSection, ...]) -> Iterator[_FormSection]:
        available = {field.id: field for field in self._fields}
        for section in layout:
            rows = tuple(tuple(available.pop(field_id) for field_id in row if field_id in available)
                         for row in section.rows)
            rows = tuple(row for row in rows if row)
            if rows:
                yield self._create_section(localized_text(section.title), rows, authored=True)
        grouped: dict[str, list[ModSetting]] = {}
        for field in available.values():
            title = localized_text(field.group) or translate_ui_phrase("Settings")
            grouped.setdefault(title, []).append(field)
        for title, fields in grouped.items():
            yield self._create_section(title, (tuple(fields),), authored=False)

    def _create_section(
        self, title: str, rows: tuple[tuple[ModSetting, ...], ...], *, authored: bool,
    ) -> _FormSection:
        card = QWidget()
        card.setProperty("class", "card")
        group_layout = QVBoxLayout(card)
        group_layout.setContentsMargins(16, 14, 16, 14)
        heading = self._label(title)
        heading.setObjectName("modSettingsGroup")
        group_layout.addWidget(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        group_layout.addLayout(grid)
        for row in rows:
            for field in row:
                self._create_field(card, field)
        return _FormSection(title, card, grid, rows, authored)

    def _create_field(self, card: QWidget, field: ModSetting) -> None:
        widget = QWidget(card)
        column = QVBoxLayout(widget)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(5)
        label = self._label(localized_text(field.label))
        column.addWidget(label)
        description = localized_text(field.description)
        help_label = self._label(description, muted=True) if description else None
        if help_label is not None:
            column.addWidget(help_label)
        column.addStretch()
        control = self._make_control(field, self._baseline[field.id])
        control.setObjectName(f"modSetting_{field.id}")
        control.setAccessibleName(label.text())
        control.setAccessibleDescription(description)
        label.setBuddy(control)
        if isinstance(control, (ScrollSafeSpinBox, ScrollSafeDoubleSpinBox, ScrollSafeComboBox)):
            # Native size hints include the entire accepted range or longest
            # choice. The layout measures the editor separately for packing.
            control.setSizePolicy(QSizePolicy.Policy.Ignored, control.sizePolicy().verticalPolicy())
        column.addWidget(control)
        error = self._label("")
        error.setProperty("validationError", True)
        column.addWidget(error)
        self._controls[field.id] = control
        self._rows[field.id] = widget
        self._labels[field.id] = (label, help_label)
        self._errors[field.id] = error

    def eventFilter(self, watched, event) -> bool:
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._reflow_timer.start(0)
        return super().eventFilter(watched, event)

    def _fits_column(self, field_id: str, width: int) -> bool:
        control = self._controls[field_id]
        if isinstance(control, (QLineEdit, QPlainTextEdit)):
            return False
        if isinstance(control, (ScrollSafeSpinBox, ScrollSafeDoubleSpinBox)):
            editor_width = control.fontMetrics().horizontalAdvance(control.text()) + 44
        else:
            editor_width = control.sizeHint().width()
        if editor_width > width:
            return False
        label, description = self._labels[field_id]
        return (
            label.minimumSizeHint().width() <= width
            and label.heightForWidth(width) <= 2 * label.fontMetrics().lineSpacing()
            and (description is None or (
                description.minimumSizeHint().width() <= width
                and description.heightForWidth(width) <= 3 * description.fontMetrics().lineSpacing()
            ))
        )

    def _reflow(self) -> None:
        viewport_width = self.scroll_area.viewport().width()
        body_left, _, body_right, _ = self.scroll_area.widget().layout().getContentsMargins()
        minimum_column = max(176, self.fontMetrics().horizontalAdvance("0" * 22))
        for section in self._sections:
            left, _, right, _ = section.card.layout().getContentsMargins()
            width = max(1, viewport_width - body_left - body_right - left - right)
            gap = section.grid.horizontalSpacing()
            columns = min(3, max(1, (width + gap) // (minimum_column + gap)))
            cell_width = (width - (columns - 1) * gap) // columns
            positions = self._positions(section, columns, cell_width)
            if positions == section.positions and columns == section.columns:
                continue
            while section.grid.count():
                section.grid.takeAt(0)
            for col in range(3):
                section.grid.setColumnStretch(col, int(col < columns))
            for field_id, row, col, span in positions:
                section.grid.addWidget(self._rows[field_id], row, col, 1, span)
            section.positions = positions
            section.columns = columns

    def _positions(
        self, section: _FormSection, columns: int, cell_width: int,
    ) -> tuple[tuple[str, int, int, int], ...]:
        row, column = 0, 0
        positions = []
        visible = set(section.visible)
        for fields in section.rows:
            full_row = section.authored and len(fields) == 1
            for field in fields:
                if field.id not in visible:
                    continue
                span = columns if full_row or not self._fits_column(field.id, cell_width) else 1
                if column + span > columns:
                    row, column = row + 1, 0
                positions.append((field.id, row, column, span))
                column += span
                if column == columns:
                    row, column = row + 1, 0
            if column:
                row, column = row + 1, 0
        return tuple(positions)

    def _make_control(self, field: ModSetting, value: object) -> QWidget:
        changed = lambda *_: self._changed(field.id)
        if field.kind == "boolean":
            control = ToggleSwitch()
            control.setChecked(value is True)
            control.toggled.connect(changed)
        elif field.kind == "choice":
            control = ScrollSafeComboBox()
            for choice in field.choices:
                control.addItem(localized_text(choice.label), choice.value)
            selected = next((i for i, choice in enumerate(field.choices)
                             if type(value) is type(choice.value) and value == choice.value), -1)
            if selected < 0:
                control.addItem(str(value), deepcopy(value))
                selected = control.count() - 1
            control.setCurrentIndex(selected)
            control.currentIndexChanged.connect(changed)
        elif field.kind == "integer" and type(value) is int and -(2**31) <= value < 2**31:
            control = ScrollSafeSpinBox()
            # Keep out-of-range saved values visible until the user fixes them.
            # The shared validator applies the author's limits without clamping.
            control.setRange(-(2**31), 2**31 - 1)
            control.setSingleStep(max(1, min(int(field.step or 1), 2**31 - 1)))
            control.setValue(value)
            control.valueChanged.connect(changed)
        elif field.kind == "number" and type(value) in (int, float) and math.isfinite(value):
            control = ScrollSafeDoubleSpinBox()
            precision = max(6, *(-Decimal(str(n)).as_tuple().exponent
                                 for n in (value, field.default, field.step or 1)
                                 if type(n) in (int, float) and math.isfinite(n)))
            control.setDecimals(min(323, precision))
            control.setRange(-1.7976931348623157e308, 1.7976931348623157e308)
            control.setSingleStep(float(field.step or 0.1))
            control.setValue(value)
            control.valueChanged.connect(changed)
        elif field.kind == "string" and "\n" in str(value):
            control = QPlainTextEdit(str(value))
            control.setMaximumHeight(110)
            control.textChanged.connect(changed)
        else:
            control = QLineEdit(str(value) if value is not None else "")
            control.setMaxLength(max(32767, len(control.text())))
            control.textChanged.connect(changed)
        return control

    def _read(self, field: ModSetting) -> object:
        control = self._controls[field.id]
        if isinstance(control, ToggleSwitch):
            return control.isChecked()
        if isinstance(control, ScrollSafeComboBox):
            return control.currentData()
        if isinstance(control, (ScrollSafeSpinBox, ScrollSafeDoubleSpinBox)):
            return control.value()
        if isinstance(control, QPlainTextEdit):
            return control.toPlainText()
        text = control.text()
        try:
            if field.kind == "integer":
                return int(text)
            if field.kind == "number":
                return float(text)
        except ValueError:
            pass
        return text

    def draft_values(self) -> dict[str, object]:
        """Return a detached draft, preserving untouched values exactly."""
        return {field.id: deepcopy(self._read(field) if field.id in self._edited
                                  else self._baseline[field.id]) for field in self._fields}

    def is_dirty(self) -> bool:
        draft = self.draft_values()
        return any(self._different(field, draft[field.id]) for field in self._fields)

    def _different(self, field: ModSetting, value: object) -> bool:
        baseline = self._baseline[field.id]
        return value != baseline or (field.kind != "number" and type(value) is not type(baseline))

    def _changed(self, field_id: str) -> None:
        if self._loading:
            return
        self._edited.add(field_id)
        self.status_label.clear()
        self._refresh()

    def _refresh(self) -> None:
        draft = self.draft_values()
        errors = validate_values(self._fields, draft)
        for field_id, label in self._errors.items():
            message = errors.get(field_id, "")
            label.setText(message)
            label.setVisible(bool(message))
        restart = self._saved_restart | {field.restart for field in self._fields
                                       if self._different(field, draft[field.id])}
        messages = {
            "game_server": "Restart the game server to apply these changes.",
            "client": "Restart the EVE client to apply these changes.",
            "launcher": "Restart the launcher to apply these changes.",
        }
        self.restart_label.setText("\n".join(translate_ui_phrase(text) for kind, text in messages.items() if kind in restart))
        self.restart_label.setVisible(bool(self.restart_label.text()))
        self.save_button.setEnabled(not self._saving and self.is_dirty())
        self.defaults_button.setEnabled(not self._saving and bool(self._fields))
        self._filter_rows()

    def _filter_rows(self, *_args) -> None:
        query = self.search_edit.text().strip().casefold()
        count = 0
        sections = []
        show_advanced = self.advanced_toggle.isChecked()
        for index, section in enumerate(self._sections):
            visible = []
            for row in section.rows:
                for field in row:
                    label, description = self._labels[field.id]
                    text = " ".join((section.title, label.text(), description.text() if description else "",
                                     localized_text(field.group))).casefold()
                    show = (not field.advanced or show_advanced) and (not query or query in text)
                    self._rows[field.id].setVisible(show)
                    if show:
                        visible.append(field.id)
            section.visible = tuple(visible)
            section.card.setVisible(bool(visible))
            if visible:
                sections.append(index)
                count += len(visible)
        current_sections = [self.section_jump.itemData(i) for i in range(self.section_jump.count())]
        if sections != current_sections:
            previous = self.section_jump.currentData()
            with QSignalBlocker(self.section_jump):
                self.section_jump.clear()
                for index in sections:
                    self.section_jump.addItem(self._sections[index].title, index)
                self.section_jump.setCurrentIndex(self.section_jump.findData(previous))
        self.section_jump.setVisible(len(sections) > 1)
        self.empty_label.setText(translate_ui_phrase("No settings match your search." if self._fields else "No settings are available."))
        self.empty_label.setVisible(count == 0)
        self._reflow()

    def _jump_to_section(self, index: int) -> None:
        section_index = self.section_jump.itemData(index)
        if section_index is not None:
            card = self._sections[section_index].card
            top = card.mapTo(self.scroll_area.widget(), card.rect().topLeft()).y()
            self.scroll_area.verticalScrollBar().setValue(top)

    def _restore_defaults(self) -> None:
        if self._saving:
            return
        self._put_values({field.id: field.default for field in self._fields})
        self._edited = {field.id for field in self._fields}
        self.status_label.clear()
        self._refresh()

    def _put_values(self, values: Mapping[str, object]) -> None:
        self._loading = True
        try:
            for field in self._fields:
                control = self._controls[field.id]
                value = values[field.id]
                if isinstance(control, ToggleSwitch):
                    control.setChecked(value is True)
                elif isinstance(control, ScrollSafeComboBox):
                    selected = next((i for i in range(control.count())
                                     if type(value) is type(control.itemData(i)) and value == control.itemData(i)), -1)
                    if selected < 0:
                        control.addItem(str(value), deepcopy(value))
                        selected = control.count() - 1
                    control.setCurrentIndex(selected)
                elif isinstance(control, (ScrollSafeSpinBox, ScrollSafeDoubleSpinBox)):
                    control.setValue(value)
                elif isinstance(control, QPlainTextEdit):
                    control.setPlainText(str(value))
                else:
                    control.setText(str(value))
        finally:
            self._loading = False

    def _save(self) -> None:
        if self._saving or not self.is_dirty():
            return
        draft = self.draft_values()
        errors = validate_values(self._fields, draft)
        if errors:
            self.show_error(translate_ui_phrase("Fix the highlighted settings before saving."))
            self.search_edit.clear()
            self.advanced_toggle.setChecked(True)
            self._refresh()
            first = next((field_id for section in self._sections for field_id in section.visible
                          if field_id in errors), None)
            if first:
                self.scroll_area.widget().layout().activate()
                self.scroll_area.ensureWidgetVisible(self._rows[first])
                self._controls[first].setFocus()
            return
        self._pending = deepcopy(draft)
        self.set_saving(True)
        self.save_requested.emit(deepcopy(draft))

    def set_saving(self, saving: bool) -> None:
        self._saving = bool(saving)
        self.scroll_area.setEnabled(not self._saving)
        self.search_edit.setEnabled(not self._saving)
        self.advanced_toggle.setEnabled(not self._saving)
        self.section_jump.setEnabled(not self._saving)
        self.cancel_button.setEnabled(not self._saving)
        self.status_label.setStyleSheet("")
        self.status_label.setText(translate_ui_phrase("Saving settings...") if saving else "")
        self._refresh()

    def show_error(self, message: str) -> None:
        self.set_saving(False)
        self.status_label.setStyleSheet(f"color: {S['danger']};")
        self.status_label.setText(str(message))

    def mark_saved(self, values: Mapping[str, object] | None = None) -> None:
        confirmed = values if values is not None else self._pending or self.draft_values()
        self._saved_restart.update(field.restart for field in self._fields
                                   if field.id in confirmed and self._different(field, confirmed[field.id]))
        self._baseline = {field.id: deepcopy(confirmed.get(field.id, self._baseline[field.id])) for field in self._fields}
        self._put_values(self._baseline)
        self._edited.clear()
        self._pending = None
        self.set_saving(False)
        self.status_label.setStyleSheet("")
        self.status_label.setText(translate_ui_phrase("Saved ✓"))
        self.cancel_button.setText(translate_ui_phrase("Close"))

    def reject(self) -> None:
        if self._saving:
            return
        if self.is_dirty() and QMessageBox.question(
            self, translate_ui_phrase("Settings"),
            translate_ui_phrase("Discard unsaved changes to these mod settings?"),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        super().reject()
