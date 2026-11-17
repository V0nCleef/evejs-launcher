"""Closed maintenance commands and recovery without a healthy client runtime."""
import dataclasses
from pathlib import Path
import sys

import pytest

from test_linux_installation import linux_installation
from src.core import linux_maintenance as maintenance
from src.core.linux_installation import LinuxSetupError, file_sha256


@pytest.fixture
def maintenance_root(linux_installation, monkeypatch):
    tools = linux_installation.root / '.evejs-linux-tools'
    tools.mkdir()
    updater = tools / 'evejs-linux-update.py'
    compat = tools / 'evejs-linux-lu-compat.py'
    updater.write_text('verified updater fixture')
    compat.write_text('verified compatibility fixture')
    monkeypatch.setattr(maintenance, 'UPDATER_SHA256', file_sha256(updater))
    monkeypatch.setitem(maintenance.SETUP_TOOL_HASHES, 'evejs-linux-lu-compat.py', file_sha256(compat))
    monkeypatch.setattr(maintenance.shutil, 'which', lambda name: sys.executable)
    return linux_installation.root


def test_backup_inventory_does_not_require_healthy_client_or_version(maintenance_root):
    (maintenance_root / 'config/version.json').write_text('broken version metadata')
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.LIST_BACKUPS, maintenance_root)
    command, root = maintenance.maintenance_command(request)
    assert command[-1] == 'list'
    assert command[-3:-1] == ['--repo', str(root)]
    assert not request.mutates


@pytest.mark.parametrize('action,tail', [
    (maintenance.LinuxMaintenanceAction.RESTORE, ['restore', '--yes']),
    (maintenance.LinuxMaintenanceAction.RESTORE_DATA, ['restore', '--data-only', '--yes']),
    (maintenance.LinuxMaintenanceAction.RESTORE_PROGRAM, ['restore', '--program-only', '--yes']),
    (maintenance.LinuxMaintenanceAction.PRUNE_BACKUPS, ['prune', '--keep', '3', '--yes']),
    (maintenance.LinuxMaintenanceAction.RESEED_MARKET, ['reseed-market', 'v1', '--yes']),
])
def test_exact_maintenance_commands(maintenance_root, action, tail):
    request = maintenance.LinuxMaintenanceRequest(action, maintenance_root)
    command, _ = maintenance.maintenance_command(request)
    assert command[4:] == tail
    assert request.mutates


def test_update_preview_never_requests_confirmation_or_mutation(maintenance_root, tmp_path):
    release = tmp_path / 'release with spaces.zip'
    release.touch()
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.PREVIEW_UPDATE,
                                                 maintenance_root, release_zip=release)
    command, _ = maintenance.maintenance_command(request)
    assert command[4:] == ['update', str(release.resolve()), '--preview']
    assert not request.mutates


@pytest.mark.parametrize('backup', ['../other', '/absolute', '--program-only', 'folder\\escape', 'line\nbreak'])
def test_restore_rejects_out_of_root_or_option_shaped_names(maintenance_root, backup):
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.RESTORE,
                                                 maintenance_root, backup=backup)
    with pytest.raises(LinuxSetupError):
        maintenance.maintenance_command(request)


@pytest.mark.parametrize('keep', [0, -1, 21, True, '3'])
def test_prune_has_a_bounded_positive_retention(maintenance_root, keep):
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.PRUNE_BACKUPS,
                                                 maintenance_root, keep=keep)
    with pytest.raises(LinuxSetupError):
        maintenance.maintenance_command(request)


def test_missing_v4_seeder_is_unavailable(maintenance_root):
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.RESEED_MARKET,
                                                 maintenance_root, market_profile='v4')
    with pytest.raises(LinuxSetupError, match='v4'):
        maintenance.maintenance_command(request)


def test_changed_helper_is_never_executed(maintenance_root):
    (maintenance_root / '.evejs-linux-tools/evejs-linux-update.py').write_text('modified')
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.LIST_BACKUPS, maintenance_root)
    with pytest.raises(LinuxSetupError, match='supported Linux Setup'):
        maintenance.maintenance_command(request)


