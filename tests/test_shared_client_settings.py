"""Actual Windows junctions and immutable per-client login with shared settings."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src import config
from src.core import profiles, shared_client_settings as shared
from src.core.client_autologin import AutoLoginLaunch, AutoLoginCapability
from src.workers.client_launch_worker import ClientLaunchRequest
from src.core.launcher import ClientLaunchContext


@pytest.fixture
def world(tmp_path, monkeypatch):
    base = tmp_path.resolve()
    profiles_root = base / "profiles"
    profiles_root.mkdir()
    local = base / "localappdata"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(profiles, "PROFILES_ROOT", profiles_root)
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: False)
    folder = base / "Shared %PATH% & settings! (é)"
    folder.mkdir()
    return profiles_root, local, folder


def account(root, name):
    tq = root / name / "tq"
    tq.mkdir(parents=True)
    (tq / "start.ini").write_text("[main]\nserver=127.0.0.1\nport=26000\n")
    return tq


def test_automatic_folder_is_created_on_demand_and_shared_by_accounts(world):
    root, _, _ = world
    expected = root.parent / "SharedSettings"
    assert shared.resolve_shared_settings_folder(profiles_root=root) == expected
    assert not expected.exists()
    first = shared.prepare_shared_settings(account(root, "account-a"))
    second = shared.prepare_shared_settings(account(root, "account-b"))
    assert first.folder == second.folder == expected
    assert first.alias.resolve() == second.alias.resolve() == expected
    profiles.prefill_username("account-a", settings_dir=first.folder, update_username=False)
    (first.folder / "core_char_21.dat").write_bytes(b"character-a")
    assert (second.alias / "core_char_21.dat").read_bytes() == b"character-a"
    assert "account-a" not in (expected / "core_public__.yaml").read_text()


def test_custom_override_does_not_create_an_automatic_folder(world):
    root, _, custom = world
    assert shared.resolve_shared_settings_folder(custom, profiles_root=root, create=True) == custom
    assert not (root.parent / "SharedSettings").exists()


def test_managed_folder_uses_config_storage_when_no_profile_root_is_supplied(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher")
    expected = config.CONFIG_DIR / "SharedSettings"
    assert shared.resolve_shared_settings_folder(create=True) == expected
    assert expected.is_dir()


def test_managed_folder_never_adopts_a_redirected_directory(world):
    root, _, target = world
    path = root.parent / "SharedSettings"
    shared._create_junction(target, path)
    with pytest.raises(shared.SharedSettingsError, match="automatic shared settings folder is unavailable"):
        shared.resolve_shared_settings_folder(profiles_root=root, create=True)
    assert path.is_junction() and path.resolve() == target


def test_two_accounts_share_settings_without_changing_original_folders(world):
    root, _, folder = world
    (folder / "core_user_11.dat").write_bytes(b"original-account-data")
    aliases = []
    originals = []
    for name in ("account-a", "account-b"):
        tq = account(root, name)
        original = shared.platform.get_eve_settings_path(str(tq))
        original.mkdir(parents=True)
        (original / "prefs.ini").write_text("original=keep\n")
        originals.append(original)
        result = shared.prepare_shared_settings(tq, folder)
        aliases.append(result.alias)
        assert result.folder == folder
        assert result.argument == "/settingsprofile=EveJSLauncherShared"
        assert result.alias.is_junction()
        assert (original / "prefs.ini").read_text() == "original=keep\n"
    (aliases[0] / "core_char_21.dat").write_bytes(b"character-a")
    (aliases[1] / "core_char_22.dat").write_bytes(b"character-b")
    assert (aliases[0] / "core_char_22.dat").read_bytes() == b"character-b"
    assert (folder / "core_user_11.dat").read_bytes() == b"original-account-data"
    # Separate mode continues to resolve the untouched default folders.
    assert profiles.get_profile_settings_path("account-a") == originals[0]


def test_stable_shared_link_can_be_reused_while_other_clients_run(world, monkeypatch):
    root, _, folder = world
    first = account(root, "account-a")
    result = shared.prepare_shared_settings(first, folder)
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    assert shared.prepare_shared_settings(first, folder) == result
    second = account(root, "account-b")
    assert shared.prepare_shared_settings(second, folder).alias.is_junction()
    other = folder.parent / "Other shared settings"
    other.mkdir()
    with pytest.raises(shared.SharedSettingsError, match="Close EVE"):
        shared.prepare_shared_settings(first, other)
    assert result.alias.resolve() == folder


@pytest.mark.parametrize("automatic_first", [True, False])
def test_switching_shared_folder_allows_six_existing_accounts_to_launch(world, monkeypatch, automatic_first):
    root, _, custom = world
    accounts = [account(root, f"account-{number}") for number in range(6)]
    original_files = {}
    for number, tq in enumerate(accounts):
        original = shared.platform.get_eve_settings_path(str(tq))
        original.mkdir(parents=True)
        for name, content in (("prefs.ini", b"original=keep\n"),
                              (f"core_char_{90000001 + number}.dat", b"original-layout")):
            path = original / name
            path.write_bytes(content)
            original_files[path] = content
    old_folder = "" if automatic_first else custom
    for tq in accounts:
        shared.prepare_shared_settings(tq, old_folder)
    selected_folder = shared.platform.get_eve_settings_path(str(accounts[0])) if automatic_first else ""
    target = shared.resolve_shared_settings_folder(selected_folder, profiles_root=root, create=True)
    # Settings were saved with all clients closed. The first successful launch
    # must not leave the remaining accounts needing an unsafe live rebind.
    first = shared.prepare_shared_settings(accounts[0], selected_folder)
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    results = [first] + [shared.prepare_shared_settings(tq, selected_folder) for tq in accounts[1:]]
    assert all(result.alias.is_junction() and result.alias.resolve() == target for result in results)
    assert all(path.read_bytes() == content for path, content in original_files.items())


def test_first_launch_repairs_peers_even_when_its_own_binding_is_current(world, monkeypatch):
    root, _, selected = world
    first, second = [account(root, name) for name in ("account-a", "account-b")]
    stale = root.parent / "Previous shared settings"
    stale.mkdir()
    shared._prepare_shared_settings_binding(first, selected)
    shared._prepare_shared_settings_binding(second, stale)
    shared.prepare_shared_settings(first, selected)
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    assert shared.prepare_shared_settings(second, selected).alias.resolve() == selected


def test_folder_switch_preserves_unowned_profiles_and_other_endpoints(world):
    root, _, old = world
    first, other_host, unowned = [account(root, name) for name in ("account-a", "other-host", "unowned")]
    shared.prepare_shared_settings(first, old)
    other = shared.prepare_shared_settings(other_host, old, host="127.0.0.2")
    unowned_alias, _ = shared.shared_settings_alias(unowned)
    unowned_alias.mkdir(parents=True)
    unowned_file = unowned_alias / "core_char_90000003.dat"
    unowned_file.write_bytes(b"leave-alone")
    selected = root.parent / "Selected shared settings"
    selected.mkdir()
    shared.prepare_shared_settings(first, selected)
    assert other.alias.resolve() == old
    assert unowned_file.read_bytes() == b"leave-alone"
    assert not unowned_alias.is_junction()


def test_folder_switch_never_adopts_a_tampered_peer_binding(world):
    root, _, old = world
    first, peer = [account(root, name) for name in ("account-a", "account-b")]
    shared.prepare_shared_settings(first, old)
    result = shared.prepare_shared_settings(peer, old)
    unexpected = root.parent / "Unowned target"
    unexpected.mkdir()
    sentinel = unexpected / "core_char_90000003.dat"
    sentinel.write_bytes(b"leave-alone")
    shared._remove_junction(result.alias)
    shared._create_junction(unexpected, result.alias)
    selected = root.parent / "Selected shared settings"
    selected.mkdir()
    with pytest.raises(shared.SharedSettingsError, match="could not be verified"):
        shared.prepare_shared_settings(first, selected)
    assert result.alias.resolve() == unexpected
    assert sentinel.read_bytes() == b"leave-alone"


def test_retarget_preserves_both_settings_folders(world):
    root, _, first = world
    tq = account(root, "account-a")
    (first / "prefs.ini").write_text("old=keep\n")
    second = first.parent / "Other settings"
    second.mkdir()
    (second / "prefs.ini").write_text("new=keep\n")
    shared.prepare_shared_settings(tq, first)
    result = shared.prepare_shared_settings(tq, second)
    assert result.alias.resolve() == second
    assert (first / "prefs.ini").read_text() == "old=keep\n"
    assert (second / "prefs.ini").read_text() == "new=keep\n"


def test_failed_retarget_restores_link_and_marker(world, monkeypatch):
    root, _, first = world
    tq = account(root, "account-a")
    original = shared.prepare_shared_settings(tq, first)
    marker = shared.binding_marker(tq, original.alias)
    before = marker.read_bytes()
    second = first.parent / "Other settings"
    second.mkdir()
    create = shared._create_junction
    def fail(target, alias):
        if target == second:
            raise OSError("fixture failure")
        create(target, alias)
    monkeypatch.setattr(shared, "_create_junction", fail)
    with pytest.raises(OSError, match="fixture failure"):
        shared.prepare_shared_settings(tq, second)
    assert original.alias.resolve() == first
    assert marker.read_bytes() == before


def test_existing_unowned_folder_is_never_replaced(world):
    root, _, folder = world
    tq = account(root, "account-a")
    alias = shared.platform.get_eve_settings_path(str(tq)).with_name("settings_" + shared.SHARED_PROFILE_NAME)
    alias.mkdir(parents=True)
    (alias / "keep.txt").write_text("keep")
    with pytest.raises(shared.SharedSettingsError, match="not owned"):
        shared.prepare_shared_settings(tq, folder)
    assert (alias / "keep.txt").read_text() == "keep"


def test_interrupted_retarget_recovers_from_journal(world):
    root, _, first = world
    tq = account(root, "account-a")
    original = shared.prepare_shared_settings(tq, first)
    second = first.parent / "New shared settings"
    second.mkdir()
    marker = shared.binding_marker(tq, original.alias)
    pending = json.loads(marker.read_text())
    pending.update(target=str(second), previousTarget=str(first))
    marker.write_text(json.dumps(pending))
    shared._remove_junction(original.alias)
    result = shared.prepare_shared_settings(tq, second)
    assert result.alias.resolve() == second
    assert first.is_dir()
    assert json.loads(marker.read_text())["previousTarget"] is None


def test_changed_link_target_is_preserved_and_rejected(world):
    root, _, first = world
    tq = account(root, "account-a")
    original = shared.prepare_shared_settings(tq, first)
    unexpected = first.parent / "Unowned target"
    unexpected.mkdir()
    (unexpected / "keep.txt").write_text("keep")
    shared._remove_junction(original.alias)
    shared._create_junction(unexpected, original.alias)
    with pytest.raises(shared.SharedSettingsError, match="could not be verified"):
        shared.prepare_shared_settings(tq, first)
    assert original.alias.resolve() == unexpected
    assert (unexpected / "keep.txt").read_text() == "keep"


def test_shared_preparation_does_not_replace_login_identity(world):
    _, _, folder = world
    template = Path(profiles.__file__).parent / "template_settings/core_public__.yaml"
    text = profiles._patch_core_public_login(template.read_text(), 123, '"existing-account"')
    (folder / "core_public__.yaml").write_text(text)
    (folder / "prefs.ini").write_text("newbie=0\nport=26000\n")
    for username in ("account-a", "account-b"):
        profiles.prefill_username(username, settings_dir=folder, update_username=False)
    assert (folder / "core_public__.yaml").read_text() == text


def test_empty_shared_folder_bootstraps_without_template_username(world):
    _, _, folder = world
    profiles.prefill_username("account-a", settings_dir=folder, update_username=False)
    text = (folder / "core_public__.yaml").read_text()
    assert "__USERNAME__" not in text and "__TS__" not in text
    assert "account-a" not in text
    assert "clusterMode=LOCAL" in (folder / "prefs.ini").read_text()


def test_existing_shared_preferences_and_binary_migration_are_left_to_eve(world):
    _, _, folder = world
    (folder / "core_public__.dat").write_bytes(b"legacy-public-settings")
    (folder / "prefs.ini").write_text("server=Tranquility\nport=26000\nnewbie=1\n")
    before = (folder / "prefs.ini").read_bytes()
    profiles.prefill_username("account-a", settings_dir=folder, update_username=False)
    assert (folder / "prefs.ini").read_bytes() == before
    assert not (folder / "core_public__.yaml").exists()
    (folder / "core_public__.yaml").write_text('ui:\n  username: [1, "keep"]\n')
    yaml_before = (folder / "core_public__.yaml").read_bytes()
    profiles.prefill_username("account-b", settings_dir=folder, update_username=False)
    assert (folder / "core_public__.yaml").read_bytes() == yaml_before


def test_shared_routing_keeps_existing_preferences_and_uses_exact_client_key(world):
    root, _, folder = world
    tq = account(root, "user@email.example")
    (folder / "prefs.ini").write_text("server=Tranquility\nport=26000\n")
    before = (folder / "prefs.ini").read_bytes()
    result = shared.prepare_shared_settings(tq, folder, host="127.0.0.2")
    assert "user@email.example" in result.alias.parent.name
    assert result.alias.parent.name.endswith("_127.0.0.2")
    profiles.configure_profile_game_endpoint("user@email.example", tq, host="127.0.0.2", port=39700,
        settings_dir=folder, write_preferences=False)
    assert (folder / "prefs.ini").read_bytes() == before
    assert "port=39700" in (tq / "start.ini").read_text()


@pytest.mark.parametrize("value", ["", "relative", "C:/missing/shared-settings", 'C:/bad\nname'])
def test_invalid_folder_selection_fails_without_creation(value):
    with pytest.raises(shared.SharedSettingsError):
        shared.validate_shared_folder(value)


def test_config_defaults_and_shared_selection_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.load()
    assert cfg["client_settings_mode"] == "separate"
    assert cfg["shared_settings_folder"] == ""
    cfg.update(client_settings_mode="shared", shared_settings_folder="C:/Games/EVE Settings")
    config.save(cfg)
    assert config.load()["shared_settings_folder"] == cfg["shared_settings_folder"]
    assert config.load()["client_settings_mode"] == "shared"


def test_requests_capture_selection_and_auto_login_remains_per_process(tmp_path, monkeypatch):
    from src.core import client_autologin as login
    monkeypatch.setattr(login, "inspect_auto_login_capability", lambda *_: AutoLoginCapability(True, "fixture"))
    first = ClientLaunchRequest("account-a", "Character A", str(tmp_path), str(tmp_path), tmp_path,
        ClientLaunchContext.native(), character_id=90000001, auto_login_enabled=True,
        client_settings_mode="shared", shared_settings_folder=str(tmp_path))
    second = replace(first, username="account-b", character_name="Character B", character_id=90000002)
    arguments = [login.require_auto_login_arguments(AutoLoginLaunch(r.username, r.character_id),
        evejs_root=r.evejs_root, client_path=r.client_path, game_host="127.0.0.1") for r in (first, second)]
    assert "/login:account-a:evejs-local" in arguments[0]
    assert "/login:account-b:evejs-local" in arguments[1]
    assert "/autoSelectCharacter:90000001" in arguments[0]
    assert "/autoSelectCharacter:90000002" in arguments[1]
