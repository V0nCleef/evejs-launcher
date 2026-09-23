"""Mod-page operations with one captured target and retained worker lifetime."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QThread, Qt, QTimer
from PyQt6 import sip

from src.core.application_operations import active_mutations
from src.core.local_mod_packages import (
    ImportPreview, LocalModPackageError, LocalModPackages,
    RegistryRelocationPreview,
)
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


@dataclass(frozen=True)
class _RegisterBeforeImport:
    root: Path
    source: Path
    source_preview: ImportPreview
    registry_preview: RegistryRelocationPreview
    runtime_signature: tuple[object, ...]


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
        self._guided_import_pending: _RegisterBeforeImport | None = None
        self._guided_step_starting = False
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
        page.registry_relocation_requested.connect(self.register_copied_registry)
        page.set_refresh_handler(self.refresh)

    def refresh(self):
        if self._page is None or getattr(self.window, "_close_in_progress", False):
            return
        if self._guided_import_pending is not None:
            self._refresh_pending = True
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
        if self._guided_import_pending is not None and not self._guided_step_starting:
            QMessageBox.information(
                self.window, "Add Mod",
                "Finish or cancel the selected package import before starting another mod operation.",
            )
            return False
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
            elif self._refresh_pending and self._guided_import_pending is None:
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
        if self._guided_import_pending is not None:
            QMessageBox.information(
                self.window, "Add Mod",
                "Finish or cancel the selected package import before opening another package.",
            )
            return
        if self._token is not None or active_mutations(self.window) or (
            self._page is not None and self._page._lifecycle_busy
        ):
            QMessageBox.information(
                self.window, "Operation In Progress",
                "Wait for the active server or mod operation to finish before adding a mod.",
            )
            return
        try:
            context = self._context(include_client=False)
        except Exception as exc:
            QMessageBox.warning(self.window, "Mod Operation", str(exc))
            return
        root = context.evejs_root.resolve(strict=True)
        runtime_signature = self._import_runtime_signature()
        if kind == "zip":
            source, _filter = QFileDialog.getOpenFileName(self.window, "Add Mod ZIP", "", "ZIP archives (*.zip)")
        else:
            source = QFileDialog.getExistingDirectory(self.window, "Add Mod Folder")
        if not source:
            return
        if not self._import_target_is_current(root, runtime_signature):
            self._import_target_changed()
            return
        try:
            captured_source = Path(source).resolve(strict=True)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self.window, "Add Mod", f"The selected package is no longer available. {exc}")
            return

        def prepare_import():
            packages = LocalModPackages(root)
            source_preview = packages.inspect(captured_source)
            inventory = load_mod_inventory(str(root))
            if inventory.relocation_preview is not None:
                return _RegisterBeforeImport(
                    root,
                    captured_source,
                    source_preview,
                    inventory.relocation_preview,
                    runtime_signature,
                )
            if inventory.registry_error:
                detail = inventory.registry_error
                if "belongs to another EveJS root" in detail:
                    raise LocalModPackageError(
                        "This folder contains mod setup from another EveJS folder, but it cannot be safely registered. "
                        "Refresh Mods and resolve its warning before importing."
                    )
                raise LocalModPackageError(
                    "Resolve the mod recovery warning on the Mods page before importing.\n\n"
                    + detail
                )
            # The destination is already usable. The package is copied by the
            # existing safe importer and remains disabled until the user acts.
            return packages.import_package(captured_source, enabled=False)

        def prepared(result: ModOperationResult) -> None:
            if result.success and isinstance(result.value, _RegisterBeforeImport):
                self._guided_import_pending = result.value
                # Let the preflight worker fully tear down before opening a
                # modal confirmation or starting the registration worker.
                QTimer.singleShot(0, lambda: self._confirm_registration_for_import(result.value))
            else:
                self._operation_result(result)

        self.run(prepare_import, prepared)

    def _import_runtime_signature(self) -> tuple[object, ...]:
        page = self._page
        return (
            bool(self.window._docker_mode()),
            getattr(page, "_runtime_backend", None),
            getattr(page, "_docker_policy", None),
        )

    def _import_target_is_current(
        self,
        root: Path,
        runtime_signature: tuple[object, ...],
    ) -> bool:
        page = self._page
        if (
            getattr(self.window, "_close_in_progress", False)
            or self._token is not None
            or active_mutations(self.window)
        ):
            return False
        if page is not None and (
            page._lifecycle_busy
            or not page._can_mutate()
        ):
            return False
        try:
            configured_root = Path(self.window._cfg["evejs_root"]).resolve(strict=True)
            if configured_root != root:
                return False
            if page is not None and Path(page._evejs_root).resolve(strict=True) != root:
                return False
            return self._import_runtime_signature() == runtime_signature
        except (KeyError, OSError, TypeError, ValueError):
            return False

    def _import_target_changed(self) -> None:
        QMessageBox.information(
            self.window,
            "Add Mod",
            "The selected folder or launcher mode changed while choosing the package. Try adding the mod again.",
        )

    def _release_guided_import(self, *, refresh: bool = False) -> None:
        self._guided_import_pending = None
        if refresh:
            self._refresh_pending = True
        if (
            self._refresh_pending
            and self._token is None
            and not getattr(self.window, "_close_in_progress", False)
        ):
            self._refresh_timer.start(0)

    def _run_guided_step(
        self,
        operation: Callable[[], object],
        callback: Callable[[ModOperationResult], None],
    ) -> bool:
        self._guided_step_starting = True
        try:
            return self.run(operation, callback)
        finally:
            self._guided_step_starting = False

    def _confirm_registration_for_import(self, request: _RegisterBeforeImport) -> None:
        if getattr(self.window, "_close_in_progress", False):
            self._release_guided_import()
            return
        if not self._import_target_is_current(request.root, request.runtime_signature):
            self._import_target_changed()
            self._release_guided_import(refresh=True)
            return
        answer = QMessageBox.question(
            self.window,
            "Register Folder and Add Mod",
            "This EveJS folder looks like it was copied or moved. Register it here so the launcher can recognize mods it manages, then add the selected package disabled.\n\n"
            f"Selected package: {request.source.name}\n"
            f"Previously set up at: {request.registry_preview.saved_root}\n\n"
            "An exact backup will be kept. Existing mod files, settings, and enabled states will stay as they are.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._release_guided_import()
            return
        if getattr(self.window, "_close_in_progress", False):
            self._release_guided_import()
            return
        if not self._import_target_is_current(request.root, request.runtime_signature):
            QMessageBox.information(
                self.window,
                "Add Mod",
                "The selected folder or launcher mode changed. Nothing was changed; try adding the mod again.",
            )
            self._release_guided_import(refresh=True)
            return
        try:
            if request.source.resolve(strict=True) != request.source:
                raise OSError("The selected package path changed.")
        except (OSError, TypeError, ValueError):
            QMessageBox.warning(
                self.window, "Add Mod",
                "The selected package is no longer available. Nothing was changed; select it again.",
            )
            self._release_guided_import()
            return

        def register_then_continue():
            current = load_mod_inventory(str(request.root))
            preview = current.relocation_preview
            if (
                preview is None
                or preview.current_root != request.registry_preview.current_root
                or preview.registry_sha256 != request.registry_preview.registry_sha256
            ):
                raise LocalModPackageError(
                    "The folder's mod setup changed during confirmation. Refresh Mods and try adding the package again."
                )
            packages = LocalModPackages(request.root)
            if packages.inspect(request.source) != request.source_preview:
                raise LocalModPackageError(
                    "The selected package changed during confirmation. Nothing was imported; select it again."
                )
            return packages.relocate_registry(request.registry_preview.registry_sha256)

        def registered(result: ModOperationResult) -> None:
            if not result.success:
                if not getattr(self.window, "_close_in_progress", False):
                    QMessageBox.warning(self.window, "Add Mod", result.error)
                self._release_guided_import(refresh=True)
                return
            if getattr(self.window, "_close_in_progress", False):
                self._release_guided_import()
                return
            # Registration is the user's one confirmed step. Do not interrupt
            # the requested import with a redundant success dialog.
            QTimer.singleShot(0, lambda: self._continue_guided_import(request))

        def start_registration() -> None:
            if getattr(self.window, "_close_in_progress", False):
                self._release_guided_import()
                return
            if not self._import_target_is_current(request.root, request.runtime_signature):
                self._import_target_changed()
                self._release_guided_import(refresh=True)
                return
            if not self._run_guided_step(register_then_continue, registered):
                self._release_guided_import(refresh=True)

        QTimer.singleShot(0, start_registration)

    def _continue_guided_import(self, request: _RegisterBeforeImport) -> None:
        if getattr(self.window, "_close_in_progress", False):
            self._release_guided_import()
            return
        if not self._import_target_is_current(request.root, request.runtime_signature):
            self._import_target_changed()
            self._release_guided_import(refresh=True)
            return
        try:
            if request.source.resolve(strict=True) != request.source:
                raise OSError("The selected package path changed.")
        except (OSError, TypeError, ValueError):
            QMessageBox.warning(
                self.window, "Add Mod",
                "Registration finished, but the selected package is no longer available. Nothing was imported; select it again.",
            )
            self._release_guided_import(refresh=True)
            return

        def import_selected_package():
            inventory = load_mod_inventory(str(request.root))
            if inventory.relocation_preview is not None or inventory.registry_error:
                raise LocalModPackageError(
                    "Folder setup changed after registration. Refresh Mods and try adding the package again."
                )
            packages = LocalModPackages(request.root)
            if packages.inspect(request.source) != request.source_preview:
                raise LocalModPackageError(
                    "The selected package changed during registration. Nothing was imported; select it again."
                )
            return packages.import_package(request.source, enabled=False)

        def imported(result: ModOperationResult) -> None:
            self._release_guided_import()
            self._operation_result(result)

        if not self._run_guided_step(import_selected_package, imported):
            self._release_guided_import(refresh=True)

    def register_copied_registry(self, preview):
        """Explicitly register a validated copied registry to the selected root."""
        page = self._page
        if page is None or preview is None:
            return
        if self._guided_import_pending is not None:
            QMessageBox.information(
                self.window, "Add Mod",
                "Finish or cancel the selected package import before registering mods.",
            )
            return
        if not page._can_mutate():
            QMessageBox.warning(
                self.window, "Register Mods",
                "Connect-only Docker mode cannot change mod or Compose state.",
            )
            return
        if page._lifecycle_busy or self._token is not None or active_mutations(self.window):
            QMessageBox.information(
                self.window, "Operation In Progress",
                "Wait for the active server or mod operation to finish before registering mods.",
            )
            return

        def selected_root():
            configured = self.window._cfg.get("evejs_root") if hasattr(self.window, "_cfg") else None
            if not configured:
                return None
            try:
                root = Path(configured).resolve(strict=True)
                displayed = Path(page._evejs_root).resolve(strict=True)
                expected = Path(preview.current_root).resolve(strict=True)
            except (OSError, TypeError, ValueError):
                return None
            return root if root == displayed == expected else None

        captured_root = selected_root()
        if captured_root is None:
            QMessageBox.warning(
                self.window, "Register Mods",
                "The selected EveJS folder changed or is no longer available. Refresh Mods and review the copied registry again.",
            )
            return
        current_preview = page._inventory.relocation_preview
        if (
            current_preview is None
            or current_preview.current_root != preview.current_root
            or current_preview.registry_sha256 != preview.registry_sha256
        ):
            QMessageBox.warning(
                self.window, "Register Mods",
                "The copied mod registry preview is stale. Refresh Mods and review it again.",
            )
            return

        answer = QMessageBox.question(
            self.window,
            "Register Mods in This Folder",
            "Register mods in this EveJS folder?\n\n"
            "This folder looks like it was copied or moved from another EveJS location.\n"
            f"Previously set up at: {preview.saved_root}\n"
            f"Register here: {captured_root}\n\n"
            "An exact backup will be kept. Existing mod files, settings, and enabled states stay as they are.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # A modal dialog can run a nested event loop. Recheck every captured
        # target and mutation gate immediately before queuing the worker.
        if selected_root() != captured_root:
            QMessageBox.warning(
                self.window, "Register Mods",
                "The selected EveJS folder changed while the confirmation was open. Nothing was changed; refresh Mods and review the registry again.",
            )
            return
        current_preview = page._inventory.relocation_preview
        if (
            current_preview is None
            or current_preview.current_root != preview.current_root
            or current_preview.registry_sha256 != preview.registry_sha256
        ):
            QMessageBox.warning(
                self.window, "Register Mods",
                "The copied mod registry preview changed while the confirmation was open. Nothing was changed; refresh Mods and review it again.",
            )
            return
        if not page._can_mutate():
            QMessageBox.warning(
                self.window, "Register Mods",
                "The current backend is read-only for mod changes. Nothing was changed.",
            )
            return
        if page._lifecycle_busy or self._token is not None or active_mutations(self.window):
            QMessageBox.information(
                self.window, "Operation In Progress",
                "An operation started during confirmation. Nothing was changed; wait for it to finish and try again.",
            )
            return

        def completed(result: ModOperationResult) -> None:
            if selected_root() != captured_root:
                self._refresh_pending = True
                return
            if result.success:
                QMessageBox.information(
                    self.window, "Mods Registered",
                    f"Mods are now registered for this EveJS folder.\n\nBackup: {result.value}",
                )
            else:
                QMessageBox.warning(self.window, "Register Mods", result.error)
            self._refresh_pending = True

        self.run(
            lambda: LocalModPackages(captured_root).relocate_registry(preview.registry_sha256),
            completed,
        )

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
