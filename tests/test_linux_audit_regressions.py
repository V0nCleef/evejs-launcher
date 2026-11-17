"""Backend authorization, unchanged-client cache and bounded helper retirement."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_linux_installation import linux_installation
from src.core import linux_client
from src.core.linux_installation import LinuxSetupError, file_sha256

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux launch/backend boundaries')


@pytest.fixture
def checked_client(linux_installation, monkeypatch):
    installation = linux_installation
    helper = installation.root / '.evejs-linux-tools/evejs-linux-client-preflight.py'
    helper.parent.mkdir()
    helper.write_text('fixture exact checked helper')
    monkeypatch.setattr(linux_client, '_PREFLIGHT_SHA256', file_sha256(helper))
    recipes = installation.root / 'tools/ClientSETUP'
    recipes.mkdir(parents=True)
    for name, target in (
        ('launchdarkly_patch_recipe.json', 'launchdarkly_stackless_client_sdk.pyd'),
        ('launchdarkly_localhost_upgrade_recipe.json', 'launchdarkly_stackless_client_sdk.pyd'),
        ('crash_upload_patch_recipe.json', 'exefile.exe'),
    ):
        (recipes / name).write_text(json.dumps({'source': {'filename': target}}))
    pyd = installation.client / 'bin64/launchdarkly_stackless_client_sdk.pyd'
    pyd.write_bytes(b'prepared fixture pyd')
    expected = {path: path.read_bytes() for path in (pyd, installation.client / 'bin64/exefile.exe')}
    calls = []
    def check(*args, **kwargs):
        calls.append(args)
        if any(path.read_bytes() != content for path, content in expected.items()):
            raise RuntimeError('exact client patch gate rejected changed binary')
    monkeypatch.setattr(linux_client, 'run_bounded', check)
    linux_client.require_client_patches(installation)
    assert len(calls) == 1
    return installation, calls


@pytest.mark.parametrize('name', ['exefile.exe', 'launchdarkly_stackless_client_sdk.pyd'])
def test_actual_checked_binary_change_invalidates_client_cache(checked_client, name):
    installation, calls = checked_client
    (installation.client / 'bin64' / name).write_bytes(b'changed invalid binary')
    with pytest.raises(RuntimeError, match='rejected'):
        linux_client.require_client_patches(installation)
    assert len(calls) == 2


def test_unchanged_multibox_cache_allows_endpoint_only_ini_edits(checked_client):
    installation, calls = checked_client
    ini = installation.client / 'start.ini'
    ini.write_text(ini.read_text() + 'port=28000\n')
    linux_client.require_client_patches(installation)
    assert len(calls) == 1  # No stopped-client helper while unchanged EVE clients may run.
    ini.write_text(ini.read_text().replace('3396210', '12345'))
    with pytest.raises(LinuxSetupError, match='identity'):
        linux_client.require_client_patches(installation)


@pytest.mark.parametrize('backend,policy', [('docker_compose', 'connect_only'), ('docker_compose', 'managed'),
                                          ('docker_compose', 'invalid'), ('invalid', 'managed')])
def test_central_docker_entry_cannot_construct_native_maintenance(qapp, monkeypatch, backend, policy):
    from src import app
    from src.widgets import linux_maintenance_dialog
    notices = []
    monkeypatch.setattr(app.QMessageBox, 'information', lambda *args: notices.append(args[1]))
    def forbidden(*args):
        raise AssertionError('Docker must not construct the Native mutating dialog')
    monkeypatch.setattr(linux_maintenance_dialog, 'LinuxMaintenanceDialog', forbidden)
    owner = SimpleNamespace(_cfg={'runtime_backend': backend, 'docker_control_policy': policy})
    app.MainWindow._open_linux_maintenance(owner)
    assert notices == ['Native maintenance unavailable']


def test_native_maintenance_still_opens_for_native_backend(qapp, tmp_path, monkeypatch):
    from src import app
    from src.widgets import linux_maintenance_dialog
    calls, busy = [], []
    class Dialog:
        def __init__(self, root, owner): calls.append(('dialog', root))
        def exec(self): calls.append(('exec',))
        def deleteLater(self): calls.append(('deleted',))
    monkeypatch.setattr(linux_maintenance_dialog, 'LinuxMaintenanceDialog', Dialog)
    owner = SimpleNamespace(_cfg={'runtime_backend': 'native', 'evejs_root': str(tmp_path)},
        _close_in_progress=False, _set_operation_controls_busy=busy.append, _refresh_characters=lambda: None)
    app.MainWindow._open_linux_maintenance(owner)
    assert calls == [('dialog', str(tmp_path)), ('exec',), ('deleted',)] and busy == [True, False]


def test_linux_settings_disables_maintenance_under_docker(qapp, tmp_path, monkeypatch):
    from src import config
    from src.pages.settings_page import SettingsPage
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config, 'CONFIG_FILE', tmp_path / 'config.json')
    page = SettingsPage()
    try:
        page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData('docker_compose'))
        assert not page.linux_maintenance_button.isEnabled()
        page.runtime_backend_combo.setCurrentIndex(page.runtime_backend_combo.findData('native'))
        assert page.linux_maintenance_button.isEnabled()
    finally:
        page.close(); page.deleteLater()


def test_completed_and_timed_out_helpers_retire_only_their_ownership(tmp_path):
    from src.core.linux_process import OwnedServiceProcess, _OWNED
    from src.core.runtime.docker_cli import DockerCommandRunner, DockerCommandError
    live = OwnedServiceProcess(subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True))
    try:
        initial = dict(_OWNED)
        runner = DockerCommandRunner(executable=sys.executable)
        for command in ('pass', 'raise SystemExit(2)', 'import time;time.sleep(30)'):
            try:
                runner.run(('-c', command), cwd=tmp_path, timeout=.2)
            except DockerCommandError:
                pass
            assert dict(_OWNED) == initial
            assert live.poll() is None
    finally:
        live.kill(); live.wait(timeout=5)
    assert _OWNED.get(live.pid) is not live
