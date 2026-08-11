"""Tests for launcher configuration migration and persistence."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src import config
from src.core.server_selection import ASK_EVERY_TIME


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def test_fresh_config_uses_ask_preference(isolated_config: Path) -> None:
    loaded = config.load()

    assert loaded["server_start_preference"] == ASK_EVERY_TIME
    assert loaded["auto_login_enabled"] is False
    assert "server_start_script" not in loaded
    assert "server_start_scripts" not in loaded
    assert "server_script_prompted" not in loaded


def test_each_load_has_independent_mutable_defaults(isolated_config: Path) -> None:
    first = config.load()
    second = config.load()

    first["hidden_characters"].append("temporary")
    first["update_skip_versions"].append("9.9.9")

    assert second["hidden_characters"] == []
    assert second["update_skip_versions"] == []
    assert config.DEFAULT_CONFIG["hidden_characters"] == []


def test_legacy_absolute_script_migrates_to_relative_filename(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {
                "server_start_script": (
                    r"C:\Games\EveJS\StartServerWithMods.bat"
                ),
                "server_script_prompted": True,
                "server_start_scripts": [r"C:\Elsewhere\StartServer.bat"],
                "server_mode": "vanilla",
            }
        ),
        encoding="utf-8",
    )

    loaded = config.load()

    assert loaded["server_start_preference"] == "StartServerWithMods.bat"
    assert loaded["server_mode"] == "vanilla"
    assert "server_start_script" not in loaded
    assert "server_start_scripts" not in loaded
    assert "server_script_prompted" not in loaded


def test_stale_legacy_absolute_script_still_migrates_by_filename(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {"server_start_script": r"Z:\Missing\OldRoot\StartServer.bat"}
        ),
        encoding="utf-8",
    )

    assert config.load()["server_start_preference"] == "StartServer.bat"


def test_valid_primary_preference_wins_over_legacy_script(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {
                "server_start_preference": "StartServer.bat",
                "server_start_script": r"G:\Old\StartServerWithMods.bat",
            }
        ),
        encoding="utf-8",
    )

    assert config.load()["server_start_preference"] == "StartServer.bat"


def test_legacy_native_config_without_backend_preserves_native_fields_and_migration(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {
                "evejs_root": "C:/Fixture/EveJS",
                "client_path": "C:/Fixture/EVE.exe",
                "proxy_url": "http://127.0.0.1:32602",
                "game_port": 32600,
                "auto_start_server": True,
                "auto_start_market": True,
                "server_mode": "vanilla",
                "server_start_script": "C:/Fixture/EveJS/StartServer.bat",
            }
        ),
        encoding="utf-8",
    )

    loaded = config.load()

    assert loaded["runtime_backend"] == "native"
    assert loaded["evejs_root"] == "C:/Fixture/EveJS"
    assert loaded["client_path"] == "C:/Fixture/EVE.exe"
    assert loaded["proxy_url"] == "http://127.0.0.1:32602"
    assert loaded["game_port"] == 32600
    assert loaded["auto_start_server"] is True
    assert loaded["auto_start_market"] is True
    assert loaded["server_mode"] == "vanilla"
    assert loaded["server_start_preference"] == "StartServer.bat"
    assert "server_start_script" not in loaded


def test_invalid_absolute_primary_preference_falls_back_to_ask(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps(
            {"server_start_preference": r"G:\Moved\StartServerWithMods.bat"}
        ),
        encoding="utf-8",
    )

    assert config.load()["server_start_preference"] == ASK_EVERY_TIME


def test_auto_login_setting_accepts_only_a_real_boolean(
    isolated_config: Path,
) -> None:
    isolated_config.write_text(
        json.dumps({"auto_login_enabled": "yes"}),
        encoding="utf-8",
    )
    assert config.load()["auto_login_enabled"] is False

    isolated_config.write_text(
        json.dumps({"auto_login_enabled": True}),
        encoding="utf-8",
    )
    assert config.load()["auto_login_enabled"] is True


def test_malformed_json_is_backed_up_and_defaults_are_loaded(
    isolated_config: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    broken_contents = '{"evejs_root": '
    isolated_config.write_text(broken_contents, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        loaded = config.load()

    backups = list(isolated_config.parent.glob("config.json.*.broken"))
    assert loaded["server_start_preference"] == ASK_EVERY_TIME
    assert not isolated_config.exists()
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == broken_contents
    assert str(backups[0]) in caplog.text


def test_save_replaces_from_temporary_file_in_same_directory(
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_replace = config.os.replace
    observed: dict[str, Path] = {}

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        observed["source"] = Path(source)
        observed["destination"] = Path(destination)
        assert observed["source"].parent == isolated_config.parent
        assert json.loads(observed["source"].read_text(encoding="utf-8"))[
            "server_start_preference"
        ] == "StartServer.bat"
        real_replace(source, destination)

    monkeypatch.setattr(config.os, "replace", recording_replace)

    cfg = config.load()
    cfg["server_start_preference"] = "StartServer.bat"
    config.save(cfg)

    assert observed["destination"] == isolated_config
    assert json.loads(isolated_config.read_text(encoding="utf-8")) == cfg
    assert not observed["source"].exists()
