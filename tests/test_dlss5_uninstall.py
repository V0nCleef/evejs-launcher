from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from src.core import dlss5
from src.core import dlss5_uninstall as uninstall
from test_dlss5_client_mod import _package, _client, _config


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


REVIEWED_VERSIONS = (
    "0.5.2-dev", "0.5.3-dev", "0.5.4-dev", "0.5.5-dev", "0.5.5", "0.5.6",
)


def _fixture_payload(name: str, version: str) -> bytes:
    if version in ("0.5.5-dev", "0.5.5", "0.5.6"):
        version = "0.5.4-dev"
    if name == "bin64/dxgi.dll":
        return (name + ":" + version).encode()
    if name == "code.ccp" and version == "0.5.4-dev":
        return (name + ":process-local-v12").encode()
    return name.encode()


@pytest.fixture(params=REVIEWED_VERSIONS)
def case(tmp_path, monkeypatch, request):
    version = request.param
    root = tmp_path / "EveJS"
    root.mkdir()
    client = _client(tmp_path)
    _config(root, client, marked=False)
    config = root / "tools/ClientSETUP/scripts/EvEJSConfig.bat"
    original_config = config.read_bytes()
    _config(root, client, marked=True)
    package, manager, descriptor = _package(
        root, monkeypatch, manager_bytes=("# fixture manager " + version).encode())
    descriptor["version"] = version
    if version == "0.5.6":
        descriptor["schemaVersion"] = 3
        descriptor["compatibility"] = {
            "evejsVersionPolicy": "any",
            "clientBuild": 3396210,
            "profile": "DLSS5",
        }
    (package / dlss5._CLIENT_MANIFEST_NAME).write_text(json.dumps(descriptor))
    state = (
        client.parent / "_evejs/dlss5/install"
        if version == "0.5.6"
        else root / "_local/dlss5/install"
    )
    backup = state / "backups/fixture"
    (backup / "client").mkdir(parents=True)
    (backup / "config").mkdir()
    (backup / "config/EvEJSConfig.bat").write_bytes(original_config)
    payload = {name: _fixture_payload(name, version) for name in (
        "code.ccp", "bin64/dxgi.dll", "bin64/nvngx_dlssnr.dll",
        "bin64/sl.dlss_nr.dll", "bin64/renodx-dlss5.addon64")}
    files = {name: (len(data), digest(data)) for name, data in payload.items()}
    monkeypatch.setattr(dlss5, "_STANDALONE_PAYLOADS_BY_VERSION", {version: files})
    monkeypatch.setattr(dlss5, "_STANDALONE_EXE", (7, digest(b"fixture")))
    operations = []
    for name, (size, installed_hash) in files.items():
        (client / name).write_bytes(payload[name])
        original = b"stock-code" if name == "code.ccp" else None
        backup_name = "client/" + name if original else None
        if original:
            (backup / backup_name).write_bytes(original)
        operations.append({"source": name, "destination": name,
                           "kind": "replace" if original else "add", "backup": backup_name,
                           "originalSha256": digest(original) if original else None,
                           "installedSha256": installed_hash, "installedBytes": size})
    (client / "bin64/ReShade.ini").write_text("[RenoDX.DLSS5]\nEnableHooks=2\n")
    receipt = {"schemaVersion": 5 if version == "0.5.6" else 4,
               "integrationVersion": version, "status": "installed",
               "profile": "DLSS5", "workspaceRoot": str(root.parent), "evejsRoot": str(root),
               "clientRoot": str(client), "stateRoot": str(state), "stateDirectory": "state",
               "backupDirectory": "backups/fixture", "operations": operations,
               "executable": {"path": "bin64/exefile.exe", "sha256": digest(b"fixture"), "modified": False},
               "config": {"path": str(config), "backup": "config/EvEJSConfig.bat",
                          "originalSha256": digest(original_config), "installedSha256": digest(config.read_bytes())},
               "reshadeConfig": {"originalExists": False, "backup": None, "managedKeys": []}}
    if version == "0.5.6":
        receipt["stateScope"] = "client"
        receipt.pop("stateDirectory")
    journal = state / "active-install.json"
    journal.write_text(json.dumps(receipt))
    events = []

    @contextmanager
    def lock():
        events.append("lock")
        try:
            yield
        finally:
            events.append("unlock")

    monkeypatch.setattr(uninstall.platform_api, "serialize_evejs_client_trust_and_spawn", lock)
    monkeypatch.setattr(uninstall, "_running_client_pids", lambda: [])

    def restore(mod, **kwargs):
        events.append("restore")
        assert kwargs["action"] == "Restore"
        assert not package.exists()
        assert mod.path.is_relative_to(root / "_local/dlss5/uninstalled-packages")
        assert mod.manager_path == mod.path / dlss5._CLIENT_MANAGER_RELATIVE_PATH
        assert mod.manager_path.read_bytes() == manager_bytes
        assert kwargs["evejs_root"] == root and kwargs["client_root"] == client
        assert kwargs["state_root"] == state and kwargs["workspace_root"] == root.parent
        for operation in receipt["operations"]:
            target = client / operation["destination"]
            if operation["kind"] == "replace":
                target.write_bytes((backup / operation["backup"]).read_bytes())
            elif target.exists():
                target.unlink()
        config.write_bytes(original_config)
        if (client / "bin64/ReShade.ini").exists():
            (client / "bin64/ReShade.ini").unlink()
        receipt["status"] = "restored"
        journal.write_text(json.dumps(receipt))

    manager_bytes = manager.read_bytes()
    monkeypatch.setattr(dlss5, "_run_dlss5_manager", restore)
    request = uninstall.DLSS5UninstallRequest(root, client, package, digest(manager_bytes))
    return {**locals(), "save": lambda: journal.write_text(json.dumps(receipt))}


