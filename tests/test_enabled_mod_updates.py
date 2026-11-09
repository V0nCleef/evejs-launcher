"""A declared enabled-update transaction never tears down pending runtime work."""
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import zipfile
import uuid
import subprocess

import pytest

from src.core import mod_updates
from src.core.mod_api_manifest import read_api_manifest, ModApiManifestError
from src.core.mod_api_runtime import ModHelperResult
from src.core.mod_manifest import scan_mods
from src.core.mod_operations import ModOperationContext
from src.core.mod_update_source import ModRelease, ModUpdateError, parse_update_source

PROTOCOL = "evejs-mod-enabled-update-v1"
ACTIONS = ["prepare_update", "apply_update", "commit_update", "rollback_update"]
SOURCE = dict(provider="github", repository="Author/Example", asset="Example-{version}.zip", preserveFiles=["config.json"])


@pytest.fixture
def enabled_fixture(tmp_path, monkeypatch):
    from src import config
    from src.core import mod_management
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "appdata")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration",
                        lambda _: (_ for _ in ()).throw(mod_management.ModNotManagedError("Fixture")))
    root = tmp_path / "EveJS"
    target = root / "mods/Example"
    target.mkdir(parents=True)
    old = dict(schemaVersion=3, id="author.example", displayName="Example", version="1.0.0",
               kind="loader", activation=dict(strategy="loader_rename"), restart="game_server", updates=SOURCE,
               launcherApi=dict(version=1, helper=dict(runtime="node", path="helper.js"),
                                capabilities=["install", "verify", "prepare_disable", "prepare_remove"]))
    (target / "evejs-launcher.mod.json").write_text(json.dumps(old))
    (target / "loader.js").write_text("// old")
    (target / "helper.js").write_text("// old helper")
    (target / "config.json").write_text('{"user":42}')
    (root / "native.bin").write_bytes(b"old native")
    (root / "financial.json").write_text('{"pending":["existing-operation"],"wallet":42}')
    incoming = dict(old, version="1.1.0", launcherApi=dict(old["launcherApi"], minLauncherVersion="1.0.79",
                    capabilities=old["launcherApi"]["capabilities"] + ACTIONS,
                    enabledUpdate=dict(protocol=PROTOCOL, compatibility="author.example.pending-v1", fromVersions=["1.0.0"])))
    archive = tmp_path / "incoming.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name, value in {"evejs-launcher.mod.json":json.dumps(incoming), "loader.js":"// new",
                            "helper.js":"// new helper", "config.json":'{"user":0}'}.items():
            z.writestr("Example/" + name, value)
    release = ModRelease(parse_update_source(SOURCE), "1.1.0", "v1.1.0", "", "", "", archive.stat().st_size,
                         mod_id="author.example", metadata_verified=True)
    return SimpleNamespace(root=root, target=target, old=old, incoming=incoming, archive=archive,
                           release=release, operation=ModOperationContext(root),
                           download=lambda _release, dest: shutil.copyfile(archive, dest))


def test_enabled_update_descriptor_is_an_explicit_versioned_opt_in(enabled_fixture):
    f = enabled_fixture
    (f.target / "evejs-launcher.mod.json").write_text(json.dumps(f.incoming))
    descriptor = read_api_manifest(f.root, f.target)
    assert descriptor.launcher_api.enabled_update.protocol == PROTOCOL
    assert descriptor.launcher_api.enabled_update.from_versions == ("1.0.0",)


@pytest.mark.parametrize("change", [dict(protocol="unknown"), dict(fromVersions=[]),
    dict(fromVersions=["*"]), dict(compatibility=""), dict(extra=True)])
def test_enabled_update_descriptor_refuses_unknown_contract(enabled_fixture, change):
    f = enabled_fixture
    document = json.loads(json.dumps(f.incoming))
    document["launcherApi"]["enabledUpdate"].update(change)
    (f.target / "evejs-launcher.mod.json").write_text(json.dumps(document))
    with pytest.raises(ModApiManifestError):
        read_api_manifest(f.root, f.target)


