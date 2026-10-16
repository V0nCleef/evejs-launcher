"""Generic user-added batch scripts below the installed Mods inventory."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from src.core.startup_scripts import ScriptEntry, ScriptError, StartupScriptController, root_key
from src.i18n import translate_ui_phrase
from src.widgets.toggle_switch import ToggleSwitch
from src.widgets.ui_translation import mark_translatable, set_translatable_accessible_name, set_translatable_text


def _button(text: str, role="signalSecondary") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("class", role)
    button.setMinimumHeight(32)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    mark_translatable(button)
    return button


def _label(text: str, role="modsRuntimeDescription") -> QLabel:
    label = QLabel(text)
    label.setProperty("class", role)
    mark_translatable(label)
    return label


class ScriptDialog(QDialog):
    def __init__(self, parent=None, *, entry: ScriptEntry | None = None, filename=""):
        super().__init__(parent)
        self._original = entry
        self.entry: ScriptEntry | None = None
        self.setWindowTitle(translate_ui_phrase("Edit startup script" if entry else "Add startup script"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(12)
        self.name_edit = QLineEdit(entry.name if entry else Path(filename).stem)
        self.name_edit.setMaxLength(128)
        self.filename_edit = QLineEdit(entry.filename if entry else filename)
        self.filename_edit.setMaxLength(4096)
        file_row = QHBoxLayout()
        file_row.addWidget(self.filename_edit, stretch=1)
        browse = _button("Browse…")
        browse.clicked.connect(self._browse)
        file_row.addWidget(browse)
        self.arguments_edit = QLineEdit(entry.arguments if entry else "")
        self.arguments_edit.setMaxLength(8192)
        self.arguments_edit.setPlaceholderText("Optional arguments, e.g. start --port 12345")
        mark_translatable(self.arguments_edit)
        form.addRow(_label("Name"), self.name_edit)
        form.addRow(_label("Batch file"), file_row)
        form.addRow(_label("Arguments"), self.arguments_edit)
        self.auto_check = QCheckBox("Start with server")
        mark_translatable(self.auto_check)
        self.auto_check.setChecked(entry.auto_start if entry else True)
        form.addRow("", self.auto_check)
        layout.addLayout(form)
        help_label = _label("Scripts open in their own console and keep running when the launcher closes.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.error_label = _label("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #ff8585;")
        layout.addWidget(self.error_label)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = _button("Cancel")
        cancel.clicked.connect(self.reject)
        save = _button("Save", "signalPrimary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)

    def _browse(self):
        filename, _ = QFileDialog.getOpenFileName(self, translate_ui_phrase("Batch file"), self.filename_edit.text(), translate_ui_phrase("Batch files (*.bat)"))
        if filename:
            self.filename_edit.setText(filename)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(filename).stem)

    def _save(self):
        try:
            item = ScriptEntry.create(self.name_edit.text(), self.filename_edit.text(),
                                      arguments=self.arguments_edit.text(), auto_start=self.auto_check.isChecked())
            if not Path(item.filename).is_file():
                raise ScriptError("Choose an existing .bat file.")
            self.entry = replace(item, id=self._original.id) if self._original else item
        except (ScriptError, OSError) as exc:
            set_translatable_text(self.error_label, str(exc) if isinstance(exc, ScriptError) else "Choose an existing .bat file.")
            return
        self.accept()


class ScriptRow(QFrame):
    run_requested = pyqtSignal(object)
    edit_requested = pyqtSignal(object)
    remove_requested = pyqtSignal(object)
    auto_changed = pyqtSignal(object, bool)

    def __init__(self, entry: ScriptEntry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setProperty("class", "modInstrument")
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        icon = QLabel("BAT")
        icon.setProperty("class", "modsRuntimeMark")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(40, 40)
        layout.addWidget(icon)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        # User-provided names and paths are data, never translation candidates.
        self.name_label = QLabel(entry.name)
        self.name_label.setProperty("class", "modName")
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label = QLabel(entry.filename)
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setProperty("class", "modsRuntimeDescription")
        for label in (self.name_label, self.path_label):
            label.setProperty("i18nIgnore", True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.path_label.setToolTip(entry.filename)
        copy.addWidget(self.name_label)
        copy.addWidget(self.path_label)
        layout.addLayout(copy, stretch=1)
        self.state_label = _label("NOT STARTED", "modState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.state_label)
        self.run_button = _button("Run now")
        self.edit_button = _button("Edit")
        self.remove_button = _button("Remove", "modManagementAction")
        self.remove_button.setProperty("managementRole", "remove")
        self.remove_button.setMinimumWidth(84)
        self.run_button.clicked.connect(lambda: self.run_requested.emit(self.entry))
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.entry))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.entry))
        for button in (self.run_button, self.edit_button, self.remove_button):
            layout.addWidget(button)
        layout.addWidget(_label("Start with server"))
        self.auto_toggle = ToggleSwitch(self)
        self.auto_toggle.setChecked(entry.auto_start)
        set_translatable_accessible_name(self.auto_toggle, "Start with server")
        self.auto_toggle.toggled.connect(lambda checked: self.auto_changed.emit(self.entry, checked))
        layout.addWidget(self.auto_toggle)

    def set_state(self, state: str, details: str):
        set_translatable_text(self.state_label, state)
        tip = translate_ui_phrase("Status refers to the batch script, not any program it starts.")
        self.state_label.setToolTip(tip + ("\n" + details if details else ""))
        self.state_label.setProperty("state", "error" if state == "FAILED" else ("enabled" if state == "RUNNING" else "readonly"))
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        running = state == "RUNNING"
        self.run_button.setEnabled(not running)
        self.edit_button.setEnabled(not running)
        self.remove_button.setEnabled(not running)


class StartupScriptsPanel(QWidget):
    def __init__(self, parent=None, *, controller: StartupScriptController | None = None):
        super().__init__(parent)
        self.controller = controller or StartupScriptController(parent=self)
        self._root = ""
        self._root_identity = ""
        self.rows: dict[str, ScriptRow] = {}
        self.setObjectName("startupScriptsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        divider = QFrame()
        divider.setProperty("class", "signalDivider")
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #00bdd1;")
        layout.addWidget(divider)
        header = QHBoxLayout()
        self.title_label = _label("STARTUP SCRIPTS", "modsManifestTitle")
        header.addWidget(self.title_label)
        header.addStretch()
        self.add_button = _button("+ Add .bat file", "signalPrimary")
        self.add_button.clicked.connect(self._add)
        header.addWidget(self.add_button)
        layout.addLayout(header)
        description = _label("Run your own .bat files after the Game server is ready.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.notice_label = _label("")
        self.notice_label.setWordWrap(True)
        self.notice_label.setStyleSheet("color: #ff8585;")
        self.notice_label.hide()
        layout.addWidget(self.notice_label)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(72)
        self._scroll.setMaximumHeight(210)
        content = QWidget()
        self._rows_layout = QVBoxLayout(content)
        self._rows_layout.setContentsMargins(0, 0, 2, 0)
        self._rows_layout.setSpacing(8)
        self._scroll.setWidget(content)
        layout.addWidget(self._scroll)
        self.controller.changed.connect(self._state_changed)
        self.controller.error.connect(self._controller_error)
        self.set_root("")

    def set_root(self, root: str):
        self._root = root
        try:
            self._root_identity = root_key(root) if root else ""
        except ScriptError:
            self._root_identity = ""
        self.refresh()

    def _notice(self, message: str, details=""):
        set_translatable_text(self.notice_label, message)
        self.notice_label.setToolTip(details)
        self.notice_label.setVisible(bool(message))

    def refresh(self):
        self.rows.clear()
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        valid = bool(self._root and Path(self._root).is_absolute() and Path(self._root).is_dir())
        self.add_button.setEnabled(valid)
        self._notice("")
        try:
            entries = self.controller.store.list(self._root) if valid else []
        except ScriptError as exc:
            entries = []
            self._notice(str(exc))
        if not entries:
            empty = _label("No startup scripts added." if valid else "Select an EveJS folder in Settings first.")
            self._rows_layout.addWidget(empty)
        for entry in entries:
            row = ScriptRow(entry)
            row.run_requested.connect(lambda item: self.controller.run(self._root, item))
            row.edit_requested.connect(self._edit)
            row.remove_requested.connect(self._remove)
            row.auto_changed.connect(lambda item, enabled: self._save(replace(item, auto_start=enabled)))
            self.rows[entry.id] = row
            self._rows_layout.addWidget(row)
        self._rows_layout.addStretch()
        self._scroll.setFixedHeight(min(210, max(72, len(entries) * 72)))
        self._state_changed(self._root_identity)

    @pyqtSlot(str)
    def _state_changed(self, root: str):
        if not root or root != self._root_identity:
            return
        for identifier, row in self.rows.items():
            row.set_state(*self.controller.status(self._root, identifier))

    @pyqtSlot(str, str)
    def _controller_error(self, root: str, message: str):
        if root and root == self._root_identity:
            self._notice(message)

    def _edit_dialog(self, entry: ScriptEntry | None):
        filename = ""
        if entry is None:
            filename, _ = QFileDialog.getOpenFileName(self, translate_ui_phrase("Add startup script"), self._root, translate_ui_phrase("Batch files (*.bat)"))
            if not filename:
                return None
        dialog = ScriptDialog(self, entry=entry, filename=filename)
        try:
            return dialog.entry if dialog.exec() == QDialog.DialogCode.Accepted else None
        finally:
            dialog.deleteLater()

    def _add(self):
        self._edit(None)

    def _edit(self, entry):
        edited = self._edit_dialog(entry)
        if edited is not None:
            self._save(edited)

    def _save(self, entry):
        try:
            self.controller.store.save(self._root, entry)
        except (OSError, ScriptError) as exc:
            self._notice("Startup script settings could not be saved.", str(exc))
            return
        self.refresh()

    def _remove(self, entry):
        try:
            self.controller.store.remove(self._root, entry.id)
        except (OSError, ScriptError) as exc:
            self._notice("Startup script settings could not be saved.", str(exc))
            return
        self.refresh()

    @pyqtSlot(str, object)
    def start_for_server(self, root: str, session: object):
        self.controller.start_for_server(root, session)
