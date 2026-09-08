import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import mod_management, mod_api_runtime
from src.core.local_mod_packages import LocalModPackages, LocalModCleanupPending
from src.core.mod_inventory import load_mod_inventory
from src.core.mod_manifest import scan_mods
from src.core.mod_operations import ModOperationContext, change_mod_state, remove_local_mod
from src.core.mod_settings import ModSettingsContext, ModSettingsSession


@pytest.fixture(autouse=True)
def isolated_enrollment(monkeypatch, tmp_path):
    from src import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher")
    def absent(_mod):
        raise mod_management.ModNotManagedError("No fixture enrollment")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", absent)


def package(root, name, *, helper=False):
    folder = root / "mods" / name
    folder.mkdir(parents=True)
    (folder / "loader.js").write_text("module.exports = {};\n")
    declaration = {"schemaVersion": 3, "id": name.lower(), "displayName": name,
        "version": "1.0.0", "kind": "loader", "restart": "game_server",
        "activation": {"strategy": "loader_rename"},
        "settings": {"schemaVersion": 1,
            "files": [{"id": "config", "base": "evejs", "path": "config/shared.json", "format": "json"}],
            "fields": [{"id": "amount", "label": "Amount", "type": "integer", "default": 0,
                        "file": "config", "key": [name]}]}}
    if helper:
        (folder / "helper.js").write_text("// fixture helper\n")
        declaration["launcherApi"] = {"version": 1, "minLauncherVersion": "1.0.53",
            "helper": {"runtime": "node", "path": "helper.js"},
            "capabilities": ["prepare_disable", "prepare_remove"]}
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(declaration))
    return next(mod for mod in scan_mods(root) if mod.path == folder)


def test_disabling_json_activated_settings_package_changes_its_actual_config(tmp_path):
    folder = tmp_path / "mods" / "SettingsOnly"
    folder.mkdir(parents=True)
    config_path = tmp_path / "options.json"
    config_path.write_text('{"enabled": true, "unrelated": 42}')
    (folder / "evejs-launcher.mod.json").write_text(json.dumps({
        "schemaVersion": 3, "id": "settings-only", "displayName": "Settings Only",
        "version": "1.0.0", "kind": "settings", "restart": "none",
        "activation": {"strategy": "json_boolean", "configPath": "options.json", "property": "enabled"},
    }))
    selected = next(mod for mod in scan_mods(tmp_path) if mod.path == folder)
    assert selected.active and selected.valid
    assert change_mod_state(selected, False, ModOperationContext(tmp_path)) is False
    assert json.loads(config_path.read_text()) == {"enabled": False, "unrelated": 42}
    assert next(mod for mod in scan_mods(tmp_path) if mod.path == folder).active is False


@pytest.mark.parametrize("kind", ["loader", "settings", "source-integrated"])
def test_enable_waits_for_declared_install_before_activation(tmp_path, monkeypatch, kind):
    mod = package(tmp_path, "Alpha", helper=True)
    declaration = json.loads(mod.manifest_path.read_text())
    declaration["kind"] = kind
    declaration["launcherApi"]["capabilities"].append("install")
    if kind == "loader":
        (mod.path / "loader.js").rename(mod.path / "loader.js.disabled")
    else:
        (mod.path / "loader.js").unlink()
        declaration["activation"] = {"strategy": "json_boolean", "configPath": "alpha.json", "property": "enabled"}
        (tmp_path / "alpha.json").write_text('{"enabled": false, "preference": 27}')
    mod.manifest_path.write_text(json.dumps(declaration))
    states = iter(["pending", "ready"])
    actions = []

    def helper(command, **_kwargs):
        request = json.loads(Path(command[command.index("--request") + 1]).read_text())
        actions.append(request["action"])
        assert not scan_mods(tmp_path)[0].active
        state = next(states)
        Path(command[command.index("--result") + 1]).write_text(json.dumps({
            "protocol": request["protocol"], "requestId": request["requestId"],
            "success": True, "state": state, "message": "Integration preparation pending.",
            "restartRequired": [], "contributions": [], "environment": {}, "arguments": [],
        }))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod_api_runtime, "run_helper_process", helper)
    operation = ModOperationContext(tmp_path)
    with pytest.raises(Exception, match="Integration preparation pending"):
        change_mod_state(scan_mods(tmp_path)[0], True, operation)
    assert not scan_mods(tmp_path)[0].active
    assert change_mod_state(scan_mods(tmp_path)[0], True, operation)
    assert scan_mods(tmp_path)[0].active
    assert actions == ["install", "install"]
    if kind != "loader":
        assert json.loads((tmp_path / "alpha.json").read_text()) == {"enabled": True, "preference": 27}


