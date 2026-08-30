from collections import Counter
import re
from string import Formatter

import pytest

from src.translations_eu import UI_PHRASES_BY_LANGUAGE
from src.translations_source import SOURCE_PHRASES


_ALLOWED_IDENTICAL = {
    "fr": {
        "({total})",
        "({visible} / {group_total})",
        "{group_name} ({member_count})",
        "CLIENTS",
        "Clients",
        "ERR",
        "INIT",
        "Mod {mod_name}",
        "Version {version}",
        "Configuration",
        "CONFIGURATION",
        "Console — {log_filename}",
        "✗ {message}",
        "Docker Compose",
    },
    "de": {
        "({total})",
        "({visible} / {group_total})",
        "{group_name} ({member_count})",
        "CLIENTS",
        "Clients",
        "INIT",
        "Offline",
        "ONLINE",
        "Online",
        "MODS",
        "Mod {mod_name}",
        "Version {version}",
        "Status",
        "Gold",
        "✗ {message}",
        "Docker Compose",
    },
    "nl": {
        "({total})",
        "({visible} / {group_total})",
        "{group_name} ({member_count})",
        "CLIENTS",
        "Clients",
        "INIT",
        "Offline",
        "ONLINE",
        "Online",
        "STOP",
        "MODS",
        "Mod {mod_name}",
        "Status",
        "Console — {log_filename}",
        "✗ {message}",
        "Docker Compose",
    },
}


def _named_fields(text: str) -> Counter[str]:
    return Counter(
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(text)
        if field_name is not None
    )


def test_european_catalogs_cover_the_reviewed_ui_phrase_corpus() -> None:
    expected_keys = set(SOURCE_PHRASES)
    # Baseline includes the complete Mods-folder onboarding surface. Future
    # supported languages may add reviewed selector copy without weakening
    # this no-regression floor.
    assert len(expected_keys) >= 1107
    assert set(UI_PHRASES_BY_LANGUAGE) == {"fr", "de", "nl"}
    for language, phrases in UI_PHRASES_BY_LANGUAGE.items():
        assert set(phrases) == expected_keys
        assert all(translated.strip() for translated in phrases.values())
        assert {
            source for source, translated in phrases.items() if source == translated
        } == _ALLOWED_IDENTICAL[language]


@pytest.mark.parametrize("language", ["fr", "de", "nl"])
def test_european_catalogs_preserve_every_named_placeholder(language: str) -> None:
    phrases = UI_PHRASES_BY_LANGUAGE[language]
    for source in SOURCE_PHRASES:
        assert _named_fields(phrases[source]) == _named_fields(source), source


def test_european_catalogs_translate_representative_launcher_controls() -> None:
    assert UI_PHRASES_BY_LANGUAGE["fr"]["Start Stack"] == "Démarrer tous les services"
    assert UI_PHRASES_BY_LANGUAGE["de"]["Mods"] == "Modifikationen"
    assert UI_PHRASES_BY_LANGUAGE["nl"]["Kill All Clients"] == "Alle clients afsluiten"
    assert UI_PHRASES_BY_LANGUAGE["fr"]["Download && Install"] == "Télécharger et installer"
    assert UI_PHRASES_BY_LANGUAGE["de"]["Manage Character Groups"] == "Charaktergruppen verwalten"
    assert UI_PHRASES_BY_LANGUAGE["nl"]["Character groups"] == "Personagegroepen"


def test_european_catalogs_translate_the_audited_surface_delta() -> None:
    assert (
        UI_PHRASES_BY_LANGUAGE["fr"]["STEP 02 / 04   RUNTIME"]
        == "ÉTAPE 02 / 04   ENVIRONNEMENT"
    )
    assert (
        UI_PHRASES_BY_LANGUAGE["de"]["Waiting for the launcher to close…"]
        == "Warten auf das Schließen des Launchers…"
    )
    assert (
        UI_PHRASES_BY_LANGUAGE["nl"]["LYRA online. Shipboard systems ready."]
        == "LYRA online. Boordsystemen gereed."
    )
    assert (
        UI_PHRASES_BY_LANGUAGE["nl"]["Executables (*.exe);;All Files (*)"]
        == "Uitvoerbare bestanden (*.exe);;Alle bestanden (*)"
    )


def test_european_catalogs_preserve_product_and_platform_tokens() -> None:
    protected_tokens = ("EveJS", "EVE", "Docker", "LYRA", "RPC", "Node.js")
    for phrases in UI_PHRASES_BY_LANGUAGE.values():
        for source, translated in phrases.items():
            for token in protected_tokens:
                token_pattern = rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])"
                if re.search(token_pattern, source):
                    assert token in translated, (source, token)
