"""Exercise the production Docker/mod helper containment paths on Linux."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux helper execution')


def test_docker_cli_execution_keeps_bounded_output_and_returns_status(tmp_path):
    from src.core.runtime.docker_cli import DockerCommandRunner
    runner = DockerCommandRunner(executable=sys.executable, output_limit=32)
    completed = runner._execute_bounded_process(
        (sys.executable, '-c', "print('x'*100)"), tmp_path, 3, stdin=subprocess.DEVNULL)
    assert completed.returncode == 0
    assert len(completed.stdout) == 33


def test_docker_cli_timeout_kills_owned_group(tmp_path):
    from src.core.runtime.docker_cli import DockerCommandRunner
    runner = DockerCommandRunner(executable=sys.executable)
    with pytest.raises(subprocess.TimeoutExpired):
        runner._execute_bounded_process((sys.executable, '-c', 'import time;time.sleep(10)'),
                                        tmp_path, 0.2, stdin=subprocess.DEVNULL)


def test_mod_helper_uses_native_linux_containment(tmp_path):
    from src.core.mod_api_runtime import run_helper_process
    result = run_helper_process([sys.executable, '-c', "print('portable helper')"], cwd=tmp_path,
        environment=os.environ.copy(), timeout=3, output_directory=tmp_path)
    assert result.returncode == 0 and result.stdout.strip() == 'portable helper'


def test_mod_helper_timeout_reaps_descendants(tmp_path):
    from src.core.mod_api_runtime import ModApiRuntimeError, run_helper_process
    with pytest.raises(ModApiRuntimeError, match='timed out'):
        run_helper_process([sys.executable, '-c', 'import time;time.sleep(10)'], cwd=tmp_path,
            environment=os.environ.copy(), timeout=0.2, output_directory=tmp_path)