def test_source_provider_remove_undo_and_reenable(tmp_path, monkeypatch):
    mod = package(tmp_path, "Alpha", helper=True)
    data = json.loads(mod.manifest_path.read_text())
    data.update(kind="source-integrated", activation={
        "strategy": "json_boolean", "configPath": "alpha.json", "property": "enabled"})
    data["launcherApi"]["capabilities"].append("install")
    mod.manifest_path.write_text(json.dumps(data))
    (mod.path / "loader.js").unlink()
    (mod.path / "notes.txt").write_text("private preferences")
    target = tmp_path / "alpha.json"
    target.write_text('{"enabled": true, "preference": 27}')
    neighbor = package(tmp_path, "Beta")
    states = iter(["pending", "ready", "ready"])
    actions = []

    def helper(command, **_kwargs):
        request = json.loads(Path(command[command.index("--request") + 1]).read_text())
        actions.append(request["action"])
        assert mod.path.is_dir()
        Path(command[command.index("--result") + 1]).write_text(json.dumps({
            "protocol": request["protocol"], "requestId": request["requestId"],
            "success": True, "state": next(states), "message": "Cleanup pending.",
            "restartRequired": [], "contributions": [], "environment": {}, "arguments": [],
        }))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod_api_runtime, "run_helper_process", helper)
    service = LocalModPackages(tmp_path)
    current = next(item for item in scan_mods(tmp_path) if item.path == mod.path)
    operation = ModOperationContext(tmp_path)
    assert service.can_manage(current)
    with pytest.raises(LocalModCleanupPending):
        remove_local_mod(current, operation)
    assert mod.path.is_dir() and json.loads(target.read_text())["enabled"]
    removed = remove_local_mod(current, operation)
    assert not mod.path.exists()
    assert json.loads(target.read_text()) == {"enabled": False, "preference": 27}
    assert (neighbor.path / "loader.js").is_file()
    target.write_text('{"enabled": true, "preference": 27}')
    with pytest.raises(Exception, match="activation setting changed"):
        service.restore(removed.record_id)
    assert not mod.path.exists()
    assert json.loads(target.read_text())["enabled"] is True
    target.write_text('{"enabled": false, "preference": 27}')
    service.restore(removed.record_id)
    restored = next(item for item in scan_mods(tmp_path) if item.path == mod.path)
    assert not restored.active
    assert (mod.path / "notes.txt").read_text() == "private preferences"
    assert change_mod_state(restored, True, operation)
    assert actions == ["prepare_remove", "prepare_remove", "install"]


