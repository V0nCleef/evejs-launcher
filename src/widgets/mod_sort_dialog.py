"""An explicit saved-order preview; this dialog never runs mod code."""
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from src.i18n import translate_ui_phrase as tr


class ModSortDialog(QDialog):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setObjectName("modSortDialog")
        self.setProperty("deepSignal", True)
        self.setWindowTitle(tr("Sort by dependencies"))
        self.resize(900, 620)
        self.setMinimumSize(620, 430)
        self.setStyleSheet("QDialog#modSortDialog { background: #071019; border: 1px solid #264554; }")
        self.preview = preview
        self.save_requested = False
        layout = QVBoxLayout(self)
        explanation = QLabel(tr("Review the saved order against declared dependencies and load rules. Mods without ordering rules keep their relative order."))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.table = QTableWidget(len(preview.saved), 2)
        self.table.setHorizontalHeaderLabels([tr("Saved order"), tr("Proposed order")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        rule_labels = {"requires": "Requires", "loadBefore": "Load before", "loadAfter": "Load after", "conflicts": "Conflicts with"}
        for column, entries in enumerate((preview.saved, preview.proposed)):
            for row, entry in enumerate(entries):
                item = QTableWidgetItem(entry.name + ("" if entry.active else " · " + tr("Disabled")))
                item.setToolTip(entry.folder + "\n" + ("\n".join(
                    tr(rule_labels[kind]) + ": " + ", ".join(ids) for kind, ids in entry.rules)
                    or tr("No declared relationships")))
                self.table.setItem(row, column, item)
        layout.addWidget(self.table, 1)
        status = QLabel(tr("Resolve the listed issues before saving an order.") if preview.issues else
                        tr("No order changes are needed.") if not preview.changed else
                        tr("Save this order, then use Apply & Restart when you are ready."))
        status.setWordWrap(True)
        layout.addWidget(status)
        self.issues = QPlainTextEdit("\n\n".join(preview.issues))
        self.issues.setReadOnly(True)
        self.issues.setVisible(bool(preview.issues))
        self.issues.setMaximumHeight(150)
        layout.addWidget(self.issues)
        note = QLabel(tr("Sorting cannot repair incompatible mods or shared login code. No mods are enabled and no server is restarted by this action."))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("Cancel"))
        self.save_button = QPushButton(tr("Save order"))
        self.save_button.setEnabled(preview.changed and not preview.issues)
        cancel.clicked.connect(self.reject)
        self.save_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

    def accept(self):
        if self.preview.issues or not self.preview.changed:
            return
        self.save_requested = True
        super().accept()
