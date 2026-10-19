import json
import time
from types import SimpleNamespace

import pytest

from src.core import mod_updater_preservation as protection
from src.core.mod_inventory import ModInventory


def fixture(tmp_path):
    path = tmp_path / protection.POLICY
    path.parent.mkdir(parents=True)
    data = {"schema": "evejs.updater-policy.v1", "keepTrees": ["_local"],
            "keepMine": ["config/server.json"], "keepFiles": ["tools/ClientSETUP/scripts/EvEJSConfig.bat"],
            "futureUpstreamField": {"preserve": True}}
    original = json.dumps(data, indent=4).encode()
    path.write_bytes(original)
    return path, original


def mod(name="automining", *, active=False, valid=True):
    return SimpleNamespace(id=name, active=active, valid=valid)


def test_disabled_mod_protects_legacy_state_and_all_mod_folders(tmp_path):
    policy, original = fixture(tmp_path)
    assert protection.protect_updater_settings(tmp_path, [mod()])
    data = json.loads(policy.read_bytes())
    assert data["futureUpstreamField"] == {"preserve": True}
    assert data["keepTrees"] == ["_local", "mods"]
    assert data["keepMine"] == ["config/server.json", *protection.AUTOMINING_LEGACY]
    assert data["keepFiles"] == ["tools/ClientSETUP/scripts/EvEJSConfig.bat"]
    backups = list((tmp_path / "_local/launcher-updater-preservation").glob("*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    before = policy.stat().st_mtime_ns
    assert not protection.protect_updater_settings(tmp_path, [mod()])
    assert policy.stat().st_mtime_ns == before


def test_existing_case_insensitive_rules_are_not_duplicated(tmp_path):
    policy, _ = fixture(tmp_path)
    data = json.loads(policy.read_bytes())
    data["keepTrees"].append("MODS")
    data["keepMine"] += [p.upper().replace("/", "\\") for p in protection.AUTOMINING_LEGACY]
    policy.write_text(json.dumps(data))
    assert not protection.protect_updater_settings(tmp_path, [mod()])


def test_disabled_npc_buyers_keeps_its_native_market_configuration(tmp_path):
    policy, _ = fixture(tmp_path)
    assert protection.protect_updater_settings(tmp_path, [mod("npc-market-buyers")])
    data = json.loads(policy.read_bytes())
    assert protection.NPC_MARKET_CONFIG in data["keepMine"]
    assert not any(p in data["keepMine"] for p in protection.AUTOMINING_LEGACY)


@pytest.mark.parametrize("mods", [[], [mod(valid=False)]])
def test_absent_or_invalid_mods_do_not_modify_updater(tmp_path, mods):
    policy, original = fixture(tmp_path)
    assert not protection.protect_updater_settings(tmp_path, mods)
    assert policy.read_bytes() == original
    assert not (tmp_path / "_local").exists()


def test_third_party_disabled_mod_gets_folder_and_declared_settings_protection(tmp_path):
    policy, _ = fixture(tmp_path)
    author = mod("another-author.weather")
    author.api_descriptor = SimpleNamespace(config_path=tmp_path / "config/weather-enabled.json",
        settings={"schemaVersion": 1, "files": [
            {"id": "options", "base": "evejs", "path": "config/weather.json", "format": "json"},
            {"id": "private", "base": "mod", "path": "private.json", "format": "json"}],
            "fields": [{"id": "rain", "label": "Rain", "type": "boolean", "default": True,
                        "file": "options", "key": ["rain"]}]})
    assert protection.protect_updater_settings(tmp_path, [author])
    data = json.loads(policy.read_bytes())
    assert data["keepTrees"] == ["_local", "mods"]
    assert data["keepMine"] == ["config/server.json", "config/weather.json", "config/weather-enabled.json"]
    assert not protection.protect_updater_settings(tmp_path, [author])


@pytest.mark.parametrize("field,value", [("schema", "future.v2"), ("keepMine", "bad"), ("keepTrees", [42])])
def test_unknown_or_invalid_policy_is_preserved(tmp_path, field, value):
    policy, _ = fixture(tmp_path)
    data = json.loads(policy.read_bytes())
    data[field] = value
    policy.write_text(json.dumps(data))
    original = policy.read_bytes()
    with pytest.raises(ValueError):
        protection.protect_updater_settings(tmp_path, [mod()])
    assert policy.read_bytes() == original


def test_older_release_without_updater_requires_no_changes(tmp_path):
    assert not protection.protect_updater_settings(tmp_path, [mod()])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("native,foreign", [(False, False), (True, True)])
def test_read_only_backend_or_foreign_registry_never_prepares_policy(tmp_path, monkeypatch, native, foreign):
    policy, original = fixture(tmp_path)
    inventory = ModInventory(str(tmp_path), mods=(mod(),), registry_error="foreign" if foreign else "")
    monkeypatch.setattr(protection, "load_mod_inventory", lambda root: inventory)
    assert protection.load_inventory_with_updater_protection(str(tmp_path), native=native) is inventory
    assert policy.read_bytes() == original


def test_failed_protection_keeps_inventory_visible_and_explains_failure(tmp_path, monkeypatch):
    policy, original = fixture(tmp_path)
    inventory = ModInventory(str(tmp_path), mods=(mod(),))
    monkeypatch.setattr(protection, "load_mod_inventory", lambda root: inventory)
    atomic = protection._atomic
    def fail_policy(path, data):
        if path == policy:
            raise PermissionError("Policy is read-only")
        atomic(path, data)
    monkeypatch.setattr(protection, "_atomic", fail_policy)
    result = protection.load_inventory_with_updater_protection(str(tmp_path), native=True)
    assert result.mods == inventory.mods
    assert "read-only" in result.updater_warning
    assert policy.read_bytes() == original


def test_mods_refresh_prepares_policy_in_worker_and_finishes_on_gui(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QMainWindow, QLabel
    from src.ui.mod_coordinator import ModCoordinator
    policy, _ = fixture(tmp_path)
    owner = QMainWindow()
    owner._close_in_progress = False
    owner._docker_mode = lambda: False
    owner._set_operation_controls_busy = lambda busy: None
    inventory = ModInventory(str(tmp_path), mods=(mod(),))
    threads = []
    def load(root):
        threads.append(QThread.currentThread() != qapp.thread())
        return inventory
    monkeypatch.setattr(protection, "load_mod_inventory", load)
    presented = []
    coordinator = ModCoordinator(owner)
    coordinator._page = SimpleNamespace(_evejs_root=str(tmp_path), operation_notice=QLabel(owner),
        show_inventory=lambda value: presented.append((value, QThread.currentThread() == qapp.thread())))
    monkeypatch.setattr(coordinator._updates, "inventory_ready", lambda: None)
    coordinator.refresh()
    deadline = time.monotonic() + 5
    while coordinator._token is not None and time.monotonic() < deadline:
        qapp.processEvents()
        if coordinator._thread is not None:
            coordinator._thread.wait(5)
    qapp.processEvents()
    assert coordinator._token is None
    assert threads == [True] and presented == [(inventory, True)]
    assert "mods" in json.loads(policy.read_bytes())["keepTrees"]
    owner.deleteLater()
