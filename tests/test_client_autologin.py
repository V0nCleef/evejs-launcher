"""Build-gated, credential-free automatic client login tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from src.core import client_autologin
from src.core import platform_win
from src.core.client_autologin import (
    AutoLoginLaunch,
    AutoLoginUnavailableError,
    build_auto_login_arguments,
    inspect_auto_login_capability,
    require_auto_login_arguments,
)


def _supported_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    root = tmp_path / "evejs"
    client = tmp_path / "client" / "tq"
    (root / "config").mkdir(parents=True)
    client.mkdir(parents=True)
    (client / "bin64").mkdir()
    (client / "bin64" / "exefile.exe").write_bytes(
        b"fixture client help: /console , /noconsole, /inherit"
    )
    (root / "config" / "server.json").write_text(
        json.dumps({"development": {"devSkipPasswordValidation": True}}),
        encoding="utf-8",
    )
    (client / "start.ini").write_text(
        "[main]\nbuild = 3396210\nserver=127.0.0.1\nport=26000\n",
        encoding="utf-8",
    )
    fixture_entries = {
        entry: f"fixture:{index}".encode("ascii")
        for index, entry in enumerate(client_autologin._REQUIRED_ENTRY_SHA256)
    }
    with zipfile.ZipFile(client / "code.ccp", "w") as archive:
        for entry, payload in fixture_entries.items():
            archive.writestr(entry, payload)
    monkeypatch.setattr(
        client_autologin,
        "_REQUIRED_ENTRY_SHA256",
        {
            entry: hashlib.sha256(payload).hexdigest().upper()
            for entry, payload in fixture_entries.items()
        },
    )
    return root, client


def test_known_build_modules_and_local_password_bypass_are_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client = _supported_fixture(tmp_path, monkeypatch)

    capability = inspect_auto_login_capability(root, client)

    assert capability.supported is True
    assert capability.build == 3396210
    assert "no client patch" in capability.reason.casefold()


def test_unknown_build_and_modified_login_module_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client = _supported_fixture(tmp_path, monkeypatch)
    (client / "start.ini").write_text("[main]\nbuild=3458726\n", encoding="utf-8")

    capability = inspect_auto_login_capability(root, client)
    assert capability.supported is False
    assert "3458726" in capability.reason

    (client / "start.ini").write_text("[main]\nbuild=3396210\n", encoding="utf-8")
    changed_entry = next(iter(client_autologin._REQUIRED_ENTRY_SHA256))
    with zipfile.ZipFile(client / "code.ccp", "r") as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries[changed_entry] = b"changed"
    with zipfile.ZipFile(client / "code.ccp", "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)

    capability = inspect_auto_login_capability(root, client)
    assert capability.supported is False
    assert "modified" in capability.reason.casefold()


def test_disabled_password_bypass_is_not_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client = _supported_fixture(tmp_path, monkeypatch)
    (root / "config" / "server.json").write_text(
        json.dumps({"development": {"devSkipPasswordValidation": False}}),
        encoding="utf-8",
    )

    capability = inspect_auto_login_capability(root, client)

    assert capability.supported is False
    assert "password bypass" in capability.reason.casefold()


def test_client_without_native_no_console_mode_is_not_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, client = _supported_fixture(tmp_path, monkeypatch)
    (client / "bin64" / "exefile.exe").write_bytes(b"fixture without switch")

    capability = inspect_auto_login_capability(root, client)

    assert capability.supported is False
    assert "no-console" in capability.reason.casefold()


def test_arguments_use_only_dummy_local_value_and_exact_character_id() -> None:
    arguments = build_auto_login_arguments(
        AutoLoginLaunch(username="fixture-account", character_id=90000001)
    )

    assert arguments == (
        "/noconsole",
        "/login:fixture-account:evejs-local",
        "/autoSelectCharacter:90000001",
    )


@pytest.mark.parametrize(
    "intent",
    [
        AutoLoginLaunch(username="", character_id=1),
        AutoLoginLaunch(username="bad:account", character_id=1),
        AutoLoginLaunch(username="bad\naccount", character_id=1),
        AutoLoginLaunch(username=" account", character_id=1),
        AutoLoginLaunch(username="account", character_id=0),
        AutoLoginLaunch(username="account", character_id=-1),
        AutoLoginLaunch(username="account", character_id=True),
    ],
)
def test_malformed_account_or_character_values_are_rejected(
    intent: AutoLoginLaunch,
) -> None:
    with pytest.raises(ValueError):
        build_auto_login_arguments(intent)


def test_non_loopback_target_is_rejected_before_client_inspection() -> None:
    with pytest.raises(AutoLoginUnavailableError, match="local EveJS"):
        require_auto_login_arguments(
            AutoLoginLaunch("fixture-account", 1),
            evejs_root="missing",
            client_path="missing",
            game_host="tranquility.servers.eveonline.com",
        )


def test_windows_launch_uses_an_argument_list_without_shell_or_redirection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(platform_win.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(platform_win, "get_client_process_flags", lambda: {})
    executable = tmp_path / "bin64" / "exefile.exe"

    result = platform_win.launch_eve_client(
        executable,
        {"FIXTURE": "1"},
        executable.parent,
        arguments=(
            "/noconsole",
            "/login:fixture-account:evejs-local",
            "/autoSelectCharacter:90000001",
        ),
    )

    assert result is not None
    assert captured["command"] == [
        str(executable),
        "/noconsole",
        "/login:fixture-account:evejs-local",
        "/autoSelectCharacter:90000001",
    ]
    kwargs = captured["kwargs"]
    assert "shell" not in kwargs
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs
