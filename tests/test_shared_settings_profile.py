"""Shared UI profiles keep numeric identities and per-client login separate."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src import config
from src.core import platform, shared_settings_profile as profile
from src.core.shared_client_settings import SharedSettingsError


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "launcher" / "Profiles"
    current = root / "account-b" / "tq"
    current.mkdir(parents=True)
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: False)
    source = profile.SharedSettingsSource("account-a", 11, 90000001, str(tmp_path / "server"))
    original = platform.get_eve_settings_path(str(root / source.username / "tq"))
    original.mkdir(parents=True)
    (original / "core_char_90000001.dat").write_bytes(b"\x7echaracter-layout-a")
    (original / "core_user_11.dat").write_bytes(b"\x7eaccount-ui-a")
    (original / "core_public__.yaml").write_text("device:\n  antiAliasing: [1, 2]\n")
    shared = root.parent / "SharedSettings"
    shared.mkdir()
    (shared / "prefs.ini").write_bytes(b"port=39700\nusername=keep-own-login\n")
    (shared / "core_char__.dat").write_bytes(b"anonymous-cache-keep")
    (shared / "core_user__.dat").write_bytes(b"anonymous-cache-keep")
    return current, source, original, shared


def apply(world, **options):
    current, source, _, folder = world
    return profile.prepare_shared_character_profile(current, folder, source,
        account_id=options.pop("account_id", 12), character_id=options.pop("character_id", 90000002),
        evejs_root=source.evejs_root, **options)


def test_selected_profile_copies_all_ui_sections_under_each_own_identity(world):
    current, source, original, folder = world
    before = {p.name: p.read_bytes() for p in original.iterdir()}
    for account_id, character_id in zip(range(12, 18), range(90000002, 90000008)):
        apply(world, account_id=account_id, character_id=character_id)
        assert (folder / f"core_char_{character_id}.dat").read_bytes() == before["core_char_90000001.dat"]
        assert (folder / f"core_user_{account_id}.dat").read_bytes() == before["core_user_11.dat"]
    assert (folder / "core_public__.yaml").read_bytes() == before["core_public__.yaml"]
    assert (folder / "prefs.ini").read_bytes() == b"port=39700\nusername=keep-own-login\n"
    for name in ("core_char__.dat", "core_user__.dat"):
        assert (folder / name).read_bytes() == b"anonymous-cache-keep"
    assert {p.name: p.read_bytes() for p in original.iterdir()} == before


def test_existing_destination_settings_are_backed_up_before_replacement(world):
    current, source, _, folder = world
    old = {"core_char_90000002.dat": b"\x7eold-layout-b", "core_user_12.dat": b"\x7eold-account-b"}
    for name, data in old.items():
        (folder / name).write_bytes(data)
    apply(world)
    backups = list((current.parent.parent.parent / "SharedProfiles").glob("*/backups/*.bin"))
    assert set(old.values()) <= {p.read_bytes() for p in backups}
    receipts = list((current.parent.parent.parent / "SharedProfiles").glob("*/backups/*.json"))
    assert any(any(item["path"].endswith("core_char_90000002.dat") for item in json.loads(p.read_text())["files"])
               for p in receipts)


def test_running_clients_use_a_stable_snapshot_then_refresh_when_all_closed(world, monkeypatch):
    _, _, _, folder = world
    apply(world)
    (folder / "core_char_90000001.dat").write_bytes(b"\x7esource-edited-after-capture")
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    apply(world, account_id=13, character_id=90000003)
    assert (folder / "core_char_90000003.dat").read_bytes() == b"\x7echaracter-layout-a"
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: False)
    apply(world, account_id=14, character_id=90000004)
    assert (folder / "core_char_90000004.dat").read_bytes() == b"\x7esource-edited-after-capture"


def test_custom_folder_uses_the_selected_character_inside_that_folder(world):
    _, _, original, folder = world
    (folder / "core_char_90000001.dat").write_bytes(b"\x7echosen-custom-character")
    (folder / "core_user_11.dat").write_bytes(b"\x7echosen-custom-account")
    apply(world, custom_folder=True)
    assert (folder / "core_char_90000002.dat").read_bytes() == b"\x7echosen-custom-character"
    assert (folder / "core_user_12.dat").read_bytes() == b"\x7echosen-custom-account"
    assert (original / "core_char_90000001.dat").read_bytes() == b"\x7echaracter-layout-a"


def test_new_profile_selection_requires_all_clients_closed(world, monkeypatch):
    _, _, _, folder = world
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    with pytest.raises(SharedSettingsError, match="Close EVE clients"):
        apply(world)
    assert not (folder / "core_char_90000002.dat").exists()


def test_managed_first_character_is_persistent_across_subsequent_accounts(world, monkeypatch):
    current, source, _, folder = world
    first = profile.resolve_shared_profile_source(current, folder, None,
        account_id=12, character_id=90000002, evejs_root=source.evejs_root)
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    second = profile.resolve_shared_profile_source(current.parent.parent / "account-c" / "tq", folder, None,
        account_id=13, character_id=90000003, evejs_root=source.evejs_root)
    assert first == second
    assert first.username == "account-b" and first.character_id == 90000002


def test_first_managed_character_reuses_its_existing_private_profile(world):
    current, source, original, folder = world
    first_tq = current.parent.parent / "account-a" / "tq"
    first = profile.resolve_shared_profile_source(first_tq, folder, None,
        account_id=11, character_id=90000001, evejs_root=source.evejs_root)
    profile.prepare_shared_character_profile(first_tq, folder, first,
        account_id=11, character_id=90000001, evejs_root=source.evejs_root, automatic_source=True)
    assert (folder / "core_char_90000001.dat").read_bytes() == (original / "core_char_90000001.dat").read_bytes()


def test_first_ever_character_can_establish_a_default_managed_profile(world):
    current, source, _, folder = world
    first = profile.resolve_shared_profile_source(current, folder, None,
        account_id=12, character_id=90000002, evejs_root=source.evejs_root)
    profile.prepare_shared_character_profile(current, folder, first,
        account_id=12, character_id=90000002, evejs_root=source.evejs_root, automatic_source=True)
    assert (folder / "core_char_90000002.dat").read_bytes() == profile._EMPTY_SETTINGS
    assert (folder / "core_user_12.dat").read_bytes() == profile._EMPTY_SETTINGS


def test_profile_from_another_server_is_rejected_without_changes(world):
    current, source, _, folder = world
    with pytest.raises(SharedSettingsError, match="current EveJS server"):
        profile.prepare_shared_character_profile(current, folder, replace(source, evejs_root=source.evejs_root + "-other"),
            account_id=12, character_id=90000002, evejs_root=source.evejs_root)
    assert not (folder / "core_char_90000002.dat").exists()


def test_failed_copy_restores_existing_destinations(world, monkeypatch):
    _, _, _, folder = world
    character = folder / "core_char_90000002.dat"
    account = folder / "core_user_12.dat"
    character.write_bytes(b"\x7eoriginal-char-b")
    account.write_bytes(b"\x7eoriginal-account-b")
    write = profile._atomic_bytes
    def fail(path, data):
        if path == account and data == b"\x7eaccount-ui-a":
            raise OSError("fixture copy failure")
        write(path, data)
    monkeypatch.setattr(profile, "_atomic_bytes", fail)
    with pytest.raises(OSError, match="fixture copy failure"):
        apply(world)
    assert character.read_bytes() == b"\x7eoriginal-char-b"
    assert account.read_bytes() == b"\x7eoriginal-account-b"


def test_snapshot_tampering_is_rejected_before_any_destination_change(world, monkeypatch):
    current, _, _, folder = world
    apply(world)
    storage = next((current.parent.parent.parent / "SharedProfiles").iterdir())
    manifest = json.loads((storage / "active.json").read_text())
    (storage / manifest["revision"] / "character.dat").write_bytes(b"\x7etampered")
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    with pytest.raises(SharedSettingsError, match="could not be verified"):
        apply(world, character_id=90000003)
    assert not (folder / "core_char_90000003.dat").exists()


@pytest.mark.parametrize("bad", [None, {"username": "../other"}, {"username": "account-a", "account_id": True,
    "character_id": 90000001, "evejs_root": "C:/Games/EveJS"}])
def test_malformed_source_config_is_not_accepted(bad):
    assert config.normalize_shared_settings_source(bad) == {}


def test_source_config_migrates_and_survives_save(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.load()
    source = {"username": "account-a", "account_id": 11, "character_id": 90000001,
              "evejs_root": str(tmp_path / "server")}
    cfg["shared_settings_source"] = source
    config.save(cfg)
    assert config.load()["shared_settings_source"] == source


def test_actual_launch_preparation_keeps_two_distinct_auto_login_targets(world, monkeypatch):
    from types import SimpleNamespace
    from src import app as application
    from src.core import profiles, shared_client_settings
    from src.core.launcher import ClientLaunchContext
    from src.workers.client_launch_worker import ClientLaunchRequest

    current, source, _, folder = world
    current.rmdir()  # Launch preparation must create its own actual client junction.
    client = current.parent.parent.parent / "Copied client" / "tq"
    client.mkdir(parents=True)
    (client / "start.ini").write_text("[main]\nserver=127.0.0.1\nport=26000\n")
    monkeypatch.setattr(profiles, "create_directory_link", shared_client_settings._create_junction)
    monkeypatch.setattr(application, "wait_for_client_endpoints", lambda *_: None)
    monkeypatch.setattr(application, "require_shared_settings_support", lambda *_: None)
    monkeypatch.setattr(application, "inspect_client_auto_login_capability", lambda *_: SimpleNamespace(supported=True))
    monkeypatch.setattr(application, "inspect_auto_login_capability", lambda *_: SimpleNamespace(supported=True))
    captured = []
    monkeypatch.setattr(application, "launch_client", lambda **kwargs: captured.append(kwargs) or SimpleNamespace(pid=4242))
    for number, username in enumerate(("account-a", "account-b")):
        request = ClientLaunchRequest(username, f"Character {number}", source.evejs_root, str(client),
            current.parent.parent, ClientLaunchContext.native(), character_id=90000001 + number,
            auto_login_enabled=True, client_settings_mode="shared", account_id=11 + number,
            shared_settings_source=source)
        application._perform_client_launch(request)
        monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    assert [(item["auto_login"].username, item["auto_login"].character_id) for item in captured] == [
        ("account-a", 90000001), ("account-b", 90000002)]
    first, second = [item["shared_settings"] for item in captured]
    assert first.folder != second.folder
    assert first.argument != second.argument
    assert (second.folder / "core_char_90000002.dat").read_bytes() == (first.folder / "core_char_90000001.dat").read_bytes()
    assert (second.folder / "core_user_12.dat").read_bytes() == (first.folder / "core_user_11.dat").read_bytes()


def test_redirected_backup_parent_is_rejected_before_creating_external_files(world):
    from src.core.shared_client_settings import _create_junction
    current, _, _, _ = world
    outside = current.parent.parent.parent / "Unrelated folder"
    outside.mkdir()
    _create_junction(outside, current.parent.parent.parent / "SharedProfiles")
    with pytest.raises(SharedSettingsError, match="redirected"):
        apply(world)
    assert list(outside.iterdir()) == []


def test_automatic_source_cannot_be_claimed_while_another_client_runs(world, monkeypatch):
    current, source, _, folder = world
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    with pytest.raises(SharedSettingsError, match="Close EVE clients"):
        profile.resolve_shared_profile_source(current, folder, None, account_id=12,
            character_id=90000002, evejs_root=source.evejs_root)
    assert not list((current.parent.parent.parent / "SharedProfiles").glob("automatic-source-*.json"))


def test_only_source_edits_persist_even_with_public_preferences_and_same_account_characters(world, monkeypatch):
    current, source, _, _ = world
    root = current.parent.parent
    source_tq = root / source.username / "tq"
    source_tq.mkdir(parents=True, exist_ok=True)
    master = profile.prepare_shared_profile_launch(source_tq, source, account_id=11,
        character_id=90000001, evejs_root=source.evejs_root)
    baseline = {p.name: p.read_bytes() for p in master.folder.iterdir() if p.is_file()}
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    follower = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=90000002, evejs_root=source.evejs_root)
    sibling = profile.prepare_shared_profile_launch(source_tq, source, account_id=11,
        character_id=90000003, evejs_root=source.evejs_root)
    assert len({master.folder, follower.folder, sibling.folder}) == 3
    for working, account_id, char_id in ((follower, 12, 90000002), (sibling, 11, 90000003)):
        (working.folder / f"core_char_{char_id}.dat").write_bytes(b"\x7efollower-window-change")
        (working.folder / f"core_user_{account_id}.dat").write_bytes(b"\x7efollower-account-change")
        (working.folder / "core_public__.yaml").write_bytes(b"device: {antiAliasing: [1, 0]}")
    assert {p.name: p.read_bytes() for p in master.folder.iterdir() if p.is_file()} == baseline
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: False)
    restarted = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=90000002, evejs_root=source.evejs_root)
    assert restarted.folder != follower.folder
    assert (restarted.folder / "core_char_90000002.dat").read_bytes() == baseline["core_char_90000001.dat"]
    assert (restarted.folder / "core_user_12.dat").read_bytes() == baseline["core_user_11.dat"]
    assert (restarted.folder / "core_public__.yaml").read_bytes() == baseline["core_public__.yaml"]
    # Only edits saved by the selected main profile become the next baseline.
    (master.folder / "core_char_90000001.dat").write_bytes(b"\x7enew-master-layout")
    (master.folder / "core_user_11.dat").write_bytes(b"\x7enew-master-account")
    (master.folder / "core_public__.yaml").write_bytes(b"device: {antiAliasing: [1, 3]}")
    updated = profile.prepare_shared_profile_launch(current, source, account_id=12,
        character_id=90000002, evejs_root=source.evejs_root)
    assert (updated.folder / "core_char_90000002.dat").read_bytes() == b"\x7enew-master-layout"
    assert (updated.folder / "core_user_12.dat").read_bytes() == b"\x7enew-master-account"
    assert (updated.folder / "core_public__.yaml").read_bytes() == b"device: {antiAliasing: [1, 3]}"


def test_source_relaunch_never_discards_its_updates_while_followers_are_open(world, monkeypatch):
    current, source, _, _ = world
    source_tq = current.parent.parent / source.username / "tq"
    source_tq.mkdir(parents=True, exist_ok=True)
    master = profile.prepare_shared_profile_launch(source_tq, source, account_id=11,
        character_id=90000001, evejs_root=source.evejs_root)
    (master.folder / "core_char_90000001.dat").write_bytes(b"\x7emaster-recently-saved")
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    resumed = profile.prepare_shared_profile_launch(source_tq, source, account_id=11,
        character_id=90000001, evejs_root=source.evejs_root)
    assert resumed.folder == master.folder
    assert (resumed.folder / "core_char_90000001.dat").read_bytes() == b"\x7emaster-recently-saved"


def test_first_launch_managed_mode_establishes_source_without_a_folder_choice(world, monkeypatch):
    current, source, _, _ = world
    source_tq = current.parent.parent / source.username / "tq"
    source_tq.mkdir(parents=True, exist_ok=True)
    first = profile.prepare_shared_profile_launch(source_tq, None, account_id=11,
        character_id=90000001, evejs_root=source.evejs_root)
    monkeypatch.setattr("src.core.overview_patch.is_eve_client_running", lambda: True)
    later = profile.prepare_shared_profile_launch(current, None, account_id=12,
        character_id=90000002, evejs_root=source.evejs_root)
    assert (later.folder / "core_char_90000002.dat").read_bytes() == (first.folder / "core_char_90000001.dat").read_bytes()
    assert later.folder != first.folder