def test_verified_previous_tool_pair_remains_usable(maintenance_root, monkeypatch):
    tools = maintenance_root / '.evejs-linux-tools'
    previous = (file_sha256(tools / 'evejs-linux-update.py'),
                file_sha256(tools / 'evejs-linux-lu-compat.py'))
    monkeypatch.setattr(maintenance, 'LEGACY_MAINTENANCE_TOOLS', frozenset({previous}))
    monkeypatch.setattr(maintenance, 'UPDATER_SHA256', '0' * 64)
    monkeypatch.setitem(maintenance.SETUP_TOOL_HASHES, 'evejs-linux-lu-compat.py', '1' * 64)
    command, _ = maintenance.maintenance_command(maintenance.LinuxMaintenanceRequest(
        maintenance.LinuxMaintenanceAction.LIST_BACKUPS, maintenance_root))
    assert command[-1] == 'list'


def test_mixed_verified_tool_versions_are_refused(maintenance_root, monkeypatch):
    tools = maintenance_root / '.evejs-linux-tools'
    previous_updater = file_sha256(tools / 'evejs-linux-update.py')
    monkeypatch.setattr(maintenance, 'LEGACY_MAINTENANCE_TOOLS',
                        frozenset({(previous_updater, '1' * 64)}))
    monkeypatch.setattr(maintenance, 'UPDATER_SHA256', '0' * 64)
    with pytest.raises(LinuxSetupError, match='mixed maintenance tools'):
        maintenance.maintenance_command(maintenance.LinuxMaintenanceRequest(
            maintenance.LinuxMaintenanceAction.LIST_BACKUPS, maintenance_root))


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux file locking')
def test_mutation_holds_root_and_client_locks_through_final_check(maintenance_root, monkeypatch):
    from src.core import linux_process
    from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lease, ModLifecycleBusyError
    from src.core.linux_client import client_launch_lock
    import threading
    request = maintenance.LinuxMaintenanceRequest(maintenance.LinuxMaintenanceAction.PRUNE_BACKUPS, maintenance_root)
    observed = []

    def check(_):
        with pytest.raises(ModLifecycleBusyError):
            acquire_mod_lifecycle_lease(maintenance_root, timeout_sec=0)
        errors = []
        def other_client():
            try:
                with client_launch_lock(timeout_seconds=0.02):
                    errors.append('unexpectedly acquired')
            except LinuxSetupError:
                pass
        thread = threading.Thread(target=other_client)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive() and not errors
        observed.append('stopped check under locks')

    def execute(*args, **kwargs):
        check(request)
        return 'done'

    monkeypatch.setattr(maintenance, 'assert_maintenance_stopped', check)
    monkeypatch.setattr(linux_process, 'run_bounded', execute)
    assert maintenance.run_maintenance(request) == 'done'
    assert len(observed) == 2
    with acquire_mod_lifecycle_lease(maintenance_root):
        pass


@pytest.mark.parametrize('terminal,flags', [
    ('konsole', ['--separate', '--nofork', '-e']),
    ('gnome-terminal', ['--wait', '--']),
    ('xterm', ['-e']),
])
def test_terminal_keeps_wizard_arguments_as_literal_argv(monkeypatch, terminal, flags):
    monkeypatch.setattr(maintenance.shutil, 'which', lambda name: '/bin/' + name if name == terminal else None)
    args = ['/bin/bash', '/folder with spaces/installer;literal.sh', '--no-launch']
    assert maintenance.terminal_command(args) == ['/bin/' + terminal, *flags, *args]


@pytest.mark.parametrize('mode,expected', [
    (maintenance.LinuxSetupMode.INSTALL, ['--mode', 'install']),
    (maintenance.LinuxSetupMode.PREPARE, ['--mode', 'compat']),
    (maintenance.LinuxSetupMode.PREFIX, ['--mode', 'prefix']),
])
def test_setup_mode_and_existing_root_are_explicit(linux_installation, tmp_path, monkeypatch, mode, expected):
    folder = tmp_path / 'verified setup'
    folder.mkdir()
    installer = folder / 'install-evejs-linux.sh'
    installer.write_bytes(b'fixture installer')
    monkeypatch.setattr(maintenance, 'verify_setup_bundle', lambda path: folder)
    monkeypatch.setattr(maintenance, 'INSTALLER_SHA256', file_sha256(installer))
    monkeypatch.setattr(maintenance.shutil, 'which', lambda name: '/bin/bash')
    request = maintenance.LinuxSetupRequest(mode, linux_installation.root if mode is not maintenance.LinuxSetupMode.INSTALL else None)
    command = maintenance.installer_command(folder, request)
    assert command[:3] == ['/bin/bash', str(installer.resolve()), '--no-launch']
    assert command[3:5] == expected
    assert command[5:] == ([] if mode is maintenance.LinuxSetupMode.INSTALL else ['--existing', str(linux_installation.root)])
