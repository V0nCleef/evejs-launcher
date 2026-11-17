"""The Linux Launch All path uses owned-process readiness instead of a Win32 probe."""
import sys
import time
from types import SimpleNamespace

import pytest
from PyQt6.QtTest import QTest

from src import app as application, config

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux queue capability')


def test_linux_queue_waits_for_owned_process_readiness_then_continues(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config, 'CONFIG_FILE', tmp_path / 'config.json')
    config.save(dict(config.DEFAULT_CONFIG, update_auto_check=False, audio_master_muted=True, close_behavior='exit'))
    monkeypatch.setattr(application, '_CLIENT_WINDOW_READY_POLL_MS', 5)
    def forbidden(*args):
        raise AssertionError('Linux must not wait on an unavailable Windows window probe')
    monkeypatch.setattr(application, 'has_visible_window_for_pid', forbidden)
    window = application.MainWindow(defer_runtime_startup=True)
    completions = []
    queue = SimpleNamespace(cancel_requested=False, item_finished=completions.append)
    ready = False
    observed = []
    def probe(pid):
        observed.append(pid)
        return ready
    process = SimpleNamespace(pid=4242, poll=lambda: None, launch_ready=probe)
    window._launch_queue = queue
    try:
        window._begin_client_window_readiness(queue, process)
        QTest.qWait(30)
        assert not completions and set(observed) == {4242}
        ready = True
        deadline = time.monotonic() + 1
        while not completions and time.monotonic() < deadline:
            QTest.qWait(10)
        assert completions == [True]
        assert window._client_window_readiness_gate is None
    finally:
        gate = window._client_window_readiness_gate
        if gate is not None:
            gate.stop(); gate.deleteLater()
            window._client_window_readiness_gate = None
        window._launch_queue = None
        window.close()
        window.deleteLater()
