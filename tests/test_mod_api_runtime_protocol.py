"""The public helper contract is package-neutral and uses disposable roots."""
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import mod_api_runtime as runtime
from src.core.mod_api_manifest import read_api_manifest
from src.core.mod_contributions import ContributionConflict, ContributionStore, FileTarget, read_target
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lock
from src.core.mod_manifest import ActivationKind, Mod
from src.core.mod_settings import ModSettingsContext, ModSettingsSession, profile_identity
from src.core.mod_settings_schema import SettingsFile


def make_mod(tmp_path, name="graphics", version="0.5.8", *, kind="client-package", helper_runtime="executable"):
    root = tmp_path / "runtime"
    folder = root / "mods" / name
    client = tmp_path / "client"
    profile = tmp_path / "config" / "Profiles" / "pilot-a"
    settings = tmp_path / "LocalAppData" / "CCP" / "EVE" / "pilot-a" / "settings"
    for path in (folder, client, profile, settings):
        path.mkdir(parents=True, exist_ok=True)
    helper = folder / {"executable": "helper.exe", "node": "helper.js", "powershell": "helper.ps1"}[helper_runtime]
    helper.write_bytes(b"INERT FIXTURE: executed only through the test runner")
    declaration = {
        "schemaVersion": 3, "id": name, "displayName": name.title(), "version": version,
        "kind": kind, "activation": {"strategy": "client_package" if kind == "client-package" else "package"},
        "restart": "client", "supportedBackends": ["native", "docker"],
        "launcherApi": {"version": 1, "minLauncherVersion": "1.0.53", "helper": {"runtime": helper_runtime, "path": helper.name},
            "capabilities": ["verify", "prepare_profile", "install", "prepare_disable", "prepare_remove", "recover"]},
        "settings": {"schemaVersion": 1, "files": [{"id": "prefs", "base": "profile", "path": "prefs.ini", "format": "ini"}],
            "fields": [{"id": "quality", "label": "Quality", "type": "integer", "default": 2, "minimum": 0, "maximum": 5, "file": "prefs", "key": ["Graphics", "Quality"]}]},
    }
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(declaration), encoding="utf-8")
    descriptor = read_api_manifest(root, folder)
    context = ModSettingsContext(root, folder, client, os.path.normcase(str(profile)), profile, settings)
    return descriptor, context


def test_private_profile_relaunch_retains_runtime_paths_and_font_settings(tmp_path):
    from src.core.mod_config_documents import edit_value
    _descriptor, context = make_mod(tmp_path)
    rows = [
        {"base": "profile", "path": "ReShade.ini", "format": "ini",
         "key": ["GENERAL", "PresetPath"], "value": str(context.mod_data_root / "ReShadePreset.ini")},
        {"base": "profile", "path": "ReShade.ini", "format": "ini",
         "key": ["STYLE", "FontScale"], "value": "1.000000"},
    ]
    edits, _files = runtime._contributions(rows, "prepare_profile", context)
    store = ContributionStore(context.client_root, allowed_roots={edit.target.allowed_root for edit in edits})
    store.commit(store.plan_edits(context.owner, edits))
    path = edits[0].target.path
    changed = edit_value(path.read_bytes(), "ini", ("GENERAL", "PresetPath"), r".\ReShadePreset.ini")
    changed = edit_value(changed, "ini", ("STYLE", "FontScale"), "1.500000")
    path.write_bytes(changed)
    for row, value in zip(rows, (r".\ReShadePreset.ini", "1.500000")):
        row["value"] = value
    refreshed, _files = runtime._contributions(rows, "prepare_profile", context)
    plan = store.plan_edits(context.owner, refreshed)
    assert plan.changed_paths == ()
    store.commit(plan)
    assert path.read_bytes() == changed
    assert store.plan_edits(context.owner, refreshed).is_noop
    store.commit(store.plan_remove(context.owner))
    assert path.read_bytes() == changed


