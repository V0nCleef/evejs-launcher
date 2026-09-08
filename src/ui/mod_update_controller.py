"""Mod update UI orchestration using the existing reserved mod worker."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import time
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QDialog
from src.i18n import translate_ui_phrase as tr
from src.core.mod_update_source import check_update
from src.core.mod_evejs_compatibility import installed_evejs_version
from src.core.mod_updates import install_release, pending_updates, recover_update
from src.core.overview_patch import is_eve_client_running
from src.widgets.localized_dialogs import LocalizedMessageBox as QMessageBox
from src.widgets.mod_update_dialog import ModUpdateDialog


class ModUpdateController(QObject):
    def __init__(self, coordinator):
        super().__init__(coordinator)
        self.host = coordinator
        self.offers = {}
        self.checked = {}
        self.pending = []
        self.checked_roots = set()
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(60_000)
        self.poll_timer.timeout.connect(self.check)
        self.poll_timer.start()

    def attach(self, page):
        page.check_updates_requested.connect(lambda: self.check(manual=True))
        page.update_mod_requested.connect(self.update)
        page.recover_update_requested.connect(self.recover)

    def key(self, mod):
        version = self.host._page._inventory.evejs_version
        return (str(mod.evejs_root), str(mod.path), mod.version, repr(mod.api_descriptor), version)

    def inventory_ready(self):
        page = self.host._page
        page.recover_update_btn.setVisible(page._inventory.update_recovery_pending)
        for row in page._rows:
            self.show_offer(row)
        self.update_badge()
        QTimer.singleShot(0, self.check)

    def show_offer(self, row):
        offer = self.offers.get(self.key(row.mod))
        if offer is None:
            row.update_btn.set_up_to_date()
        else:
            row.update_btn.set_update_available(offer.version)

    def update_badge(self):
        navigation = getattr(getattr(self.host, 'window', None), '_nav', None)
        if navigation is not None:
            count = sum(self.key(mod) in self.offers for mod in self.host._page.mods())
            navigation.set_mod_update_count(count)

    def check(self, *, manual=False):
        host, page = self.host, self.host._page
        if host._token is not None or not page or not page._evejs_root:
            return
        mods = [mod for mod in page.mods() if mod.valid and mod.api_descriptor and mod.api_descriptor.updates]
        # Descriptor contains author dictionaries, so use an immutable repr key.
        due = [mod for mod in mods if manual or time.monotonic() - self.checked.get(self.key(mod), -3601) > 3600]
        if not due and not manual and page._evejs_root in self.checked_roots:
            return
        root = page._evejs_root
        def operation():
            evejs_version = installed_evejs_version(root)
            def one(mod):
                try:
                    return mod, check_update(mod.api_descriptor.updates, mod.version,
                        evejs_version=evejs_version, expected_mod_id=mod.id), ''
                except Exception as exc:
                    return mod, None, str(exc)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(one, due))
            return results, pending_updates(root)
        def present(result):
            if root != page._evejs_root:
                return
            if not result.success:
                if manual:
                    QMessageBox.warning(host.window, tr('Mod update'), tr('Mod updates could not be checked.') + '\n' + result.error)
                return
            results, self.pending = result.value
            self.checked_roots.add(root)
            errors = []
            for mod, offer, error in results:
                key = self.key(mod)
                self.checked[key] = time.monotonic()
                self.offers.pop(key, None)
                if offer:
                    self.offers[key] = offer
                if error:
                    errors.append(mod.name + ': ' + error)
            for row in page._rows:
                self.show_offer(row)
            self.update_badge()
            page.recover_update_btn.setVisible(bool(self.pending))
            if manual and errors:
                QMessageBox.warning(host.window, tr('Mod update'), tr('Mod updates could not be checked.') + '\n' + '\n'.join(errors))
            elif manual and not any(self.key(mod) in self.offers for mod in mods):
                QMessageBox.information(host.window, tr('Mod update'), tr('No mod updates are available.'))
        host.run(operation, present)

    def guard(self, *, client_package=False):
        # No process is stopped automatically. This also catches clients opened
        # outside this launcher; package providers apply their own scoped guards.
        if is_eve_client_running():
            raise RuntimeError('Close EVE clients before updating mods, then retry.')
        if not client_package and self.host.window._native_game_running(fail_closed=True):
            raise RuntimeError('Stop the game server before updating mods, then retry.')
        if not client_package and self.host.window._docker_mode():
            raise RuntimeError('Switch to a stopped Native target to update package files; Docker mod updates are not supported yet.')

    def update(self, mod):
        offer = self.offers.get(self.key(mod))
        if not offer:
            return
        try:
            context = self.host._context(mod)
        except Exception as exc:
            QMessageBox.warning(self.host.window, tr('Mod update'), str(exc))
            return
        dialog = ModUpdateDialog(mod, offer, self.host.window)
        def completed(result):
            self.offers.pop(self.key(mod), None)
            self.checked.pop(self.key(mod), None)
            self.update_badge()
            self.host._refresh_pending = True
            dialog.finish(result)

        def start():
            if dialog.running:
                return
            dialog.begin()
            launched = self.host.run(lambda: install_release(mod, offer, context,
                guard=lambda: self.guard(client_package=bool(mod.api_descriptor and mod.api_descriptor.kind == 'client-package')),
                progress=dialog.progress_received.emit), completed)
            if not launched:
                dialog.running = False
                dialog.reject()

        dialog.start_requested.connect(start)
        dialog.exec()
        dialog.deleteLater()

    def recover(self):
        try:
            context = self.host._context()
        except Exception as exc:
            QMessageBox.warning(self.host.window, tr('Mod update'), str(exc))
            return
        def operation():
            for journal, data in pending_updates(context.evejs_root):
                recover_update(journal, context, guard=lambda: self.guard(client_package=data.get('packageKind') == 'client-package'))
        self.host.run(operation, self.host._operation_result)