def test_restore_archives_first_and_preserves_receipt_backups(case):
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert result.success, result.message
    assert result.request == case["request"]
    assert result.archive_path.is_dir()
    assert not case["package"].exists()
    assert case["backup"].is_dir() and case["journal"].is_file()
    assert case["events"] == ["lock", "restore", "unlock"]
    assert dlss5.discover_dlss5_client_mod(case["root"]) is None


def test_other_native_version_refused_before_archival(case):
    for other in REVIEWED_VERSIONS:
        if _fixture_payload("bin64/dxgi.dll", other) == _fixture_payload("bin64/dxgi.dll", case["version"]):
            continue
        (case["client"] / "bin64/dxgi.dll").write_bytes(_fixture_payload("bin64/dxgi.dll", other))
        result = uninstall.uninstall_dlss5_client_mod(case["request"])
        assert not result.success and result.archive_path is None
        assert case["package"].exists() and "restore" not in case["events"]


@pytest.mark.parametrize("rewrite_receipt", [False, True])
def test_other_guard_version_refused_before_archival_even_with_matching_claim(case, rewrite_receipt):
    # 0.5.2 and 0.5.3 share V11 code; 0.5.4 must never be accepted as either.
    other = "0.5.3-dev" if case["version"] in ("0.5.4-dev", "0.5.5-dev", "0.5.5", "0.5.6") else "0.5.4-dev"
    data = _fixture_payload("code.ccp", other)
    (case["client"] / "code.ccp").write_bytes(data)
    if rewrite_receipt:
        operation = next(op for op in case["receipt"]["operations"] if op["destination"] == "code.ccp")
        operation.update(installedBytes=len(data), installedSha256=digest(data))
        case["save"]()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and result.archive_path is None
    assert case["package"].exists() and "restore" not in case["events"]


def test_restore_failure_retains_recovery_kit_outside_discovery(case, monkeypatch):
    def fail(*args, **kwargs):
        raise dlss5.DLSS5ClientModError("backup mismatch")
    monkeypatch.setattr(dlss5, "_run_dlss5_manager", fail)
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success
    assert result.archive_path.is_dir()
    assert "backup mismatch" in result.message and str(result.archive_path) in result.message
    assert "not complete" in result.message.lower()
    assert not case["package"].exists() and case["backup"].exists()


def test_archive_failure_never_runs_restore(case, monkeypatch):
    monkeypatch.setattr(uninstall, "_archive_package", lambda *args: (_ for _ in ()).throw(OSError("rename denied")))
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and result.archive_path is None
    assert case["package"].exists()
    assert "restore" not in case["events"]


def test_running_client_refused_before_any_move(case, monkeypatch):
    monkeypatch.setattr(uninstall, "_running_client_pids", lambda: [1234])
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and "1234" in result.message
    assert case["package"].exists() and result.archive_path is None