@pytest.mark.parametrize("base,action,format,enabled", [
    ("profile", "prepare_profile", "ini", True),
    ("profile_settings", "prepare_profile", "ini", False),
    ("profile", "install", "ini", False),
    ("client", "install", "ini", False),
    ("profile", "prepare_profile", "text", False),
])
def test_runtime_value_acceptance_is_limited_to_private_configuration(tmp_path, base, action, format, enabled):
    _descriptor, context = make_mod(tmp_path)
    row = {"base": base, "path": "prefs.ini" if format == "ini" else "prefs.txt", "format": format,
           "key": ["Graphics", "Quality"] if format == "ini" else ["BEGIN", "END"], "value": "2"}
    edits, _files = runtime._contributions([row], action, context)
    assert edits[0].accept_current is enabled


@pytest.mark.parametrize("action,status", [("launch_result", "started"), ("client_exit", "exited")])
def test_notifications_keep_profile_context_and_reject_launch_mutations(tmp_path, action, status):
    descriptor, context = make_mod(tmp_path)
    declaration = json.loads(descriptor.manifest_path.read_text())
    declaration["launcherApi"]["capabilities"].append(action)
    descriptor.manifest_path.write_text(json.dumps(declaration))
    descriptor = read_api_manifest(descriptor.root, descriptor.folder)
    event = {"launchId": "942d7e04-d779-4de9-b6f3-f1e26da76eda", "status": status,
             "pid": 123, "exitCode": 0 if action == "client_exit" else None, "errorType": ""}
    requests = []
    runtime.run_mod_helper(descriptor, action, context, event=event,
        runner=helper_runner(receipt=False, requests=requests)).require_ready()
    request = requests[0][0]
    assert request["profile"]["id"] == context.profile_id
    assert request["runtime"]["clientRoot"] == str(context.client_root)
    assert request["event"] == event
    with pytest.raises(runtime.ModApiRuntimeError, match="Notification replies"):
        runtime.run_mod_helper(descriptor, action, context, event=event,
            runner=helper_runner(receipt=False, environment={"MOD_EVENT_EDIT": "no"}))


def test_staged_source_overlay_uses_public_commit_and_removal(tmp_path):
    descriptor, context = make_mod(tmp_path, kind="settings")
    source = descriptor.folder / "replacement.payload"
    source.write_bytes(b"module.exports = 42;\n")
    target = descriptor.root / "server/example.js"
    target.parent.mkdir()
    target.write_bytes(b"module.exports = 0;\n")
    def reply(payload, request):
        payload["contributions"] = [{"base": "evejs", "path": "server/example.js", "format": "file", "source": source.name}]
    result = runtime.run_mod_helper(descriptor, "install", context,
        runner=helper_runner(receipt=False, mutate=reply)).require_ready()
    assert target.read_bytes() == b"module.exports = 0;\n"
    runtime.commit_helper_contributions(result)
    assert target.read_bytes() == source.read_bytes()
    store = ContributionStore(descriptor.root)
    store.commit(store.plan_remove(result.context.owner))
    assert target.read_bytes() == b"module.exports = 0;\n"


def test_staged_overlay_conflict_produces_exact_review_before_commit(tmp_path, qapp):
    from src.core.mod_contributions import RemovalReviewRequired
    from src.widgets.mod_conflict_dialog import ModConflictDialog
    import hashlib
    first, context_a = make_mod(tmp_path, name="first", kind="settings")
    second, context_b = make_mod(tmp_path, name="second", kind="settings")
    target = first.root / "server/shared.bin"
    target.parent.mkdir()
    target.write_bytes(b"original")
    results = []
    for descriptor, context, content in [(first, context_a, b"first\x00\xff"), (second, context_b, b"second\x00\xfe")]:
        (descriptor.folder / "staged.payload").write_bytes(content)
        def reply(payload, request):
            payload["contributions"] = [{"base": "evejs", "path": "server/shared.bin", "format": "file", "source": "staged.payload"}]
        results.append(runtime.run_mod_helper(descriptor, "install", context,
            runner=helper_runner(receipt=False, mutate=reply)).require_ready())
    runtime.commit_helper_contributions(results[0])
    with pytest.raises(RemovalReviewRequired) as raised:
        runtime.commit_helper_contributions(results[1])
    review = raised.value.review
    assert review.kind == "helper" and review.preserve.is_noop
    assert target.read_bytes() == b"first\x00\xff"
    dialog = ModConflictDialog(review)
    dialog.policy.setCurrentIndex(1)
    assert hashlib.sha256(b"first\x00\xff").hexdigest() in dialog.current.toPlainText()
    assert hashlib.sha256(b"second\x00\xfe").hexdigest() in dialog.proposed.toPlainText()
    dialog.deleteLater()
    store = ContributionStore(first.root)
    committed = store.commit(review.restore)
    assert target.read_bytes() == b"second\x00\xfe"
    assert (store.transactions / committed.transaction_id / "0.before").read_bytes() == b"first\x00\xff"
    assert runtime.commit_helper_contributions(results[1]).changed_paths == ()
    store.commit(store.plan_remove(results[1].context.owner))
    assert target.read_bytes() == b"first\x00\xff"