@pytest.fixture
def producer(enabled_fixture, monkeypatch):
    from src.core import mod_enabled_updates as enabled
    f = enabled_fixture
    calls = []
    native_journal = f.root / "native-update.json"
    terminal = f.root / "native-terminal.json"
    f.fail_action = None
    f.lose_commit_ack = False

    def helper(descriptor, action, context, **kwargs):
        calls.append(action)
        assert descriptor.folder.name == "provider"
        assert json.loads((descriptor.folder / "config.json").read_text()) == {"user":42}
        assert kwargs["update"]["fromModRoot"] == str(f.target)
        assert kwargs["update"]["fromVersion"] == "1.0.0"
        if action == "prepare_update":
            if not native_journal.exists():
                native_journal.write_text(json.dumps(dict(old=(f.root / "native.bin").read_text(),
                                                         context=kwargs["update"])))
        elif action == "apply_update":
            (f.root / "native.bin").write_bytes(b"new native")
        elif action == "rollback_update":
            # Native rollback must precede restoring the old package folder.
            assert (f.root / "native.bin").read_bytes() in (b"old native", b"new native")
            (f.root / "native.bin").write_text(json.loads(native_journal.read_text())["old"])
            terminal.write_text("rolled_back")
        elif action == "commit_update":
            assert json.loads(next((f.root / mod_updates.UPDATE_ROOT).glob("*/update.json")).read_text())["phase"] == "committing"
            assert scan_mods(f.root)[0].active
            terminal.write_text("committed")
            if f.lose_commit_ack:
                f.lose_commit_ack = False
                raise RuntimeError("lost commit acknowledgement")
        if f.fail_action == action:
            f.fail_action = None
            raise RuntimeError("injected provider failure")
        return ModHelperResult(descriptor, context, action, "test-request", True, "ready", "", (), (), (), {}, (), None,
                               f.root / "request.json")

    monkeypatch.setattr(enabled, "run_mod_helper_locked", helper)
    monkeypatch.setattr(mod_updates, "change_mod_state", lambda *a, **k: pytest.fail("No old/new install or ordinary cleanup in handoff"))
    monkeypatch.setattr("src.core.local_mod_packages.LocalModPackages.disable", lambda *a, **k: pytest.fail("No ordinary disable in handoff"))
    f.calls = calls
    f.install = lambda: mod_updates.install_release(scan_mods(f.root)[0], f.release, f.operation,
                                                    guard=lambda: None, downloader=f.download)
    return f


def test_enabled_update_preserves_pending_work_and_settings_without_old_cleanup(producer):
    f = producer
    before = (f.root / "financial.json").read_bytes()
    assert f.install() == "Update complete."
    assert f.calls == ["prepare_update", "apply_update", "commit_update"]
    assert scan_mods(f.root)[0].version == "1.1.0" and scan_mods(f.root)[0].active
    assert (f.root / "native.bin").read_bytes() == b"new native"
    assert (f.root / "financial.json").read_bytes() == before
    assert json.loads((f.target / "config.json").read_text()) == {"user": 42}
    assert not mod_updates.pending_updates(f.root)
    journal = next((f.root / mod_updates.UPDATE_ROOT).glob("*/update.json"))
    assert not (journal.parent / "provider").exists()


def test_failed_apply_rolls_back_native_before_old_package_without_old_install(producer):
    f = producer
    f.fail_action = "apply_update"
    before = (f.root / "financial.json").read_bytes()
    with pytest.raises(ModUpdateError, match="restored"):
        f.install()
    assert f.calls == ["prepare_update", "apply_update", "rollback_update"]
    assert scan_mods(f.root)[0].version == "1.0.0" and scan_mods(f.root)[0].active
    assert (f.root / "native.bin").read_bytes() == b"old native"
    assert (f.root / "financial.json").read_bytes() == before
    assert not mod_updates.pending_updates(f.root)


@pytest.mark.parametrize("phase,changed", [("applying", False), ("complete", True)])
def test_provider_cleanup_retains_unfinished_or_changed_evidence(producer, phase, changed):
    from src.core import mod_enabled_updates as enabled
    f = producer
    job = f.root / mod_updates.UPDATE_ROOT / ("a" * 32)
    provider = job / "provider"
    provider.mkdir(parents=True)
    payload = provider / "helper.js"
    payload.write_text("// retained provider")
    packages = enabled.LocalModPackages(f.root)
    data = {"mode": enabled.ENABLED_UPDATE_PROTOCOL, "phase": phase,
            "providerFingerprint": enabled._fingerprint(provider, packages.limits)}
    journal = job / "update.json"
    journal.write_text(json.dumps(data))
    if changed:
        payload.write_text("// unexpected concurrent edit")
    enabled._discard_provider(packages, journal)
    assert payload.exists()


def test_lost_commit_ack_finishes_commit_never_rolls_back(producer):
    f = producer
    f.lose_commit_ack = True
    assert f.install() == "Update complete."
    assert f.calls == ["prepare_update", "apply_update", "commit_update", "commit_update"]
    assert scan_mods(f.root)[0].version == "1.1.0" and scan_mods(f.root)[0].active
    assert (f.root / "native.bin").read_bytes() == b"new native"


@pytest.mark.parametrize("phase", ["prepared", "preparing", "handoff_ready", "quiescing", "swapping", "installed",
                                   "applying", "applied", "enabling", "enabled", "committing", "complete"])
