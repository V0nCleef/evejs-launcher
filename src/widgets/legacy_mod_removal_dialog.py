"""Read-only fallback for installers without a shared-file composition contract."""
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from src.core.mod_management import LegacyRemovalConflict
from src.i18n import translate_ui_phrase as tr
from src.widgets.scroll_safe_controls import ScrollSafeComboBox


class LegacyModRemovalDialog(QDialog):
    def __init__(self, review: tuple[LegacyRemovalConflict, ...], parent=None):
        super().__init__(parent)
        self.review = review
        self.setObjectName("legacyModRemovalDialog")
        self.setProperty("deepSignal", True)
        self.setStyleSheet("QDialog#legacyModRemovalDialog { background: #071019; border: 1px solid #264554; }")
        self.setWindowTitle(tr("Review Mod Conflict"))
        self.resize(960, 650)
        layout = QVBoxLayout(self)
        heading = QLabel(tr("Review Mod Conflict"))
        heading.setStyleSheet("color: #00d9ed; font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        explanation = QLabel(tr("This installer cannot preserve overlapping edits automatically. Nothing was removed. Ask the mod author for a compatible removal method, then retry."))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.files = ScrollSafeComboBox()
        self.files.addItems([item.relative_path for item in review])
        layout.addWidget(self.files)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        columns = QHBoxLayout()
        self.current, self.expected = QPlainTextEdit(), QPlainTextEdit()
        for title, editor in (("Current file", self.current), ("After this action", self.expected)):
            column = QVBoxLayout()
            column.addWidget(QLabel(tr(title)))
            editor.setReadOnly(True)
            column.addWidget(editor)
            columns.addLayout(column)
        layout.addLayout(columns)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.files.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        index = self.files.currentIndex()
        if not 0 <= index < len(self.review):
            return
        item = self.review[index]
        self.reason.setText(tr(item.reason))
        if item.unavailable:
            current = item.unavailable
        elif item.current is None:
            current = tr("File does not exist")
        else:
            encoding = "utf-16" if item.current.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            try:
                current = item.current.decode(encoding)
                if "\x00" in current:
                    raise UnicodeError()
            except UnicodeError:
                current = tr("Binary file; text preview unavailable.")
        self.current.setPlainText(current)
        expected = (tr("File does not exist") if item.expected_state == "absent" else
                    tr("Only a target hash is recorded. Replacement contents and a safe merge are unavailable.")
                    + "\n\nSHA-256: " + str(item.expected_sha256))
        self.expected.setPlainText(expected)
