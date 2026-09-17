"""Bundled examples execute only in disposable roots through the real host."""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import zipfile
from urllib.parse import unquote, urlsplit

import pytest

from src.core.local_mod_packages import LocalModPackages
from src.core.mod_api_manifest import read_api_manifest
from src.core.mod_api_runtime import commit_helper_contributions, run_mod_helper
from src.core.mod_settings import ModSettingsContext, ModSettingsSession, profile_identity
from src.core.mod_settings_schema import parse_settings_schema
from src.core.mod_guide import catalog_paths


SOURCE = Path(__file__).resolve().parents[1]
EXAMPLES = SOURCE / "examples/mods"


def copy_example(tmp_path, name):
    root = tmp_path / "runtime"
    root.mkdir()
    service = LocalModPackages(root)
    preview = service.inspect(EXAMPLES / name)
    record = service.import_package(EXAMPLES / name)
    folder = root / record.relative_path
    assert preview.public_descriptor is True
    descriptor = read_api_manifest(root, folder)
    client = tmp_path / "client"
    profile = tmp_path / "config" / "Profiles" / "pilot"
    settings = tmp_path / "LocalAppData" / "CCP" / "EVE" / "pilot" / "settings"
    for path in (client, profile, settings):
        path.mkdir(parents=True)
    context = ModSettingsContext(root, folder, client, profile_identity(profile), profile, settings,
        profile_settings_storage_root=tmp_path / "LocalAppData")
    if descriptor.settings:
        parse_settings_schema(descriptor.settings)
    return descriptor, context


def test_configure_demo_saves_reopens_and_has_no_executable_helper(tmp_path):
    descriptor, context = copy_example(tmp_path, "configure-demo")
    assert descriptor.launcher_api is None
    session = ModSettingsSession.open(context, descriptor.settings)
    assert not (context.mod_folder / "preferences.json").exists()
    session.save({"scanInterval": 20})
    assert json.loads((context.mod_folder / "preferences.json").read_text()) == {"scanInterval": 20}
    reopened = ModSettingsSession.open(context, descriptor.settings)
    assert reopened.values["scanInterval"] == 20


def test_source_overlay_folder_and_zip_work_together_through_real_helpers(tmp_path):
    from src.core.mod_manifest import scan_mods
    from src.core.mod_operations import ModOperationContext, change_mod_state, remove_local_mod
    root = tmp_path / "runtime"
    (root / "server").mkdir(parents=True)
    example = EXAMPLES / "source-overlay-demo"
    baseline = (example / "example-source.js").read_bytes()
    target = root / "server/launcher-overlay-demo.js"
    target.write_bytes(baseline)
    packages = LocalModPackages(root)
    first = packages.import_package(example)
    archive = tmp_path / "second.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in example.iterdir():
            content = path.read_bytes()
            if path.name == "evejs-launcher.mod.json":
                declaration = json.loads(content)
                declaration["id"] = "source-overlay-second"
                content = json.dumps(declaration).encode()
            output.writestr("SecondOverlay/" + path.name, content)
    second = packages.import_package(archive)
    def mod(record):
        return next(item for item in scan_mods(root) if item.path == root / record.relative_path)
    operation = ModOperationContext(root)
    for record, region, value in [(first, "first", "alpha"), (second, "second", "beta")]:
        selected = mod(record)
        assert selected.valid and not selected.active
        session = ModSettingsSession.open(ModSettingsContext(root, selected.path), selected.settings_schema)
        session.save({"region": region, "replacement": value})
        assert change_mod_state(selected, True, operation)
    assert b'exports.first = "alpha";' in target.read_bytes()
    assert b'exports.second = "beta";' in target.read_bytes()
    removed = remove_local_mod(mod(first), operation)
    assert b'exports.first = "original";' in target.read_bytes()
    assert b'exports.second = "beta";' in target.read_bytes()
    packages.restore(removed.record_id)
    assert not mod(first).active
    assert change_mod_state(mod(first), True, operation)
    assert b'exports.first = "alpha";' in target.read_bytes()
    assert not change_mod_state(mod(second), False, operation)
    remove_local_mod(mod(first), operation)
    assert target.read_bytes() == baseline
    assert (example / "example-source.js").read_bytes() == baseline