def test_crash_reentry_obeys_durable_commit_boundary(producer, monkeypatch, phase):
    from src.core import mod_enabled_updates as enabled
    f = producer
    save = enabled._save
    class Crash(BaseException):
        pass
    def crash(journal, data, current_phase):
        save(journal, data, current_phase)
        if current_phase == phase:
            raise Crash()
    monkeypatch.setattr(enabled, "_save", crash)
    before = (f.root / "financial.json").read_bytes()
    with pytest.raises(Crash):
        f.install()
    monkeypatch.setattr(enabled, "_save", save)
    journal = next((f.root / mod_updates.UPDATE_ROOT).glob("*/update.json"))
    mod_updates.recover_update(journal, f.operation, guard=lambda: None)
    committed = phase in {"committing", "complete"}
    mod = scan_mods(f.root)[0]
    assert mod.active and mod.version == ("1.1.0" if committed else "1.0.0")
    assert (f.root / "native.bin").read_bytes() == (b"new native" if committed else b"old native")
    assert (f.root / "financial.json").read_bytes() == before
    assert not mod_updates.pending_updates(f.root)
    if committed:
        assert "rollback_update" not in f.calls


def test_crash_between_package_moves_recovers_with_retained_incoming_provider(producer, monkeypatch):
    f = producer
    rename = Path.rename
    class Crash(BaseException):
        pass
    def crash(source, target):
        if source.name == "package":
            raise Crash()
        return rename(source, target)
    monkeypatch.setattr(Path, "rename", crash)
    with pytest.raises(Crash):
        f.install()
    monkeypatch.setattr(Path, "rename", rename)
    journal, _ = mod_updates.pending_updates(f.root)[0]
    mod_updates.recover_update(journal, f.operation, guard=lambda: None)
    assert scan_mods(f.root)[0].version == "1.0.0" and scan_mods(f.root)[0].active
    assert f.calls == ["prepare_update", "rollback_update"]


def test_ineligible_opt_in_never_calls_old_cleanup_or_incoming_helper(producer):
    f = producer
    with zipfile.ZipFile(f.archive) as z:
        files = {name:z.read(name) for name in z.namelist()}
    doc = json.loads(files["Example/evejs-launcher.mod.json"])
    doc["launcherApi"]["enabledUpdate"]["fromVersions"] = ["0.9.0"]
    files["Example/evejs-launcher.mod.json"] = json.dumps(doc)
    with zipfile.ZipFile(f.archive, "w") as z:
        for name, content in files.items():z.writestr(name, content)
    with pytest.raises(ModUpdateError, match="does not admit"):
        f.install()
    assert not f.calls and scan_mods(f.root)[0].active


@pytest.mark.parametrize("action", ACTIONS)
def test_real_helper_protocol_carries_exact_update_context(enabled_fixture, action):
    from src.core import mod_api_runtime as runtime
    from src.core.mod_settings import ModSettingsContext
    f = enabled_fixture
    (f.target / "evejs-launcher.mod.json").write_text(json.dumps(f.incoming))
    descriptor = read_api_manifest(f.root, f.target)
    update = dict(protocol=PROTOCOL, transactionId=str(uuid.uuid4()), fromVersion="1.0.0",
                  fromModRoot=str(f.target), toVersion="1.1.0")
    def runner(command, **kwargs):
        request = json.loads(Path(command[-3]).read_text())
        assert request["update"] == update and request["action"] == action
        Path(command[-1]).write_text(json.dumps(dict(protocol=request["protocol"], requestId=request["requestId"],
            success=True, state="ready", message="", restartRequired=[], contributions=[], environment={}, arguments=[])))
        return subprocess.CompletedProcess(command, 0)
    assert runtime.run_mod_helper(descriptor, action, ModSettingsContext(f.root, f.target),
                                  update=update, runner=runner).require_ready()


@pytest.mark.parametrize("change", [dict(protocol="forged"), dict(transactionId="../wrong"),
    dict(fromVersion="0.9.0"), dict(toVersion="2.0.0"), dict(fromModRoot="C:/Other/mods/Example"), dict(extra=True)])
def test_invalid_update_request_refuses_before_helper_execution(enabled_fixture, change):
    from src.core import mod_api_runtime as runtime
    from src.core.mod_settings import ModSettingsContext
    f = enabled_fixture
    (f.target / "evejs-launcher.mod.json").write_text(json.dumps(f.incoming))
    descriptor = read_api_manifest(f.root, f.target)
    update = dict(protocol=PROTOCOL, transactionId=str(uuid.uuid4()), fromVersion="1.0.0",
                  fromModRoot=str(f.target), toVersion="1.1.0", **{})
    update.update(change)
    with pytest.raises(runtime.ModApiRuntimeError):
        runtime.run_mod_helper(descriptor, "prepare_update", ModSettingsContext(f.root, f.target),
            update=update, runner=lambda *a, **kw: pytest.fail("Unvalidated helper was executed"))


