"""Explicit disposable frozen Linux update check with synthetic owned processes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

_ENV = 'EVEJS_PRIVATE_LINUX_UPDATE_CHECK'
_SCRIPT = '''const fs=require('fs'),http=require('http');
const [role,ready]=process.argv.slice(2);
const server=http.createServer((q,r)=>r.end('fixture'));
server.listen(0,'127.0.0.1',()=>fs.writeFileSync(ready,JSON.stringify({pid:process.pid,port:server.address().port})));
for(const sig of ['SIGINT','SIGTERM']) process.on(sig,()=>server.close(()=>process.exit(0)));
setTimeout(()=>process.exit(124),150000).unref();
'''


class LinuxUpdateCheck:
    @classmethod
    def from_argv(cls, argv):
        if '--linux-update-check' in argv:
            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument('--linux-update-check', type=Path, required=True)
            parser.add_argument('--linux-update-package', required=True)
            parser.add_argument('--linux-update-expect-rollback', action='store_true')
            args = parser.parse_args(argv)
            return cls(args.linux_update_check, args)
        return cls(Path(os.environ[_ENV]), None) if _ENV in os.environ else None

    def __init__(self, root, args):
        self.root, self.args = root, args
        self.children = []

    def _write(self):
        temporary = self.root / 'check.new'
        temporary.write_text(json.dumps(self.data, indent=2))
        os.replace(temporary, self.root / 'check.json')

    def prepare(self):
        if not getattr(sys, 'frozen', False) or not self.root.is_absolute():
            raise ValueError('Linux update diagnostics require a frozen build and an absolute private directory.')
        for path in (self.root, *self.root.parents):
            if path.exists() and path.is_symlink():
                raise ValueError('Linux update diagnostic storage must not be redirected.')
        if self.args is not None:
            url = urllib.parse.urlsplit(self.args.linux_update_package)
            if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password:
                raise ValueError('Linux diagnostic packages must use loopback HTTP.')
            self.root.mkdir(mode=0o700)
            self.data = {'protocol': 'evejs_linux_packaged_update_v1', 'phase': 'prepared',
                'executable': str(Path(sys.executable).resolve()), 'package': self.args.linux_update_package,
                'expectRollback': self.args.linux_update_expect_rollback, 'desktopControlUsed': False,
                'processesAreSynthetic': True, 'realEveClientExecuted': False}
            (self.root / 'world/server').mkdir(parents=True)
            (self.root / 'world/server/index.js').write_text('// synthetic update fixture\n')
            (self.root / 'fixture.js').write_text(_SCRIPT)
            config = self.root / 'config/evejs-launcher'
            config.mkdir(parents=True)
            (config / 'config.json').write_text(json.dumps({'evejs_root': str(self.root / 'world'),
                'client_path': '', 'runtime_backend': 'native', 'auto_start_server': False,
                'auto_start_market': False, 'update_auto_check': False, 'audio_master_muted': True,
                'audio_music_enabled': False, 'audio_voice_enabled': False, 'close_behavior': 'exit',
                'animations_enabled': False, 'language': 'en'}))
            self._write()
        else:
            self.data = json.loads((self.root / 'check.json').read_bytes())
            if (self.data.get('protocol') != 'evejs_linux_packaged_update_v1'
                    or self.data.get('phase') != 'awaiting_restart'
                    or self.data.get('executable') != str(Path(sys.executable).resolve())
                    or os.environ.get('XDG_CONFIG_HOME') != str(self.root / 'config')):
                raise ValueError('The Linux diagnostic restart context changed.')
        os.environ.update(XDG_CONFIG_HOME=str(self.root / 'config'), XDG_CACHE_HOME=str(self.root / 'cache'),
                          XDG_STATE_HOME=str(self.root / 'state'), QT_QPA_PLATFORM='offscreen')
        os.environ[_ENV] = str(self.root)
        self.watchdog = threading.Timer(120, lambda: self.fail('diagnostic_timeout'))
        self.watchdog.daemon = True
        self.watchdog.start()
        if self.args is not None:
            from src.updater import linux_update
            # Only this explicit diagnostic injects loopback I/O. Production
            # release URL, archive and startup validation still run normally.
            def download(url, destination, limit, progress=None):
                local = self.data['package'] + ('.sha256' if url.endswith('.sha256') else '')
                with urllib.request.urlopen(local, timeout=20) as source, destination.open('xb') as output:
                    count = 0
                    while chunk := source.read(65536):
                        count += len(chunk)
                        if count > limit: raise ValueError('Diagnostic package exceeds its limit.')
                        output.write(chunk)
                        if progress: progress(count, 0)
            linux_update._download = download

    def fail(self, reason):
        self.data.update(phase='failed', error=reason)
        self._write()
        for child in self.children:
            if child.poll() is None:
                child.kill()
        os._exit(1)

    def attach(self, window, app, recovered):
        from PyQt6.QtCore import QTimer
        self.window, self.app, self.recovered = window, app, recovered
        QTimer.singleShot(100, self._origin if self.args is not None else self._resumed)

    def _origin(self):
        from PyQt6.QtCore import QTimer
        from .host import external_process_environment
        from .linux_process import ProtonClientProcess
        from .linux_supervisor import start_supervised_service
        if not self.children:
            node = shutil.which('node')
            if not node: self.fail('node_not_available')
            for role in ('game', 'market'):
                process = start_supervised_service([node, str(self.root / 'fixture.js'), role, str(self.root / (role + '.json'))],
                    cwd=self.root, env=external_process_environment(), log=self.root / (role + '.log'))
                setattr(self.window, '_server_proc' if role == 'game' else '_market_proc', process)
                self.children.append(process)
            executable = self.root / 'wine64'
            try: os.link(node, executable)
            except OSError: shutil.copy2(node, executable)
            eve = self.root / 'exefile.exe'
            eve.write_text('synthetic argument marker only')
            marker = '/settingsprofile=EveJSLauncher_update_check'
            raw = subprocess.Popen([str(executable), str(self.root / 'fixture.js'), 'client', str(self.root / 'client.json'), str(eve), marker],
                env=external_process_environment(), start_new_session=True)
            client = ProtonClientProcess(raw, marker, eve)
            self.children.append(client)
            self.window._tracker.add('update-fixture', 'Update Fixture', client)
        paths = [self.root / (role + '.json') for role in ('game', 'market', 'client')]
        if not all(path.is_file() for path in paths):
            QTimer.singleShot(100, self._origin)
            return
        self.data.update(phase='awaiting_restart', originalLauncherPid=os.getpid(),
                         expected={path.stem: json.loads(path.read_bytes()) for path in paths})
        self._write()
        from src.updater.checker import get_current_version
        self.window._latest_version = get_current_version()
        self.window._latest_download_url = ('https://github.com/V0nCleef/evejs-launcher/releases/download/v'
            + get_current_version() + '/EveJS-Launcher-Linux-x86_64.tar.gz')
        self.window._begin_update_install()

    def _resumed(self):
        from PyQt6.QtCore import QTimer
        if not (self.recovered.game and self.recovered.market and self.recovered.clients == 1):
            self.fail('runtime_ownership_was_not_restored')
        self.children = [self.window._server_proc, self.window._market_proc, *[client.process for client in self.window._tracker.running]]
        actual = {'game': self.window._server_proc.pid, 'market': self.window._market_proc.pid,
                  'client': self.window._tracker.running[0].pid}
        if actual != {role: record['pid'] for role, record in self.data['expected'].items()}:
            self.fail('runtime_pid_changed')
        transactions = sorted((self.root / 'cache/evejs-launcher/updates').glob('transaction-*/result.json'))
        if len(transactions) != 1:
            self.fail('update_transaction_result_missing')
        outcome = json.loads(transactions[0].read_bytes())['state']
        if outcome != ('rolled-back' if self.data['expectRollback'] else 'committed'):
            self.fail('unexpected_transaction_result')
        self.data.update(phase='resumed', updateOutcome=outcome, incomingLauncherPid=os.getpid(), sameProcessPids=True,
                         serviceOwnershipRestored=True, clientOwnershipRestored=True)
        self._write()
        self.window._tracker.kill_all()
        QTimer.singleShot(100, self._stop)

    def _stop(self):
        from PyQt6.QtCore import QTimer
        self.window._tracker.prune_dead()
        if self.window._tracker.running_count:
            QTimer.singleShot(100, self._stop)
            return
        for process in (self.window._server_proc, self.window._market_proc):
            if process.poll() is None: process.terminate()
        QTimer.singleShot(100, self._finish)

    def _finish(self):
        from PyQt6.QtCore import QTimer
        if any(process.poll() is None for process in self.children):
            QTimer.singleShot(100, self._finish)
            return
        self.data.update(phase='passed', stoppedOwnedProcesses=True,
            gameExit=self.window._server_proc.poll(), marketExit=self.window._market_proc.poll(),
            clientExitStatusKnown=False)
        self._write()
        self.window._server_proc = self.window._market_proc = None
        self.watchdog.cancel()
        self.window.close()
