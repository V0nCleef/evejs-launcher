"""Deep Signal controls for a verified, offline save transfer."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.core.save_migration import SaveInstallation, SaveMigrationError, Transfer, migrate, review_transfer
from src.widgets.localized_dialogs import LocalizedFileDialog, LocalizedMessageBox
from src.widgets.page_header import PageHeader
from src.i18n import current_language, translate_ui_phrase
from src.widgets.ui_translation import (
    mark_translatable, register_translatable_widget_tree, retranslate_widget_tree,
    set_translatable_text,
)


class _TransferProgressDialog(QDialog):
    def reject(self):
        # A transfer may be between rename and verification. Esc must not
        # dismiss its progress window or offer an unsafe cancellation.
        pass

    def closeEvent(self, event):
        event.ignore()


class _TransferThread(QThread):
    progress = pyqtSignal(str)

    def __init__(self, request, verifier, parent):
        super().__init__(parent)
        self.request = request
        self.verifier = verifier
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = migrate(self.request, progress=self.progress.emit, verify_game=self.verifier)
        except Exception as error:
            self.error = str(error)


def _label(text="", *, subtle=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if subtle:
        label.setStyleSheet("color: #afc3d2;")
    return mark_translatable(label)


def _translated_message(source):
    # The record path and unrecognized technical diagnostics remain literal.
    # Known launcher framing can be translated independently on each line.
    return "\n".join(translate_ui_phrase(line, allow_templates=True, template_min_literal=24)
                     for line in source.split("\n"))


def _panel(layout, heading):
    panel = QFrame()
    panel.setProperty("class", "signalInstrument")
    content = QVBoxLayout(panel)
    content.setContentsMargins(14, 11, 14, 12)
    content.setSpacing(8)
    label = _label(heading)
    label.setStyleSheet("color: #aee7ef; font-weight: 600;")
    content.addWidget(label)
    layout.addWidget(panel)
    return content


def _choice(label, group, parent):
    button = QRadioButton(label, parent)
    button.setStyleSheet("""
        QRadioButton { color: #d7e5ec; spacing: 8px; padding: 5px; }
        QRadioButton::indicator { width: 14px; height: 14px; border-radius: 8px;
            border: 1px solid #8296a5; background: #08111d; }
        QRadioButton::indicator:checked { border: 1px solid #00c8e0; background: #00c8e0; }
        QRadioButton:focus { border: 1px solid #00c8e0; border-radius: 3px; }
    """)
    group.addButton(button)
    return button


class SaveMigrationPage(QWidget):
    busy_changed = pyqtSignal(object)
    transfer_finished = pyqtSignal(object)

    def __init__(self, *, verifier, can_start, installation_label, parent=None):
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        self._verifier = verifier
        self._can_start = can_start
        self._installation_label = installation_label
        self._worker = None
        self._dialog = None
        self._seed = None
        self._last_backup = None
        self._status_source = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(11)
        self.page_header = PageHeader(
            "Save Migration", "Copy or move a playthrough between your installations.",
            "Playthrough Transfer", self,
        )
        layout.addWidget(self.page_header)

        route = _panel(layout, "INSTALLATIONS")
        columns = QGridLayout()
        route.addLayout(columns)
        self.from_label = _label("COPY FROM")
        self.to_label = _label("COPY TO")
        self.source = QComboBox()
        self.target = QComboBox()
        self.source.setAccessibleName("Source installation")
        self.target.setAccessibleName("Destination installation")
        self.source_detail = _label(subtle=True)
        self.target_detail = _label(subtle=True)
        self.swap = QPushButton("⇄  Swap")
        self.swap.clicked.connect(self._swap)
        for column, title, picker, details in (
            (0, self.from_label, self.source, self.source_detail),
            (2, self.to_label, self.target, self.target_detail),
        ):
            columns.setColumnStretch(column, 1)
            columns.addWidget(title, 0, column)
            columns.addWidget(picker, 1, column)
            columns.addWidget(details, 2, column)
            picker.currentIndexChanged.connect(self._route_changed)
        columns.addWidget(self.swap, 1, 1)
        self.route_note = _label(subtle=True)
        route.addWidget(self.route_note)

        content_panel = _panel(layout, "WHAT TO BRING")
        content_panel.addWidget(_label("Playthrough · Always included"))
        content_panel.addWidget(_label("Characters, ships, inventories, wallets, portraits and world progress.", subtle=True))
        content_panel.addWidget(_label("Market"))
        self.market = QButtonGroup(self)
        self.fresh = _choice("Start with a fresh market", self.market, self)
        self.bring = _choice("Bring market data", self.market, self)
        self.fresh.setChecked(True)
        options = QHBoxLayout()
        options.addWidget(self.fresh, 1)
        options.addWidget(self.bring, 1)
        content_panel.addLayout(options)
        self.market_note = _label()
        content_panel.addWidget(self.market_note)
        self.seed_button = QPushButton("Choose a clean market seed…")
        self.seed_button.clicked.connect(self._choose_seed)
        self.seed_detail = _label(subtle=True)
        seed_row = QHBoxLayout()
        seed_row.addWidget(self.seed_button)
        seed_row.addWidget(self.seed_detail, 1)
        content_panel.addLayout(seed_row)
        self.market.buttonToggled.connect(self._render)

        action = _panel(layout, "TRANSFER TYPE")
        self.mode = QButtonGroup(self)
        self.copy = _choice("Copy", self.mode, self)
        self.move = _choice("Move", self.mode, self)
        self.copy.setChecked(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy)
        buttons.addWidget(self.move)
        buttons.addStretch()
        self.review = QPushButton("Review transfer")
        self.review.setProperty("class", "signalPrimary")
        self.review.clicked.connect(self._review)
        buttons.addWidget(self.review)
        action.addLayout(buttons)
        self.mode_note = _label(subtle=True)
        action.addWidget(self.mode_note)
        self.mode.buttonToggled.connect(self._render)
        action.addWidget(_label("The destination is backed up before its save or market is replaced.", subtle=True))
        self.status = _label()
        self.status.setProperty("i18nIgnore", True)
        layout.addWidget(self.status)
        self.backup_button = QPushButton("Open backup folder")
        self.backup_button.hide()
        self.backup_button.clicked.connect(self._open_backup)
        layout.addWidget(self.backup_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self._render()
        register_translatable_widget_tree(self)

    def retranslate_ui(self):
        self._render()
        self._set_status(self._status_source)
        retranslate_widget_tree(self, current_language())

    def _set_status(self, source):
        self._status_source = source
        self.status.setText(_translated_message(source))

    @property
    def busy(self):
        return self._worker is not None

    def set_installations(self, profiles, active):
        if self.busy:
            return
        previous = (self.source.currentData(), self.target.currentData())
        entries = [entry for entry in profiles if entry["settings"].get("evejs_root")]
        first = self.source.currentData(Qt.ItemDataRole.UserRole + 1) or active
        second = self.target.currentData(Qt.ItemDataRole.UserRole + 1)
        keys = [entry["id"] for entry in entries]
        if first not in keys:
            first = next(iter(keys), None)
        if second not in keys:
            second = next((identity for identity in keys if identity != first), None)
        for picker, desired in ((self.source, first), (self.target, second)):
            picker.blockSignals(True)
            picker.clear()
            for entry in entries:
                picker.addItem(self._installation_label(entry), SaveInstallation.from_profile(entry))
                picker.setItemData(picker.count() - 1, entry["id"], Qt.ItemDataRole.UserRole + 1)
            if desired is not None:
                picker.setCurrentIndex(keys.index(desired))
            picker.blockSignals(False)
        if previous != (self.source.currentData(), self.target.currentData()):
            self._seed = None
            self._set_status("")
        self._render()

    def _swap(self):
        left, right = self.source.currentIndex(), self.target.currentIndex()
        self.source.setCurrentIndex(right)
        self.target.setCurrentIndex(left)

    def _route_changed(self):
        self._seed = None
        self._set_status("")
        self._render()

    def _render(self, *_):
        source, target = self.source.currentData(), self.target.currentData()
        for record, label in ((source, self.source_detail), (target, self.target_detail)):
            mode = "Docker" if record and record.settings.get("runtime_backend") == "docker_compose" else "Native"
            if record:
                try:
                    version = record.version
                except (OSError, ValueError):
                    version = None
                version = version or translate_ui_phrase("Unknown version")
                label.setText(f"{translate_ui_phrase(mode)} · EveJS {version}\n{record.root}")
            else:
                label.clear()
        ready = bool(source and target and source.root != target.root)
        self.swap.setEnabled(ready)
        self.review.setEnabled(ready and not self.busy)
        if self.source.count() < 2:
            note = "Register two installations before starting a transfer."
        elif not ready:
            note = "Choose two different installations."
        else:
            note = ("Stop Game and Market on both installations before transferring.\n"
                    "Both installations must use the same verified EveJS base version and save format.")
        set_translatable_text(self.route_note, note)
        verb = "MOVE" if self.move.isChecked() else "COPY"
        set_translatable_text(self.from_label, verb + " FROM")
        set_translatable_text(self.to_label, verb + " TO")
        fresh = self.fresh.isChecked()
        set_translatable_text(self.market_note,
            "Items listed for sale won’t appear in the new inventory. "
            "Cancel active sell orders and collect the items in the source installation before transferring. "
            "ISK tied up in open buy orders stays behind too." if fresh else
            "Bring orders, escrow and market history when the source and destination formats match."
        )
        self.market_note.setStyleSheet("color: #ffd070;" if fresh else "color: #afc3d2;")
        self.seed_button.setVisible(fresh)
        self.seed_detail.setVisible(fresh)
        if self._seed:
            self.seed_detail.setText(self._seed.name)
            self.seed_detail.setProperty("i18nIgnore", True)
        else:
            self.seed_detail.setProperty("i18nIgnore", False)
            set_translatable_text(self.seed_detail, "Uses the destination market if it is still clean.")
        set_translatable_text(self.mode_note,
            "The original save is backed up and removed after the destination copy is verified."
            if self.move.isChecked() else "Your original playthrough stays where it is."
        )

    def _choose_seed(self):
        path, _ = LocalizedFileDialog.getOpenFileName(
            self, "Choose a clean market seed", "", "SQLite databases (*.sqlite *.db)"
        )
        if path:
            self._seed = Path(path)
            self._render()

    def _review(self):
        if self.busy or not self._can_start():
            return
        source, target = self.source.currentData(), self.target.currentData()
        if not source or not target or source.root == target.root:
            return
        request = Transfer(source, target, self.move.isChecked(), self.bring.isChecked(), self._seed)
        try:
            review_transfer(request)
        except (OSError, SaveMigrationError, ValueError) as error:
            self._set_status(str(error))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Review transfer")
        dialog.setMinimumWidth(430)
        content = QVBoxLayout(dialog)
        content.addWidget(_label(f"{self.source.currentText()}  →  {self.target.currentText()}"))
        content.addWidget(_label(
            "Move the playthrough after verifying its destination copy." if request.move else
            "Copy the playthrough and keep the original."
        ))
        content.addWidget(_label("Any existing destination save and market are backed up first."))
        if request.carry_market:
            content.addWidget(_label("Bring the market database if the formats match."))
        else:
            content.addWidget(_label("Start with a clean destination market. Orders and their escrow stay behind."))
        if request.move and not request.carry_market:
            content.addWidget(_label("The old market remains in the source installation."))
        content.addWidget(_label("The destination keeps its own installation settings and mods."))
        content.addWidget(_label("The transfer stops if the save format cannot be verified."))
        acknowledge = QCheckBox("I’ve checked my open orders and understand what stays behind.")
        if not request.carry_market:
            content.addWidget(acknowledge)
        controls = QHBoxLayout()
        controls.addStretch()
        back = QPushButton("Back")
        back.clicked.connect(dialog.reject)
        controls.addWidget(back)
        confirm = QPushButton("Move playthrough" if request.move else "Copy playthrough")
        confirm.setProperty("class", "signalPrimary")
        confirm.setEnabled(request.carry_market)
        confirm.clicked.connect(dialog.accept)
        acknowledge.toggled.connect(confirm.setEnabled)
        controls.addWidget(confirm)
        content.addLayout(controls)
        register_translatable_widget_tree(dialog)
        if dialog.exec() == QDialog.DialogCode.Accepted and self._can_start():
            self._start(request)
        dialog.deleteLater()

    def _start(self, request):
        worker = _TransferThread(request, self._verifier, self)
        waiting = _TransferProgressDialog(self)
        waiting.setWindowTitle("Save Migration")
        waiting.setWindowModality(Qt.WindowModality.ApplicationModal)
        waiting.setWindowFlag(Qt.WindowType.CustomizeWindowHint, True)
        waiting.setWindowFlag(Qt.WindowType.WindowTitleHint, True)
        waiting.setMinimumWidth(410)
        layout = QVBoxLayout(waiting)
        message = _label("Preparing the transfer…")
        layout.addWidget(message)
        worker.progress.connect(lambda text: set_translatable_text(message, text))
        worker.finished.connect(self._finished)
        self._worker, self._dialog = worker, waiting
        self.busy_changed.emit(worker)
        register_translatable_widget_tree(waiting)
        waiting.show()
        try:
            worker.start()
        except Exception as error:
            worker.error = str(error)
            self._finished()

    def _finished(self):
        worker, self._worker = self._worker, None
        dialog, self._dialog = self._dialog, None
        dialog.done(QDialog.DialogCode.Accepted)
        dialog.deleteLater()
        self.busy_changed.emit(None)
        if worker.error:
            self._set_status(worker.error)
            LocalizedMessageBox.warning(self, "Save Migration stopped", _translated_message(worker.error))
        else:
            result = worker.result
            self._last_backup = result.backup
            self.backup_button.show()
            self._set_status(result.note or ("Playthrough moved." if result.moved else "Playthrough copied."))
            self.transfer_finished.emit(worker.request)
        worker.deleteLater()
        self._render()

    def _open_backup(self):
        if self._last_backup is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_backup)))
