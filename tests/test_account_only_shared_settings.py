"""New-account login must work with shared character settings enabled."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from src import app as application
from src.app import MainWindow
from src.core import profiles
from src.core.launcher import ClientLaunchContext


@pytest.mark.parametrize("selected_source", [False, True])
@pytest.mark.parametrize("profile_exists", [False, True])
@pytest.mark.parametrize("auto_login_enabled", [False, True])
def test_account_only_launch_uses_real_isolated_profile_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected_source: bool,
    profile_exists: bool,
    auto_login_enabled: bool,
) -> None:
    client = tmp_path / "Copied client" / "tq"
    client.mkdir(parents=True)
    (client / "start.ini").write_text("[main]\nserver=127.0.0.1\nport=26000\n")
    profile_root = tmp_path / "Profiles"
    monkeypatch.setattr(profiles, "PROFILES_ROOT", profile_root)
    monkeypatch.setattr(application, "PROFILES_ROOT", profile_root)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    source_settings = profiles.get_eve_settings_path(str(client))
    source_settings.mkdir(parents=True)
    for name, value in {
        "prefs.ini": b"newbie=0\nport=26000\n",
        "core_public__.yaml": b"private-source-login-do-not-copy",
        "core_user_11.dat": b"private-account-cache",
        "core_char_90000001.dat": b"private-character-cache",
    }.items():
        (source_settings / name).write_bytes(value)
    (source_settings / "Browser").mkdir()
    (source_settings / "Browser" / "session").write_bytes(b"private-browser-session")
    shared_folder = tmp_path / "SharedSettings"
    shared_folder.mkdir()
    (shared_folder / "core_user__.dat").write_bytes(b"existing-shared-cache")
    before_shared = {p.name: p.read_bytes() for p in shared_folder.iterdir()}

    cfg = {
        "evejs_root": str(tmp_path / "EveJS"), "client_path": str(client),
        "auto_login_enabled": auto_login_enabled, "client_settings_mode": "shared",
        "shared_settings_folder": str(shared_folder),
        "shared_settings_source": {
            "username": "source-account", "account_id": 11,
            "character_id": 90000001, "evejs_root": str(tmp_path / "EveJS"),
        } if selected_source else {},
    }
    window = SimpleNamespace(
        _cfg=cfg,
        _resolve_client_launch_context=lambda: (ClientLaunchContext.native(), ""),
        _resolve_configured_client_path=lambda _client, _root: client,
        _tracker=SimpleNamespace(is_account_running=lambda _username: False),
        _pending_client_launches=set(), _accounts=[], _docker_mode=lambda: False,
    )
    request = MainWindow._make_client_launch_request(window, "fixture-new", "")
    assert request is not None
    assert request.client_settings_mode == "separate"
    assert request.shared_settings_folder == ""
    assert request.shared_settings_source is None
    assert request.auto_login_enabled is auto_login_enabled
    assert request.hide_console is True

    monkeypatch.setattr(application, "wait_for_client_endpoints", lambda _context: None)
    monkeypatch.setattr(application, "inspect_client_auto_login_capability",
                        lambda *_args: SimpleNamespace(supported=True, reason="supported"))
    monkeypatch.setattr(application, "inspect_auto_login_capability",
                        lambda *_args: SimpleNamespace(supported=True, reason="supported"))
    endpoint_check = create_autospec(application.require_client_endpoints_ready)
    monkeypatch.setattr(application, "require_client_endpoints_ready", endpoint_check)
    spawn = create_autospec(application.launch_client, return_value=SimpleNamespace(pid=4242))
    monkeypatch.setattr(application, "launch_client", spawn)
    junction = profile_root / request.username / "tq"
    try:
        if profile_exists:
            # A previous Simple attempt may already have created its junction.
            profiles.create_profile(request.username, str(client), profile_root,
                                    bootstrap_settings=False)
        # Keep profile creation, bootstrap, username and endpoint writes real.
        process = application._perform_client_launch(request)
        assert process.pid == 4242
        settings = profiles.get_profile_settings_path(request.username)
        public = (settings / "core_public__.yaml").read_text(encoding="utf-8")
        assert "fixture-new" in public
        assert "private-source-login-do-not-copy" not in public
        assert not list(settings.glob("core_user*.dat"))
        assert not list(settings.glob("core_char*.dat"))
        assert not (settings / "Browser").exists()
        assert {p.name: p.read_bytes() for p in shared_folder.iterdir()} == before_shared
        assert cfg["client_settings_mode"] == "shared"
        assert cfg["shared_settings_folder"] == str(shared_folder)
        assert "shared_settings" not in spawn.call_args.kwargs
        intent = spawn.call_args.kwargs["auto_login"]
        if auto_login_enabled:
            assert intent.username == "fixture-new"
            assert intent.character_id is None
        else:
            assert intent is None
        assert spawn.call_args.kwargs["hide_console"] is True
        spawn.call_args.kwargs["pre_spawn_check"]()
        endpoint_check.assert_called_once_with(request.launch_context)
    finally:
        if profiles._path_entry_exists(junction):
            profiles.remove_directory_link(junction)
