"""One reviewed recovery for all pilots, with finite background I/O."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
from pathlib import Path

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLabel, QPushButton, QTableView, QVBoxLayout

from src.core.application_operations import active_mutations
from src.core.drone_recovery_batch import RecoveryContext, RecoveryChangedError, RecoveryScan, apply_recovery, scan_recovery
from src.core.groups import installation_group_identity
from src.core.installation_profiles import client_profiles_root
from src.core.runtime.data import NativeDataSource, SqliteGameStoreDataSource
from src.core.shared_client_settings import SharedSettingsError
from src.i18n import translate_ui_phrase as tr
from src.widgets.localized_dialogs import LocalizedMessageBox as QMessageBox
from src.widgets.scroll_safe_controls import ScrollSafeComboBox
from src.workers.lifecycle import move_worker_to_thread

log = logging.getLogger(__name__)

TITLE = 'Recover drone groups'
FAILURE = 'Recovery could not be completed. Existing profiles were kept.'
CHANGED = 'Saved data changed. Scan again before restoring groups.'
CLOSE_CLIENTS = 'Close EVE clients before recovering drone groups.'
UNAVAILABLE = 'Drone recovery is unavailable for this runtime. It needs a verified local game database.'


def recovery_context(cfg: dict, selection, profiles_root: Path) -> RecoveryContext:
    """Use only the selected provider; never read a Docker project's stale host data."""
    if cfg.get('client_settings_mode') != 'shared':
        raise SharedSettingsError('Select Shared settings and save your settings before recovering drone groups.')
    if selection is None:
        raise SharedSettingsError(UNAVAILABLE)
    scope = installation_group_identity(cfg, selection)
    source = selection.data_source
    world = str(cfg.get('evejs_root', ''))
    if isinstance(source, NativeDataSource) and cfg.get('runtime_backend', 'native') == 'native':
        if os.path.normcase(str(Path(world).resolve())) != os.path.normcase(str(Path(source.evejs_root).resolve())):
            raise SharedSettingsError(UNAVAILABLE)
        database = Path(source.evejs_root) / '_local' / 'gameStore' / 'gamestore.sqlite'
        target = None
    elif isinstance(source, SqliteGameStoreDataSource) and cfg.get('runtime_backend') == 'docker_compose':
        database = source.game_store_path / 'gamestore.sqlite'
        target = selection.target_identity
    else:
        raise SharedSettingsError(UNAVAILABLE)
    storage = client_profiles_root(cfg, profiles_root).parent / 'SharedProfiles'
    roots = [storage, profiles_root.parent / 'SharedProfiles']
    for entry in cfg.get('installation_profiles', ()):
        roots.append(client_profiles_root({'active_installation_profile': entry['id']}, profiles_root).parent / 'SharedProfiles')
    return RecoveryContext(storage, scope, database, world, tuple(dict.fromkeys(roots)), target)


@dataclass(frozen=True)
class RecoveryResult:
    value: object = None
    error: str = ''


class RecoveryWorker(QObject):
    completed = pyqtSignal(object)
    cleanup = pyqtSignal()

    def __init__(self, operation: Callable[[], object]):
        super().__init__()
        self.operation = operation

    @pyqtSlot()
    def run(self):
        from src.core.overview_patch import is_eve_client_running
        try:
            if is_eve_client_running():
                result = RecoveryResult(error=CLOSE_CLIENTS)
            else:
                result = RecoveryResult(self.operation())
        except RecoveryChangedError:
            result = RecoveryResult(error=CHANGED)
        except SharedSettingsError as exc:
            message = CLOSE_CLIENTS if str(exc).startswith('Close EVE clients') else FAILURE
            log.warning('Drone group recovery was blocked: %s', type(exc).__name__)
            result = RecoveryResult(error=message)
        except Exception:
            log.exception('Drone group recovery failed')
            result = RecoveryResult(error=FAILURE)
        self.completed.emit(result)
        self.cleanup.emit()