def test_cleanup_example_retains_provider_then_retries_after_lost_reply(tmp_path):
    from src.core.local_mod_packages import LocalModCleanupPending
    from src.core.mod_manifest import scan_mods
    from src.core.mod_operations import ModOperationContext, change_mod_state, remove_local_mod
    root = tmp_path / "runtime"
    root.mkdir()
    packages = LocalModPackages(root)
    record = packages.import_package(EXAMPLES / "cleanup-demo")
    folder = root / record.relative_path
    operation = ModOperationContext(root)
    def selected():
        return next(mod for mod in scan_mods(root) if mod.id == "cleanup-demo")
    assert change_mod_state(selected(), True, operation)
    state_path = folder / "demo-state.json"
    state = json.loads(state_path.read_bytes())
    foreign = {"owner": "foreign-mod", "id": "same-family", "savedValue": 17}
    state["records"].append(foreign)
    state_path.write_text(json.dumps(state))
    with pytest.raises(LocalModCleanupPending):
        remove_local_mod(selected(), operation)
    assert folder.is_dir() and selected().active
    state = json.loads(state_path.read_bytes())
    assert state["phase"] == "retiring" and len(state["records"]) == 2
    assert foreign in state["records"]
    # Actual helper process loses its result after committing the remaining work.
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"protocol": "evejs_launcher_mod_v1", "requestId": "lost-reply",
        "action": "prepare_remove", "mod": {"identity": selected().identity}}))
    result = tmp_path / "already-exists.json"
    result.write_text("retain me")
    failed = subprocess.run(["node", str(folder / "helper.js"), "--request", str(request),
        "--result", str(result)], capture_output=True, timeout=20)
    assert failed.returncode != 0 and result.read_text() == "retain me"
    assert json.loads(state_path.read_bytes())["records"] == [foreign]
    # Host retries through the original provider; repeated cleanup is now ready.
    removed = remove_local_mod(selected(), operation)
    assert removed.status == "quarantined" and not folder.exists()
    restored = packages.restore(removed.record_id)
    assert restored.enabled is False and not selected().active
    assert json.loads(state_path.read_bytes())["records"] == [foreign]


def test_hello_loader_can_be_imported_and_runs_only_as_an_explicit_preload(tmp_path):
    descriptor, context = copy_example(tmp_path, "hello-loader")
    node = shutil.which("node")
    assert node, "Node.js is required to validate the bundled Node examples"
    disabled = context.mod_folder / "loader.js.disabled"
    completed = subprocess.run([node, "--require", str(disabled), "-e", ""], cwd=tmp_path,
        capture_output=True, timeout=10, check=True, creationflags=0x08000000)
    assert completed.stdout == b"[hello-loader] Selected launcher preload is running.\n"
    assert not (context.mod_folder / "loader.js").exists()


def test_real_node_example_reads_typed_saved_preferences_and_noop_preserves_bytes(tmp_path):
    descriptor, context = copy_example(tmp_path, "profile-options")
    form = ModSettingsSession.open(context, descriptor.settings, scope="profile")
    form.save({"label": "Test Pilot", "intensity": 4, "enabled": False})
    path = context.mod_data_root / "preferences.ini"
    before = path.read_bytes()
    result = run_mod_helper(descriptor, "prepare_profile", context, timeout=10).require_ready()
    assert not commit_helper_contributions(result).is_noop
    repeated = run_mod_helper(descriptor, "prepare_profile", context, timeout=10).require_ready()
    assert commit_helper_contributions(repeated).is_noop
    assert path.read_bytes() == before
    assert result.environment["LAUNCHER_PROFILE_OPTIONS"] == str(path)
    assert b"Enabled=0" in before and b"Intensity=4" in before


