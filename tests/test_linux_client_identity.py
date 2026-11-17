"""Reconnect exact client identities without signalling another prefix user."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux pidfd client ownership')


def client(tmp_path, marker):
    from src.core.linux_process import ProtonClientProcess
    executable = tmp_path / 'exefile.exe'
    executable.touch()
    code = ('import subprocess,sys;'
        f'p=subprocess.Popen([{str(executable)!r},"-c","import time;time.sleep(60)",{marker!r}],executable=sys.executable);'
        'sys.exit(p.wait())')
    raw = subprocess.Popen([sys.executable, '-c', code])
    return ProtonClientProcess(raw, marker, executable)


def test_reconnected_client_stop_keeps_second_client_alive(tmp_path):
    from src.core.linux_client_identity import capture_client, PinnedClientProcess, UNKNOWN_EXIT_STATUS
    first = client(tmp_path, '/settingsprofile=EveJSLauncher_run_a')
    second = client(tmp_path, '/settingsprofile=EveJSLauncher_run_b')
    recovered = None
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                descriptor = capture_client(first)
                break
            except ValueError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        recovered = PinnedClientProcess(descriptor)
        recovered.terminate()
        assert recovered.wait(timeout=5) == UNKNOWN_EXIT_STATUS
        assert second.poll() is None
        assert recovered.exit_status_known is False
    finally:
        if recovered is not None:
            recovered.kill()
            recovered.close()
        for process in (first, second):
            process.kill()
            process.wait(timeout=5)


def test_changed_start_identity_cannot_be_reconnected(tmp_path):
    from src.core.linux_client_identity import capture_client, PinnedClientProcess
    process = client(tmp_path, '/settingsprofile=EveJSLauncher_run_fixture')
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                descriptor = capture_client(process)
                break
            except ValueError:
                assert time.monotonic() < deadline
                time.sleep(0.02)
        descriptor['processes'][0]['started'] += '1'
        with pytest.raises(ValueError, match='exited or changed'):
            PinnedClientProcess(descriptor)
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=5)


def test_client_checkpoint_survives_old_gui_and_restores_tracker(tmp_path, monkeypatch):
    from src import config
    from src.core.process_tracker import ProcessTracker
    from src.core.runtime_handoff import resume_runtime_handoff
    from src.core.linux_client_identity import UNKNOWN_EXIT_STATUS
    from types import SimpleNamespace
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path / 'config')
    root = tmp_path / 'world'
    root.mkdir()
    cfg = {'runtime_backend': 'native', 'evejs_root': str(root)}
    code = '''
import json,os,sys,time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from src import config
from src.core.linux_process import ProtonClientProcess
from src.core.runtime_handoff import prepare_runtime_handoff
from src.core.linux_client_identity import capture_client
config.CONFIG_DIR=Path(sys.argv[1])
cfg=json.loads(sys.argv[2]);exe=Path(cfg['evejs_root'])/'exefile.exe';exe.touch()
marker='/settingsprofile=EveJSLauncher_run_handoff'
import subprocess
raw=subprocess.Popen([sys.executable,'-c','import subprocess,sys;p=subprocess.Popen([sys.argv[1],"-c","import time;time.sleep(60)",sys.argv[2]],executable=sys.executable);sys.exit(p.wait())',str(exe),marker])
process=ProtonClientProcess(raw,marker,exe)
deadline=time.monotonic()+3
while True:
    try: capture_client(process);break
    except ValueError:
        assert time.monotonic()<deadline
        time.sleep(.02)
entry=SimpleNamespace(username='fixture-account',character_name='Fixture Pilot',process=process,started_at=datetime.now(),has_seen_window=True)
owner=SimpleNamespace(_cfg=cfg,_server_proc=None,_market_proc=None,_tracker=SimpleNamespace(running=[entry]))
assert prepare_runtime_handoff(owner,sys.executable) is not None
'''
    subprocess.run([sys.executable, '-c', code, str(config.CONFIG_DIR), json.dumps(cfg)],
        cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)
    tracker = ProcessTracker()
    owner = SimpleNamespace(_cfg=cfg, _server_proc=None, _market_proc=None, _tracker=tracker)
    restored = resume_runtime_handoff(owner, sys.executable)
    assert restored.clients == 1
    entry = tracker.running[0]
    try:
        assert entry.username == 'fixture-account' and entry.has_seen_window
        entry.process.terminate()
        assert entry.process.wait(timeout=5) == UNKNOWN_EXIT_STATUS
        assert not resume_runtime_handoff(owner, sys.executable).clients
    finally:
        entry.process.kill()
        entry.process.close()
