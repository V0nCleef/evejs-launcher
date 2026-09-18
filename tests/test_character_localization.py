"""Real character widgets retain official names and labels across refreshes."""
import pytest

from src.core.character_names import ship_display_name, location_display_name
from src.core.db import Account, Character
from src.core.process_tracker import ProcessTracker
from src.i18n import LANGUAGES, set_language, translate, translate_ui_phrase
from src.pages.characters_page import CharactersPage
from src.widgets.ui_translation import retranslate_widget_tree


@pytest.fixture(autouse=True)
def language_reset():
    set_language("en")
    yield
    set_language("en")


@pytest.mark.parametrize("language,ship,system", [
    ("zh_CN", "回旋者级", "佩尼尔格曼"),
    ("ja", "レトリーバー", "ペニルグマン"),
    ("ko", "리트리버", "페니르그먼"),
    ("fr", "Retriever", "Penirgman"),
    ("de", "Retriever", "Penirgman"),
    ("nl", "Retriever", "Penirgman"),
    ("ru", "Retriever", "Penirgman"),
    ("en", "Retriever", "Penirgman"),
])
def test_existing_page_switches_language_and_preserves_selection(qapp, language, ship, system):
    page = CharactersPage()
    char = Character(char_id=101, name="Settings", ship_name="Retriever", ship_type_id=17478,
                     location="Penirgman · 0.9", isk=2_500_000, skill_points=12000, security_status=0.04)
    accounts = [Account("Launch", 1, "0", False, [char])]
    tracker = ProcessTracker()
    try:
        page.refresh(accounts, [], tracker)
        page._selected_key = ("Launch", 101)
        original = page._cards[page._selected_key]
        for code in (language, "en", language):
            set_language(code)
            page.retranslate_ui()
            retranslate_widget_tree(page, code)
            page.refresh(accounts, [], tracker)
            expected_ship = ship if code == language else "Retriever"
            expected_location = (system if code == language else "Penirgman") + " · 0.9"
            assert page._cards[("Launch", 101)] is original
            assert original.ship == expected_ship
            assert original.location == expected_location
            assert original.char_name == "Settings"
            assert original.username == "Launch"
            rows = page.detail_panel._stat_rows
            assert rows["Ship"][1].text() == expected_ship
            assert rows["Location"][1].text() == expected_location
            assert rows["SP"][0].text() == translate("character.skill_points").upper()
            assert rows["ISK"][0].text() == translate("character.balance").upper()
            assert rows["Ship"][0].text() == translate_ui_phrase("SHIP")
            assert rows["Location"][0].toolTip() == translate("character.location_hint")
            assert rows["Sec Status"][1].text() == "0.04"
        # Detail reads use the same presentation path as roster loads.
        assert page.apply_character_detail("Launch", 101, {"shipName": "Retriever", "shipTypeID": 17478,
                                                           "solarSystemName": "Penirgman · 0.9"})
        assert page.detail_panel._stat_rows["Ship"][1].text() == ship
        assert page.detail_panel._stat_rows["Location"][1].text() == system + " · 0.9"
        assert char.ship_name == "Retriever" and char.location == "Penirgman · 0.9"
    finally:
        page.deleteLater()
        qapp.processEvents()


def test_custom_unknown_and_missing_translations_stay_intact():
    set_language("ja")
    assert ship_display_name("My Retriever", 17478) == "My Retriever"
    assert ship_display_name("Retriever", 999999999) == "Retriever"
    assert ship_display_name("Market", 17478) == "Market"
    assert location_display_name("Custom site · -0.3") == "Custom site · -0.3"
    assert location_display_name("System 123") == "System 123"
    assert ship_display_name("Retriever", 17478) == "レトリーバー"
    set_language("nl")
    assert ship_display_name("Retriever", 17478) == "Retriever"


def test_character_headings_have_every_supported_language():
    for language in LANGUAGES:
        set_language(language.code)
        for key in ("balance", "skill_points", "location_hint", "security_hint"):
            assert not translate("character." + key).startswith("character.")
