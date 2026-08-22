"""Atomic per-launch EVE game endpoint profile configuration."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core import profiles


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    profile_tq = tmp_path / "profile" / "tq"
    settings = tmp_path / "settings"
    profile_tq.mkdir(parents=True)
    settings.mkdir(parents=True)
    return profile_tq, settings


def test_bootstrap_copies_only_safe_settings_without_account_cache_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source-settings"
    destination = tmp_path / "profile-settings"
    source.mkdir()
    (source / "prefs.ini").write_text("source-prefs", encoding="utf-8")
    (source / "core_public__.yaml").write_text(
        "private-source-profile",
        encoding="utf-8",
    )
    (source / "core_user__.dat").write_bytes(b"private-user-cache")
    (source / "core_char__.dat").write_bytes(b"private-character-cache")
    browser = source / "Browser"
    browser.mkdir()
    (browser / "private-state").write_bytes(b"private-browser-cache")

    module_dir = tmp_path / "module"
    template_dir = module_dir / "template_settings"
    template_dir.mkdir(parents=True)
    (template_dir / "prefs.ini").write_text("template-prefs", encoding="utf-8")
    (template_dir / "core_public__.yaml").write_text(
        "generic-template",
        encoding="utf-8",
    )
    monkeypatch.setattr(profiles, "__file__", str(module_dir / "profiles.py"))
    monkeypatch.setattr(
        profiles,
        "get_profile_settings_path",
        lambda _username: destination,
    )
    monkeypatch.setattr(
        profiles,
        "get_eve_settings_path",
        lambda _client_path: source,
    )

    profiles._bootstrap_settings("fixture-account", "C:/Fixture/tq")

    assert (destination / "prefs.ini").read_text(encoding="utf-8") == "source-prefs"
    assert (
        destination / "core_public__.yaml"
    ).read_text(encoding="utf-8") == "generic-template"
    assert {path.name for path in destination.iterdir()} == {
        "prefs.ini",
        "core_public__.yaml",
    }


def _write_complete_core_public_template(template_dir: Path) -> str:
    text = (
        "audio:\n"
        "  masterVolume: [1, 0.7]\n"
        "device:\n"
        "  WindowMode: [1, 1]\n"
        "generic: {}\n"
        "ui:\n"
        "  customSetting: [1, true]\n"
        "  username: [__TS__, __USERNAME__]\n"
        "  usernames:\n"
        "  - __TS__\n"
        "  - [__USERNAME__]\n"
    )
    template_dir.mkdir(parents=True)
    (template_dir / "core_public__.yaml").write_text(text, encoding="utf-8")
    return text


def test_prefill_username_serializes_numeric_account_as_yaml_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    module_dir = tmp_path / "module"
    _write_complete_core_public_template(module_dir / "template_settings")
    monkeypatch.setattr(profiles, "__file__", str(module_dir / "profiles.py"))
    monkeypatch.setattr(
        profiles,
        "get_profile_settings_path",
        lambda _username: settings,
    )

    profiles.prefill_username("5259819")

    text = (settings / "core_public__.yaml").read_text(encoding="utf-8")
    assert ', "5259819"]' in text
    assert '  - ["5259819"]' in text
    assert ", 5259819]" not in text


def test_prefill_username_repairs_incomplete_yaml_and_preserves_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    malformed = (
        "generic: {}\n"
        "ui:\n"
        "  retainedUserSetting: [77, true]\n"
        "  username: [1, old-account]\n"
        "  usernames:\n"
        "  - 1\n"
        "  - [old-account]\n"
    )
    yaml_path = settings / "core_public__.yaml"
    yaml_path.write_text(malformed, encoding="utf-8")
    module_dir = tmp_path / "module"
    _write_complete_core_public_template(module_dir / "template_settings")
    monkeypatch.setattr(profiles, "__file__", str(module_dir / "profiles.py"))
    monkeypatch.setattr(
        profiles,
        "get_profile_settings_path",
        lambda _username: settings,
    )

    profiles.prefill_username("5259819")

    repaired = yaml_path.read_text(encoding="utf-8")
    assert all(f"{section}:" in repaired for section in ("audio", "device", "generic", "ui"))
    assert ', "5259819"]' in repaired
    backup = settings / "core_public__.yaml.launcher-backup"
    assert backup.read_text(encoding="utf-8") == malformed
    assert "retainedUserSetting: [77, true]" in repaired

    profiles.prefill_username("another-account")

    assert backup.read_text(encoding="utf-8") == malformed
    assert "retainedUserSetting: [77, true]" in yaml_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("generic_line", ("generic: false", "generic:"))
def test_prefill_username_repairs_scalar_sections_and_missing_login_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    generic_line: str,
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    malformed = (
        "audio: null\n"
        "device: []\n"
        f"{generic_line}\n"
        "ui:\n"
        "  retainedUserSetting: [77, true]\n"
    )
    yaml_path = settings / "core_public__.yaml"
    yaml_path.write_text(malformed, encoding="utf-8")
    module_dir = tmp_path / "module"
    _write_complete_core_public_template(module_dir / "template_settings")
    monkeypatch.setattr(profiles, "__file__", str(module_dir / "profiles.py"))
    monkeypatch.setattr(
        profiles,
        "get_profile_settings_path",
        lambda _username: settings,
    )

    profiles.prefill_username("5259819")

    repaired = yaml_path.read_text(encoding="utf-8")
    assert "audio:\n  masterVolume:" in repaired
    assert "device:\n  WindowMode:" in repaired
    assert "generic: {}" in repaired
    assert "retainedUserSetting: [77, true]" in repaired
    assert ', "5259819"]' in repaired
    assert '  - ["5259819"]' in repaired


def test_prefill_username_preserves_complete_existing_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings"
    settings.mkdir()
    module_dir = tmp_path / "module"
    complete = _write_complete_core_public_template(module_dir / "template_settings")
    existing = complete.replace(
        "  customSetting: [1, true]",
        "  customSetting: [99, false]",
    )
    yaml_path = settings / "core_public__.yaml"
    yaml_path.write_text(existing, encoding="utf-8")
    monkeypatch.setattr(profiles, "__file__", str(module_dir / "profiles.py"))
    monkeypatch.setattr(
        profiles,
        "get_profile_settings_path",
        lambda _username: settings,
    )

    profiles.prefill_username('account "quoted"')

    patched = yaml_path.read_text(encoding="utf-8")
    assert "customSetting: [99, false]" in patched
    assert 'account \\"quoted\\"' in patched
    assert not (settings / "core_public__.yaml.launcher-backup").exists()


def test_existing_profile_is_patched_to_remapped_game_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_tq, settings = _paths(tmp_path)
    (profile_tq / "start.ini").write_text(
        "[main]\nrole=client\nserver=127.0.0.1\nport=26000\nedition=standard\n",
        encoding="utf-8",
    )
    (settings / "prefs.ini").write_text(
        "clusterMode=LOCAL\nnewbie=0\nport=26000\nlanguageID=EN\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(profiles, "get_profile_settings_path", lambda _username: settings)

    profiles.configure_profile_game_endpoint(
        "fixture-account",
        profile_tq,
        host="127.0.0.1",
        port=32600,
    )

    start = (profile_tq / "start.ini").read_text(encoding="utf-8")
    prefs = (settings / "prefs.ini").read_text(encoding="utf-8")
    assert "server=127.0.0.1\n" in start
    assert "port=32600\n" in start
    assert "role=client\n" in start and "edition=standard\n" in start
    assert "port=32600\n" in prefs
    assert "clusterMode=LOCAL\n" in prefs and "languageID=EN\n" in prefs


def test_repeated_launch_replaces_stale_values_without_duplicate_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_tq, settings = _paths(tmp_path)
    (profile_tq / "start.ini").write_text(
        "[main]\r\nrole=client\r\nserver=127.0.0.1\r\nport=32600\r\n",
        encoding="utf-8",
        newline="",
    )
    (settings / "prefs.ini").write_text("newbie=0\r\nport=32600\r\n", encoding="utf-8", newline="")
    monkeypatch.setattr(profiles, "get_profile_settings_path", lambda _username: settings)

    profiles.configure_profile_game_endpoint(
        "fixture-account",
        profile_tq,
        host="127.0.0.1",
        port=42600,
    )

    start = (profile_tq / "start.ini").read_text(encoding="utf-8")
    prefs = (settings / "prefs.ini").read_text(encoding="utf-8")
    assert start.count("server=") == 1
    assert start.count("port=") == 1
    assert prefs.count("port=") == 1
    assert "port=42600" in start and "port=42600" in prefs
    assert "32600" not in start and "32600" not in prefs


def test_missing_endpoint_keys_are_added_without_damaging_other_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_tq, settings = _paths(tmp_path)
    (profile_tq / "start.ini").write_text(
        "[main]\nrole=client\n[other]\nport=9999\n",
        encoding="utf-8",
    )
    (settings / "prefs.ini").write_text("newbie=0\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "get_profile_settings_path", lambda _username: settings)

    profiles.configure_profile_game_endpoint(
        "fixture-account",
        profile_tq,
        host="127.0.0.1",
        port=32600,
    )

    start = (profile_tq / "start.ini").read_text(encoding="utf-8")
    assert "server=127.0.0.1" in start
    assert "port=32600" in start
    assert "[other]\nport=9999" in start
    assert "port=32600" in (settings / "prefs.ini").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("host", "port"),
    [("example.invalid", 32600), ("127.0.0.1\nserver=bad", 32600), ("127.0.0.1", 0), ("127.0.0.1", 70000)],
)
def test_unsafe_game_endpoint_is_rejected_before_file_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    port: int,
) -> None:
    profile_tq, settings = _paths(tmp_path)
    start_path = profile_tq / "start.ini"
    prefs_path = settings / "prefs.ini"
    start_path.write_text("[main]\nserver=127.0.0.1\nport=26000\n", encoding="utf-8")
    prefs_path.write_text("newbie=0\nport=26000\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "get_profile_settings_path", lambda _username: settings)

    with pytest.raises(ValueError):
        profiles.configure_profile_game_endpoint(
            "fixture-account",
            profile_tq,
            host=host,
            port=port,
        )

    assert start_path.read_text(encoding="utf-8").endswith("port=26000\n")
    assert prefs_path.read_text(encoding="utf-8").endswith("port=26000\n")
    assert list(tmp_path.rglob("*.tmp")) == []