def test_public_text_regions_compose_and_remove_independently(tmp_path):
    first, context_a = make_mod(tmp_path, name="first", kind="settings")
    second, context_b = make_mod(tmp_path, name="second", kind="settings")
    target = first.root / "server/shared.js"
    target.parent.mkdir()
    original = "// first\nold A\n// first end\n// second\nold B\n// second end\n"
    target.write_bytes(original.encode())
    results = []
    for descriptor, context, label in [(first, context_a, "first"), (second, context_b, "second")]:
        def reply(payload, request, label=label):
            payload["contributions"] = [{"base": "evejs", "path": "server/shared.js", "format": "text",
                "key": [f"// {label}\n", f"// {label} end"], "value": f"new {label}\n"}]
        result = runtime.run_mod_helper(descriptor, "install", context,
            runner=helper_runner(receipt=False, mutate=reply)).require_ready()
        runtime.commit_helper_contributions(result)
        results.append(result)
    assert "new first\n" in target.read_text() and "new second\n" in target.read_text()
    store = ContributionStore(first.root)
    store.commit(store.plan_remove(results[0].context.owner))
    assert "old A\n" in target.read_text() and "new second\n" in target.read_text()
    store.commit(store.plan_remove(results[1].context.owner))
    assert target.read_bytes() == original.encode()


def row_for(descriptor, *, active=True, valid=True):
    return Mod(descriptor.display_name, descriptor.folder, active, id=descriptor.id, version=descriptor.version,
        activation_kind=ActivationKind.CLIENT_PACKAGE, evejs_root=descriptor.root, api_descriptor=descriptor, valid=valid)


