from copy import deepcopy

import pytest

from src.core.installation_profiles import (
    InstallationProfileError, add_profile, ensure_profiles, remove_profile,
    rename_profile, select_profile, sync_active_profile, client_profiles_root,
)


def test_legacy_migration_and_two_worlds_keep_private_preferences(tmp_path):
    original = {"evejs_root": str(tmp_path / "normal"), "runtime_backend": "native",
                "hidden_characters": ["Pilot"], "language": "nl", "game_port": 26000}
    cfg = ensure_profiles(original)
    assert original.get("installation_profiles") is None
    assert cfg["active_installation_profile"] == "default"
    assert client_profiles_root(cfg, tmp_path / "Profiles") == tmp_path / "Profiles"
    other = add_profile(cfg, "Ironman", {"evejs_root": str(tmp_path / "ironman"), "game_port": 26100})
    chosen = select_profile(other, other["installation_profiles"][-1]["id"])
    assert chosen["evejs_root"].endswith("ironman")
    assert chosen["hidden_characters"] == []
    assert chosen["language"] == "nl"
    assert client_profiles_root(chosen, tmp_path / "Profiles") != tmp_path / "Profiles"
    chosen["hidden_characters"] = ["Second Pilot"]
    chosen = sync_active_profile(chosen)
    back = select_profile(chosen, "default")
    assert back["hidden_characters"] == ["Pilot"]
    assert back["game_port"] == 26000


def test_names_do_not_change_identity_and_remove_only_registration(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    sentinel = world / "save.sqlite"
    sentinel.write_bytes(b"preserve")
    cfg = add_profile(ensure_profiles({"evejs_root": "first"}), "Second", {"evejs_root": str(world)})
    second_id = cfg["installation_profiles"][-1]["id"]
    renamed = rename_profile(cfg, second_id, "Renamed")
    assert renamed["installation_profiles"][-1]["id"] == second_id
    assert remove_profile(renamed, second_id)["active_installation_profile"] == "default"
    assert sentinel.read_bytes() == b"preserve"
    with pytest.raises(InstallationProfileError):
        remove_profile(cfg, "default")


def test_invalid_duplicate_and_path_looking_profile_ids_are_rejected():
    cfg = ensure_profiles({})
    corrupt = deepcopy(cfg)
    corrupt["installation_profiles"].append(deepcopy(corrupt["installation_profiles"][0]))
    with pytest.raises(InstallationProfileError):
        ensure_profiles(corrupt)
    corrupt = deepcopy(cfg)
    corrupt["installation_profiles"][0]["id"] = "../outside"
    with pytest.raises(InstallationProfileError):
        ensure_profiles(corrupt)
    with pytest.raises(InstallationProfileError):
        add_profile(cfg, "bad\nname", {})


def test_config_persistence_captures_active_world_and_preserves_others(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = ensure_profiles(config._default_config())
    cfg["evejs_root"] = "normal"
    cfg = add_profile(sync_active_profile(cfg), "Ironman", {"evejs_root": "ironman"})
    config.save(cfg)
    loaded = config.load()
    loaded = select_profile(loaded, loaded["installation_profiles"][-1]["id"])
    loaded["game_port"] = 26123
    config.save(loaded)
    assert config.load()["game_port"] == 26123
    assert select_profile(config.load(), "default")["evejs_root"] == "normal"
