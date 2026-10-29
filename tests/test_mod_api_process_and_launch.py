"""Actual disposable helper containment and the client spawn integration seam."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import time
from types import MappingProxyType

import psutil
import pytest

from src.core import launcher, mod_api_runtime as runtime
from src.core.mod_api_manifest import read_api_manifest
from src.core.mod_manifest import ActivationKind, Mod


def test_helper_process_contains_and_reaps_its_descendant_after_timeout(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    pid_path = tmp_path / "child.pid"
    code = "import pathlib, subprocess, sys, time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
    with pytest.raises(runtime.ModApiRuntimeError, match="timed out"):
        runtime.run_helper_process([sys.executable, "-B", "-c", code, str(pid_path)], cwd=tmp_path,
            environment=dict(os.environ), timeout=1.2, output_directory=output)
    assert pid_path.is_file()
    child = int(pid_path.read_text())
    deadline = time.monotonic() + 3
    while psutil.pid_exists(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(child)


def test_helper_output_limit_stops_a_verbose_helper(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    code = "import sys, time; sys.stdout.write('x'*300000); sys.stdout.flush(); time.sleep(30)"
    with pytest.raises(runtime.ModApiRuntimeError, match="output limit"):
        runtime.run_helper_process([sys.executable, "-B", "-c", code], cwd=tmp_path,
            environment=dict(os.environ), timeout=5, output_directory=output)


def fixture_launcher(tmp_path, monkeypatch, folder_name):
    root = tmp_path / "runtime"
    folder = root / "mods" / folder_name
    folder.mkdir(parents=True)
    (folder / "helper.exe").write_bytes(b"INERT")
    manifest = {"schemaVersion": 3, "id": folder_name, "displayName": folder_name, "version": "0.5.8",
        "kind": "client-package", "activation": {"strategy": "client_package"}, "restart": "client",
        "launcherApi": {"version": 1, "helper": {"runtime": "executable", "path": "helper.exe"}, "capabilities": ["verify", "prepare_profile"]}}
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(manifest), encoding="utf-8")
    descriptor = read_api_manifest(root, folder)
    mod = Mod(folder_name, folder, True, id=folder_name, activation_kind=ActivationKind.CLIENT_PACKAGE,
        evejs_root=root, api_descriptor=descriptor)
    profile_tq = tmp_path / "Profiles" / "pilot" / "tq"
    exe = profile_tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"INERT")
    client = tmp_path / "physical" / "tq"
    client.mkdir(parents=True)
    monkeypatch.setattr(launcher, "get_client_exe_path", lambda _path: exe)
    monkeypatch.setattr(launcher, "_resolve_client_resource_cache", lambda *_args: tmp_path / "ResFiles")
    monkeypatch.setattr(launcher, "serialize_evejs_client_trust_and_spawn", nullcontext)
    monkeypatch.setattr(launcher, "scan_mods", lambda _root: [mod])
    monkeypatch.setattr(launcher, "prepare_evejs_client_certificate_trust", lambda *_args: False)
    return root, profile_tq, client, mod


def test_public_dlss_folder_cannot_request_receipt_verification_before_spawn(tmp_path, monkeypatch):
    root, profile, client, mod = fixture_launcher(tmp_path, monkeypatch, "DLSS5")
    events = []
    monkeypatch.setattr(launcher, "prepare_installed_dlss5", lambda *_args: {"TRINITYPLATFORM": "dx12"})
    def prepare(*args, **kwargs):
        assert kwargs["mods"] == ()
        assert kwargs["protected_arguments"] == ("/port:26000",)
        events.append("profile-committed")
        return runtime.PreparedClientMods(MappingProxyType({"TRINITYPLATFORM": "dx12"}), ("/fixture:enabled",), ())
    monkeypatch.setattr(launcher, "prepare_public_client_mods", prepare)
    def spawn(exe, environment, cwd, *, arguments):
        from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock, ModLifecycleBusyError
        with pytest.raises(ModLifecycleBusyError):
            with acquire_mod_lifecycle_lock(client):
                pytest.fail('Physical client lease must survive through spawn')
        events.append("spawn")
        assert environment["TRINITYPLATFORM"] == "dx12"
        assert arguments == ("/port:26000", "/fixture:enabled")
        return "spawned"
    monkeypatch.setattr(launcher, "launch_eve_client", spawn)
    assert launcher.launch_client(str(root), profile, client_path=str(client)) == "spawned"
    assert events == ["profile-committed", "spawn"]


def test_shared_settings_preserve_distinct_auto_login_arguments_and_mod_context(tmp_path, monkeypatch):
    from src.core.client_autologin import AutoLoginLaunch
    from src.core.shared_client_settings import SharedClientSettings, SHARED_PROFILE_ARGUMENT
    root, profile, client, _mod = fixture_launcher(tmp_path, monkeypatch, "DLSS5")
    folder = tmp_path / "Shared EVE settings"
    folder.mkdir()
    captured = []
    def prepare(*args, **kwargs):
        assert kwargs["profile_settings_root"] == folder
        assert SHARED_PROFILE_ARGUMENT in kwargs["protected_arguments"]
        return runtime.PreparedClientMods({}, (), ())
    monkeypatch.setattr(launcher, "prepare_public_client_mods", prepare)
    monkeypatch.setattr(launcher, "require_auto_login_arguments", lambda intent, **kwargs: (
        "/noconsole", f"/login:{intent.username}:evejs-local", f"/autoSelectCharacter:{intent.character_id}"))
    monkeypatch.setattr(launcher, "launch_eve_client", lambda exe, env, cwd, *, arguments: captured.append(arguments) or object())
    for username, char_id in (("account-a", 90000001), ("account-b", 90000002)):
        launcher.launch_client(str(root), profile, client_path=str(client),
            auto_login=AutoLoginLaunch(username, char_id), shared_settings=SharedClientSettings(folder, tmp_path / "alias"))
    assert "/login:account-a:evejs-local" in captured[0]
    assert "/login:account-b:evejs-local" in captured[1]
    assert "/autoSelectCharacter:90000001" in captured[0]
    assert "/autoSelectCharacter:90000002" in captured[1]
    assert all(SHARED_PROFILE_ARGUMENT in arguments for arguments in captured)
    assert all("/server:127.0.0.1" in arguments for arguments in captured)


@pytest.mark.parametrize("fails", [False, True])
def test_spawn_dispatches_captured_notification_outcome(tmp_path, monkeypatch, fails):
    root, profile, client, _mod = fixture_launcher(tmp_path, monkeypatch, "DLSS5")
    prepared = runtime.PreparedClientMods({}, (), ())
    monkeypatch.setattr(launcher, "prepare_public_client_mods", lambda *_args, **_kwargs: prepared)
    events = []
    process = object()
    def spawn(*args, **kwargs):
        events.append("spawn")
        if fails:
            raise OSError("private command text must not be sent to a helper")
        return process
    def notify(captured, **kwargs):
        assert captured is prepared
        events.append(kwargs)
    monkeypatch.setattr(launcher, "launch_eve_client", spawn)
    monkeypatch.setattr(launcher, "start_client_notifications", notify)
    if fails:
        with pytest.raises(OSError):
            launcher.launch_client(str(root), profile, client_path=str(client))
        assert events == ["spawn", {"backend": "native", "error_type": "OSError"}]
    else:
        assert launcher.launch_client(str(root), profile, client_path=str(client)) is process
        assert events == ["spawn", {"backend": "native", "process": process}]


def test_unrelated_public_mod_keeps_installed_renderer_detection(tmp_path, monkeypatch):
    root, profile, client, _mod = fixture_launcher(tmp_path, monkeypatch, "another-mod")
    events = []
    monkeypatch.setattr(launcher, "prepare_installed_dlss5", lambda *_args: events.append("renderer") or {})
    monkeypatch.setattr(launcher, "prepare_public_client_mods", lambda *_args, **_kwargs: runtime.PreparedClientMods({}, (), ()))
    monkeypatch.setattr(launcher, "launch_eve_client", lambda *_args, **_kwargs: events.append("spawn"))
    launcher.launch_client(str(root), profile, client_path=str(client))
    assert events == ["renderer", "spawn"]


def test_failed_public_preparation_never_spawns_the_client(tmp_path, monkeypatch):
    root, profile, client, _mod = fixture_launcher(tmp_path, monkeypatch, "DLSS5")
    def fail(*_args, **_kwargs):
        raise runtime.ModApiRuntimeError("profile conflict")
    monkeypatch.setattr(launcher, "prepare_public_client_mods", fail)
    monkeypatch.setattr(launcher, "launch_eve_client", lambda *_args, **_kwargs: pytest.fail("must not spawn"))
    with pytest.raises(runtime.ModApiRuntimeError, match="profile conflict"):
        launcher.launch_client(str(root), profile, client_path=str(client))
