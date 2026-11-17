"""Verified Linux update preparation and startup acknowledgement."""
from __future__ import annotations

import hashlib
import hmac
import os
import math
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from src.core.host import cache_directory, external_process_environment
from src.core.helpers.linux_update_agent import (
    EXECUTABLE, identity, load_request, private_directory, publish, read_json, running, tree_digest,
)
from .linux_package import PACKAGE_NAME, extract_linux_package, validate_executable
from .tls import update_ssl_context

_active_directory = None


def release_version(url):
    from .github import GITHUB_REPO
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(r'/' + re.escape(GITHUB_REPO) + r'/releases/download/v?(\d+\.\d+\.\d+)/' + re.escape(PACKAGE_NAME), parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or not match):
        raise ValueError('The update URL is not an official Linux launcher release asset.')
    return match.group(1)


def _download(url, destination, limit, progress=None):
    request = urllib.request.Request(url, headers={'User-Agent': 'EveJS-Launcher-Linux'})
    with urllib.request.urlopen(request, context=update_ssl_context(), timeout=30) as source:
        final = urllib.parse.urlsplit(source.geturl())
        if final.scheme != 'https' or final.hostname not in ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
            raise ValueError('The GitHub release download redirected outside its asset hosts.')
        expected = int(source.headers.get('Content-Length', 0))
        if expected > limit:
            raise ValueError('The Linux update download exceeds its size limit.')
        count = 0
        with destination.open('xb') as output:
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    raise ValueError('The Linux update download exceeds its size limit.')
                output.write(chunk)
                if progress:
                    progress(count, expected)
        if expected and expected != count:
            raise ValueError('The Linux update download was incomplete.')


def _reap(process):
    process.wait()


def _prune_transactions(parent, install):
    """Keep one previous completed receipt; active or uncertain transactions stay."""
    completed = []
    for folder in parent.glob('transaction-*'):
        try:
            if not re.fullmatch(r'transaction-[a-z0-9_]{8}', folder.name):
                continue
            private_directory(folder)
            request = read_json(folder / 'request.json')
            result = read_json(folder / 'result.json')
            if (request.get('schema') != 1 or request.get('install') != str(install)
                    or request['parent'].get('uid') != os.getuid()
                    or request['parent'].get('image') != str(install / EXECUTABLE)
                    or type(request.get('created')) not in (int, float) or not math.isfinite(request['created'])
                    or running(request['parent'])
                    or result.get('state') not in ('failed', 'rolled-back', 'committed')):
                continue
            owned = {'agent.py', 'agent.log', 'request.json', 'result.json', 'ready.json', 'candidate.json',
                     'ack.json', 'commit.json', 'cancel.json', 'startup.log', 'rollback.log', 'checksum.txt',
                     'package.tar.gz', 'unpacked'}
            if not {path.name for path in folder.iterdir()}.issubset(owned):
                continue
            ready = folder / 'ready.json'
            candidate = folder / 'candidate.json'
            if ready.exists() and running(read_json(ready)['agent']):
                continue
            if candidate.exists() and running(read_json(candidate)['process']):
                continue
            completed.append((request['created'], folder))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    for _, folder in sorted(completed, reverse=True)[1:]:
        # The root is private and attributed; rmtree does not traverse symlinks.
        shutil.rmtree(folder)


def run_updater(download_url, current_exe_path, progress_callback=None, status_callback=None):
    global _active_directory
    if not getattr(sys, 'frozen', False):
        raise ValueError('Linux self-update requires the installed launcher build; source checkouts are updated with Git.')
    executable = Path(current_exe_path).absolute()
    if executable.name != EXECUTABLE or executable.is_symlink() or executable.resolve() != Path(sys.executable).resolve():
        raise ValueError('The Linux update target is not this launcher executable.')
    validate_executable(executable)
    version = release_version(download_url)
    python = shutil.which('python3')
    if not python or Path(python).resolve().is_relative_to(executable.parent):
        raise ValueError('Linux self-update requires system python3 outside the launcher bundle.')
    parent = cache_directory() / 'updates'
    private_directory(parent)
    _prune_transactions(parent, executable.parent.resolve())
    directory = Path(tempfile.mkdtemp(prefix='transaction-', dir=parent)).resolve()
    process = None
    try:
        if status_callback:
            status_callback('download', 'Downloading the Linux launcher…')
        archive = directory / 'package.tar.gz'
        checksum = directory / 'checksum.txt'
        _download(download_url + '.sha256', checksum, 512)
        match = re.fullmatch(r'([a-fA-F0-9]{64})[ \t]+\*?' + re.escape(PACKAGE_NAME) + r'\s*', checksum.read_text('ascii'))
        if not match:
            raise ValueError('The Linux update checksum record is invalid.')
        _download(download_url, archive, 512 * 1024**2, progress_callback)
        with archive.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if not hmac.compare_digest(actual, match.group(1).lower()):
            raise ValueError('The Linux update checksum does not match the release.')
        if status_callback:
            status_callback('verify', 'Verifying and preparing the Linux launcher…')
        package = extract_linux_package(archive, directory / 'unpacked', expected_version=version)
        request = {'schema': 1, 'install': str(executable.parent.resolve()), 'created': time.time(),
            'parent': identity(os.getpid()), 'token': secrets.token_hex(32), 'version': version,
            'digest': tree_digest(package.directory), 'originalDigest': tree_digest(executable.parent),
            'environment': external_process_environment()}
        publish(directory / 'request.json', request)
        source = Path(__file__).parents[1] / 'core/helpers/linux_update_agent.py'
        script = directory / 'agent.py'
        script.write_bytes(source.read_bytes())
        script.chmod(0o600)
        with (directory / 'agent.log').open('ab') as output:
            process = subprocess.Popen([python, '-I', str(script), str(directory)],
                env=external_process_environment(), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if (directory / 'ready.json').exists():
                ready = read_json(directory / 'ready.json')
                if (ready.get('token') != request['token'] or ready['agent']['pid'] != process.pid
                        or not running(ready['agent'])):
                    raise ValueError('The Linux updater readiness record is invalid.')
                threading.Thread(target=_reap, args=(process,), daemon=True, name='linux-updater-wait').start()
                _active_directory = directory
                return True
            if process.poll() is not None:
                raise ValueError('The Linux updater could not prepare its installation. See its private update log.')
            time.sleep(0.05)
        raise TimeoutError('The Linux updater did not become ready. The current launcher remains open.')
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        # Keep small failure evidence; the package is disposable after failure.
        for path in (directory / 'unpacked',):
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path)
        (directory / 'package.tar.gz').unlink(missing_ok=True)
        raise


