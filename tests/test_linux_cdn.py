"""An interrupted client recipe cannot leave a startable half-changed profile."""
from pathlib import Path

import pytest

from test_linux_installation import linux_installation
from src.core import linux_cdn, linux_maintenance, linux_process
from src.core.linux_installation import LinuxInstallation, LinuxSetupError, file_sha256, read_json


@pytest.fixture
def cdn_fixture(linux_installation, monkeypatch):
    installation = linux_installation
    helper = installation.root / '.evejs-linux-tools/evejs-linux-client-preflight.py'
    helper.parent.mkdir()
    helper.write_bytes(b'fixture pinned helper')
    monkeypatch.setitem(linux_maintenance.SETUP_TOOL_HASHES, helper.name, file_sha256(helper))
    monkeypatch.setattr(linux_cdn.shutil, 'which', lambda name: '/usr/bin/python3')
    sdk = installation.client / 'bin64/launchdarkly_stackless_client_sdk.pyd'
    sdk.write_bytes(b'original sdk')
    exe = installation.client / 'bin64/exefile.exe'
    before = {path: path.read_bytes() for path in (installation.root / '.evejs-linux.conf', sdk, exe)}
    return installation, sdk, exe, before


def test_failed_recipe_restores_exact_preimages(cdn_fixture, monkeypatch):
    installation, sdk, exe, before = cdn_fixture
    def failing_recipe(command, **kwargs):
        sdk.write_bytes(b'changed sdk')
        exe.write_bytes(b'changed executable')
        raise RuntimeError('fixture recipe failure')
    monkeypatch.setattr(linux_process, 'run_bounded', failing_recipe)
    with pytest.raises(LinuxSetupError, match='were restored'):
        linux_cdn.prepare_unprivileged_cdn(installation.root)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert LinuxInstallation.load(installation.root).cdn_port == 443


def test_crash_blocks_startup_until_explicit_recovery(cdn_fixture, monkeypatch):
    installation, sdk, exe, before = cdn_fixture
    def crash(command, **kwargs):
        sdk.write_bytes(b'partially changed')
        raise SystemExit('simulated process loss')
    monkeypatch.setattr(linux_process, 'run_bounded', crash)
    with pytest.raises(SystemExit):
        linux_cdn.prepare_unprivileged_cdn(installation.root)
    with pytest.raises(LinuxSetupError, match='interrupted'):
        LinuxInstallation.load(installation.root)
    linux_cdn.recover_cdn_profile(installation.root)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert LinuxInstallation.load(installation.root).cdn_port == 443


def test_success_pairs_client_recipe_and_configuration(cdn_fixture, monkeypatch):
    installation, sdk, exe, before = cdn_fixture
    calls = []
    def recipe(command, **kwargs):
        calls.append(command.copy())
        if command[2] == 'apply':
            sdk.write_bytes(b'verified 8443 sdk')
        return 'verified profile'
    monkeypatch.setattr(linux_process, 'run_bounded', recipe)
    assert 'without changing host port policy' in linux_cdn.prepare_unprivileged_cdn(installation.root)
    assert LinuxInstallation.load(installation.root).cdn_port == 8443
    assert [command[2] for command in calls] == ['apply', 'check']
    assert all(command[-2:] == ['--cdn-port', '8443'] for command in calls)
    assert read_json(installation.root / linux_cdn.JOURNAL / 'manifest.json')['phase'] == 'applied'


def test_changed_rollback_is_detected_before_any_restore(cdn_fixture, monkeypatch):
    installation, sdk, exe, _ = cdn_fixture
    def crash(*args, **kwargs):
        sdk.write_bytes(b'partially changed')
        raise SystemExit()
    monkeypatch.setattr(linux_process, 'run_bounded', crash)
    with pytest.raises(SystemExit):
        linux_cdn.prepare_unprivileged_cdn(installation.root)
    (installation.root / linux_cdn.JOURNAL / 'exefile.exe').write_bytes(b'corrupted rollback')
    with pytest.raises(LinuxSetupError, match='changed'):
        linux_cdn.recover_cdn_profile(installation.root)
    assert sdk.read_bytes() == b'partially changed'
