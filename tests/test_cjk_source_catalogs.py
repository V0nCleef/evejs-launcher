"""Parity and placeholder-safety checks for the complete CJK UI catalogs."""

from __future__ import annotations

from collections import Counter
from string import Formatter

import pytest

from src.translations_ja_ko import UI_PHRASES_BY_LANGUAGE
from src.translations_source import SOURCE_PHRASES, SOURCE_PHRASE_SET
from src.translations_zh_cn import UI_PHRASES as ZH_CN_UI_PHRASES


CATALOGS = {
    "zh_CN": ZH_CN_UI_PHRASES,
    **UI_PHRASES_BY_LANGUAGE,
}


def _fields(template: str) -> Counter[str]:
    return Counter(
        field_name
        for _, field_name, _, _ in Formatter().parse(template)
        if field_name is not None
    )


def test_source_corpus_is_unique_and_deterministic() -> None:
    assert SOURCE_PHRASES == tuple(sorted(SOURCE_PHRASES))
    assert len(SOURCE_PHRASES) == len(SOURCE_PHRASE_SET)


@pytest.mark.parametrize("language", ("zh_CN", "ja", "ko"))
def test_cjk_catalog_has_exact_source_parity(language: str) -> None:
    catalog = CATALOGS[language]
    assert set(catalog) == SOURCE_PHRASE_SET
    assert all(isinstance(value, str) and value for value in catalog.values())


@pytest.mark.parametrize("language", ("zh_CN", "ja", "ko"))
def test_cjk_catalog_preserves_named_placeholders(language: str) -> None:
    catalog = CATALOGS[language]
    mismatches = {
        source: (_fields(source), _fields(catalog[source]))
        for source in SOURCE_PHRASES
        if _fields(source) != _fields(catalog[source])
    }
    assert mismatches == {}


@pytest.mark.parametrize(
    ("language", "expected_start_stack", "expected_mods"),
    (
        ("zh_CN", "启动服务栈", "模组"),
        ("ja", "サービス一式を起動", "モッド"),
        ("ko", "서비스 스택 시작", "모드"),
    ),
)
def test_originally_reported_controls_are_translated(
    language: str,
    expected_start_stack: str,
    expected_mods: str,
) -> None:
    catalog = CATALOGS[language]
    assert catalog["Start Stack"] == expected_start_stack
    assert catalog["MODS"] == expected_mods


@pytest.mark.parametrize(
    ("language", "allowed_unchanged"),
    (
        ("zh_CN", {"Docker Compose", "✗ {message}"}),
        ("ja", {"Docker Compose", "✗ {message}"}),
        (
            "ko",
            {
                "({total})",
                "({visible} / {group_total})",
                "Docker Compose",
                "✗ {message}",
            },
        ),
    ),
)
def test_only_technical_or_placeholder_only_sources_remain_unchanged(
    language: str,
    allowed_unchanged: set[str],
) -> None:
    catalog = CATALOGS[language]
    unchanged = {
        source for source in SOURCE_PHRASES if catalog[source] == source
    }
    assert unchanged == allowed_unchanged
