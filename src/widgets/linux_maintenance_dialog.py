"""Linux Setup maintenance with retained worker ownership and explicit scope."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from src.core.linux_maintenance import (
    LinuxMaintenanceAction as Action, LinuxMaintenanceRequest,
    LinuxSetupMode, LinuxSetupRequest, run_installer, run_maintenance,
)
from src.widgets.localized_dialogs import (
    LocalizedFileDialog as QFileDialog, LocalizedMessageBox as QMessageBox,
)


class _MaintenanceWorker(QObject):
    completed = pyqtSignal(object)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    @pyqtSlot()
    def run(self):
        try:
            result = (True, self.operation())
        except Exception as exc:
            result = (False, str(exc) or type(exc).__name__)
        self.completed.emit(result)


class LinuxMaintenanceDialog(QDialog):
    """One installation, one immutable request, no close during mutation."""
    changed = pyqtSignal()

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Linux installation and maintenance')
        self.resize(760, 580)
        self.root = Path(root).expanduser().absolute() if root else None
        self._thread = None
        self._worker = None
        self._result = None
        self._finished = False
        self._mutating = False
        layout = QVBoxLayout(self)
        label = QLabel('Installation: ' + (str(self.root) if self.root else 'No installation selected'))
        label.setWordWrap(True)
        layout.addWidget(label)
        notice = QLabel('Close Game, Market and all EVE clients before changing an installation. '
                        'Updates and restores use the selected installation’s Linux Setup backups.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.form = QWidget()
        form = QFormLayout(self.form)
        self.action = QComboBox()
        for title, action in (
            ('Preview an EveJS update', Action.PREVIEW_UPDATE),
            ('Apply an EveJS update', Action.UPDATE),
            ('List backups', Action.LIST_BACKUPS),
            ('Restore program and data', Action.RESTORE),
            ('Restore program only', Action.RESTORE_PROGRAM),
            ('Restore data only', Action.RESTORE_DATA),
            ('Prune old backups', Action.PRUNE_BACKUPS),
            ('Reseed Market', Action.RESEED_MARKET),
            ('Build Market from source', Action.BUILD_MARKET),
            ('Use unprivileged HTTPS port 8443', Action.PREPARE_CDN_8443),
            ('Recover an interrupted HTTPS profile change', Action.RECOVER_CDN),
        ):
            self.action.addItem(title, action)
        form.addRow('Operation:', self.action)
        release_row = QWidget()
        row = QHBoxLayout(release_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.release = QLineEdit()
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse_release)
        row.addWidget(self.release, 1)
        row.addWidget(browse)
        form.addRow('EveJS release ZIP:', release_row)
        self.backup = QComboBox()
        form.addRow('Backup:', self.backup)
        self.keep = QSpinBox()
        self.keep.setRange(1, 20)
        self.keep.setValue(3)
        form.addRow('Backups to retain:', self.keep)
        self.seed = QComboBox()
        for name in ('v1', 'v2', 'v3', 'v4'):
            self.seed.addItem(name, name)
        form.addRow('Market seed profile:', self.seed)
        layout.addWidget(self.form)
        self.run_button = QPushButton('Run selected operation')
        self.run_button.clicked.connect(self._run_selected)
        layout.addWidget(self.run_button)
        self.setup_mode = QComboBox()
        self.setup_mode.addItem('New EveJS installation', LinuxSetupMode.INSTALL)
        if self.root is not None:
            self.setup_mode.addItem('Prepare the selected installation’s runtime', LinuxSetupMode.PREPARE)
            self.setup_mode.addItem('Select or reuse its Proton prefix', LinuxSetupMode.PREFIX)
        layout.addWidget(self.setup_mode)
        self.install_button = QPushButton('Open Linux Setup…')
        self.install_button.setToolTip('Downloads the verified Linux Setup v1.19 wizard and opens it in a terminal.')
        self.install_button.clicked.connect(self._install)
        layout.addWidget(self.install_button)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.document().setMaximumBlockCount(8000)
        layout.addWidget(self.output, 1)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.action.currentIndexChanged.connect(self._sync_action)
        self._refresh_backups()
        self._sync_action()

    def _refresh_backups(self):
        selected = self.backup.currentText()
        self.backup.clear()
        self.backup.addItem('Select a backup', '')
        if self.root is None:
            return
        folder = self.root / '_backup'
        try:
            if folder.is_symlink() or not folder.is_dir():
                return
            for path in sorted(folder.iterdir(), key=lambda p: p.name, reverse=True):
                if path.is_dir() and not path.is_symlink():
                    self.backup.addItem(path.name, path.name)
        except OSError:
            return
        index = self.backup.findText(selected)
        if index >= 0:
            self.backup.setCurrentIndex(index)

    def _sync_action(self):
        action = self.action.currentData()
        self.release.parentWidget().setEnabled(action in (Action.PREVIEW_UPDATE, Action.UPDATE))
        self.backup.setEnabled(action in (Action.RESTORE, Action.RESTORE_PROGRAM, Action.RESTORE_DATA))
        self.keep.setEnabled(action is Action.PRUNE_BACKUPS)
        self.seed.setEnabled(action is Action.RESEED_MARKET)
        self.run_button.setEnabled(self.root is not None)

    def _browse_release(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Select EveJS release ZIP', '', 'ZIP archives (*.zip)')
        if filename:
            self.release.setText(filename)

    def _run_selected(self):
        if self._thread is not None or self.root is None:
            return
        action = self.action.currentData()
        backup = self.backup.currentData() or ''
        if action in (Action.RESTORE, Action.RESTORE_PROGRAM, Action.RESTORE_DATA) and not backup:
            self.output.setPlainText('Select the exact backup to restore.')
            return
        request = LinuxMaintenanceRequest(action, self.root,
            release_zip=Path(self.release.text()) if self.release.text() else None,
            backup=backup, keep=self.keep.value(), market_profile=self.seed.currentData())
        if request.mutates:
            detail = {
                Action.BUILD_MARKET: 'Compile the selected Market sources and native source mods with Cargo? This requires an installed Linux C/Rust toolchain and may download locked Cargo dependencies. Player/Market databases are preserved.',
                Action.UPDATE: f'Apply {request.release_zip} to this installation? Current data is backed up by Linux Setup.',
                Action.RESTORE: f'Replace program files and player/server data with backup {backup}?',
                Action.RESTORE_PROGRAM: f'Replace program files with backup {backup}?',
                Action.RESTORE_DATA: f'Replace player/server data with backup {backup}?',
                Action.PRUNE_BACKUPS: f'Permanently remove old Linux Setup backups, retaining {request.keep}?',
                Action.RESEED_MARKET: f'Replace the Market database using seed profile {request.market_profile}?',
                Action.PREPARE_CDN_8443: 'Prepare the copied client and this installation for localhost HTTPS on port 8443? The exact previous client binaries and configuration are retained for recovery. Host port policy is unchanged.',
                Action.RECOVER_CDN: 'Restore the saved client binaries and configuration from the interrupted HTTPS profile change?',
            }[action]
            if QMessageBox.warning(self, 'Confirm Linux maintenance', str(self.root) + '\n\n' + detail,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return
        # Only the frozen request crosses to the worker, never GUI controls.
        self._start(lambda: run_maintenance(request), mutating=request.mutates)

    def _install(self):
        if self._thread is not None:
            return
        mode = self.setup_mode.currentData()
        request = LinuxSetupRequest(mode, self.root if mode is not LinuxSetupMode.INSTALL else None)
        scope = str(self.root) if request.root is not None else 'A new destination chosen in the wizard'
        if QMessageBox.question(self, 'Open Linux Setup?',
            self.setup_mode.currentText() + '\n' + scope + '\n\n'
            'Download the verified Linux Setup v1.19 wizard and open it in a terminal? '
            'It asks before installing system dependencies. Close all EveJS services and EVE clients first.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self._start(lambda: run_installer(request), mutating=True)

    def _start(self, operation, *, mutating: bool):
        self._result = None
        self._finished = False
        self._mutating = mutating
        self.form.setEnabled(False)
        self.run_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.setup_mode.setEnabled(False)
        self.close_button.setEnabled(False)
        self.output.setPlainText('Running… Keep the launcher open until this finishes.')
        thread = QThread(self)
        worker = _MaintenanceWorker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._completed)
        worker.completed.connect(thread.quit)
        worker.completed.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        self._thread, self._worker = thread, worker
        thread.start()

    @pyqtSlot(object)
    def _completed(self, result):
        self._result = result
        self._finalize()

    @pyqtSlot()
    def _thread_finished(self):
        if self.sender() is not self._thread:
            return
        self._finished = True
        self._finalize()

    def _finalize(self):
        if self._result is None or not self._finished:
            return
        success, output = self._result
        thread = self._thread
        self._thread = self._worker = None
        thread.deleteLater()
        self.output.setPlainText(('Completed.\n\n' if success else 'Failed.\n\n') + output)
        self.form.setEnabled(True)
        self.install_button.setEnabled(True)
        self.setup_mode.setEnabled(True)
        self.close_button.setEnabled(True)
        self._refresh_backups()
        self._sync_action()
        if self._mutating:
            # Failed commands can also have completed a backed-up mutation.
            self.changed.emit()

    def reject(self):
        if self._thread is None:
            super().reject()

    def closeEvent(self, event):
        if self._thread is not None:
            event.ignore()
        else:
            super().closeEvent(event)