def helper_runner(*, contributions=None, environment=None, arguments=None, mutate=None, requests=None, receipt=True, receipt_state=None):
    def run(command, **kwargs):
        request_flag, result_flag = (("-RequestPath", "-ResultPath") if "-RequestPath" in command else ("--request", "--result"))
        request_path = Path(command[command.index(request_flag) + 1])
        result_path = Path(command[command.index(result_flag) + 1])
        request = json.loads(request_path.read_bytes())
        if requests is not None:
            requests.append((request, command, kwargs))
        edits = []
        if request["action"] == "prepare_profile":
            edits = contributions(request) if callable(contributions) else contributions
            if edits is None:
                edits = [{"base": "profile", "path": "prefs.ini", "format": "ini", "key": ["Graphics", "Quality"], "value": request["settings"]["profile"]["quality"]}]
        reply = {"protocol": runtime.PROTOCOL, "requestId": request["requestId"], "success": True, "state": "ready", "message": "ready",
            "restartRequired": [], "contributions": edits, "environment": environment or {}, "arguments": arguments or []}
        if receipt and request["action"] != "prepare_profile":
            root = Path(request["runtime"]["clientRoot"])
            path = root / "_local" / "mod-receipts" / (request["mod"]["id"] + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            state = receipt_state or ("restored" if request["action"] in {"prepare_disable", "prepare_remove"} else "active")
            path.write_text(json.dumps({"schemaVersion": 1, "state": state, "modIdentity": request["mod"]["identity"], "clientRoot": str(root)}), encoding="utf-8")
            reply["receipt"] = {"base": "client", "path": path.relative_to(root).as_posix(), "state": state, "schemaVersion": 1}
        if mutate:
            mutate(reply, request)
        result_path.write_text(json.dumps(reply), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


@pytest.mark.parametrize("action", ["verify", "recover"])
def test_verification_can_confirm_restored_but_not_unresolved_state(tmp_path, action):
    descriptor, context = make_mod(tmp_path)
    result = runtime.run_mod_helper(descriptor, action, context, runner=helper_runner(receipt_state="restored"))
    assert result.require_ready().receipt.state == "restored"
    with pytest.raises(runtime.ModApiRuntimeError, match="requested binary state"):
        runtime.run_mod_helper(descriptor, action, context, runner=helper_runner(receipt_state="recoverable"))


def test_launch_verification_still_requires_installed_state(tmp_path, monkeypatch):
    descriptor, context = make_mod(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setattr(runtime.platform_api, "get_eve_settings_path", lambda _path: context.profile_settings_root)
    with pytest.raises(runtime.ModApiRuntimeError, match="not installed"):
        runtime.prepare_public_client_mods(descriptor.root, context.client_root, context.profile_root / "tq",
            mods=[row_for(descriptor)], runner=helper_runner(receipt_state="restored"))


def test_profile_helper_is_correlated_and_proposed_keys_commit_only_when_host_commits(tmp_path):
    descriptor, context = make_mod(tmp_path)
    requests = []
    result = runtime.run_mod_helper(descriptor, "prepare_profile", context, runner=helper_runner(requests=requests))
    request, command, kwargs = requests[0]
    assert request["mod"]["identity"] == descriptor.identity
    assert request["profile"]["modDataRoot"] == str(context.mod_data_root)
    assert request["settings"] == {"global": {}, "profile": {"quality": 2}}
    assert command[1] == "--request"
    assert kwargs["cwd"] == descriptor.folder
    assert not context.mod_data_root.exists()
    committed = runtime.commit_helper_contributions(result)
    assert len(committed.changed_paths) == 1
    assert (context.mod_data_root / "prefs.ini").read_bytes() == b"[Graphics]\nQuality=2\n"
    assert runtime.commit_helper_contributions(result).is_noop


@pytest.mark.parametrize("change", [
    lambda reply, _request: reply.update(requestId="different-request"),
    lambda reply, _request: reply.update(protocol="unknown-protocol"),
    lambda reply, _request: reply.update(success="true"),
    lambda reply, _request: reply.update(state=[]),
    lambda reply, _request: reply.update(restartRequired=["none", "client"]),
    lambda reply, _request: reply.update(environment={"HTTP_PROXY": "http://wrong:1234"}),
    lambda reply, _request: reply.update(environment={"EVEJS_CLIENT_PATH": "wrong"}),
    lambda reply, _request: reply.update(arguments=["/port:9999"]),
    lambda reply, _request: reply.update(arguments=["/login:credential"]),
    lambda reply, _request: reply.update(contributions=[{"base": "client", "path": "prefs.ini", "format": "ini", "key": ["x"], "value": 2}]),
    lambda reply, _request: reply.update(contributions=[{"base": "profile", "path": "../prefs.ini", "format": "ini", "key": ["x"], "value": 2}]),
    lambda reply, _request: reply.update(contributions=[{"base": "profile", "path": "prefs.ini", "format": "ini", "key": ["x"], "value": {"nested": 1}}]),
])
def test_malformed_or_overreaching_replies_do_not_write_profile_targets(tmp_path, change):
    descriptor, context = make_mod(tmp_path)
    with pytest.raises((runtime.ModApiRuntimeError, ContributionConflict)):
        runtime.run_mod_helper(descriptor, "prepare_profile", context, runner=helper_runner(mutate=change))
    assert not context.mod_data_root.exists()


@pytest.mark.parametrize("action", ["install", "verify", "prepare_disable", "prepare_remove", "recover"])
def test_global_client_actions_require_a_bound_receipt_and_never_receive_a_profile(tmp_path, action):
    descriptor, context = make_mod(tmp_path)
    with pytest.raises(runtime.ModApiRuntimeError, match="bound binary receipt"):
        runtime.run_mod_helper(descriptor, action, context, runner=helper_runner(receipt=False))
    requests = []
    result = runtime.run_mod_helper(descriptor, action, context, runner=helper_runner(requests=requests))
    assert result.require_ready().receipt is not None
    assert requests[-1][0]["profile"] is None
    assert requests[-1][0]["settings"]["profile"] == {}
    assert not context.mod_data_root.exists()


def test_receipt_change_before_host_commit_is_not_accepted(tmp_path):
    descriptor, context = make_mod(tmp_path)
    result = runtime.run_mod_helper(descriptor, "install", context, runner=helper_runner())
    result.receipt.target.path.write_text("{}", encoding="utf-8")
    with pytest.raises(runtime.ModApiRuntimeError, match="receipt changed"):
        runtime.commit_helper_contributions(result)


def test_descriptor_and_helper_change_are_detected_without_source_hash_allowlists(tmp_path):
    descriptor, context = make_mod(tmp_path)
    runner = helper_runner(mutate=lambda _reply, _request: descriptor.launcher_api.helper.path.write_bytes(b"CHANGED"))
    with pytest.raises(runtime.ModApiRuntimeError, match="changed during"):
        runtime.run_mod_helper(descriptor, "prepare_profile", context, runner=runner)
    assert not context.mod_data_root.exists()


def test_future_launcher_requirement_is_checked_before_any_helper_execution(tmp_path):
    descriptor, context = make_mod(tmp_path)
    payload = json.loads(descriptor.manifest_path.read_bytes())
    payload["launcherApi"]["minLauncherVersion"] = "9.0.0"
    descriptor.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    descriptor = read_api_manifest(descriptor.root, descriptor.folder)
    with pytest.raises(runtime.ModApiRuntimeError, match="requires launcher"):
        runtime.run_mod_helper(descriptor, "prepare_profile", context, runner=lambda *_a, **_k: pytest.fail("must not execute"))


def test_host_held_client_lease_does_not_nest_the_same_lock(tmp_path):
    descriptor, context = make_mod(tmp_path)
    with acquire_mod_lifecycle_lock(context.client_root):
        result = runtime.run_mod_helper_locked(descriptor, "prepare_profile", context, runner=helper_runner())
        runtime.commit_helper_contributions_locked(result)
    assert (context.mod_data_root / "prefs.ini").is_file()


def test_powershell_uses_structured_file_arguments_without_a_command_string(tmp_path):
    descriptor, context = make_mod(tmp_path, helper_runtime="powershell")
    requests = []
    runtime.run_mod_helper(descriptor, "prepare_profile", context, runner=helper_runner(requests=requests))
    command = requests[0][1]
    assert command[1:7] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    assert "-RequestPath" in command and "-ResultPath" in command
    assert "-Command" not in command


@pytest.mark.parametrize("version,name", [("0.5.8", "graphics"), ("0.5.9", "graphics"), ("7.1.2", "unrelated-tool")])
def test_two_adapter_versions_and_an_unrelated_package_use_the_same_host(tmp_path, monkeypatch, version, name):
    descriptor, first = make_mod(tmp_path, name, version)
    second_profile = first.profile_root.parent / "pilot-b"
    second_profile.mkdir()
    second_settings = first.profile_settings_root.parent.parent / "pilot-b" / "settings"
    second_settings.mkdir(parents=True)
    second = replace(first, profile_id=os.path.normcase(str(second_profile)), profile_root=second_profile, profile_settings_root=second_settings)
    first_form = ModSettingsSession.open(first, descriptor.settings, scope="profile")
    first_form.save({"quality": 3})
    second_form = ModSettingsSession.open(second, descriptor.settings, scope="profile")
    second_form.save({"quality": 4})
    monkeypatch.setattr(runtime, "scan_mods", lambda _root: [row_for(descriptor)])
    contexts = {str(first.profile_root / "tq"): first, str(second.profile_root / "tq"): second}
    monkeypatch.setattr(runtime.platform_api, "get_eve_settings_path", lambda path: contexts[path].profile_settings_root)
    for context in (first, second):
        result = runtime.prepare_public_client_mods(descriptor.root, context.client_root, context.profile_root / "tq", runner=helper_runner(environment={"TRINITYPLATFORM": "dx12"}))
        assert result.environment == {"TRINITYPLATFORM": "dx12"}
    assert (first.mod_data_root / "prefs.ini").read_bytes().endswith(b"Quality=3\n")
    assert (second.mod_data_root / "prefs.ini").read_bytes().endswith(b"Quality=4\n")
    ContributionStore(first.client_root, allowed_roots={first.profile_storage_root}).commit(
        ContributionStore(first.client_root, allowed_roots={first.profile_storage_root}).plan_remove(first.owner))
    assert b"Quality=4" in (second.mod_data_root / "prefs.ini").read_bytes()


def test_environment_conflict_between_mods_prevents_every_profile_contribution(tmp_path, monkeypatch):
    first, first_context = make_mod(tmp_path, "one")
    second, second_context = make_mod(tmp_path, "two")
    monkeypatch.setattr(runtime, "scan_mods", lambda _root: [row_for(first), row_for(second)])
    monkeypatch.setattr(runtime.platform_api, "get_eve_settings_path", lambda _path: first_context.profile_settings_root)
    def runner(command, **kwargs):
        return helper_runner(environment={"TRINITYPLATFORM": "dx12" if kwargs["cwd"] == first.folder else "dx11"})(command, **kwargs)
    with pytest.raises(runtime.ModApiRuntimeError, match="environment conflicts"):
        runtime.prepare_public_client_mods(first.root, first_context.client_root, first_context.profile_root / "tq", runner=runner)
    assert not first_context.mod_data_root.exists()
    assert not second_context.mod_data_root.exists()


def test_unselected_and_malformed_optional_rows_are_not_helper_dependencies(tmp_path, monkeypatch):
    descriptor, context = make_mod(tmp_path)
    monkeypatch.setattr(runtime, "scan_mods", lambda _root: [row_for(descriptor, active=False), row_for(descriptor, valid=False)])
    result = runtime.prepare_public_client_mods(descriptor.root, context.client_root, context.profile_root / "tq", runner=lambda *_a, **_k: pytest.fail("must not execute"))
    assert result.results == () and result.environment == {} and result.arguments == ()
    assert not (context.client_root / "_local").exists()


def test_actual_eve_settings_use_the_same_localappdata_anchor_as_the_settings_form(tmp_path, monkeypatch):
    descriptor, context = make_mod(tmp_path)
    local = context.profile_settings_root.parents[3]
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    context = replace(context, profile_id=profile_identity(context.profile_root), profile_settings_storage_root=local)
    payload = json.loads(descriptor.manifest_path.read_bytes())
    payload["settings"]["files"][0].update(base="profile_settings")
    descriptor.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    descriptor = read_api_manifest(descriptor.root, descriptor.folder)
    form = ModSettingsSession.open(context, descriptor.settings, scope="profile")
    form.save({"quality": 4})
    monkeypatch.setattr(runtime, "scan_mods", lambda _root: [row_for(descriptor)])
    monkeypatch.setattr(runtime.platform_api, "get_eve_settings_path", lambda _path: context.profile_settings_root)
    def contributions(request):
        return [{"base": "profile_settings", "path": "prefs.ini", "format": "ini", "key": ["Graphics", "Quality"], "value": request["settings"]["profile"]["quality"]}]
    runtime.prepare_public_client_mods(descriptor.root, context.client_root, context.profile_root / "tq", runner=helper_runner(contributions=contributions))
    form = ModSettingsSession.open(context, descriptor.settings, scope="profile")
    form.save({"quality": 5})
    assert (context.profile_settings_root / "prefs.ini").read_bytes().endswith(b"Quality=5\n")
    store = ContributionStore(context.client_root, allowed_roots={local})
    store.commit(store.plan_remove(context.owner))
    assert b"Quality" not in (context.profile_settings_root / "prefs.ini").read_bytes()


@pytest.mark.parametrize("kind,base", [("client-package", "mod"), ("settings", "client")])
def test_global_helper_cannot_move_its_commit_to_a_different_coordination_root(tmp_path, kind, base):
    descriptor, context = make_mod(tmp_path, kind=kind)
    def mutate(reply, _request):
        reply["contributions"] = [{"base": base, "path": "unsafe.json", "format": "json", "key": ["enabled"], "value": True}]
    with pytest.raises(runtime.ModApiRuntimeError, match="coordination root"):
        runtime.run_mod_helper(descriptor, "install", context, runner=helper_runner(mutate=mutate))
    assert not (context.mod_folder / "unsafe.json").exists()
    assert not (context.client_root / "unsafe.json").exists()
