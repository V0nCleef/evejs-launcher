"""Authenticate and consume Linux service ownership after the old GUI exits."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux runtime handoff')


def test_checkpoint_recovers_service_once_after_old_launcher_exits(tmp_path, monkeypatch):
    from src import config
    from src.core.runtime_handoff import resume_runtime_handoff
    config_home = tmp_path / 'config'
    config_home.mkdir()
    monkeypatch.setattr(config, 'CONFIG_DIR', config_home)
    root = tmp_path / 'world'
    root.mkdir()
    cfg = {'runtime_backend': 'native', 'evejs_root': str(root), 'client_path': ''}
    script = '''
import json,os,sys
from pathlib import Path
from types import SimpleNamespace
from src import config
from src.core.linux_supervisor import start_supervised_service
from src.core.runtime_handoff import prepare_runtime_handoff
config.CONFIG_DIR=Path(sys.argv[1])
cfg=json.loads(sys.argv[2])
process=start_supervised_service([sys.executable,'-c','import time;time.sleep(60)'],
    cwd=Path(cfg['evejs_root']),env=dict(os.environ),log=Path(cfg['evejs_root'])/'console.log')
owner=SimpleNamespace(_cfg=cfg,_server_proc=process,_market_proc=None)
ticket=prepare_runtime_handoff(owner,sys.executable)
assert ticket is not None
process.detach()
'''
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / 'state'))
    subprocess.run([sys.executable, '-c', script, str(config_home), json.dumps(cfg)],
                   cwd=Path(__file__).resolve().parents[1], env=env, check=True, timeout=10)
    owner = SimpleNamespace(_cfg=cfg, _server_proc=None, _market_proc=None)
    result = resume_runtime_handoff(owner, sys.executable)
    assert result.game and not result.market
    process = owner._server_proc
    try:
        assert process.poll() is None
        assert not resume_runtime_handoff(owner, sys.executable).game
        process.send_group_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == -signal.SIGTERM
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.release()


def test_payload_tampering_is_rejected_before_process_control(tmp_path, monkeypatch):
    from src import config
    from src.core.runtime_handoff_linux import _protect, _unprotect
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path)
    content = _protect(b'{"records":[]}')
    envelope = json.loads(content)
    envelope['payload'] = 'e30='  # Different JSON, unchanged authenticator.
    with pytest.raises(ValueError, match='changed'):
        _unprotect(json.dumps(envelope).encode())


def test_normal_launch_does_not_consume_a_live_parent_checkpoint(tmp_path, monkeypatch):
    from src import config
    from src.core.linux_supervisor import start_supervised_service
    from src.core.runtime_handoff import prepare_runtime_handoff, resume_runtime_handoff, cancel_runtime_handoff
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path / 'config')
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    process = start_supervised_service([sys.executable, '-c', 'import time;time.sleep(60)'],
        cwd=tmp_path, env=dict(os.environ), log=tmp_path / 'log')
    cfg = {'runtime_backend': 'native', 'evejs_root': str(tmp_path)}
    owner = SimpleNamespace(_cfg=cfg, _server_proc=process, _market_proc=None)
    ticket = prepare_runtime_handoff(owner, sys.executable)
    try:
        other = SimpleNamespace(_cfg=cfg, _server_proc=None, _market_proc=None)
        assert not resume_runtime_handoff(other, sys.executable).game
        assert ticket.path.exists() and other._server_proc is None
    finally:
        cancel_runtime_handoff(ticket)
        process.kill()
        process.wait(timeout=5)
        process.release()
