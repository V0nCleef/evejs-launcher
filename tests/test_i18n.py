"""Contracts for the launcher's dependency-free localization catalog."""
from __future__ import annotations

import pytest

from src import i18n


@pytest.fixture(autouse=True)
def reset_language() -> None:
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def test_supported_languages_have_stable_codes_flags_and_native_names() -> None:
    assert [(item.code, item.flag, item.display_name) for item in i18n.LANGUAGES] == [
        ("en", "🇬🇧", "English"),
        ("zh_CN", "🇨🇳", "简体中文"),
        ("ja", "🇯🇵", "日本語"),
        ("ko", "🇰🇷", "한국어"),
        ("fr", "🇫🇷", "Français"),
        ("de", "🇩🇪", "Deutsch"),
        ("nl", "🇳🇱", "Nederlands"),
        ("ru", "🇷🇺", "Русский"),
    ]
    assert all(item.native_name == item.display_name for item in i18n.LANGUAGES)
    assert all(item.label.startswith(item.flag) for item in i18n.LANGUAGES)


def test_every_catalog_covers_the_complete_english_shell_contract() -> None:
    expected_keys = set(i18n._ENGLISH)

    assert expected_keys
    assert set(i18n._TRANSLATIONS) == {item.code for item in i18n.LANGUAGES}
    for code, catalog in i18n._TRANSLATIONS.items():
        assert set(catalog) == expected_keys, code


def test_every_non_english_catalog_covers_every_reviewed_ui_phrase() -> None:
    assert set(i18n.UI_PHRASES_BY_LANGUAGE) == {
        item.code for item in i18n.LANGUAGES if item.code != "en"
    }
    assert all(
        not missing
        for missing in i18n.missing_ui_phrase_translations().values()
    )


@pytest.mark.parametrize(
    ("language", "start_stack", "mods"),
    [
        ("zh_CN", "启动服务栈", "模组"),
        ("ja", "サービス一式を起動", "モッド"),
        ("ko", "서비스 스택 시작", "모드"),
        ("fr", "Démarrer tous les services", "Modifications"),
        ("de", "Alle Dienste starten", "Modifikationen"),
        ("nl", "Alle diensten starten", "Aanpassingen"),
        ("ru", "Запустить стек", "Моды"),
    ],
)
def test_primary_static_controls_translate_in_every_supported_language(
    language: str,
    start_stack: str,
    mods: str,
) -> None:
    assert i18n.translate_ui_phrase("Start Stack", language) == start_stack
    assert i18n.translate_ui_phrase("Mods", language) == mods


@pytest.mark.parametrize(
    ("language", "one", "many"),
    [
        ("zh_CN", "1 个客户端运行中", "2 个客户端运行中"),
        ("ja", "クライアント 1 件が実行中", "クライアント 2 件が実行中"),
        ("ko", "클라이언트 1개 실행 중", "클라이언트 2개 실행 중"),
        ("fr", "1 client en cours", "2 clients en cours"),
        ("de", "1 Client läuft", "2 Clients laufen"),
        ("nl", "1 client actief", "2 clients actief"),
    ],
)
def test_dynamic_client_counts_translate_in_every_supported_language(
    language: str,
    one: str,
    many: str,
) -> None:
    assert i18n.translate_ui_phrase("1 client running", language) == one
    assert i18n.translate_ui_phrase("2 clients running", language) == many


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("zh", "zh_CN"),
        ("zh-Hans", "zh_CN"),
        ("zh_cn", "zh_CN"),
        ("ja", "ja"),
        ("ru-RU", "ru"),
        ("unknown", "en"),
        (None, "en"),
    ],
)
def test_language_codes_are_normalized_or_fail_closed_to_english(
    source: object,
    expected: str,
) -> None:
    assert i18n.normalize_language(source) == expected


