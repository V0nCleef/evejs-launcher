"""Mod-page operations with one captured target and retained worker lifetime."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QThread, Qt, QTimer
from PyQt6 import sip

from src.core.application_operations import active_mutations
from src.core.local_mod_packages import LocalModPackages
from src.core.mod_contributions import ContributionStore, ConfigurationReview, ContributionConflict
from src.core.mod_inventory import ModInventory, load_mod_inventory, folder_key
from src.core.mod_operations import ModOperationContext, change_mod_state, remove_local_mod, run_public_action
from src.core.mod_manifest import ActivationKind
from src.core.mod_settings import ModSettingsContext, ModSettingsSession, profile_identity
from src.core.mod_settings_schema import parse_settings_schema
from src.core.platform import get_eve_settings_path
from src.core.profiles import PROFILES_ROOT
from src.i18n import translate_ui_phrase, format_ui_phrase
from src.widgets.localized_dialogs import LocalizedInputDialog as QInputDialog, LocalizedMessageBox as QMessageBox, LocalizedFileDialog as QFileDialog
from src.widgets.mod_settings_dialog import ModSettingsDialog
from src.widgets.mod_conflict_dialog import ModConflictDialog
from src.workers.mod_operation_worker import ModOperationResult, ModOperationWorker

log = logging.getLogger(__name__)


class ModCoordinator(QObject):
    """Own mod work until both its result and thread teardown have arrived."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._thread = self._worker = self._token = self._result = None
        self._finished = False
        self._presenting = False
        self._callback = None
        self._dialog: ModSettingsDialog | None = None
        self._page = None
        self._refresh_pending = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        from .mod_update_controller import ModUpdateController
        self._updates = ModUpdateController(self)

    def attach(self, page):
        self._page = page
        self._updates.attach(page)
        page.configure_mod_requested.connect(self.configure)
        page.import_requested.connect(self.import_package)
        page.undo_requested.connect(self.undo_removal)
        page.recover_requested.connect(self.recover_packages)
        page.activation_requested.connect(self.activate)
        page.move_requested.connect(self.move)
        page.helper_requested.connect(self.public_action)
        page.remove_mod_requested.connect(self.remove)
        page.set_refresh_handler(self.refresh)

    def refresh(self):
        if self._page is None or getattr(self.window, "_close_in_progress", False):
            return
        if not self._page._evejs_root.strip():
            self._page.show_inventory(ModInventory(self._page._evejs_root))
            self._updates.inventory_ready()
            return
        if self._token is not None or active_mutations(self.window):
            self._refresh_pending = True
            self._refresh_timer.start(500)
            return
        self._refresh_pending = False
        root = self._page._evejs_root
        def present(result):
            if root != self._page._evejs_root:
                self._refresh_pending = True
            elif result.success:
                self._page.show_inventory(result.value)
                self._updates.inventory_ready()
            else:
                self._page.operation_notice.setText(result.error)
                self._page.operation_notice.show()
        self.run(lambda: load_mod_inventory(root), present)

    def run(self, operation: Callable[[], object], callback: Callable[[ModOperationResult], None]) -> bool:
        if self._token is not None or active_mutations(self.window) or getattr(self.window, "_close_in_progress", False):
            QMessageBox.information(self.window, "Operation In Progress", "Wait for the active operation to finish before changing mods.")
            return False
        token = object()
        thread = QThread(self)
        worker = ModOperationWorker(token, operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._completed)
        worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(self._thread_finished, Qt.ConnectionType.QueuedConnection)
        self._token, self._thread, self._worker = token, thread, worker
        self._callback, self._result, self._finished = callback, None, False
        self.window._mod_operation_request = token
        self.window._mod_operation_thread = thread
        self.window._set_operation_controls_busy(True)
        try:
            thread.start()
        except Exception as exc:
            if thread.isRunning():
                log.exception("Mod thread start raised after the thread became active")
                return True
            sip.delete(worker)
            self._result = ModOperationResult(token, False, error=str(exc))
            self._finished = True
            self._finish()
        return True

    def _completed(self, result):
        if isinstance(result, ModOperationResult) and result.token is self._token and self._result is None:
            self._result = result
            self._finish()

    def _thread_finished(self):
        self._finished = True
        if self._result is None:
            # Let an already queued terminal signal arrive before diagnosing it.
            QTimer.singleShot(0, self._missing_result)
        self._finish()

    def _missing_result(self):
        if self._token is not None and self._finished and self._result is None:
            self._result = ModOperationResult(self._token, False, error="The mod operation ended without a result. Refresh before retrying.")
            self._finish()

    def _finish(self):
        if self._presenting or not self._finished or self._result is None or self._token is None:
            return
        self._presenting = True
        result, callback, thread = self._result, self._callback, self._thread
        # Keep the shared operation reservation through any modal result UI.
        self._callback = None
        try:
            if callback is not None:
                callback(result)
        except Exception:
            log.exception("Could not present a completed mod operation")
        finally:
            self._token = self._thread = self._worker = self._result = None
            self._finished = False
            self._presenting = False
            self.window._mod_operation_request = None
            self.window._mod_operation_thread = None
            thread.deleteLater()
            self.window._set_operation_controls_busy(False)
            if getattr(self.window, "_close_in_progress", False):
                QTimer.singleShot(0, self.window.close)
            elif self._refresh_pending:
                self._refresh_timer.start(0)

    def _context(self, mod=None, *, include_client=True) -> ModOperationContext:
        if self._page is not None and not self._page._can_mutate():
            raise RuntimeError("Connect-only Docker mode cannot change mod or Compose state.")
        cfg = self.window._cfg
        root = Path(cfg["evejs_root"]).resolve(strict=True)
        client = Path(cfg["client_path"]).resolve(strict=True) if include_client and cfg.get("client_path") else None
        backend = "docker" if self.window._docker_mode() else "native"
        context = ModOperationContext(root, client, backend)
        if mod is not None:
            context.capture(mod)
        return context

    def _operation_result(self, result):
        self._refresh_pending = True
        if not result.success and isinstance(result.value, ConfigurationReview):
            retry = self._worker.operation
            dialog = ModConflictDialog(result.value, self.window)
            dialog.exec()
            plan = dialog.selected_plan
            dialog.deleteLater()
            if result.value.kind == "helper" and plan == result.value.preserve:
                # Keeping the existing files declines this proposed activation.
                # Retrying here would immediately show the same conflict again.
                return
            if plan is not None:
                def resume():
                    def commit_and_retry():
                        store = ContributionStore(plan.storage_root, allowed_roots={file.target.allowed_root for file in plan.files})
                        store.commit(plan)
                        return retry()
                    self.run(commit_and_retry, self._operation_result)
                QTimer.singleShot(0, resume)
            return
        if not result.success:
            QMessageBox.warning(self.window, "Mod Operation", result.error)
        elif hasattr(result.value, "state") and result.value.state != "ready":
            QMessageBox.information(self.window, "Mod Operation", result.value.message)
        elif hasattr(result.value, "message") and result.value.message:
            QMessageBox.information(self.window, "Mod Operation", result.value.message)

    def activate(self, mod, desired):
        try:
            context = self._context(mod)
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        self.run(lambda: change_mod_state(mod, desired, context), self._operation_result)

    def public_action(self, mod, action):
        try:
            context = self._context(mod)
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        operation = (lambda: change_mod_state(mod, True, context)) if action == "install" else (lambda: run_public_action(mod, action, context))
        self.run(operation, self._operation_result)

    def import_package(self, kind):
        try:
            context = self._context(include_client=False)
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        if kind == "zip":
            source, _filter = QFileDialog.getOpenFileName(self.window, "Add Mod ZIP", "", "ZIP archives (*.zip)")
        else:
            source = QFileDialog.getExistingDirectory(self.window, "Add Mod Folder")
        if not source:
            return
        # Imports never execute the package, and start disabled. The backend
        # displays installer-only archives as unsupported instead of guessing.
        self.run(lambda: LocalModPackages(context.evejs_root).import_package(source), self._operation_result)

    def move(self, mod, amount):
        try:
            context = self._context(mod, include_client=False)
            mods = self._page.mods()
            index = next(i for i, item in enumerate(mods) if folder_key(item) == folder_key(mod))
            others = [i for i, item in enumerate(mods) if item.activation_kind is ActivationKind.LOADER_RENAME]
            position = others.index(index)
            destination = position + amount
            if not 0 <= destination < len(others):
                return
            other = others[destination]
            mods[index], mods[other] = mods[other], mods[index]
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        self.run(lambda: LocalModPackages(context.evejs_root).set_order(mods), self._operation_result)

    def remove(self, mod):
        if self._page is None or folder_key(mod) not in self._page._inventory.local_removable:
            self.window._on_mod_remove_requested(mod)
            return
        try:
            context = self._context(mod)
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        if QMessageBox.question(
            self.window, "Remove Mod",
            format_ui_phrase("Remove {mod}? Its folder will be kept for Undo Removal. Private settings are kept; shared changes are restored where ownership is recorded.", mod=mod.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        def remove_when_stopped():
            if mod.activation_kind in {ActivationKind.LOADER_RENAME, ActivationKind.JSON_BOOLEAN} and self.window._native_game_running(fail_closed=True):
                raise RuntimeError("Stop the game server before removing this mod, then try again.")
            return remove_local_mod(mod, context)
        self.run(remove_when_stopped, self._operation_result)

    def undo_removal(self):
        try:
            context = self._context(include_client=False)
            records = self._page._inventory.quarantined
            if not records:
                return
            labels = [record.relative_path for record in records]
            selected, accepted = QInputDialog.getItem(self.window, "Undo Removal", "Choose a removed mod", labels, 0, False)
            if not accepted:
                return
            record_id = records[labels.index(selected)].record_id
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        self.run(lambda: LocalModPackages(context.evejs_root).restore(record_id), self._operation_result)

    def recover_packages(self):
        try:
            context = self._context(include_client=False)
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        self.run(lambda: LocalModPackages(context.evejs_root).recover_pending(), self._operation_result)

    def configure(self, mod):
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        if not getattr(mod, "settings_schema", None):
            return
        try:
            if self._page is not None:
                self._context(mod, include_client=False)
            schema = parse_settings_schema(mod.settings_schema)
            scopes = sorted({field.scope for field in schema.fields})
            scope = scopes[0]
            if len(scopes) > 1:
                labels = [translate_ui_phrase("Global settings"), translate_ui_phrase("Profile settings")]
                chosen, accepted = QInputDialog.getItem(self.window, "Mod Settings", "Settings scope", labels, 0, False)
                if not accepted:
                    return
                scope = scopes[labels.index(chosen)]
            username = ""
            profile_root = settings_root = settings_anchor = None
            scope_label = translate_ui_phrase("Global settings")
            if scope == "profile":
                names = sorted({account.username for account in getattr(self.window, "_accounts", ()) if account.username})
                if not names:
                    raise RuntimeError("No profiles are available for this EveJS installation.")
                username, accepted = QInputDialog.getItem(self.window, "Mod Settings", "Choose a profile", names, 0, False)
                if not accepted:
                    return
                profile_root = PROFILES_ROOT / username
                if profile_root.parent != PROFILES_ROOT:
                    raise RuntimeError("The selected profile path is invalid.")
                if any(file.base == "profile_settings" for file in schema.files):
                    settings_root = get_eve_settings_path(str(profile_root / "tq"))
                    settings_anchor = Path(os.environ["LOCALAPPDATA"])
                scope_label = format_ui_phrase("Profile: {profile}", profile=username)
            cfg = self.window._cfg
            client_settings = any(file.base not in {"mod", "evejs"} for file in schema.files)
            context = ModSettingsContext(
                Path(cfg["evejs_root"]), mod.path,
                Path(cfg["client_path"]) if client_settings and cfg.get("client_path") else None,
                profile_identity(profile_root) if profile_root is not None else "", profile_root, settings_root,
                profile_settings_storage_root=settings_anchor,
            )
            if mod.evejs_root is not None and context.evejs_root != mod.evejs_root.resolve():
                raise RuntimeError("The selected EveJS installation changed. Refresh Mods and try again.")
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Settings", str(exc))
            return

        def present(result):
            if getattr(self.window, "_close_in_progress", False):
                return
            if not result.success:
                QMessageBox.warning(self.window, "Mod Settings", result.error)
                return
            session = result.value
            dialog = ModSettingsDialog(mod.name, session.fields, session.values, scope_label=scope_label, parent=self.window)
            self._dialog = dialog

            def save(values):
                current = dialog.property("settingsSession")
                dialog.set_saving(True)

                def saved(outcome):
                    if outcome.success and isinstance(outcome.value, ConfigurationReview):
                        review = outcome.value
                        chooser = ModConflictDialog(review, dialog)
                        chooser.exec()
                        plan = chooser.selected_plan
                        chooser.deleteLater()
                        if plan is None:
                            dialog.set_saving(False)
                        else:
                            def apply_choice():
                                if not self.run(lambda: current.apply_review(review, plan), saved):
                                    dialog.show_error(translate_ui_phrase("Wait for the active operation to finish before changing mods."))
                            QTimer.singleShot(0, apply_choice)
                    elif outcome.success:
                        dialog.setProperty("settingsSession", outcome.value)
                        dialog.mark_saved(outcome.value.values)
                    else:
                        dialog.show_error(outcome.error)

                def save_or_review():
                    try:
                        return current.save(values)
                    except ContributionConflict:
                        return current.review_save(values)
                if not self.run(save_or_review, saved):
                    dialog.show_error(translate_ui_phrase("Wait for the active operation to finish before changing mods."))

            dialog.setProperty("settingsSession", session)
            dialog.save_requested.connect(save)
            dialog.finished.connect(lambda _code: self._release_dialog(dialog))
            dialog.show()

        self.run(lambda: ModSettingsSession.open(context, mod.settings_schema, scope=scope, manifest_path=mod.manifest_path), present)

    def _release_dialog(self, dialog):
        if self._dialog is dialog:
            self._dialog = None
        dialog.deleteLater()

    def close_settings(self) -> bool:
        """Let the form protect its draft before the parent window closes."""
        dialog = self._dialog
        if dialog is None:
            return True
        dialog.reject()
        return self._dialog is None
