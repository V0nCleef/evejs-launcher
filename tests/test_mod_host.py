"""Portable helpers are enabled explicitly; Windows payloads are never run."""
from types import SimpleNamespace

import pytest

from src.core import mod_host
from src.core.mod_api_manifest import ModApiHelper


def test_linux_rejects_powershell_before_spawning(monkeypatch, tmp_path):
    from src.core.mod_api_runtime import _command, ModApiRuntimeError
    monkeypatch.setattr(mod_host, 'IS_LINUX', True)
    helper = ModApiHelper('powershell', tmp_path / 'helper.ps1')
    descriptor = SimpleNamespace(launcher_api=SimpleNamespace(helper=helper))
    with pytest.raises(ModApiRuntimeError, match='Windows PowerShell'):
        _command(descriptor, tmp_path / 'request', tmp_path / 'result')


def test_linux_executable_capability_requires_native_file_and_execute_permission(monkeypatch, tmp_path):
    monkeypatch.setattr(mod_host, 'IS_LINUX', True)
    path = tmp_path / 'helper'
    helper = ModApiHelper('executable', path)
    monkeypatch.setattr(mod_host.os, 'access', lambda *args: True)
    path.write_bytes(b'MZ' + bytes(128))
    assert 'native Linux' in mod_host.helper_unavailable_reason(helper)
    path.write_bytes(b'#!/bin/sh\nexit 0\n')
    assert mod_host.helper_unavailable_reason(helper) == ''
    monkeypatch.setattr(mod_host.os, 'access', lambda *args: False)
    assert 'not executable' in mod_host.helper_unavailable_reason(helper)


def test_windows_helper_capability_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(mod_host, 'IS_LINUX', False)
    assert mod_host.helper_unavailable_reason(ModApiHelper('powershell', tmp_path / 'helper.ps1')) == ''
