"""Tests for the compact operational Home layout."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QPushButton, QTextEdit

from src.pages.home_page import HomePage, extract_latest_release
from src.theme import build_qss
from src.widgets.hero_banner import HeroBanner


def test_latest_release_parser_uses_the_first_release_and_caps_highlights() -> None:
    changelog = """# Changelog

## v9.9.9 — 2026-07-28

### Added
- First highlight
- Second highlight
- Third highlight
- Fourth highlight

## v9.9.8 — 2026-07-27
- Older highlight
"""

    version, highlights = extract_latest_release(changelog, limit=3)

    assert version == "v9.9.9 — 2026-07-28"
    assert highlights == ["First highlight", "Second highlight", "Third highlight"]


def test_home_uses_a_compact_operational_lower_layout(
    qapp: QApplication,
) -> None:
    page = HomePage()

    assert HeroBanner.HEIGHT == 176
    assert page.hero.maximumHeight() == 176
    assert page.release_card.maximumHeight() == 164
    assert page.resources_card.maximumHeight() == 164
    assert page.findChildren(QTextEdit) == []
    assert page.release_card.version_label.text()
    assert page.release_card.highlights_label.text()


def test_resources_card_routes_each_console_action_through_home(
    qapp: QApplication,
) -> None:
    page = HomePage()
    requested: list[str] = []
    page.console_requested.connect(requested.append)

    page.resources_card.btn_game_console.click()
    page.resources_card.btn_market_console.click()

    assert requested == ["server", "market"]


def test_resources_buttons_use_a_compact_non_clipping_contract(
    qapp: QApplication,
) -> None:
    original_style = qapp.styleSheet()
    qapp.setStyleSheet(
        build_qss({"header": "Segoe UI", "body": "Segoe UI", "mono": "Consolas"})
    )
    page = HomePage()
    page.resize(756, 640)
    page.show()
    qapp.processEvents()

    try:
        assert page.resources_card.maximumHeight() == 164
        buttons = tuple(page.resources_card.findChildren(QPushButton))
        assert {button.text() for button in buttons} == {
            "Discord",
            "Changelog",
            "Game Console",
            "Market Console",
        }
        for button in buttons:
            assert button.property("class") == "compactGhost"
            assert button.height() == 32
            assert button.height() >= button.fontMetrics().height() + 8
            assert button.contentsRect().width() >= (
                button.fontMetrics().horizontalAdvance(button.text()) + 18
            )
    finally:
        page.close()
        page.deleteLater()
        qapp.setStyleSheet(original_style)
