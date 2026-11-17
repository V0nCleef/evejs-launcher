"""Linux host implementation; unsupported preview features fail explicitly."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import signal
import subprocess

from .linux_client import client_launch_lock as serialize_evejs_client_trust_and_spawn
from .linux_process import signal_owned_service


def get_client_process_flags():
    return {"start_new_session": True}


def get_hidden_process_flags():
    return {"start_new_session": True}


def get_graceful_server_process_flags():
    return {"start_new_session": True}


def request_graceful_server_shutdown(pid):
    return signal_owned_service(pid, signal.SIGINT)


def terminate_process_tree(process):
    return signal_owned_service(process if isinstance(process, int) else process.pid, signal.SIGKILL)


def create_directory_link(target: Path, link: Path):
    link.symlink_to(target, target_is_directory=True)


def remove_directory_link(link: Path):
    if not link.is_symlink():
        raise OSError("Refusing to remove an ordinary Linux directory.")
    link.unlink()


def get_client_exe_name():
    return "exefile.exe"


def get_client_exe_path(profile_tq_path):
    return Path(profile_tq_path) / "bin64/exefile.exe"


def get_market_binary_name():
    return "market-server"


def get_exe_file_filter():
    return "EVE client (exefile.exe);;All Files (*)"


def get_eve_settings_path(client_install_path):
    from .linux_profiles import account_settings_path
    return account_settings_path(Path(client_install_path))


def prepare_evejs_client_certificate_trust(evejs_root, client_path):
    from .linux_client import require_client_trust
    from .linux_installation import LinuxInstallation
    installation = LinuxInstallation.load(evejs_root)
    if Path(client_path).resolve() != installation.client:
        raise OSError("The client differs from the selected Linux installation.")
    require_client_trust(installation)
    return False


def open_text_editor(file_path):
    from .host import external_process_environment
    editor = os.environ.get("EDITOR", "")
    return subprocess.Popen([*shlex.split(editor), str(file_path)] if editor else ["xdg-open", str(file_path)],
                            start_new_session=True, env=external_process_environment())


def find_eve_window(*args, **kwargs):
    return None


def has_visible_window_for_pid(*args, **kwargs):
    return False


def find_and_focus_eve_window(*args, **kwargs):
    return False


find_and_focus_eve_window_for_pid = find_and_focus_eve_window
center_tool_window_for_process_tree = find_and_focus_eve_window


def _unsupported(*args, **kwargs):
    raise OSError("This Windows integration is unavailable in the Linux stage 1 preview.")


launch_eve_client = _unsupported  # Linux uses the captured installation request.
build_tool_batch_command = _unsupported
launch_tool_wrapper = _unsupported
get_suspended_hidden_process_flags = get_hidden_process_flags
create_kill_on_close_job = _unsupported
resume_process = _unsupported


def terminate_job(job):
    from .linux_process import OwnedServiceProcess
    if not isinstance(job, OwnedServiceProcess):
        raise OSError("The Linux process ownership record is unavailable.")
    job.kill()
    return True


def close_job(job):
    from .linux_process import OwnedServiceProcess
    if not isinstance(job, OwnedServiceProcess):
        raise OSError("The Linux process ownership record is unavailable.")
    if job.poll() is None:
        job.kill()
    job.wait(timeout=5)


def run_updater(*args, **kwargs):
    from ..updater.linux_update import run_updater as update
    return update(*args, **kwargs)


def hard_exit(code=0):
    os._exit(code)
