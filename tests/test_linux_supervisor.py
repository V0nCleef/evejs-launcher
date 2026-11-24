"""Real service ownership and wait status survive the first GUI process."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux service supervisor')


def test_service_survives_launcher_exit_and_reports_real_graceful_status(tmp_path):
    from src.core.linux_supervisor import SupervisedServiceProcess
    script = tmp_path / 'service.py'
    script.write_text('import signal,sys,time\nsignal.signal(signal.SIGINT,lambda *_:sys.exit(0))\nprint("ready",flush=True)\ntime.sleep(60)\n')
    record = tmp_path / 'handoff.json'
    log = tmp_path / 'service.log'
    bootstrap = '''
import json,os,sys
from pathlib import Path
from src.core.linux_supervisor import start_supervised_service
process = start_supervised_service([sys.executable,str(Path(sys.argv[1]))],
    cwd=Path(sys.argv[1]).parent,env=dict(os.environ),log=Path(sys.argv[2]),retain_seconds=10)
Path(sys.argv[3]).write_text(json.dumps(process.descriptor))
'''
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path / 'state'))
    subprocess.run([sys.executable, '-c', bootstrap, str(script), str(log), str(record)],
                   cwd=Path(__file__).resolve().parents[1], env=env, timeout=10, check=True)
    descriptor = json.loads(record.read_text())
    process = SupervisedServiceProcess(descriptor)
    try:
        deadline = time.monotonic() + 3
        while not log.exists() or 'ready' not in log.read_text():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert process.poll() is None
        process.send_group_signal(signal.SIGINT)
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.release()


def test_crash_is_preserved_across_reconnection(tmp_path, monkeypatch):
    from src.core.linux_supervisor import start_supervised_service, SupervisedServiceProcess
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    process = start_supervised_service([sys.executable, '-c', 'raise SystemExit(17)'],
        cwd=tmp_path, env=dict(os.environ), log=tmp_path / 'log')
    process.detach()
    recovered = SupervisedServiceProcess(process.descriptor)
    try:
        assert recovered.wait(timeout=5) == 17
    finally:
        recovered.release()
        process._supervisor_popen.wait(timeout=5)


def test_wrong_key_cannot_control_service_or_unrelated_process(tmp_path, monkeypatch):
    from src.core.linux_supervisor import start_supervised_service, SupervisedServiceProcess
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    process = start_supervised_service([sys.executable, '-c', 'import time;time.sleep(60)'],
        cwd=tmp_path, env=dict(os.environ), log=tmp_path / 'log')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'], start_new_session=True)
    try:
        wrong = dict(process.descriptor, key='0' * 64, pid=unrelated.pid)
        with pytest.raises(OSError):
            SupervisedServiceProcess(wrong)
        assert process.poll() is None and unrelated.poll() is None
    finally:
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        process.release()
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_dead_supervisor_never_reports_success(tmp_path, monkeypatch):
    from src.core.linux_supervisor import start_supervised_service
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    process = start_supervised_service([sys.executable, '-c', 'import time;time.sleep(60)'],
        cwd=tmp_path, env=dict(os.environ), log=tmp_path / 'log')
    # SIGTERM is the supervisor's own emergency cleanup path. A GUI whose
    # capability is lost cannot retrieve a real child status and must say so.
    process.detach()
    process._supervisor_popen.terminate()
    process._supervisor_popen.wait(timeout=5)
    assert process.poll() is None
    with pytest.raises(OSError, match='no longer available'):
        process.wait(timeout=1)


def test_native_services_write_to_the_selected_installation_console(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.core import evejs_compatibility, linux_services, native_config_preflight, server_launcher

    root = tmp_path / "selected"
    (root / "server").mkdir(parents=True)
    (root / "externalservices/market-server").mkdir(parents=True)
    market_binary = root / "market-fixture"
    market_binary.write_text("#!/bin/sh\nprintf 'Market fixture output\\n'\n", encoding="utf-8")
    market_binary.chmod(0o755)
    installation = SimpleNamespace(
        root=root,
        node=Path(sys.executable),
        cdn_port=0,
        verified_market_binary=lambda: market_binary,
        server_environment=lambda: dict(os.environ),
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(server_launcher, "_LOGS_DIR", tmp_path / "launcher-logs")
    monkeypatch.setattr(linux_services, "_prepared", lambda _root: installation)
    monkeypatch.setattr(linux_services, "run_bounded", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        evejs_compatibility, "inspect_evejs_runtime",
        lambda _root: SimpleNamespace(node_report_directory=root / "reports"),
    )
    monkeypatch.setattr(
        native_config_preflight, "validate_native_server_config",
        lambda *_args, **_kwargs: SimpleNamespace(supported=True, valid=True, message="Config verified"),
    )
    monkeypatch.setattr(
        server_launcher, "build_game_server_command",
        lambda *_args: ["node", "-c", "print('Game fixture output')"],
    )
    for launch, console, expected in (
        (lambda: linux_services.start_game(str(root), "vanilla"), server_launcher.get_server_console_log, "Game"),
        (lambda: linux_services.start_market(str(root)), server_launcher.get_market_console_log, "Market"),
    ):
        process = launch()
        try:
            assert process.wait(timeout=5) == 0
            assert f"{expected} fixture output" in console(root).read_text(encoding="utf-8")
            assert not console(tmp_path / "other").exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.release()