def test_process_enumeration_failure_is_closed(case, monkeypatch):
    monkeypatch.setattr(uninstall, "_running_client_pids", lambda: (_ for _ in ()).throw(OSError("snapshot failed")))
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()


@pytest.mark.parametrize("status", ["prepared", "backedUp", "applying", "unknown", None])
def test_partial_or_unknown_receipt_refused_untouched(case, status):
    case["receipt"]["status"] = status
    case["save"]()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()
    assert "Partial or unknown" in result.message


def test_missing_receipt_refused(case):
    case["journal"].unlink()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()
    assert "No install receipt" in result.message


@pytest.mark.parametrize("key", ["workspaceRoot", "evejsRoot", "clientRoot", "stateRoot"])
def test_other_installation_receipt_refused(case, key):
    case["receipt"][key] = str(case["root"] / "another-root")
    case["save"]()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()


def test_stale_manager_snapshot_refused(case):
    request = uninstall.DLSS5UninstallRequest(case["root"], case["client"], case["package"], "0" * 64)
    result = uninstall.uninstall_dlss5_client_mod(request)
    assert not result.success and case["package"].exists()


def test_tampered_manager_refused(case):
    case["manager"].write_bytes(b"tampered")
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()


def test_wrong_package_path_refused(case):
    request = uninstall.DLSS5UninstallRequest(case["root"], case["client"], case["package"].parent, case["request"].manager_sha256)
    result = uninstall.uninstall_dlss5_client_mod(request)
    assert not result.success and case["package"].exists()


@pytest.mark.parametrize("target", ["client", "config", "backup"])
def test_user_drift_refused_before_archival(case, target):
    path = {"client": case["client"] / "code.ccp", "config": case["config"], "backup": case["backup"] / "client/code.ccp"}[target]
    path.write_bytes(b"newer user content")
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()
    assert path.read_bytes() == b"newer user content"
    assert "changed or mismatched" in result.message


def test_operation_outside_exact_payload_refused(case):
    case["receipt"]["operations"][0]["destination"] = "../other-root/file"
    case["save"]()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()


@pytest.mark.parametrize("status", ["restored", "rolledBack"])
def test_clean_terminal_receipt_archives_without_manager(case, monkeypatch, status):
    # Materialize the same post-Restore state without invoking any real manager.
    for operation in case["receipt"]["operations"]:
        path = case["client"] / operation["destination"]
        if operation["kind"] == "replace":
            path.write_bytes((case["backup"] / operation["backup"]).read_bytes())
        else:
            path.unlink()
    case["config"].write_bytes(case["original_config"])
    (case["client"] / "bin64/ReShade.ini").unlink()
    case["receipt"]["status"] = status
    case["save"]()
    monkeypatch.setattr(dlss5, "_run_dlss5_manager", lambda *a, **k: pytest.fail("terminal receipt must not invoke Restore"))
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert result.success, result.message


def test_terminal_receipt_cannot_clean_up_another_installation(case):
    case["receipt"]["status"] = "restored"
    case["save"]()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()
    assert (case["client"] / "bin64/dxgi.dll").exists()


def test_success_exit_without_restored_receipt_is_failure(case, monkeypatch):
    monkeypatch.setattr(dlss5, "_run_dlss5_manager", lambda *a, **k: None)
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and result.archive_path.is_dir()


@pytest.mark.parametrize("linked", ["package", "state", "client", "config_parent", "archive_parent"])
def test_reparse_boundary_refused(case, monkeypatch, linked):
    archive_parent = case["root"] / "_local/dlss5/uninstalled-packages"
    archive_parent.mkdir(parents=True)
    target = {"package": case["package"], "state": case["state"], "client": case["client"],
              "config_parent": case["config"].parent, "archive_parent": archive_parent}[linked]
    original = dlss5._is_reparse_point
    monkeypatch.setattr(dlss5, "_is_reparse_point", lambda p: p == target or original(p))
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()
    assert "unlinked" in result.message


def test_client_appearing_after_archival_blocks_restore(case, monkeypatch):
    checks = iter([[], [9876]])
    monkeypatch.setattr(uninstall, "_running_client_pids", lambda: next(checks))
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and result.archive_path.is_dir()
    assert "appeared" in result.message and "restore" not in case["events"]


