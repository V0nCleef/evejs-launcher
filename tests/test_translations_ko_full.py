"""Completeness and placeholder-safety checks for the full Korean catalog."""
from __future__ import annotations

from collections import Counter
from string import Formatter

from src.translations_ko_full import UI_PHRASES
from src.translations_source import SOURCE_PHRASES


def _named_fields(text: str) -> Counter[str]:
    return Counter(
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(text)
        if field_name is not None
    )


def test_korean_catalog_covers_the_complete_source_corpus() -> None:
    assert set(UI_PHRASES) == set(SOURCE_PHRASES)
    assert len(UI_PHRASES) == len(SOURCE_PHRASES)
    assert all(UI_PHRASES.values())


def test_korean_catalog_preserves_every_named_placeholder() -> None:
    for source, translated in UI_PHRASES.items():
        assert _named_fields(translated) == _named_fields(source), source


def test_only_technical_or_placeholder_only_values_are_unchanged() -> None:
    unchanged = {
        source
        for source, translated in UI_PHRASES.items()
        if source == translated
    }
    assert unchanged == {
        "({total})",
        "({visible} / {group_total})",
        "Docker Compose",
        "✗ {message}",
    }


def test_representative_dynamic_korean_copy_is_natural_and_safe() -> None:
    assert UI_PHRASES["Start the service stack"] == "서비스 스택 시작"
    assert UI_PHRASES["Launch {character}"] == "{character} 실행"
    assert UI_PHRASES["playing {track}"] == "{track} 재생 중"
    assert UI_PHRASES["{count} EVE client(s) running"] == (
        "EVE 클라이언트 {count}개 실행 중"
    )