def test_real_powershell_receipt_example_installs_verifies_restores_and_recovers(tmp_path):
    descriptor, context = copy_example(tmp_path, "client-receipt-demo")
    result = run_mod_helper(descriptor, "verify", context, timeout=15)
    assert not result.success and result.state == "failed"
    installed = run_mod_helper(descriptor, "install", context, timeout=15).require_ready()
    assert installed.receipt.state == "active"
    run_mod_helper(descriptor, "verify", context, timeout=15).require_ready()
    prepared = run_mod_helper(descriptor, "prepare_profile", context, timeout=15).require_ready()
    assert prepared.contributions == () and prepared.environment == {}
    restored = run_mod_helper(descriptor, "prepare_remove", context, timeout=15).require_ready()
    assert restored.receipt.state == "restored"
    recovered = run_mod_helper(descriptor, "recover", context, timeout=15).require_ready()
    assert recovered.receipt.state == "restored"
    assert not (context.client_root / "bin64").exists()


def test_shared_profile_examples_remove_one_owner_without_erasing_the_other(tmp_path):
    from dataclasses import replace
    from src.core.mod_manifest import scan_mods
    from src.core.mod_operations import ModOperationContext, remove_local_mod

    descriptor, first = copy_example(tmp_path, "profile-options")
    packages = LocalModPackages(first.evejs_root)
    record = packages.import_package(EXAMPLES / "shared-profile-companion")
    companion_folder = first.evejs_root / record.relative_path
    companion = read_api_manifest(first.evejs_root, companion_folder)
    second_root = first.profile_root.parent / "pilot-b"
    second_settings = first.profile_settings_root.parent.parent / "pilot-b" / "settings"
    second_root.mkdir()
    second_settings.mkdir(parents=True)
    second = replace(first, profile_id=profile_identity(second_root), profile_root=second_root,
                     profile_settings_root=second_settings)
    paths = []
    for context, label, hint in ((first, "Alpha", 2), (second, "Beta", 4)):
        path = context.profile_settings_root / "prefs.ini"
        path.write_bytes(b"; keep this comment\r\n[Existing]\r\nValue=99\r\n")
        form = ModSettingsSession.open(context, descriptor.settings, scope="profile")
        form.save({**form.values, "label": label})
        result = run_mod_helper(descriptor, "prepare_profile", context, timeout=10).require_ready()
        commit_helper_contributions(result)
        companion_context = replace(context, mod_folder=companion_folder)
        ModSettingsSession.open(companion_context, companion.settings, scope="profile").save({"hint_level": hint})
        assert f"Label={label}" in path.read_text() and f"HintLevel={hint}" in path.read_text()
        paths.append((path, hint, context.mod_data_root / "preferences.ini"))
    operation = ModOperationContext(first.evejs_root, first.client_root,
        profiles_root=first.profile_root.parent,
        local_appdata=tmp_path / "LocalAppData")
    selected = next(mod for mod in scan_mods(first.evejs_root) if mod.id == descriptor.id)
    remove_local_mod(selected, operation)
    for path, hint, private in paths:
        text = path.read_text()
        assert "Label=" not in text and f"HintLevel={hint}" in text
        assert "; keep this comment" in text and "Value=99" in text
        assert private.is_file()
    selected = next(mod for mod in scan_mods(first.evejs_root) if mod.id == companion.id)
    remove_local_mod(selected, operation)
    for path, _hint, _private in paths:
        text = path.read_text()
        assert "HintLevel=" not in text and "Value=99" in text and "; keep this comment" in text


def test_guide_catalog_contains_every_local_documentation_link_and_no_external_files():
    catalog_path = SOURCE / "docs/mod-authoring/navigation.json"
    catalog = json.loads(catalog_path.read_bytes())
    paths = [SOURCE / path for path in catalog_paths(catalog)]
    allowed = {path.resolve() for path in paths}
    assert len(allowed) == len(paths)
    for path in paths:
        assert path.is_file() and path.resolve().is_relative_to(SOURCE)
        if path.suffix != ".md":
            continue
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            url = urlsplit(match[1])
            if url.scheme or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            assert target in allowed, f"{path.name} links to an unbundled file: {match[1]}"