def test_real_source_helper_restores_its_patch_and_reinstalls_after_undo(tmp_path):
    mod = package(tmp_path, "Alpha", helper=True)
    data = json.loads(mod.manifest_path.read_text())
    data.update(kind="source-integrated", activation={
        "strategy": "json_boolean", "configPath": "alpha.json", "property": "enabled"})
    data["launcherApi"]["capabilities"].append("install")
    mod.manifest_path.write_text(json.dumps(data))
    (mod.path / "loader.js").unlink()
    (tmp_path / "alpha.json").write_text('{"enabled": false, "preference": 27}')
    target = tmp_path / "shared.js"
    target.write_text('exports.alpha = 0;\nexports.beta = 7;\n')
    (mod.path / "helper.js").write_text(r'''
const fs = require('fs');
const path = require('path');
const args = process.argv.slice(2);
const request = JSON.parse(fs.readFileSync(args[args.indexOf('--request') + 1], 'utf8'));
const target = path.join(request.runtime.evejsRoot, 'shared.js');
const oldValue = request.action === 'install' ? 'exports.alpha = 0;' : 'exports.alpha = 1;';
const newValue = request.action === 'install' ? 'exports.alpha = 1;' : 'exports.alpha = 0;';
const current = fs.readFileSync(target, 'utf8');
if (!current.includes(oldValue) && !current.includes(newValue)) throw Error('Unknown source state');
fs.writeFileSync(target, current.replace(oldValue, newValue));
fs.writeFileSync(args[args.indexOf('--result') + 1], JSON.stringify({
    protocol: request.protocol, requestId: request.requestId,
    success: true, state: 'ready', message: 'Complete', restartRequired: [],
    contributions: [], environment: {}, arguments: []
}));
''')
    def current():
        return next(item for item in scan_mods(tmp_path) if item.path == mod.path)

    operation = ModOperationContext(tmp_path)
    assert change_mod_state(current(), True, operation)
    assert target.read_text() == 'exports.alpha = 1;\nexports.beta = 7;\n'
    target.write_text(target.read_text().replace('beta = 7', 'beta = 9'))
    removed = remove_local_mod(current(), operation)
    assert target.read_text() == 'exports.alpha = 0;\nexports.beta = 9;\n'
    LocalModPackages(tmp_path).restore(removed.record_id)
    assert not current().active
    assert target.read_text() == 'exports.alpha = 0;\nexports.beta = 9;\n'
    assert change_mod_state(current(), True, operation)
    assert target.read_text() == 'exports.alpha = 1;\nexports.beta = 9;\n'
    assert json.loads((tmp_path / "alpha.json").read_text())["preference"] == 27


def save_amount(root, mod, value):
    session = ModSettingsSession.open(ModSettingsContext(root, mod.path), mod.settings_schema)
    session.save({"amount": value})


def test_remove_and_undo_preserve_neighbor_and_manual_data(tmp_path):
    a, b = package(tmp_path, "Alpha"), package(tmp_path, "Beta")
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / "shared.json"
    target.write_text('{"Alpha": 1, "Beta": 2, "manual": 3}')
    save_amount(tmp_path, a, 10)
    save_amount(tmp_path, b, 20)
    target.write_text(target.read_text().replace('"manual": 3', '"manual": 4'))
    (a.path / "notes.txt").write_text("User modified package data")
    operation = ModOperationContext(tmp_path)
    removed = remove_local_mod(a, operation)
    assert json.loads(target.read_text()) == {"Alpha": 1, "Beta": 20, "manual": 4}
    assert not a.path.exists() and (b.path / "loader.js").is_file()
    inventory = load_mod_inventory(str(tmp_path))
    assert [mod.name for mod in inventory.mods] == ["Beta"]
    assert len(inventory.quarantined) == 1
    LocalModPackages(tmp_path).restore(removed.record_id)
    assert (a.path / "notes.txt").read_text() == "User modified package data"
    assert (b.path / "loader.js").read_text() == "module.exports = {};\n"


def test_shared_conflict_keeps_mod_installed_for_repair(tmp_path):
    mod = package(tmp_path, "Alpha")
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / "shared.json"
    target.write_text('{"Alpha": 1}')
    save_amount(tmp_path, mod, 10)
    target.write_text('{"Alpha": 99}')
    with pytest.raises(Exception, match="unrecorded edit"):
        remove_local_mod(mod, ModOperationContext(tmp_path))
    assert mod.path.is_dir()
    assert json.loads(target.read_text()) == {"Alpha": 99}


