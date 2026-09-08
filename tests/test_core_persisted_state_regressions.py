"""Regression coverage for malformed settings and retained client ownership."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src import config
from src.core.process_tracker import ProcessTracker


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    return path


def test_bad_backend_policy_and_interval_preserve_other_preferences(
    config_file: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    stored = {
        "runtime_backend": [],
        "docker_control_policy": {},
        "update_check_interval_hours": "six",
        "evejs_root": "C:/Games/My EveJS",
        "client_path": "D:/Games/Shared Client/tq",
        "update_auto_check": False,
        "audio_music_volume": 73,
        "hidden_characters": ["Hidden Pilot"],
        "future_mod_preferences": {"profile-a": {"enabled": False, "value": 12}},
    }
    original = json.dumps(stored).encode("utf-8")
    config_file.write_bytes(original)

    with caplog.at_level(logging.WARNING, logger=config.__name__):
        loaded = config.load()

    assert loaded["runtime_backend"] == "native"
    assert loaded["docker_control_policy"] == "connect_only"
    assert loaded["update_check_interval_hours"] == 6
    for key in set(stored) - {
        "runtime_backend", "docker_control_policy", "update_check_interval_hours",
    }:
        assert loaded[key] == stored[key]
    assert config_file.read_bytes() == original
    assert not list(config_file.parent.glob("*.broken"))
    assert "runtime_backend" in caplog.text
    assert "update_check_interval_hours" in caplog.text
    assert "My EveJS" not in caplog.text
    assert "Hidden Pilot" not in caplog.text

    config.save(loaded)
    assert config.load() == loaded


def test_supported_field_types_are_safe_for_ui_consumers(config_file: Path) -> None:
    config_file.write_text(json.dumps({
        "evejs_root": [], "client_path": {}, "proxy_url": None,
        "game_port": "bad", "server_mode": [], "theme": {},
        "auto_start_server": "false", "auto_start_market": [True],
        "hide_test_characters": None, "animations_enabled": "false",
        "update_auto_check": {}, "stagger_delay_sec": "many",
        "hero_rotation_interval_sec": {}, "update_last_checked": [],
        "update_skip_version": 123,
        "hidden_characters": ["Pilot A", {}, 42, "Pilot B"],
        "never_hide_characters": "Pilot C",
        "update_skip_versions": ["v1.0.51", [], False, "v1.0.52"],
    }), encoding="utf-8")

    loaded = config.load()

    for key in (
        "evejs_root", "client_path", "proxy_url", "game_port", "server_mode",
        "theme", "auto_start_server", "auto_start_market", "hide_test_characters",
        "animations_enabled", "update_auto_check", "stagger_delay_sec",
        "hero_rotation_interval_sec", "update_last_checked", "update_skip_version",
    ):
        assert loaded[key] == config.DEFAULT_CONFIG[key], key
    assert set(loaded["hidden_characters"]) == {"Pilot A", "Pilot B"}
    assert loaded["never_hide_characters"] == []
    assert loaded["update_skip_versions"] == ["v1.0.51", "v1.0.52"]


@pytest.mark.parametrize(("key", "value"), [
    ("game_port", 0), ("game_port", 65536), ("game_port", True),
    ("stagger_delay_sec", -1), ("stagger_delay_sec", float("nan")),
    ("update_check_interval_hours", -1),
    ("update_check_interval_hours", float("inf")),
    ("update_check_interval_hours", 10 ** 400),
])
def test_invalid_numeric_settings_fall_back_per_field(
    config_file: Path, key: str, value: object,
) -> None:
    config_file.write_text(json.dumps({key: value}), encoding="utf-8")
    assert config.load()[key] == config.DEFAULT_CONFIG[key]


def test_valid_numeric_boundaries_and_legacy_zero_interval_survive(config_file: Path) -> None:
    stored = {
        "game_port": 65535, "stagger_delay_sec": 0,
        "update_check_interval_hours": 0, "hero_rotation_interval_sec": 17,
    }
    config_file.write_text(json.dumps(stored), encoding="utf-8")
    loaded = config.load()
    assert {key: loaded[key] for key in stored} == stored


def test_legacy_integer_strings_are_normalized_without_losing_the_value(config_file: Path) -> None:
    config_file.write_text(json.dumps({
        "game_port": "32600", "stagger_delay_sec": "12",
        "update_check_interval_hours": "24",
    }), encoding="utf-8")
    loaded = config.load()
    assert loaded["game_port"] == 32600
    assert loaded["stagger_delay_sec"] == 12
    assert loaded["update_check_interval_hours"] == 24


def test_huge_audio_numbers_do_not_overflow_configuration_load(config_file: Path) -> None:
    config_file.write_text(json.dumps({
        "audio_music_volume": 10 ** 400,
        "audio_voice_rate": -(10 ** 400),
    }), encoding="utf-8")
    loaded = config.load()
    assert loaded["audio_music_volume"] == 100
    assert loaded["audio_voice_rate"] == -1.0


class ControlledProcess:
    def __init__(self, pid: int, *, refuses: bool = False) -> None:
        self.pid = pid
        self.refuses = refuses
        self.alive = True
        self.terminate_calls = 0

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.refuses:
            raise PermissionError("termination refused")
        # A successful termination request is not evidence that it has exited.


def test_kill_all_retains_failed_and_pending_processes_until_observed_exit() -> None:
    tracker = ProcessTracker()
    pending = ControlledProcess(4101)
    refused = ControlledProcess(4102, refuses=True)
    tracker.add("account-a", "Pilot A", pending)
    tracker.add("account-b", "Pilot B", refused)

    assert tracker.kill_all() == 1
    assert tracker.running_count == 2
    assert tracker.is_account_running("account-a")
    assert tracker.is_account_running("account-b")
    assert tracker.prune_dead() == 0

    pending.alive = False
    assert tracker.prune_dead() == 1
    assert tracker.running_count == 1
    refused.refuses = False
    assert tracker.kill_all() == 1
    assert refused.terminate_calls == 2
    refused.alive = False
    assert tracker.prune_dead() == 1
    assert tracker.running_count == 0


def test_kill_all_does_not_consume_the_exit_event_before_pruning() -> None:
    tracker = ProcessTracker()
    exited = ControlledProcess(4103)
    tracker.add("account-c", "Pilot C", exited)
    exited.alive = False

    assert tracker.kill_all() == 0
    assert exited.terminate_calls == 0
    assert tracker.prune_dead() == 1
