"""Review exact configuration outcomes before a removal transaction resumes."""
import hashlib
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from src.core.mod_contributions import ConfigurationReview
from src.i18n import translate_ui_phrase as tr
from src.widgets.scroll_safe_controls import ScrollSafeComboBox


class ModConflictDialog(QDialog):
    def __init__(self, review: ConfigurationReview, parent=None):
        super().__init__(parent)
        self.setObjectName("modConflictDialog")
        self.setProperty("deepSignal", True)
        self.setStyleSheet("QDialog#modConflictDialog { background: #071019; border: 1px solid #264554; }")
        self.setWindowTitle(tr("Review Mod Conflict"))
        self.resize(960, 650)
        self.setMinimumSize(640, 430)
        self.review = review
        self.selected_plan = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        heading = QLabel(tr("Review Mod Conflict"))
        heading.setStyleSheet("color: #00d9ed; font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)
        explanation = QLabel(tr("Review this mod's proposed changes. Keeping current files cancels this action. Replaced files are backed up."
            if review.kind == "helper" else "Choose how to handle local edits. Shared settings from other mods remain recorded. Files are backed up before replacement."))
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #afc2ce;")
        layout.addWidget(explanation)
        owners = QLabel("\n".join(dict.fromkeys(owner.mod_relative_path for owner in review.owners)))
        owners.setWordWrap(True)
        layout.addWidget(owners)
        self.policy = ScrollSafeComboBox()
        self.policy.addItems([tr("Keep current files") if review.kind == "helper" else tr("Keep current settings") if review.kind == "settings" else tr("Keep local edits"),
                              tr("Apply mod changes") if review.kind == "helper" else tr("Apply my settings") if review.kind == "settings" else tr("Restore remaining mod settings")])
        layout.addWidget(self.policy)
        self.files = ScrollSafeComboBox()
        self.files.addItems([str(file.target.path) for file in review.restore.files])
        layout.addWidget(self.files)
        columns = QHBoxLayout()
        self.current, self.proposed = QPlainTextEdit(), QPlainTextEdit()
        for title, editor in (("Current file", self.current), ("After this action", self.proposed)):
            column = QVBoxLayout()
            column.addWidget(QLabel(tr(title)))
            editor.setReadOnly(True)
            column.addWidget(editor)
            columns.addLayout(column)
        layout.addLayout(columns)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(tr("Cancel"))
        apply = QPushButton(tr("Apply reviewed changes"))
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        layout.addLayout(buttons)
        self.policy.currentIndexChanged.connect(self._refresh)
        self.files.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        plan = self.review.preserve if self.policy.currentIndex() == 0 else self.review.restore
        index = self.files.currentIndex()
        if not 0 <= index < len(plan.files):
            return
        change = plan.files[index]
        def text(content):
            if content is None:
                return tr("File does not exist")
            if change.target.format == "file":
                try:
                    decoded = content.decode("utf-8-sig")
                    if "\0" not in decoded:
                        return decoded[:128000] + ("\n" + tr("Preview truncated; the full file is preserved.") if len(decoded) > 128000 else "")
                except UnicodeError:
                    pass
                return f"{tr('Binary file')}\n{len(content):,} {tr('bytes')}\nSHA-256: {hashlib.sha256(content).hexdigest()}"
            encoding = change.target.encoding or ("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
            decoded = content.decode(encoding, errors="replace")
            return decoded[:128000] + ("\n" + tr("Preview truncated; the full file is preserved.") if len(decoded) > 128000 else "")
        self.current.setPlainText(text(change.before))
        self.proposed.setPlainText(text(change.after))

    def accept(self):
        self.selected_plan = self.review.preserve if self.policy.currentIndex() == 0 else self.review.restore
        super().accept()