class RecoveryTableModel(QAbstractTableModel):
    """Lazy rows keep a large roster out of a GUI construction loop."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = ()

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row = self.rows[index.row()]
        return (row.pilot.name, ', '.join(group.name for group in row.plan.groups), str(row.drone_count))[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(('Character', 'Groups', 'Drones to restore')[section])
        return super().headerData(section, orientation, role)


class DroneRecoveryDialog(QDialog):
    """Review one explicit source world, then stage all its verified pilots."""
    def __init__(self, context: RecoveryContext, *, selection_valid=lambda: True, parent=None):
        super().__init__(parent)
        self.context = context
        self.selection_valid = selection_valid
        self.scan_result: RecoveryScan | None = None
        self._thread = None
        self._worker = None
        self._result = None
        self._finished = False
        self._action = ''
        self.setWindowTitle(tr(TITLE))
        self.resize(760, 500)
        layout = QVBoxLayout(self)
        self.help = QLabel(tr('Recovery uses exact saved drone identities and current ownership. It keeps existing assignments and skips conflicting history.'))
        self.help.setWordWrap(True)
        layout.addWidget(self.help)
        destination = QLabel(tr('Destination: {root}').format(root=context.world_root))
        destination.setTextFormat(Qt.TextFormat.PlainText)
        destination.setWordWrap(True)
        layout.addWidget(destination)
        self.sources = ScrollSafeComboBox()
        self.sources.setMinimumContentsLength(24)
        form = QFormLayout()
        form.addRow(tr('Recovery source:'), self.sources)
        layout.addLayout(form)
        self.model = RecoveryTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox()
        self.restore = self.buttons.addButton(tr('Restore groups'), QDialogButtonBox.ButtonRole.ActionRole)
        self.rescan = self.buttons.addButton(tr('Scan again'), QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel = self.buttons.addButton(tr('Close'), QDialogButtonBox.ButtonRole.RejectRole)
        self.restore.clicked.connect(self.apply)
        self.rescan.clicked.connect(self.scan)
        self.buttons.rejected.connect(self.reject)
        self.sources.currentIndexChanged.connect(self._source_changed)
        layout.addWidget(self.buttons)
        self.restore.setEnabled(False)
        QTimer.singleShot(0, self.scan)

    @property
    def busy(self):
        return self._thread is not None

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def _start(self, action, operation):
        if self.busy:
            return
        if not self.selection_valid():
            self.status.setText(tr('This scan belongs to a different installation. Close it and scan again.'))
            self.restore.setEnabled(False)
            return
        self._action = action
        self._result = None
        self._finished = False
        self.status.setText(tr('Scanning saved drone groups…' if action == 'scan' else 'Restoring drone groups…'))
        for widget in (self.restore, self.rescan, self.cancel, self.sources):
            widget.setEnabled(False)
        thread = QThread(self)
        worker = RecoveryWorker(operation)
        move_worker_to_thread(worker, thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._received)
        thread.finished.connect(self._retired, Qt.ConnectionType.QueuedConnection)
        self._thread, self._worker = thread, worker
        thread.start()

    def scan(self):
        if self.busy:
            return
        self.scan_result = None
        self.model.set_rows(())
        self.sources.clear()
        self._start('scan', lambda: scan_recovery(self.context))

    def apply(self):
        scan = self.scan_result
        source = self.sources.currentData()
        if scan is not None and source is not None and self.restore.isEnabled():
            self._start('apply', lambda: apply_recovery(scan, source))

    @pyqtSlot(object)
    def _received(self, result):
        self._result = result
        self._finish()

    @pyqtSlot()
    def _retired(self):
        self._finished = True
        self._finish()

    def _finish(self):
        if self._result is None or not self._finished:
            return
        result = self._result
        thread = self._thread
        self._thread = self._worker = None
        if thread is not None:
            thread.deleteLater()
        self.cancel.setEnabled(True)
        self.rescan.setEnabled(True)
        self.sources.setEnabled(True)
        if not self.selection_valid():
            self.restore.setEnabled(False)
            self.status.setText(tr('This scan belongs to a different installation. Close it and scan again.'))
        elif result.error:
            self.restore.setEnabled(False)
            self.status.setText(tr(result.error))
        elif self._action == 'apply':
            self.scan_result = None
            self.restore.setEnabled(False)
            self.status.setText(tr('Group recovery complete. Launch characters through this launcher to use the recovered memberships.'))
        else:
            self.scan_result = result.value
            for world in result.value.worlds:
                if world.characters:
                    self.sources.addItem(world.source_root, world.source_root)
            self._source_changed()

    def _source_changed(self):
        source = self.sources.currentData()
        world = next((row for row in self.scan_result.worlds if row.source_root == source), None) if self.scan_result else None
        self.model.set_rows(world.characters if world else ())
        self.restore.setEnabled(bool(world and world.characters) and not self.busy)
        if not self.busy:
            message = ('Some characters could not be checked; their profiles will be kept.' if world and self.scan_result.blocked_characters
                       else 'Recovery uses exact saved drone identities and current ownership. It keeps existing assignments and skips conflicting history.' if world
                       else 'No verified missing memberships were found. Current groups were kept.')
            self.status.setText(tr(message))


def open_recovery(window, profiles_root: Path):
    """Retain mutation ownership through scanning, review, writing and teardown."""
    if active_mutations(window) or getattr(window, '_close_in_progress', False):
        QMessageBox.information(window, TITLE, 'Wait for the active operation to finish before recovering drone groups.')
        return
    if window._settings_page.is_dirty():
        QMessageBox.information(window, TITLE, 'Save or discard your settings changes before recovering drone groups.')
        return
    try:
        selection = window._data_selection
        if selection is not None and not window._docker_data_selection_is_current(selection):
            raise SharedSettingsError(UNAVAILABLE)
        context = recovery_context(window._cfg, selection, Path(profiles_root))
    except (SharedSettingsError, ValueError, KeyError) as exc:
        QMessageBox.information(window, TITLE, str(exc) if isinstance(exc, SharedSettingsError) else UNAVAILABLE)
        return
    def selection_valid():
        try:
            current = window._data_selection
            return (current is not None and current.target_identity == selection.target_identity
                    and window._docker_data_selection_is_current(current)
                    and recovery_context(window._cfg, current, Path(profiles_root)) == context)
        except (ValueError, SharedSettingsError, KeyError):
            return False
    window._drone_recovery_request = context
    window._set_operation_controls_busy(True)
    try:
        dialog = DroneRecoveryDialog(context, selection_valid=selection_valid, parent=window)
        dialog.exec()
        dialog.deleteLater()
    finally:
        window._drone_recovery_request = None
        window._set_operation_controls_busy(False)