@pytest.mark.parametrize(
    ("system_locale", "expected"),
    [
        ("zh_CN", "zh_CN"),
        ("zh-TW", "zh_CN"),
        ("ja_JP", "ja"),
        ("ko-KR", "ko"),
        ("fr_FR", "fr"),
        ("de_DE", "de"),
        ("nl_NL", "nl"),
        ("ru", "ru"),
        ("ru_RU", "ru"),
        ("es_ES", "en"),
        ("", "en"),
    ],
)
def test_system_locale_chooses_supported_language_or_english(
    system_locale: str,
    expected: str,
) -> None:
    assert i18n.language_for_system_locale(system_locale) == expected


def test_first_start_detects_system_language_but_keeps_saved_preference() -> None:
    assert i18n.language_for_startup(
        has_saved_config=False,
        saved_language="en",
        system_locale="ja_JP",
    ) == "ja"
    assert i18n.language_for_startup(
        has_saved_config=False,
        saved_language="en",
        system_locale="es_ES",
    ) == "en"
    assert i18n.language_for_startup(
        has_saved_config=True,
        saved_language="ru",
        system_locale="ja_JP",
    ) == "ru"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1 client running", "1 запущенный клиент"),
        ("2 clients running", "2 запущенных клиента"),
        ("5 clients running", "5 запущенных клиентов"),
        ("21 characters", "21 персонаж"),
        ("22 characters", "22 персонажа"),
        ("25 characters", "25 персонажей"),
    ],
)
def test_russian_dynamic_counts_use_correct_plural_forms(
    source: str,
    expected: str,
) -> None:
    assert i18n.translate_ui_phrase(source, "ru") == expected


def test_service_actions_translate_names_and_keep_structured_state() -> None:
    i18n.set_language("zh_CN")

    assert i18n.translate_service_action("Server") == "游戏服务"
    assert i18n.translate_service_action("▶ Start Server") == "▶ 启动游戏服务"
    assert i18n.translate_service_action("Server: Starting…") == (
        "游戏服务：正在启动…"
    )
    assert i18n.translate_service_action("■ Stop Market") == "■ 停止市场服务"
    assert i18n.translate_service_action("unstructured status") == (
        "unstructured status"
    )


def test_english_service_actions_preserve_exact_source_punctuation() -> None:
    i18n.set_language("en")

    assert i18n.translate_service_action("Server: Starting…") == (
        "Server: Starting…"
    )
    assert i18n.translate_service_action("Server: Starting...") == (
        "Server: Starting..."
    )


def test_service_tooltips_translate_without_guessing_unknown_messages() -> None:
    i18n.set_language("zh_CN")

    assert i18n.translate_service_tooltip(
        "Connect-only Docker mode cannot change containers."
    ) == "仅连接 Docker 模式无法更改容器。"
    assert i18n.translate_service_tooltip("Server is starting") == (
        "游戏服务正在启动"
    )
    assert i18n.translate_service_tooltip("Stop Server first") == (
        "请先停止游戏服务"
    )
    assert i18n.translate_service_tooltip("user supplied detail") == (
        "user supplied detail"
    )


def test_arbitrary_diagnostics_are_never_reverse_matched_as_ui_templates() -> None:
    diagnostic = "Could not create a backup of every affected table."

    assert i18n.translate_ui_phrase(diagnostic, "zh_CN") == diagnostic


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
@pytest.mark.parametrize(
    ("diagnostic", "preserved"),
    [
        (r"Path does not exist: C:\用户\EveJS", r"C:\用户\EveJS"),
        (r"Path is not a directory: C:\用户\EveJS", r"C:\用户\EveJS"),
        ("Missing SSL cert (server may not be configured): cert.pem", "cert.pem"),
        ("Missing Client config script: EvEJSConfig.bat", "EvEJSConfig.bat"),
        ("Missing server start script (StartServer*.bat) or server/index.js", "StartServer*.bat"),
        ("Missing game store: expected _local/gameStore/gamestore.sqlite", "gamestore.sqlite"),
        ("Docker project root must be an absolute path.", ""),
        (r"Docker project root does not exist: C:\用户\Docker", r"C:\用户\Docker"),
        (r"Docker project root is not a directory: C:\用户\Docker", r"C:\用户\Docker"),
        ("Docker Compose file must be an absolute path.", ""),
        (r"Docker Compose file does not exist: C:\用户\compose.yaml", r"C:\用户\compose.yaml"),
        (r"Docker Compose path is not a file: C:\用户\compose.yaml", r"C:\用户\compose.yaml"),
    ],
)
def test_discovery_diagnostics_translate_without_touching_paths(
    language: str,
    diagnostic: str,
    preserved: str,
) -> None:
    translated = i18n.translate_discovery_diagnostic(diagnostic, language)

    assert translated != diagnostic
    assert preserved in translated


