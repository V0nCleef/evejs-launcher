"""Read-only release preview; remote release notes are never interpreted as HTML."""
from PyQt6.QtCore import Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QProgressBar
from src.i18n import translate_ui_phrase as tr
from src.constants import SEMANTIC_COLORS as S
from src.widgets.update_button import UpdateButton


class ModUpdateDialog(QDialog):
    start_requested = pyqtSignal()
    progress_received = pyqtSignal(str, object, object)

    def __init__(self, mod, release, parent=None):
        super().__init__(parent)
        self.running = False
        self.progress_received.connect(self.show_progress, Qt.ConnectionType.QueuedConnection)
        self.setObjectName('modUpdateDialog')
        self.setProperty('deepSignal', True)
        self.setWindowTitle(tr('Mod update'))
        self.resize(700, 520)
        self.setMinimumSize(560, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        for index, text in enumerate((mod.name, f"{tr('Installed version')}: {mod.version}  →  {tr('Available version')}: {release.version}", tr('Release notes'))):
            label = QLabel(text)
            label.setObjectName('updateTitle' if index == 0 else 'updateDetail')
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlainText(release.notes or tr('No release notes were provided.'))
        layout.addWidget(self.notes, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.progress = QProgressBar()
        self.progress.setAccessibleName(tr('Mod update'))
        self.status.hide()
        self.progress.hide()
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        link = QPushButton(tr('View GitHub release'))
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.page_url)))
        buttons.addWidget(link)
        buttons.addStretch()
        cancel = QPushButton(tr('Cancel'))
        cancel.clicked.connect(self.reject)
        self.close_btn = cancel
        buttons.addWidget(cancel)
        self.update_btn = UpdateButton()
        self.update_btn.set_update_available(release.version)
        self.update_btn.setText(tr('Update mod'))
        self.update_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.update_btn.clicked.connect(self.start_requested.emit)
        buttons.addWidget(self.update_btn)
        layout.addLayout(buttons)
        self.setStyleSheet(f'''
            QDialog#modUpdateDialog {{ background: {S['background']}; color: {S['text_primary']}; }}
            QLabel {{ background: transparent; color: {S['text_secondary']}; }}
            QLabel#updateTitle {{ font-size: 21px; font-weight: 600; color: {S['text_primary']}; }}
            QPlainTextEdit {{ background: {S['surface']}; color: {S['text_primary']};
                border: 1px solid {S['border']}; border-radius: 8px; padding: 12px; }}
        ''')

    def begin(self):
        self.running = True
        self.update_btn.hide()
        self.close_btn.setEnabled(False)
        self.status.show()
        self.progress.show()
        self.show_progress('Checking update', 0, 0)

    @pyqtSlot(str, object, object)
    def show_progress(self, phase, done=0, total=0):
        if not self.running:
            return
        self.status.setText(tr(phase))
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done * 100 // total)))
            self.progress.setFormat(f'%p% — {done / 1048576:.1f} / {total / 1048576:.1f} MiB')
        else:
            self.progress.setRange(0, 0)

    def finish(self, result):
        self.running = False
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if result.success else 0)
        self.progress.setFormat(tr('Update complete.' if result.success else 'Update failed'))
        self.status.setText(tr('Update complete.') if result.success else tr('Update failed') + '\n' + result.error)
        self.close_btn.setText(tr('Close'))
        self.close_btn.setEnabled(True)
        self.close_btn.setFocus()

    def reject(self):
        if not self.running:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            event.ignore()
        else:
            super().closeEvent(event)
