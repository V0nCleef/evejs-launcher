from __future__ import annotations

import json

import pytest

import main
from src import config
from src.i18n import current_language, set_language


@pytest.fixture(autouse=True)
def _restore_english() -> None:
    yield
    set_language("en")


@pytest.mark.parametrize(
    ("system_locale", "expected_language"),
    [("ja_JP", "ja"), ("ru_RU", "ru")],
)
def test_first_start_persists_supported_system_language(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    system_locale: str,
    expected_language: str,
) -> None:
    config_file = tmp_path / "EveJS-Launcher" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_file.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)

    cfg = main._initialize_ui_language(system_locale)

    assert cfg["language"] == expected_language
    assert current_language() == expected_language
    assert json.loads(config_file.read_text(encoding="utf-8"))["language"] == expected_language


def test_first_start_persists_english_for_unsupported_locale(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "EveJS-Launcher" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_file.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)

    cfg = main._initialize_ui_language("es_ES")

    assert cfg["language"] == "en"
    assert current_language() == "en"
    assert json.loads(config_file.read_text(encoding="utf-8"))["language"] == "en"


@pytest.mark.parametrize(
    ("system_locale", "expected_language"),
    [
        ("ko_KR", "ko"),
        ("ru", "ru"),
        ("ru_RU", "ru"),
        ("es_ES", "en"),
    ],
)
def test_legacy_config_without_language_uses_system_locale_and_persists_choice(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    system_locale: str,
    expected_language: str,
) -> None:
    config_file = tmp_path / "EveJS-Launcher" / "config.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps({"evejs_root": r"C:\Legacy\EveJS"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_DIR", config_file.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)

    cfg = main._initialize_ui_language(system_locale)

    assert cfg["evejs_root"] == r"C:\Legacy\EveJS"
    assert cfg["language"] == expected_language
    assert current_language() == expected_language
    persisted = json.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["language"] == expected_language


def test_saved_choice_wins_over_system_locale(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "EveJS-Launcher" / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", config_file.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.save({**config.DEFAULT_CONFIG, "language": "nl"})

    cfg = main._initialize_ui_language("ko_KR")

    assert cfg["language"] == "nl"
    assert current_language() == "nl"
