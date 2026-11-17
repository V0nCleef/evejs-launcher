"""Frozen Qt libraries must not change the ABI used by host executables."""
from src.core import host


def test_frozen_linux_restores_system_library_and_plugin_search(monkeypatch):
    monkeypatch.setattr(host, 'IS_LINUX', True)
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(host.sys, '_MEIPASS', '/bundle/_internal', raising=False)
    env = {'LD_LIBRARY_PATH': '/bundle/_internal:/system/lib', 'LD_LIBRARY_PATH_ORIG': '/system/lib',
           'QT_PLUGIN_PATH': '/bundle/_internal/PyQt6/plugins', 'PATH': '/usr/bin', 'EXAMPLE': 'kept'}
    result = host.external_process_environment(env)
    assert result == {'LD_LIBRARY_PATH': '/system/lib', 'PATH': '/usr/bin', 'EXAMPLE': 'kept'}
    assert host.external_process_environment(result) == result
    assert env['QT_PLUGIN_PATH']  # No process-wide mutation while other workers run.


def test_source_and_windows_environment_are_preserved(monkeypatch):
    monkeypatch.setattr(host, 'IS_LINUX', False)
    monkeypatch.setattr(host.sys, 'frozen', True, raising=False)
    env = {'LD_LIBRARY_PATH': '/user/libraries', 'PATH': '/user/bin'}
    assert host.external_process_environment(env) == env
