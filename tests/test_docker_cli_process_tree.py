"""Windows integration proof for bounded Docker CLI process-tree cleanup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import psutil
import pytest

from src.core import platform
from src.core.runtime.docker_cli import DockerCommandError, DockerCommandRunner


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_production_runner_timeout_reaps_parent_and_descendant(tmp_path: Path) -> None:
    """A timed-out helper and the child it creates must both be absent on return."""
    pid_file = tmp_path / "contained-pids.json"
    helper = (
        "import json, os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "pathlib.Path(sys.argv[1]).write_text("
        "json.dumps({'parent': os.getpid(), 'child': child.pid}), encoding='utf-8'); "
        "time.sleep(60)"
    )
    pids: list[int] = []

    try:
        runner = DockerCommandRunner(executable=sys.executable)
        with pytest.raises(DockerCommandError) as raised:
            runner.run(("-c", helper, os.fspath(pid_file)), cwd=tmp_path, timeout=1.0)

        assert raised.value.result.timed_out is True
        assert pid_file.is_file(), "the resumed helper never recorded its process tree"
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        pids = [int(payload["parent"]), int(payload["child"])]

        deadline = time.monotonic() + 2.0
        while any(psutil.pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert all(not psutil.pid_exists(pid) for pid in pids)
    finally:
        # Exact-PID safety net for a failing test; never perform broad process cleanup.
        for pid in pids:
            if psutil.pid_exists(pid):
                platform.terminate_process_tree(pid)