def test_cleanup_pending_survives_retry_and_prevents_unload(tmp_path, monkeypatch):
    mod = package(tmp_path, "Alpha", helper=True)
    states = iter(["pending", "ready"])
    requests = []
    def helper(command, **_kwargs):
        request = json.loads(Path(command[command.index("--request") + 1]).read_text())
        requests.append(request)
        state = next(states)
        result = {"protocol": request["protocol"], "requestId": request["requestId"],
            "success": True, "state": state, "message": "Restart once to finish cleanup." if state == "pending" else "Done",
            "restartRequired": ["game_server"] if state == "pending" else [],
            "contributions": [], "environment": {}, "arguments": []}
        Path(command[command.index("--result") + 1]).write_text(json.dumps(result))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(mod_api_runtime, "run_helper_process", helper)
    operation = ModOperationContext(tmp_path)
    with pytest.raises(LocalModCleanupPending):
        change_mod_state(mod, False, operation)
    assert (mod.path / "loader.js").is_file()
    assert LocalModPackages(tmp_path).records()[0].cleanup["ready"] is False
    with pytest.raises(LocalModCleanupPending):
        LocalModPackages(tmp_path).disable(mod)
    assert change_mod_state(mod, False, operation) is False
    assert not (mod.path / "loader.js").exists()
    assert (mod.path / "loader.js.disabled").exists()
    assert [request["action"] for request in requests] == ["prepare_disable", "prepare_disable"]


def test_settings_only_package_enables_without_a_helper(tmp_path):
    mod = package(tmp_path, "Alpha")
    data = json.loads(mod.manifest_path.read_text())
    data.update(kind="settings", restart="none", activation={"strategy": "package"})
    mod.manifest_path.write_text(json.dumps(data))
    (mod.path / "loader.js").unlink()
    current = scan_mods(tmp_path)[0]
    operation = ModOperationContext(tmp_path)
    assert change_mod_state(current, True, operation)
    assert scan_mods(tmp_path)[0].active
    assert not change_mod_state(scan_mods(tmp_path)[0], False, operation)
    assert not scan_mods(tmp_path)[0].active


def test_integrated_disable_waits_for_declared_cleanup(tmp_path, monkeypatch):
    mod = package(tmp_path, "Alpha", helper=True)
    data = json.loads(mod.manifest_path.read_text())
    data.update(kind="source-integrated", activation={
        "strategy": "json_boolean", "configPath": "config/alpha.json", "property": "enabled",
    })
    mod.manifest_path.write_text(json.dumps(data))
    (mod.path / "loader.js").unlink()
    (tmp_path / "config").mkdir()
    target = tmp_path / "config" / "alpha.json"
    original = '{"enabled": true, "userPreference": 27}'
    target.write_text(original)
    states = iter(["pending", "ready"])
    actions = []

    def helper(command, **_kwargs):
        request = json.loads(Path(command[command.index("--request") + 1]).read_text())
        actions.append(request["action"])
        state = next(states)
        Path(command[command.index("--result") + 1]).write_text(json.dumps({
            "protocol": request["protocol"], "requestId": request["requestId"],
            "success": True, "state": state, "message": "Finish cleanup first.",
            "restartRequired": ["game_server"] if state == "pending" else [],
            "contributions": [], "environment": {}, "arguments": [],
        }))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mod_api_runtime, "run_helper_process", helper)
    operation = ModOperationContext(tmp_path)
    current = scan_mods(tmp_path)[0]
    assert current.valid and current.active
    assert not LocalModPackages(tmp_path).can_manage(current)
    with pytest.raises(LocalModCleanupPending, match="Finish cleanup first"):
        change_mod_state(current, False, operation)
    assert target.read_text() == original
    assert change_mod_state(scan_mods(tmp_path)[0], False, operation) is False
    assert json.loads(target.read_text()) == {"enabled": False, "userPreference": 27}
    assert actions == ["prepare_disable", "prepare_disable"]
    assert LocalModPackages(tmp_path).records() == ()
