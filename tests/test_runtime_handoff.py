"""Ownership is restored only from authenticated, matching, one-use evidence."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src import config
from src.core import runtime_handoff as handoff
from src.core.process_identity_win import (
    ReconnectedProcess, capture_process_identity, identity_is_running, protect_checkpoint,
)
from src.core.process_tracker import ProcessTracker


@pytest.fixture
def child():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "profile")
    monkeypatch.setattr(handoff, "identity_is_running", lambda _identity: False)
    executable = tmp_path / "Launcher folder" / "EveJS-Launcher-V1.exe"
    cfg = {"runtime_backend": "native", "evejs_root": str(tmp_path / "world")}

    def window():
        return SimpleNamespace(_cfg=dict(cfg), _server_proc=None, _market_proc=None,
                               _tracker=ProcessTracker())
    return executable, window


def test_dpapi_roundtrip_and_tamper_refusal():
    message = b'{"fixture":"private tracking metadata"}'
    encrypted = protect_checkpoint(message)
    assert message not in encrypted
    assert protect_checkpoint(encrypted, decrypt=True) == message
    corrupt = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
    with pytest.raises(OSError):
        protect_checkpoint(corrupt, decrypt=True)


@pytest.mark.parametrize("change", ["created", "image"])
def test_reused_pid_or_different_image_never_gets_control(child, change):
    identity = capture_process_identity(child)
    different = replace(identity, **({"created": identity.created + 1} if change == "created"
                                     else {"image": str(Path(identity.image).with_name("other.exe"))}))
    with pytest.raises(ValueError):
        ReconnectedProcess(different)
    assert child.poll() is None


def test_reconnected_handle_can_wait_and_stop_only_the_owned_child(child):
    process = ReconnectedProcess(capture_process_identity(child))
    assert process.pid == child.pid and process.poll() is None
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=0.01)
    process.terminate()
    assert process.wait(timeout=5) == 1
    assert child.wait(timeout=5) == 1


def test_exited_parent_with_a_retained_handle_does_not_block_ownership_transfer(child):
    identity = capture_process_identity(child)
    assert identity_is_running(identity)
    child.terminate()
    child.wait(timeout=5)
    # A Windows process object may outlive its image metadata. The updater or
    # outside observer can still hold a handle after the old GUI has exited.
    assert identity_is_running(identity) is False


@pytest.mark.parametrize("kind", ["game", "market", "client", "docker-client"])
def test_roundtrip_restores_identity_and_client_metadata_once(child, environment, kind):
    executable, window = environment
    old = window()
    if kind.endswith("client"):
        if kind == "docker-client":
            old._cfg["runtime_backend"] = "docker_compose"
        client = old._tracker.add("fixture-user", "fixture-character", child)
        client.has_seen_window = True
    else:
        setattr(old, "_server_proc" if kind == "game" else "_market_proc", child)
    ticket = handoff.prepare_runtime_handoff(old, executable)
    assert ticket and ticket.path.exists()
    fresh = window()
    fresh._cfg = dict(old._cfg)
    result = handoff.resume_runtime_handoff(fresh, executable)
    if kind.endswith("client"):
        assert result.clients == 1
        recovered = fresh._tracker.running[0]
        assert (recovered.pid, recovered.username, recovered.character_name) == (child.pid, "fixture-user", "fixture-character")
        assert recovered.started_at == client.started_at and recovered.has_seen_window
        assert fresh._server_proc is None and fresh._market_proc is None
    else:
        assert getattr(result, kind)
        assert getattr(fresh, "_server_proc" if kind == "game" else "_market_proc").pid == child.pid
    assert not ticket.path.exists()
    assert handoff.resume_runtime_handoff(window(), executable) == handoff.ResumeResult()
    assert child.poll() is None


@pytest.mark.parametrize("change", ["root", "backend", "profile", "port", "image", "created", "expired", "corrupt", "schema", "duplicate", "too_large"])
def test_changed_or_unsafe_checkpoint_grants_no_control(child, environment, change):
    executable, window = environment
    old, fresh = window(), window()
    old._server_proc = child
    ticket = handoff.prepare_runtime_handoff(old, executable)
    data = json.loads(protect_checkpoint(ticket.path.read_bytes(), decrypt=True))
    if change == "root":
        fresh._cfg["evejs_root"] += "-different"
    elif change == "backend":
        fresh._cfg["runtime_backend"] = "docker_compose"
    elif change == "profile":
        fresh._cfg["active_installation_profile"] = "different"
    elif change == "port":
        fresh._cfg["game_port"] = 12345
    elif change in ("image", "created"):
        value = data["records"][0]["process"]
        value[change] = value[change] + 1 if change == "created" else str(Path(value[change]).with_name("other.exe"))
    elif change == "expired":
        data["time"] -= 3601
    elif change == "schema":
        data["schema"] = 999
    elif change == "duplicate":
        data["records"].append(data["records"][0])
    content = protect_checkpoint(json.dumps(data).encode())
    if change == "corrupt":
        content = b"not an authenticated checkpoint"
    if change == "too_large":
        content = b"x" * (handoff._MAX_BYTES + 1)
    ticket.path.write_bytes(content)
    result = handoff.resume_runtime_handoff(fresh, executable)
    assert not result.game and fresh._server_proc is None
    assert child.poll() is None


def test_live_parent_retains_checkpoint_and_exclusive_control(child, environment, monkeypatch):
    executable, window = environment
    old = window()
    old._server_proc = child
    ticket = handoff.prepare_runtime_handoff(old, executable)
    monkeypatch.setattr(handoff, "identity_is_running", lambda _identity: True)
    fresh = window()
    assert handoff.resume_runtime_handoff(fresh, executable) == handoff.ResumeResult()
    assert fresh._server_proc is None and ticket.path.exists()
    with pytest.raises(FileExistsError):
        handoff.prepare_runtime_handoff(old, executable)
    handoff.cancel_runtime_handoff(ticket)
    assert not ticket.path.exists() and child.poll() is None


def test_exited_child_is_skipped_without_adopting_another_process(child, environment):
    executable, window = environment
    old = window()
    old._server_proc = child
    handoff.prepare_runtime_handoff(old, executable)
    child.terminate()
    child.wait(timeout=5)
    fresh = window()
    result = handoff.resume_runtime_handoff(fresh, executable)
    assert not result.game and result.skipped == 1 and fresh._server_proc is None


def test_external_and_docker_services_need_no_process_checkpoint(environment):
    executable, window = environment
    owner = window()
    owner._cfg["runtime_backend"] = "docker_compose"
    owner._service_reachability = (True, True)
    assert handoff.prepare_runtime_handoff(owner, executable) is None
    assert not handoff.checkpoint_path(executable).exists()


def test_failed_update_cannot_delete_someone_elses_checkpoint(child, environment):
    executable, window = environment
    owner = window()
    owner._server_proc = child
    ticket = handoff.prepare_runtime_handoff(owner, executable)
    ticket.path.write_bytes(b"different checkpoint")
    handoff.cancel_runtime_handoff(ticket)
    assert ticket.path.read_bytes() == b"different checkpoint"


def test_native_evidence_catches_up_exactly_once_across_the_update_gap(tmp_path, monkeypatch):
    import time
    from src.core import server_launcher as launcher
    source = tmp_path / "console.log"
    status = tmp_path / "mod-status.log"
    prefix, before, gap = b"launcher diagnostics\n", b"before update\n", b"during updater\n"
    source.write_bytes(prefix + before + gap)
    status.write_bytes(before)
    process = SimpleNamespace(
        poll=lambda: 0,
        mod_runtime_receipt=launcher.NativeModRuntimeLaunchReceipt("a" * 64, "native:fixture", status, len(prefix)),
    )
    monkeypatch.setattr(launcher, "SERVER_CONSOLE_LOG", source)
    launcher.resume_native_console_evidence(process)
    deadline = time.monotonic() + 2
    while status.read_bytes() != before + gap and time.monotonic() < deadline:
        time.sleep(.01)
    assert status.read_bytes() == before + gap


def test_installed_launcher_restart_preserves_private_profile_and_resets_bootloader(tmp_path, monkeypatch):
    from src.updater import handoff as updater
    calls = []
    monkeypatch.setenv("APPDATA", str(tmp_path / "private"))
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "previous application")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda argv, **kwargs: calls.append((argv, kwargs)))
    exe = tmp_path / "Launcher folder" / "EveJS-Launcher-V1.exe"
    updater.launch_installed_launcher(exe)
    argv, options = calls[0]
    assert argv == [str(exe)]
    assert options["env"]["APPDATA"] == str(tmp_path / "private")
    assert options["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert options["close_fds"] is True


def test_native_runtime_evidence_and_missing_console_bytes_survive_checkpoint(child, environment, monkeypatch):
    import threading
    import time
    from src.core import server_launcher as launcher
    from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock
    from src.core.mod_runtime_state import (
        build_mod_runtime_plan, build_native_mod_runtime_snapshot, write_mod_runtime_snapshot,
    )
    executable, window = environment
    old = window()
    root = Path(old._cfg["evejs_root"])
    root.mkdir()
    plan = build_mod_runtime_plan(root, (), backend="native", mode="vanilla",
                                  runtime_identity="native:update-fixture", selected_loader_ids=())
    snapshot = build_native_mod_runtime_snapshot(plan, (), b"", pid=child.pid)
    with acquire_mod_lifecycle_lock(root):
        write_mod_runtime_snapshot(snapshot)
    console = root / "console.log"
    console.write_bytes(b"diagnostics\nbefore\ngap\n")
    status = launcher.get_native_mod_status_log(root)
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_bytes(b"before\n")
    monkeypatch.setattr(launcher, "SERVER_CONSOLE_LOG", console)
    child.mod_runtime_receipt = launcher.NativeModRuntimeLaunchReceipt(
        plan.plan_sha256, plan.runtime_identity, status, len(b"diagnostics\n"),
    )
    old._server_proc = child
    handoff.prepare_runtime_handoff(old, executable)
    fresh = window()
    published = []
    fresh._publish_mod_runtime_snapshot = published.append
    assert handoff.resume_runtime_handoff(fresh, executable).game
    assert published == [snapshot]
    assert fresh._server_proc.mod_runtime_receipt == child.mod_runtime_receipt
    deadline = time.monotonic() + 2
    while status.read_bytes() != b"before\ngap\n" and time.monotonic() < deadline:
        time.sleep(.01)
    assert status.read_bytes() == b"before\ngap\n"
    child.terminate()
    child.wait(timeout=5)
    for thread in threading.enumerate():
        if thread.name == "resumed-native-mod-evidence":
            thread.join(timeout=2)
            assert not thread.is_alive()
