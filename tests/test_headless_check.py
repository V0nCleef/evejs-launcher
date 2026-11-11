"""Offscreen diagnostics must never inherit or rewrite normal launcher state."""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from src.core.headless_check import HeadlessCheck


def test_normal_launch_and_update_handoff_do_not_enter_diagnostics():
    assert HeadlessCheck.from_argv([]) is None
    assert HeadlessCheck.from_argv(['--apply-update', 'handoff.json']) is None


@pytest.mark.parametrize('arguments', [
    ['--headless-check'], ['--check-mode', 'immediate'],
    ['--headless-check', 'relative'], ['--headless-check=relative'],
    ['--headless-check-typo'],
    ['--headless-check', str(Path.cwd()), '--check-mode', 'unknown'],
    ['--headless-check', str(Path.cwd()), '--apply-update', 'handoff.json'],
])
def test_bad_diagnostic_arguments_fail_before_normal_startup(arguments):
    with pytest.raises(SystemExit) as error:
        HeadlessCheck.from_argv(arguments)
    assert error.value.code == 2


def test_prepare_uses_unique_state_and_does_not_replace_existing_config(tmp_path, monkeypatch):
    original = tmp_path / 'appdata/EveJS-Launcher/config.json'
    original.parent.mkdir(parents=True)
    original.write_bytes(b'original-state')
    saved_hook = sys.excepthook
    check = HeadlessCheck(tmp_path, 'immediate')
    for name in ('APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'QT_QPA_PLATFORM'):
        monkeypatch.setenv(name, 'original-environment')
    try:
        check.prepare()
        assert check.state.parent == tmp_path and check.state != tmp_path
        assert original.read_bytes() == b'original-state'
        assert os.environ['QT_QPA_PLATFORM'] == 'offscreen'
        assert Path(os.environ['APPDATA']).is_relative_to(check.state)
        cfg = json.loads((Path(os.environ['APPDATA']) / 'EveJS-Launcher/config.json').read_text())
        assert cfg['auto_start_server'] is cfg['auto_start_market'] is cfg['update_auto_check'] is False
        assert Path(cfg['evejs_root']).is_relative_to(check.state)
    finally:
        check.watchdog.cancel()
        sys.excepthook = saved_hook


@pytest.mark.parametrize('mode', ['immediate', 'normal', 'profiles', 'updater', 'wizard'])
def test_real_entry_point_closes_in_isolated_offscreen_process(tmp_path, mode):
    root = Path(__file__).resolve().parents[1]
    state = tmp_path / mode
    fake_normal = tmp_path / 'normal-appdata'
    result = subprocess.run([sys.executable, '-B', str(root/'main.py'), '--headless-check', str(state), '--check-mode', mode],
        cwd=root, env=dict(os.environ, APPDATA=str(fake_normal)),
        capture_output=True, text=True, encoding='utf-8', timeout=45)
    reports = list(state.glob('run-*/result.json'))
    (tmp_path / 'entry-point.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, (result.returncode, result.stderr[-2500:], [p.read_text() for p in reports])
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['status'] == 'passed' and report['platform'] == 'offscreen'
    assert report['closeRequested'] and report['runningThreads'] == []
    assert report['mainWindowRetired']
    assert not fake_normal.exists()
    if mode == 'profiles':
        assert report['profileSwitchRoundTrip']
    if mode == 'updater':
        assert report['closeWaitedForRunningUpdater']
        assert report['updaterMetadata'] == 'injected-delayed-current-version'
    if mode == 'wizard':
        assert report['wizardConstructed']
        assert report['wizardRetired']
