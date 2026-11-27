"""Queue a one-time copy for existing characters on the selected installation."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout,
)

from src.core.application_operations import active_mutations
from src.core.overview_patch import OverviewPatchState, inspect_overview_patch, is_eve_client_running
from src.core.overview_state import OverviewSnapshotRequired, queue_overview_copies
from src.i18n import translate_ui_phrase as tr
from src.widgets.localized_dialogs import LocalizedMessageBox as QMessageBox
from src.workers.overview_patch_worker import OverviewPatchAction

TITLE = "Copy overview once"
CLOSE_CLIENTS = "Close EVE clients before copying their overviews."
SEPARATE = "Save the Separate settings option first so the recipients can keep their own changes."
NO_SNAPSHOT = "Launch the source character once to capture its overview, then close EVE."
PATCH_REQUIRED = "Install the Overview Bridge from Characters → New Character. Restore an older bridge before patching again."
QUEUED = "Overview copy queued. It will apply once when each selected character next logs in."
DOCKER_ROSTER_REQUIRED = "Wait for the launcher to load characters for the selected Docker project."


class OverviewCopyDialog(QDialog):
    def __init__(self, rows, captured, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr(TITLE))
        self.resize(460, 415)
        layout = QVBoxLayout(self)
        explanation = QLabel(tr("Choose a source character and the characters that should receive its overview."))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.source = QComboBox(self)
        self.source.setAccessibleName(tr("Source character"))
        self.recipients = QListWidget(self)
        self.recipients.setAccessibleName(explanation.text())
        self._rows = tuple(rows)
        for character_id, label in rows:
            if character_id in captured:
                self.source.addItem(label, character_id)
        layout.addWidget(self.source)
        layout.addWidget(self.recipients, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._confirm = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._confirm.setText(tr(TITLE))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.source.currentIndexChanged.connect(self._update_recipients)
        self.recipients.itemChanged.connect(self._update_confirm)
        layout.addWidget(buttons)
        self._update_recipients()

    def _update_recipients(self):
        self.recipients.clear()
        for character_id, label in self._rows:
            if character_id != self.source.currentData():
                item = QListWidgetItem(label, self.recipients)
                item.setData(Qt.ItemDataRole.UserRole, character_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
        self._update_confirm()

    def selection(self):
        return self.source.currentData(), tuple(
            entry.data(Qt.ItemDataRole.UserRole)
            for index in range(self.recipients.count())
            if (entry := self.recipients.item(index)).checkState() == Qt.CheckState.Checked
        )

    def _update_confirm(self):
        self._confirm.setEnabled(bool(self.selection()[1]))


def open_overview_copy(window):
    if window._cfg.get("client_settings_mode", "separate") != "separate":
        QMessageBox.information(window, TITLE, SEPARATE)
        return
    if _busy(window):
        QMessageBox.information(window, TITLE, CLOSE_CLIENTS)
        return
    client_path = window._cfg.get("client_path", "")
    patch = inspect_overview_patch(client_path)
    if patch.state is not OverviewPatchState.PATCHED:
        if window._docker_mode() and (patch.can_patch or patch.can_restore):
            action = OverviewPatchAction.PATCH if patch.can_patch else OverviewPatchAction.RESTORE
            window._begin_overview_patch(action, from_settings=True)
        else:
            QMessageBox.information(window, TITLE, PATCH_REQUIRED)
        return
    scope = window._overview_kwargs()
    if scope is None:
        QMessageBox.information(window, TITLE, DOCKER_ROSTER_REQUIRED)
        return
    generation = getattr(window, "_settings_generation", 0)
    target = window._current_observed_docker_target_identity() if window._docker_mode() else None
    rows = _eligible_characters(window)
    captured = window._snapshot_ready_ids()
    if not captured.intersection(character_id for character_id, _ in rows):
        QMessageBox.information(window, TITLE, NO_SNAPSHOT)
        return
    dialog = OverviewCopyDialog(rows, captured, window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    if _busy(window):
        QMessageBox.information(window, TITLE, CLOSE_CLIENTS)
        return
    source, recipients = dialog.selection()
    if (scope != window._overview_kwargs()
            or generation != getattr(window, "_settings_generation", 0)
            or (window._docker_mode() and target != window._current_observed_docker_target_identity())
            or window._cfg.get("client_path", "") != client_path
            or not {source, *recipients} <= {character_id for character_id, _ in _eligible_characters(window)}):
        QMessageBox.information(window, TITLE, "The selected installation or characters changed. Reopen Copy overview once.")
        return
    if window._cfg.get("client_settings_mode", "separate") != "separate":
        QMessageBox.information(window, TITLE, SEPARATE)
        return
    if inspect_overview_patch(client_path).state is not OverviewPatchState.PATCHED:
        QMessageBox.information(window, TITLE, PATCH_REQUIRED)
        return
    try:
        queue_overview_copies(source, recipients, **scope)
    except OverviewSnapshotRequired:
        QMessageBox.warning(window, TITLE, NO_SNAPSHOT)
        return
    except (OSError, ValueError) as exc:
        QMessageBox.warning(window, TITLE, str(exc))
        return
    QMessageBox.information(window, TITLE, QUEUED)


def _eligible_characters(window):
    return [(character.char_id, f"{character.name} ({account.username})")
            for account in window._accounts if not account.banned
            for character in account.characters if character.char_id > 0]


def _busy(window):
    return (
        getattr(window, "_close_in_progress", False) or active_mutations(window)
        or window._lifecycle_active() or window._tracker.running_count or is_eve_client_running()
    )
