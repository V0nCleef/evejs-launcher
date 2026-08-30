"""Contract tests for the Japanese modal and stable-diagnostic slice."""
from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import re
from string import Formatter

from src.translations_ja_additional_modals import (
    ADDITIONAL_JA_MODAL_PHRASES,
    _ADDITIONAL_JA_MODAL_ITEMS,
)


def _canonical_modal_slice() -> set[str]:
    source_path = Path(__file__).parents[1] / "src" / "translations_source.py"
    source_text = source_path.read_text(encoding="utf-8")
    marker_line = next(
        line_number
        for line_number, line in enumerate(source_text.splitlines(), start=1)
        if "# App-owned modal titles." in line
    )
    end_marker_line = next(
        line_number
        for line_number, line in enumerate(source_text.splitlines(), start=1)
        if "# Final audited launcher-owned localization surface." in line
    )
    diagnostics_marker_line = next(
        line_number
        for line_number, line in enumerate(source_text.splitlines(), start=1)
        if "# Stable launcher diagnostics surfaced" in line
    )
    tree = ast.parse(source_text)
    corpus = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_ADDITIONAL_SOURCE_PHRASES"
    )
    assert isinstance(corpus, ast.Tuple)
    return {
        item.value
        for item in corpus.elts
        if isinstance(item, ast.Constant)
        and isinstance(item.value, str)
        and (
            marker_line < item.lineno < end_marker_line
            or item.lineno > diagnostics_marker_line
        )
    }


def _named_fields(text: str) -> Counter[str]:
    return Counter(
        field_name
        for _literal, field_name, _format_spec, _conversion in Formatter().parse(text)
        if field_name is not None
    )


def test_japanese_modal_catalog_matches_only_the_three_canonical_sections() -> None:
    expected = _canonical_modal_slice()
    assert len(expected) == 179
    assert set(ADDITIONAL_JA_MODAL_PHRASES) == expected


def test_japanese_modal_catalog_has_no_duplicate_source_keys() -> None:
    sources = [source for source, _translated in _ADDITIONAL_JA_MODAL_ITEMS]
    assert len(sources) == len(set(sources))
    assert len(sources) == len(ADDITIONAL_JA_MODAL_PHRASES)


def test_japanese_modal_catalog_preserves_named_placeholders() -> None:
    for source, translated in ADDITIONAL_JA_MODAL_PHRASES.items():
        assert translated
        assert _named_fields(translated) == _named_fields(source), source


def test_only_the_proper_technical_name_remains_unchanged() -> None:
    unchanged = {
        source
        for source, translated in ADDITIONAL_JA_MODAL_PHRASES.items()
        if source == translated
    }
    assert unchanged == {"Docker Compose"}
    assert all(
        source == "Docker Compose"
        or re.search(r"[ぁ-んァ-ヶ一-龯]", translated)
        for source, translated in ADDITIONAL_JA_MODAL_PHRASES.items()
    )