@pytest.mark.parametrize("change", [dict(environment={"UNRELATED":"1"}), dict(arguments=["/other:1"]),
    dict(restartRequired=["game_server"]), dict(clientScriptDelivery="login_expression_v1")])
def test_update_reply_cannot_smuggle_unrelated_changes(enabled_fixture, change):
    from src.core import mod_api_runtime as runtime
    from src.core.mod_settings import ModSettingsContext
    f = enabled_fixture
    (f.target / "evejs-launcher.mod.json").write_text(json.dumps(f.incoming))
    descriptor = read_api_manifest(f.root, f.target)
    reply = dict(protocol=runtime.PROTOCOL, requestId="correlated", success=True, state="ready", message="",
                 restartRequired=[], contributions=[], environment={}, arguments=[])
    reply.update(change)
    with pytest.raises(runtime.ModApiRuntimeError):
        runtime.validate_helper_result(reply, descriptor, ModSettingsContext(f.root, f.target),
                                        "prepare_update", "correlated", f.root / "request.json")


@pytest.mark.parametrize("drift", ["provider", "target", "registry"])
def test_recovery_refuses_drift_before_native_rollback(producer, monkeypatch, drift):
    from src.core import mod_enabled_updates as enabled
    f = producer
    save = enabled._save
    class Crash(BaseException):pass
    def crash(journal, data, phase):
        save(journal, data, phase)
        if phase == "applied":raise Crash()
    monkeypatch.setattr(enabled, "_save", crash)
    with pytest.raises(Crash):f.install()
    monkeypatch.setattr(enabled, "_save", save)
    journal, data = mod_updates.pending_updates(f.root)[0]
    if drift == "provider":(journal.parent / "provider/helper.js").write_text("unreviewed helper")
    elif drift == "target":(f.target / "loader.js.disabled").write_text("changed user payload")
    else:
        from src.core.local_mod_packages import LocalModPackages
        packages = LocalModPackages(f.root)
        registry = packages._load()
        registry["records"][data["recordId"]]["archive_path"] = "changed-owner"
        packages._save(registry)
    before = list(f.calls)
    with pytest.raises(ModUpdateError):mod_updates.recover_update(journal, f.operation, guard=lambda: None)
    assert f.calls == before
    assert (f.root / "native.bin").read_bytes() == b"new native"


@pytest.mark.parametrize("phase", ["rolling_back", "restoring"])
def test_interrupted_rollback_reenters_through_incoming_provider(producer, monkeypatch, phase):
    from src.core import mod_enabled_updates as enabled
    f = producer
    save = enabled._save
    class Crash(BaseException):pass
    def crash(journal, data, current_phase):
        save(journal, data, current_phase)
        if current_phase == phase:raise Crash()
    f.fail_action = "apply_update"
    monkeypatch.setattr(enabled, "_save", crash)
    with pytest.raises(Crash):f.install()
    monkeypatch.setattr(enabled, "_save", save)
    journal, _ = mod_updates.pending_updates(f.root)[0]
    mod_updates.recover_update(journal, f.operation, guard=lambda: None)
    assert scan_mods(f.root)[0].version == "1.0.0" and scan_mods(f.root)[0].active
    assert (f.root / "native.bin").read_bytes() == b"old native"
    assert f.calls[-1] == "rollback_update"


def test_schema1_prepared_failure_verifies_old_without_install(enabled_fixture, monkeypatch):
    from src.core import mod_api_runtime as runtime
    from src.core.local_mod_packages import LocalModPackages
    f = enabled_fixture
    packages = LocalModPackages(f.root)
    old = scan_mods(f.root)[0]
    record = packages.adopt(old)
    folder = f.root / mod_updates.UPDATE_ROOT / uuid.uuid4().hex
    folder.mkdir(parents=True)
    journal = folder / "update.json"
    data = dict(schemaVersion=1, phase="prepared", target="mods/Example", modId=old.id,
                packageKind="loader", oldVersion="1.0.0", newVersion="1.1.0", enabled=True,
                recordId=record.record_id, oldFingerprint="")
    mod_updates._save(journal, data)
    calls = []
    def helper(descriptor, action, context, **kwargs):
        calls.append(action)
        return ModHelperResult(descriptor, context, action, "verify-old", True, "ready", "", (), (), (), {}, (), None,
                               f.root / "request.json")
    monkeypatch.setattr(runtime, "run_mod_helper_locked", helper)
    monkeypatch.setattr(mod_updates, "change_mod_state", lambda *a, **kw: pytest.fail("Unchanged old package must not install again"))
    mod_updates.recover_update(journal, f.operation, guard=lambda: None)
    assert calls == ["verify"] and scan_mods(f.root)[0].active
    assert json.loads(journal.read_text())["phase"] == "rolled_back"