def test_archive_preserves_unrelated_root_data(case):
    sentinel = case["root"] / "_local/gameStore.sqlite"
    sentinel.parent.mkdir(exist_ok=True)
    sentinel.write_bytes(b"player data")
    other = case["root"] / "mods/OtherMod"
    other.mkdir()
    (other / "loader.js").write_bytes(b"unchanged")
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert result.success, result.message
    assert sentinel.read_bytes() == b"player data"
    assert (other / "loader.js").read_bytes() == b"unchanged"


def test_duplicate_json_keys_are_rejected(case):
    schema = case["receipt"]["schemaVersion"]
    case["journal"].write_text(f'{{"schemaVersion":{schema},"schemaVersion":{schema}}}')
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and "duplicate" in result.message.lower()
    assert case["package"].exists()


def test_stale_installed_receipt_with_original_config_is_refused(case):
    case["config"].write_bytes(case["original_config"])
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and "changed or mismatched" in result.message
    assert case["package"].exists()


def test_changed_manager_after_archive_keeps_kit_and_never_executes(case, monkeypatch):
    archive = uninstall._archive_package
    def changed(*args):
        path = archive(*args)
        (path / dlss5._CLIENT_MANAGER_RELATIVE_PATH).write_bytes(b"changed after move")
        return path
    monkeypatch.setattr(uninstall, "_archive_package", changed)
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and result.archive_path.is_dir()
    assert "approved snapshot" in result.message and "restore" not in case["events"]


def test_manager_action_restore_is_fixed_and_ensure_remains_default(tmp_path, monkeypatch):
    root = tmp_path / "EveJS"
    root.mkdir()
    _package(root, monkeypatch)
    package = dlss5.discover_dlss5_client_mod(root)
    powershell = tmp_path / "powershell.exe"
    monkeypatch.setattr(dlss5, "_windows_powershell_path", lambda: powershell)
    monkeypatch.setattr(dlss5, "_windows_powershell_environment", lambda _: {})
    commands = []
    monkeypatch.setattr(dlss5, "_run_dlss5_preparation_process", lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    args = dict(workspace_root=root.parent, evejs_root=root, client_root=root / "client/tq",
                state_root=root / "client/_evejs/dlss5/install")
    dlss5._run_dlss5_manager(package, **args)
    dlss5._run_dlss5_manager(package, **args, action="Restore")
    assert [c[c.index("-Action") + 1] for c in commands] == ["Ensure", "Restore"]
    assert all("-Force" not in c for c in commands)
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5._run_dlss5_manager(package, **args, action="Install")
    assert len(commands) == 2


@pytest.mark.parametrize("failure", [None, "snapshot", "enumeration"])
def test_native_process_snapshot_is_fail_closed_and_closes_handle(monkeypatch, failure):
    closed = []
    class Function:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    class API:
        pass
    api = API()
    api.CreateToolhelp32Snapshot = Function(lambda *args: uninstall.ctypes.c_void_p(-1).value if failure == "snapshot" else 44)
    api.CloseHandle = Function(lambda handle: closed.append(handle) or 1)
    entries = iter([(10, "other.exe"), (20, "ExeFile.exe"), (30, "exefile.exe")])
    def advance(handle, pointer):
        try:
            pid, name = next(entries)
        except StopIteration:
            uninstall.ctypes.set_last_error(5 if failure == "enumeration" else 18)
            return 0
        pointer._obj.pid = pid
        pointer._obj.name = name
        return 1
    api.Process32FirstW = Function(advance)
    api.Process32NextW = Function(advance)
    monkeypatch.setattr(uninstall.ctypes, "WinDLL", lambda *args, **kwargs: api)
    if failure:
        with pytest.raises(OSError):
            uninstall._running_client_pids()
    else:
        assert uninstall._running_client_pids() == [20, 30]
    assert closed == ([] if failure == "snapshot" else [44])


@pytest.mark.parametrize("backup", ["backups/.. ", "backups/fixture.", "../outside", "backups/../outside"])
def test_ambiguous_backup_directory_refused(case, backup):
    case["receipt"]["backupDirectory"] = backup
    case["save"]()
    result = uninstall.uninstall_dlss5_client_mod(case["request"])
    assert not result.success and case["package"].exists()
