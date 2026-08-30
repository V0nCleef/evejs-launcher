"""Completeness and safety checks for the Japanese and Korean catalogs."""
from __future__ import annotations

from collections import Counter
from string import Formatter

from src.audio.events import VOICE_LINE_TEXT
from src.translations_ja_ko import JA_UI_PHRASES, KO_UI_PHRASES
from src.translations_source import SOURCE_PHRASES
from src.translations_zh_cn import UI_PHRASES as ZH_CN_UI_PHRASES


def _named_fields(text: str) -> Counter[str]:
    return Counter(
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(text)
        if field_name is not None
    )


def test_japanese_and_korean_cover_the_reviewed_widget_phrase_surface() -> None:
    expected = set(ZH_CN_UI_PHRASES)

    assert set(JA_UI_PHRASES) == expected
    assert set(KO_UI_PHRASES) == expected
    assert all(JA_UI_PHRASES.values())
    assert all(KO_UI_PHRASES.values())


def test_reported_japanese_gaps_have_real_translations() -> None:
    assert JA_UI_PHRASES["Start Stack"] == "サービス一式を起動"
    assert JA_UI_PHRASES["Mods"] == "モッド"
    assert JA_UI_PHRASES["MOD MANIFEST"] == "モッド一覧"


def test_catalogs_preserve_product_and_technical_tokens() -> None:
    for catalog in (JA_UI_PHRASES, KO_UI_PHRASES):
        assert "EveJS" in catalog["EveJS Root:"]
        assert "EVE" in catalog["EVE Client Path:"]
        assert "Compose" in catalog["Compose File (optional):"]
        assert "GM" in catalog["GM account:"]
        assert "LYRA" in catalog["PREVIEW LYRA"]


def test_all_cjk_catalogs_preserve_every_named_placeholder() -> None:
    for catalog in (ZH_CN_UI_PHRASES, JA_UI_PHRASES, KO_UI_PHRASES):
        assert set(catalog) == set(SOURCE_PHRASES)
        for source in SOURCE_PHRASES:
            assert _named_fields(catalog[source]) == _named_fields(source), source


def test_audited_cjk_surfaces_have_reviewed_non_english_copy() -> None:
    audited_sources = (
        "STEP 02 / 04   RUNTIME",
        "Runtime: Native — directly on Windows\nEveJS Root: {evejs_root}\nCLIENT Path: {client_path}\n\nClick Next to save these settings.",
        "Downloading update…",
        "The downloaded update is incomplete. Your existing installation is unchanged.",
        "MIX & ACCESS",
        "Compose files (*.yaml *.yml);;YAML files (*.yaml *.yml);;All Files (*)",
        "LYRA online. Shipboard systems ready.",
        "Launch sequence complete, with errors.",
        "Created account '{username}' and character '{character_name}'.",
    )
    for catalog in (ZH_CN_UI_PHRASES, JA_UI_PHRASES, KO_UI_PHRASES):
        for source in audited_sources:
            assert catalog[source]
            assert catalog[source] != source


def test_all_fixed_voice_captions_have_reviewed_cjk_copy() -> None:
    sources = tuple(VOICE_LINE_TEXT.values())
    assert len(sources) == 25
    assert len(set(sources)) == len(sources)
    for catalog in (ZH_CN_UI_PHRASES, JA_UI_PHRASES, KO_UI_PHRASES):
        for source in sources:
            assert source in catalog
            assert catalog[source]
            assert catalog[source] != source