def finish_handoff(window):
    """Drain transient CLI/log workers, preserving actual services and EVE clients."""
    from PyQt6.QtCore import QTimer
    from src.core.platform import hard_exit
    from src.core.runtime_handoff import cancel_runtime_handoff
    window._runtime_startup_deferred = True
    window._service_monitor_restart_pending = False
    window._stop_docker_log_stream()
    window._stop_service_monitor()
    window._cancel_background_views_for_close()
    deadline = time.monotonic() + 40
    timer = QTimer(window)
    window._linux_update_drain_timer = timer
    timer.setInterval(50)

    def drain():
        try:
            if _active_directory is None:
                raise ValueError('The Linux updater readiness record is unavailable.')
            ready = read_json(_active_directory / 'ready.json')
            if not running(ready['agent']):
                raise ValueError('The Linux updater exited before the running launcher was ready.')
            pending = (window._background_data_active() or window._service_thread is not None
                       or window._docker_log_thread is not None or window._has_running_update_checker())
            if not pending:
                timer.stop()
                hard_exit(0)
            elif time.monotonic() >= deadline:
                raise TimeoutError('Background launcher workers did not stop in time. The update was canceled.')
        except (OSError, ValueError, TimeoutError):
            timer.stop()
            if _active_directory is not None:
                request = read_json(_active_directory / 'request.json')
                publish(_active_directory / 'cancel.json', {'token': request['token']})
            cancel_runtime_handoff(getattr(window, '_update_runtime_ticket', None))
            window._update_runtime_ticket = None
            window._runtime_startup_deferred = False
            window._update_handoff_pending = False
            window._set_operation_controls_busy(False)
            window._schedule_service_monitor_start()
            window._refresh_characters()
            dialog = window._update_progress_dialog
            if dialog is not None:
                dialog.show_error('The launcher could not finish its update handoff. It is still running; try again.')

    timer.timeout.connect(drain)
    timer.start()


class StartupHandshake:
    """No runtime checkpoint or background worker starts until the agent commits."""
    def __init__(self, request_path):
        self.directory = Path(request_path).absolute().parent
        root = (cache_directory() / 'updates').resolve()
        if (Path(request_path).name != 'request.json' or self.directory.is_symlink()
                or self.directory.parent != root or not self.directory.name.startswith('transaction-')):
            raise ValueError('The Linux startup request is outside the private update directory.')
        self.request = load_request(self.directory)
        from .checker import get_current_version
        if (Path(sys.executable).absolute() != Path(self.request['install']) / EXECUTABLE
                or get_current_version() != self.request['version']):
            raise ValueError('This launcher does not match the Linux update startup request.')
        ready = read_json(self.directory / 'ready.json')
        if ready.get('token') != self.request['token'] or not running(ready['agent']):
            raise ValueError('The Linux update agent is unavailable.')

    def attach(self, window, application, on_commit):
        from PyQt6.QtCore import QTimer
        from src.core.platform import hard_exit
        self.timer = QTimer(window)
        self.timer.setInterval(50)
        self.deadline = time.monotonic() + 55
        pin = identity(os.getpid())

        def check():
            try:
                candidate = self.directory / 'candidate.json'
                if not candidate.exists():
                    if time.monotonic() >= self.deadline:
                        raise TimeoutError('The Linux update agent did not identify this build.')
                    return
                expected = {'token': self.request['token'], 'process': pin}
                if read_json(candidate) != expected:
                    raise ValueError('The Linux update candidate process identity changed.')
                if not (self.directory / 'ack.json').exists():
                    publish(self.directory / 'ack.json', expected)
                commit = self.directory / 'commit.json'
                if commit.exists() and read_json(commit) == expected:
                    self.timer.stop()
                    # Stop treating this as provisional before scheduling work.
                    on_commit()
                elif time.monotonic() >= self.deadline:
                    raise TimeoutError('The Linux update was not committed.')
            except (OSError, ValueError, TimeoutError):
                self.timer.stop()
                hard_exit(1)  # Deferred startup has not created or adopted any runtime.

        self.timer.timeout.connect(check)
        self.timer.start()
