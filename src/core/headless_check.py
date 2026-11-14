"""Explicit, isolated offscreen startup/shutdown diagnostics for release checks.

Ordinary launches never enter this path. Each invocation creates a new private
state directory and uses Qt's offscreen platform; it never drives desktop input.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

MODES = ('immediate', 'normal', 'profiles', 'updater', 'wizard', 'roster', 'ownership')


class HeadlessCheck:
    def __init__(self, root: Path, mode: str):
        self.root = root
        self.mode = mode
        self.state = None
        self.started = time.monotonic()
        self.result = {'protocol': 'evejs_headless_check_v1', 'mode': mode,
                       'status': 'starting', 'desktopControlUsed': False}
        self.update_started = threading.Event()
        self.update_release = threading.Event()

    @classmethod
    def from_argv(cls, argv):
        if not any(arg.startswith(('--headless-check', '--check-mode')) for arg in argv):
            return None
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('--headless-check', required=True, type=Path)
        parser.add_argument('--check-mode', choices=MODES, default='immediate')
        args = parser.parse_args(argv)
        if not args.headless_check.is_absolute():
            parser.error('Headless check root must be absolute')
        return cls(args.headless_check, args.check_mode)

    def prepare(self):
        # Refuse redirected storage, and never replace an existing config/report.
        for path in (self.root, *self.root.parents):
            if path.exists() and (path.is_symlink() or path.is_junction()):
                raise ValueError('Headless check storage must not be redirected')
        self.root.mkdir(parents=True, exist_ok=True)
        self.state = self.root / ('run-' + uuid.uuid4().hex)
        self.state.mkdir()
        for name in ('appdata', 'localappdata', 'temp', 'world-one', 'world-two'):
            (self.state / name).mkdir()
        os.environ.update(APPDATA=str(self.state / 'appdata'), LOCALAPPDATA=str(self.state / 'localappdata'),
                          TEMP=str(self.state / 'temp'), TMP=str(self.state / 'temp'), QT_QPA_PLATFORM='offscreen')
        cfg = {'evejs_root': '' if self.mode == 'wizard' else str(self.state / 'world-one'),
               'client_path': '', 'runtime_backend': 'native', 'game_port': 47881,
               'proxy_url': 'http://127.0.0.1:47882', 'auto_start_server': False,
               'auto_start_market': False, 'update_auto_check': False, 'update_check_interval_hours': 0,
               'audio_master_muted': True, 'audio_music_enabled': False, 'audio_voice_enabled': False,
               'animations_enabled': False, 'language': 'en'}
        folder = self.state / 'appdata/EveJS-Launcher'
        folder.mkdir()
        (folder / 'config.json').write_text(json.dumps(cfg), encoding='utf-8')
        if self.mode == 'ownership':
            self._seed_ownership()
        self.result['stateDirectory'] = str(self.state)
        self._write()
        self._saved_excepthook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.fail(kind.__name__)
        self.watchdog = threading.Timer(35, lambda: self.fail('deadline_exceeded', code=124))
        self.watchdog.daemon = True
        self.watchdog.start()
        if self.mode == 'updater':
            self.result['updaterMetadata'] = 'injected-delayed-current-version'
            from src.updater import checker
            def delayed_metadata():
                self.update_started.set()
                self.update_release.wait(10)
                return {'tag_name': checker.get_current_version(), 'assets': []}
            checker.get_latest_release = delayed_metadata

    def _write(self):
        (self.state / 'result.json').write_text(json.dumps(self.result, indent=2), encoding='utf-8')

    def fail(self, reason, code=1):
        self.result.update(status='failed', reason=reason, seconds=round(time.monotonic() - self.started, 3))
        self._write()
        # Only this explicitly isolated diagnostic process exits on a deadline.
        os._exit(code)

    def attach_wizard(self, wizard):
        from PyQt6.QtCore import QTimer
        self.result['wizardConstructed'] = True
        wizard.destroyed.connect(self._wizard_destroyed)
        QTimer.singleShot(0, wizard.reject)

    def _wizard_destroyed(self):
        self.result['wizardRetired'] = True

    def attach(self, window, app):
        from PyQt6.QtCore import QTimer
        from src import config
        if app.platformName() != 'offscreen' or not config.CONFIG_DIR.resolve().is_relative_to(self.state.resolve()):
            self.fail('isolation_failed')
        self.window = window
        self.result.update(platform=app.platformName(), frozen=bool(getattr(sys, 'frozen', False)),
                           mainWindowConstructed=True, mainWindowShownOffscreen=window.isVisible())
        self.shown = time.monotonic()
        if self.mode == 'ownership':
            QTimer.singleShot(0, self._ownership_start)
        elif self.mode == 'roster':
            QTimer.singleShot(0, self._roster)
        elif self.mode == 'profiles':
            QTimer.singleShot(0, self._profiles)
        elif self.mode == 'updater':
            QTimer.singleShot(0, self._start_updater)
        else:
            QTimer.singleShot(1200 if self.mode == 'normal' else 0, self._close)

    def _seed_ownership(self):
        from .local_mod_packages import LocalModPackages
        from .mod_manifest import MANIFEST_FILENAME, scan_mods
        root = self.state / 'world-one'
        folder = root / 'mods/OwnershipExample'
        folder.mkdir(parents=True)
        (folder / 'loader.js.disabled').write_bytes(b'module.exports = {};\n')
        (folder / MANIFEST_FILENAME).write_text(json.dumps({
            'schemaVersion': 3, 'id': 'fixture.ownership', 'displayName': 'Ownership Example',
            'version': '1.0.0', 'kind': 'loader', 'restart': 'game_server',
            'activation': {'strategy': 'loader_rename'},
        }), encoding='utf-8')
        packages = LocalModPackages(root)
        record = packages.adopt(scan_mods(root)[0])
        registry = json.loads(packages.registry_path.read_bytes())
        duplicate = dict(registry['records'][record.record_id], record_id='f' * 32, fingerprint='0' * 64)
        registry['records'][duplicate['record_id']] = duplicate
        self.ownership_original = json.dumps(registry, indent=3).encode('utf-8')
        # Deliberately damaged metadata exists only in this explicit private fixture.
        packages.registry_path.write_bytes(self.ownership_original)
        self.ownership_files = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    def _ownership_start(self):
        from PyQt6.QtCore import QTimer
        page, coordinator = self.window._mods_page, self.window._mod_coordinator
        if coordinator._token is not None:
            QTimer.singleShot(10, self._ownership_start)
            return
        if not page._inventory.registry_conflict:
            self.fail('ownership_conflict_not_detected')
        if (page.register_mods_btn.isHidden() or not page.register_mods_btn.isEnabled()
                or len(page._rows) != 1 or page._rows[0].remove_btn.text() != 'OWNERSHIP'
                or page._rows[0].toggle.isEnabled() or page.apply_btn.isEnabled()):
            self.fail('ownership_warning_or_guard_missing')
        self.result['conflictingActionsBlocked'] = True
        self.ownership_confirmed = self.ownership_notified = False
        self.ownership_dialog_timer = QTimer(self.window)
        self.ownership_dialog_timer.timeout.connect(self._ownership_dialog)
        self.ownership_dialog_timer.start(10)
        page.register_mods_btn.click()
        QTimer.singleShot(10, self._ownership_finish)

    def _ownership_dialog(self):
        from PyQt6.QtWidgets import QApplication, QMessageBox
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        if not isinstance(dialog, QMessageBox):
            self.fail('unexpected_ownership_dialog')
        if dialog.windowTitle() == 'Repair Mod Ownership':
            if 'Verified redundant records: 1' not in dialog.text() or 'mods/ownershipexample' not in dialog.text():
                self.fail('ownership_review_not_concrete')
            self.ownership_confirmed = True
            dialog.button(QMessageBox.StandardButton.Yes).click()
        elif dialog.windowTitle() == 'Mod Ownership Repaired':
            self.ownership_notified = True
            dialog.button(QMessageBox.StandardButton.Ok).click()
        else:
            self.fail('unexpected_ownership_message')

    def _ownership_finish(self):
        from PyQt6.QtCore import QTimer
        from .local_mod_packages import LocalModPackages
        page, coordinator = self.window._mods_page, self.window._mod_coordinator
        if coordinator._token is not None or page._inventory.registry_conflict or not self.ownership_notified:
            QTimer.singleShot(10, self._ownership_finish)
            return
        packages = LocalModPackages(self.state / 'world-one')
        folder = self.state / 'world-one/mods/OwnershipExample'
        backups = list(packages.registry_path.parent.glob('registry.before-duplicate-repair-*.json'))
        if (not self.ownership_confirmed or len(packages.records()) != 1 or len(backups) != 1
                or backups[0].read_bytes() != self.ownership_original
                or self.ownership_files != {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
                or not page.registry_banner.isHidden() or page._rows[0].remove_btn.text() != 'REMOVE'):
            self.fail('ownership_repair_not_preserved')
        self.ownership_dialog_timer.stop()
        self.result.update(registryRepairedAfterConfirmation=True, registryBackupMatchesOriginal=True,
                           packageFilesPreserved=True, ownershipBannerCleared=True)
        self._close()

    def _roster(self):
        """Exercise the real data worker, parser, GUI warning and recovery."""
        import subprocess
        from src.core.runtime.character_display import DISPLAY_SCRIPT
        from src.core.runtime.data import DockerExportDataSource, RuntimeDataSelection
        from src.core.runtime.docker_cli import DockerCommandRunner
        from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord
        from src.core.service_status import DockerControlPolicy, ServiceState
        self.roster_phase = 0
        account = dict(accountId=1, accountName='fixture-account', banned=False, isGM=False)
        exported = dict(accounts=[account], players=[dict(
            characterId='140000001', characterName='Fixture Pilot', **account)])
        second = dict(account, accountId=2, accountName='Fixture-Account')
        exported['accounts'].append(second)
        exported['players'].append(dict(
            characterId='140000002', characterName='Fixture Second', **second))
        def execute(argv, cwd, timeout):
            output = ('invalid JSON' if self.roster_phase == 0 else '{"players":[]}')
            if DISPLAY_SCRIPT not in argv:
                output = json.dumps(exported)
            return subprocess.CompletedProcess(argv, 0, output, '')
        provider = DockerExportDataSource(
            ComposeTarget(self.state/'compose.yaml', self.state, 'fixture-world'),
            ContainerRecord('server', 'fixture-server-1', '0123456789ab',
                            ServiceState.ONLINE, 'healthy', 0, (), True, 'running'),
            DockerCommandRunner(executable='docker.exe', execute=execute),
            'docker:fixture', control_policy=DockerControlPolicy.MANAGED)
        self.window._make_data_selection_factory = lambda: lambda: RuntimeDataSelection(
            provider, None, 'native:fixture-roster')
        self.window._refresh_characters()
        self._check_roster()

    def _check_roster(self):
        from PyQt6.QtCore import QTimer
        window = self.window
        if window._account_thread is not None:
            QTimer.singleShot(10, self._check_roster)
            return
        page = window._characters_page
        if (len(window._accounts) != 2 or len(page._cards) != 2 or window._data_load_error
                or {(a.username, a.account_id) for a in window._accounts}
                != {('fixture-account', 1), ('Fixture-Account', 2)}):
            self.fail('roster_not_preserved')
            return
        if self.roster_phase == 0:
            if not page._data_warning:
                self.fail('roster_warning_missing')
                return
            self.result['validRosterPreservedDuringDisplayFailure'] = True
            self.result['caseDistinctAccountsRetained'] = True
            self.roster_phase = 1
            window._refresh_characters()
            QTimer.singleShot(10, self._check_roster)
        else:
            if page._data_warning:
                self.fail('roster_warning_not_cleared')
                return
            self.result['rosterWarningClearedAfterRecovery'] = True
            self._check_mod_warning_presentation()
            self._close()

    def _check_mod_warning_presentation(self):
        """Check frozen Home presentation and the client guard without Docker."""
        from dataclasses import replace
        from types import SimpleNamespace
        from src.core.runtime.data_diagnostics import DOCKER_MODS_UNAVAILABLE_MESSAGE
        from src.core.service_status import DockerControlPolicy, RuntimeBackend, RuntimeSnapshot, ServiceState
        degraded = RuntimeSnapshot(
            ServiceState.ONLINE, ServiceState.ONLINE, 0,
            backend=RuntimeBackend.DOCKER_COMPOSE,
            docker_control_policy=DockerControlPolicy.MANAGED,
            target_identity='docker:fixture', settings_identity='fixture-settings',
            monitor_generation=7, docker_mod_error=DOCKER_MODS_UNAVAILABLE_MESSAGE)
        home = self.window._home_page
        home.apply_runtime_snapshot(degraded)
        if 'MODS' not in home.overall_status_label.text().upper():
            self.fail('degraded_mods_reported_nominal')
            return
        target = SimpleNamespace(
            _docker_mode=lambda: True, _lifecycle_active=lambda: False,
            _runtime_snapshot=degraded, _monitor_generation=7,
            _docker_monitor_settings_identity=lambda: 'fixture-settings')
        context, reason = type(self.window)._resolve_client_launch_context(target)
        if context is not None or reason != DOCKER_MODS_UNAVAILABLE_MESSAGE:
            self.fail('degraded_mods_authorized_client')
            return
        self.result['degradedModsBlockClientContext'] = True
        home.apply_runtime_snapshot(replace(degraded, docker_mod_error=None))
        if 'NOMINAL' not in home.overall_status_label.text().upper():
            self.fail('home_mod_warning_not_cleared')
            return
        self.result['homeModWarningClearedAfterRecovery'] = True

    def _close(self):
        self.result['closeRequestedAfterShowMs'] = round((time.monotonic() - self.shown) * 1000, 2)
        self.result['closeRequested'] = True
        self._write()
        self.window.close()

    def _idle_then(self, callback):
        from PyQt6.QtCore import QTimer
        if self.window._installation_bar.rejection():
            QTimer.singleShot(20, lambda: self._idle_then(callback))
        else:
            callback()

    def _profiles(self):
        self._idle_then(self._switch_to_second)

    def _switch_to_second(self):
        from src.core.installation_profiles import add_profile
        self.window._cfg = add_profile(self.window._cfg, 'Diagnostic second world',
                                       {'evejs_root': str(self.state / 'world-two')})
        bar = self.window._installation_bar
        bar.refresh()
        # Exercises the actual popup/event filter that previously shadowed window().
        bar.selector.showPopup()
        bar.selector.hidePopup()
        if bar.window() is not self.window:
            self.fail('selector_window_owner')
        self.first = self.window._cfg['active_installation_profile']
        bar.selector.setCurrentIndex(1)
        if self.window._cfg['active_installation_profile'] == self.first:
            self.fail('profile_switch_failed')
        self._idle_then(self._switch_back)

    def _switch_back(self):
        self.window._installation_bar.selector.setCurrentIndex(0)
        if self.window._cfg['active_installation_profile'] != self.first:
            self.fail('profile_return_failed')
        self.result['profileSwitchRoundTrip'] = True
        self._close()

    def _start_updater(self):
        from PyQt6.QtCore import QTimer
        self.window._start_update_checker(self.window._update_checker)
        def wait_started():
            if not self.update_started.is_set():
                QTimer.singleShot(5, wait_started)
                return
            self._close()
            QTimer.singleShot(75, self._verify_deferred_update_close)
        wait_started()

    def _verify_deferred_update_close(self):
        if not self.window.isVisible() or not self.window._has_running_update_checker():
            self.fail('running_updater_was_not_retained')
        self.result['closeWaitedForRunningUpdater'] = True
        self.update_release.set()

    def finish(self, code):
        from PyQt6.QtCore import QThread
        self.watchdog.cancel()
        self.watchdog.join(timeout=1)
        sys.excepthook = self._saved_excepthook
        running = [thread.objectName() or type(thread).__name__ for thread in self.window.findChildren(QThread) if thread.isRunning()]
        if self.watchdog.is_alive():
            running.append('diagnostic-watchdog')
        if code or running or self.window.isVisible() or not self.result.get('closeRequested'):
            self.result.update(status='failed', exitCode=code, runningThreads=running)
            self._write()
            return 1
        self.result.update(status='passed', exitCode=0, runningThreads=[],
                           seconds=round(time.monotonic() - self.started, 3))
        self._write()
        # Do not retain QApplication or its widgets in the process-global error
        # hook/timer cycle until interpreter shutdown. Qt must tear down while
        # the normal main() frame still owns its application and window.
        self.window = None
        self.watchdog = None
        return 0

    def retired(self):
        self.result['mainWindowRetired'] = True
        self._write()
