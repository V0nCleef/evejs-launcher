"""Stable installation metadata without weakening authoritative runtime identity."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import config
from src.app import MainWindow
from src.core.db import Account, Character
from src.core.groups import (CharacterGroup, GroupMember, TargetGroupState, find_relink_candidates,
    installation_group_identity, load_installation_groups, load_target_groups, save_target_groups)
from src.core.runtime.data import native_data_selection, docker_project_identity, select_docker_data_source
from src.core.runtime.docker_compose import ComposeTarget, parse_compose_config
from src.core.service_status import DockerControlPolicy

STATE = TargetGroupState((CharacterGroup("fleet", "Fleet", "gold", (GroupMember(1, 101),)),
                         CharacterGroup("empty", "Empty", "steel")), "fleet")


def cfg(profile="world-a", backend="native"):
    return dict(active_installation_profile=profile, runtime_backend=backend)


def test_native_root_upgrade_retains_groups_and_original_legacy_entry(tmp_path):
    path = tmp_path / "groups.json"
    old, new = native_data_selection("C:/Fixture/EveJS-old"), native_data_selection("C:/Fixture/EveJS-new")
    assert old.target_identity != new.target_identity
    key = installation_group_identity(cfg(), old)
    assert key == installation_group_identity(cfg(), new)
    save_target_groups(old.target_identity, STATE, path=path)
    assert load_installation_groups(key, old.target_identity, path=path) == STATE
    assert load_installation_groups(key, new.target_identity, path=path) == STATE
    assert load_target_groups(old.target_identity, path=path) == STATE
    assert load_target_groups(key, path=path) == STATE


def test_empty_scope_and_new_edits_take_precedence_over_legacy(tmp_path):
    path = tmp_path / "groups.json"
    save_target_groups("legacy", STATE, path=path)
    save_target_groups("stable", TargetGroupState(), path=path)
    assert load_installation_groups("stable", "legacy", path=path) == TargetGroupState()
    assert load_target_groups("legacy", path=path) == STATE


def test_different_profiles_and_backends_never_automatically_merge(tmp_path):
    path = tmp_path / "groups.json"
    source = native_data_selection("C:/Fixture/EveJS")
    first = installation_group_identity(cfg(), source)
    second = installation_group_identity(cfg("world-b"), source)
    docker = installation_group_identity(cfg(backend="docker_compose"), SimpleNamespace(target_identity="runtime", group_identity="data"))
    assert len({first, second, docker}) == 3
    save_target_groups(first, STATE, path=path)
    assert load_installation_groups(second, "new-runtime", path=path) == TargetGroupState()
    assert load_installation_groups(docker, "docker-runtime", path=path) == TargetGroupState()


def test_already_moved_legacy_groups_remain_explicitly_recoverable(tmp_path):
    path = tmp_path / "groups.json"
    save_target_groups("old-root", STATE, path=path)
    assert load_installation_groups("new-scope", "new-root", path=path) == TargetGroupState()
    accounts = [Account("fixture", 1, "0", False, [Character(101, "Pilot")])]
    assert find_relink_candidates("new-scope", accounts, path=path) == (STATE,)
    save_target_groups("new-scope", STATE, path=path)  # Explicit reviewed Restore/Save path.
    assert load_installation_groups("new-scope", "later-root", path=path) == STATE
    assert load_target_groups("old-root", path=path) == STATE


def _compose(mount, *, actual="persistent-world"):
    return {"name": "fixture-world", "volumes": {"data": {"name": actual}}, "services": {
        "server": {"image": "fixture-server:old", "volumes": [mount], "ports": [
            {"target": port, "published": str(port), "host_ip": "127.0.0.1"} for port in (26000, 26001, 26002, 26003, 5222)]},
        "market": {"image": "fixture-market:old", "ports": [{"target": 40110, "published": "40110", "host_ip": "127.0.0.1"}]}}}


def test_docker_image_and_preload_changes_keep_groups_but_invalidate_runtime(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text("old image")
    target = ComposeTarget(compose, tmp_path, "fixture-world")
    payload = _compose({"type": "volume", "source": "data", "target": "/var/lib/evejs"})
    first = parse_compose_config(payload)
    old_runtime = docker_project_identity(target, first.project_name, config=first)
    changed = deepcopy(payload)
    changed["services"]["server"].update(image="fixture-server:new", environment={"NODE_OPTIONS": "--require /app/mods/new/loader.js"})
    changed["services"]["market"]["image"] = "fixture-market:new"
    second = parse_compose_config(changed)
    compose.write_text("new image and mods")
    new_runtime = docker_project_identity(target, second.project_name, config=second)
    assert old_runtime != new_runtime and first.game_store_identity == second.game_store_identity
    selections = [select_docker_data_source(target, item, {}, object(), control_policy=DockerControlPolicy.MANAGED) for item in (first, second)]
    keys = [installation_group_identity(cfg(backend="docker_compose"), selection) for selection in selections]
    assert keys[0] == keys[1] and keys[0].startswith("installation-groups:")
    path = tmp_path / "groups.json"
    save_target_groups(old_runtime, STATE, path=path)
    assert load_installation_groups(keys[0], old_runtime, path=path) == STATE
    assert load_installation_groups(keys[1], new_runtime, path=path) == STATE


def test_docker_different_actual_volume_or_store_subpath_gets_different_scope():
    payload = _compose({"type": "volume", "source": "data", "target": "/var/lib/evejs"})
    first = parse_compose_config(payload).game_store_identity
    payload["volumes"]["data"]["name"] = "different-world"
    assert parse_compose_config(payload).game_store_identity != first
    payload["volumes"]["data"]["name"] = "persistent-world"
    payload["services"]["server"]["volumes"][0]["target"] += "/gameStore"
    assert parse_compose_config(payload).game_store_identity != first


def test_docker_volume_alias_rename_preserves_same_actual_storage():
    payload = _compose({"type": "volume", "source": "data", "target": "/var/lib/evejs"})
    first = parse_compose_config(payload).game_store_identity
    payload["volumes"]["renamed"] = payload["volumes"].pop("data")
    payload["services"]["server"]["volumes"][0]["source"] = "renamed"
    assert parse_compose_config(payload).game_store_identity == first


def test_docker_bind_scope_ignores_build_but_separates_world_paths(tmp_path):
    payload = _compose({"type": "bind", "source": str(tmp_path / "world-a"), "target": "/var/lib/evejs"})
    first = parse_compose_config(payload).game_store_identity
    payload["services"]["server"]["image"] = "fixture:new"
    assert parse_compose_config(payload).game_store_identity == first
    payload["services"]["server"]["volumes"][0]["source"] = str(tmp_path / "world-b")
    assert parse_compose_config(payload).game_store_identity != first


def test_group_scope_and_migration_are_used_by_real_mainwindow_methods(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMainWindow
    from src.core import groups
    monkeypatch.setattr(groups, "GROUPS_FILE", tmp_path / "groups.json")
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = cfg()
    window._data_selection = native_data_selection("C:/Fixture/old")
    save_target_groups(window._data_selection.target_identity, STATE)
    window._sync_character_groups(window._data_selection.target_identity)
    assert window._group_state == STATE
    original_scope = window._group_storage_identity
    window._data_selection = native_data_selection("C:/Fixture/new")
    window._sync_character_groups(window._data_selection.target_identity)
    assert window._group_state == STATE and window._group_storage_identity == original_scope
    window._refresh_character_views = lambda: None
    window._on_group_selection_changed("empty")
    assert load_target_groups(original_scope).selected_group_id == "empty"
    window.deleteLater()


@pytest.mark.parametrize("bad", [None, "../escape", [], 123])
def test_unregistered_or_invalid_profile_does_not_guess_scope(bad):
    selection = native_data_selection("C:/Fixture")
    assert installation_group_identity(cfg(bad), selection) == selection.target_identity