def test_unknown_discovery_diagnostic_remains_verbatim() -> None:
    diagnostic = r"Docker daemon said: C:\Users\Pilot\raw backend detail"

    assert i18n.translate_discovery_diagnostic(diagnostic, "zh_CN") == diagnostic


def test_explicit_template_translation_prefers_specific_optional_framing() -> None:
    source = (
        "Delete Character?\n\nPilot 42\n\n"
        "EveJS will run its native character cleanup. The launcher will keep "
        "a recoverable backup of every affected table and portrait. Account "
        "profile/settings folders are preserved."
    )

    translated = i18n.translate_ui_phrase(
        source,
        "zh_CN",
        allow_templates=True,
    )

    assert translated.startswith("删除Character？\n\nPilot 42")
    assert "{service_note}" not in translated
    assert "affected table" not in translated


@pytest.mark.parametrize(
    "language",
    ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"],
)
def test_deletion_confirmation_translates_launcher_grammar_without_mixed_english(
    language: str,
) -> None:
    i18n.set_language(language)
    account_text = i18n.format_character_deletion_confirmation(
        "account",
        username="Pilot Settings",
        character_name="Character Settings",
        character_names="Alpha Settings, Beta Settings",
        character_count=2,
        services_owned=True,
    )
    character_text = i18n.format_character_deletion_confirmation(
        "character",
        username="Pilot Settings",
        character_name="Character Settings",
        character_names="Alpha Settings, Beta Settings",
        character_count=2,
        services_owned=True,
    )

    # User-controlled values survive exactly; launcher-owned English grammar does not.
    assert "Pilot Settings" in account_text
    assert "Alpha Settings, Beta Settings" in account_text
    assert "Pilot Settings" in character_text
    assert "Character Settings" in character_text
    mixed_english_fragments = (
        "Delete ",
        " character(s)",
        "Characters:",
        " will be retained.",
        "native character cleanup",
        "profile/settings folders are preserved",
        "Launcher-owned EveJS services",
    )
    for fragment in mixed_english_fragments:
        assert fragment not in account_text, (language, fragment, account_text)
        assert fragment not in character_text, (language, fragment, character_text)


def test_english_deletion_confirmation_preserves_existing_copy_and_optional_note() -> None:
    account_text = i18n.format_character_deletion_confirmation(
        "account",
        username="pilot",
        character_name="unused",
        character_names="Alpha, Beta",
        character_count=2,
        services_owned=False,
    )
    assert account_text.startswith("Delete account 'pilot' and 2 character(s)?")
    assert "Characters: Alpha, Beta" in account_text
    assert "Launcher-owned EveJS services" not in account_text

    character_text = i18n.format_character_deletion_confirmation(
        "character",
        username="pilot",
        character_name="Alpha",
        character_names="unused",
        character_count=1,
        services_owned=True,
    )
    assert character_text.startswith("Delete character 'Alpha'?")
    assert "Account 'pilot' will be retained." in character_text
    assert character_text.endswith(
        "Launcher-owned EveJS services will be stopped and restored."
    )

    with pytest.raises(ValueError, match="Unsupported character deletion scope"):
        i18n.format_character_deletion_confirmation(
            "server",
            username="pilot",
            character_name="Alpha",
            character_names="Alpha",
            character_count=1,
            services_owned=False,
        )
