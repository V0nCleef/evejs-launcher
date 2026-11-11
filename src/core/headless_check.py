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

MODES = ('immediate', 'normal', 'profiles', 'updater', 'wizard')


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
        if self.mode == 'profiles':
            QTimer.singleShot(0, self._profiles)
        elif self.mode == 'updater':
            QTimer.singleShot(0, self._start_updater)
        else:
            QTimer.singleShot(1200 if self.mode == 'normal' else 0, self._close)

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
