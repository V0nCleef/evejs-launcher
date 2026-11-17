"""Real Linux child-process tests, including timeout and unrelated processes."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process ownership")


def test_owned_service_shutdown_never_signals_unowned_process(tmp_path):
    from src.core.linux_process import OwnedServiceProcess, signal_owned_service
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True)
    own = OwnedServiceProcess(subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"], start_new_session=True))
    try:
        with pytest.raises(OSError, match="does not own"):
            signal_owned_service(unrelated.pid, signal.SIGTERM)
        own.terminate()
        own.wait(timeout=5)
        assert unrelated.poll() is None
    finally:
        if own.poll() is None:
            own.kill()
            own.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_timeout_reaps_owned_helper_children(tmp_path):
    from src.core.linux_process import run_bounded, start_ticks
    marker = tmp_path / "child-pid"
    child_code = "import time;time.sleep(30)"
    parent_code = ("import subprocess,sys,time;from pathlib import Path;"
                   f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
                   f"Path({str(marker)!r}).write_text(str(p.pid));time.sleep(30)")
    with pytest.raises(RuntimeError, match="timed out"):
        run_bounded([sys.executable, "-c", parent_code], cwd=tmp_path, timeout=0.5)
    pid = int(marker.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        stat = Path(f"/proc/{pid}/stat")
        if not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("The timed-out helper left a live child")


def test_proton_client_stop_leaves_other_client_in_same_prefix_alive(tmp_path):
    from src.core.linux_process import ProtonClientProcess
    script = tmp_path / "exefile.exe"
    script.write_text("import time;time.sleep(30)")
    marker_a = "/settingsprofile=EveJSLauncher_a"
    marker_b = "/settingsprofile=EveJSLauncher_b"
    raw_a = subprocess.Popen([sys.executable, str(script), marker_a])
    raw_b = subprocess.Popen([sys.executable, str(script), marker_b])
    client_a = ProtonClientProcess(raw_a, marker_a, script)
    try:
        assert client_a.poll() is None
        client_a.terminate()
        client_a.wait(timeout=5)
        assert raw_b.poll() is None
    finally:
        if raw_a.poll() is None:
            raw_a.kill()
        raw_a.wait(timeout=5)
        raw_b.terminate()
        raw_b.wait(timeout=5)


def test_helper_timeout_still_owns_child_after_wrapper_exit(tmp_path):
    from src.core.linux_process import run_bounded
    marker = tmp_path / 'delegated-pid'
    command = ("import subprocess,sys;from pathlib import Path;"
               "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
               f"Path({str(marker)!r}).write_text(str(p.pid))")
    with pytest.raises(RuntimeError, match='timed out'):
        run_bounded([sys.executable, '-c', command], cwd=tmp_path, timeout=0.4)
    pid = int(marker.read_text())
    stat = Path(f'/proc/{pid}/stat')
    assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'


def test_helper_output_limit_stops_a_live_writer(tmp_path):
    from src.core.linux_process import run_bounded
    command = "import os,time;os.write(1, b'x'*100000);time.sleep(30)"
    start = time.monotonic()
    with pytest.raises(RuntimeError, match='output limit'):
        run_bounded([sys.executable, '-c', command], cwd=tmp_path, limit=1024, timeout=5)
    assert time.monotonic() - start < 3


def test_client_pid_reuse_between_open_and_signal_has_no_shutdown_authority(monkeypatch):
    from src.core import linux_process
    observed = []
    monkeypatch.setattr(linux_process.os, 'pidfd_open', lambda pid: observed.append(('opened', pid)) or 42)
    monkeypatch.setattr(linux_process, 'start_ticks', lambda pid: 'replacement-start')
    monkeypatch.setattr(linux_process.signal, 'pidfd_send_signal', lambda *args: observed.append(('signaled', args)))
    monkeypatch.setattr(linux_process.os, 'close', lambda descriptor: observed.append(('closed', descriptor)))
    linux_process.signal_pinned_client(123, 'original-start', signal.SIGTERM)
    assert observed == [('opened', 123), ('closed', 42)]


def test_linux_queue_requires_owned_wine_process_after_startup_grace(tmp_path, monkeypatch):
    from src.core.linux_process import ProtonClientProcess
    script = tmp_path / 'exefile.exe'
    script.write_text('import time; time.sleep(30)')
    marker = '/settingsprofile=EveJSLauncher_queue_fixture'
    raw = subprocess.Popen([sys.executable, str(script), marker])
    client = ProtonClientProcess(raw, marker, script)
    try:
        assert not client.launch_ready(raw.pid)
        client._started -= 21
        assert not client.launch_ready(raw.pid)  # Python/Proton wrapper alone is insufficient.
        monkeypatch.setattr(os, 'readlink', lambda path: '/fixture/wine64-preloader')
        assert client.launch_ready(raw.pid)
        assert not client.launch_ready(raw.pid + 1)
    finally:
        raw.terminate(); raw.wait(timeout=5)
