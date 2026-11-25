from src.core.character_visibility import change_visibility, migrate_legacy_visibility, visibility_ids
from src.core.dashboard import visible_character_rows
from src.core.db import Account, Character


def test_legacy_visibility_pins_both_duplicate_names_and_keeps_unresolved_detached():
    accounts = [
        Account("first", 1, "0", False, [Character(101, "Sabrine")]),
        Account("second", 2, "0", False, [Character(102, "Sabrine")]),
        Account("third", 3, "0", False, [Character(103, "Laurent")]),
    ]
    settings = {"hidden_characters": ["Sabrine", "Missing"], "never_hide_characters": ["Laurent"]}
    assert migrate_legacy_visibility(settings, accounts)
    assert settings["hidden_characters"] == ["Missing"]
    assert visibility_ids(settings["hidden_character_ids"]) == {(1, 101), (2, 102)}
    assert visibility_ids(settings["never_hide_character_ids"]) == {(3, 103)}
    accounts[0].characters[0].name = "Renamed"
    accounts[2].characters[0].name = "Missing"
    assert not migrate_legacy_visibility(settings, accounts)
    change_visibility(settings, (1, 101), hidden=False)
    visible = visible_character_rows(accounts, visibility_ids(settings["hidden_character_ids"]))
    assert [character.char_id for _, character in visible] == [101, 103]


def test_new_id_override_takes_precedence_when_migrating_legacy_prefs():
    accounts = [Account("test", 1, "0", False, [Character(101, "Pilot")])]
    settings = {"hidden_characters": ["Pilot"]}
    change_visibility(settings, (1, 101), hidden=False)
    assert migrate_legacy_visibility(settings, accounts)
    assert settings["hidden_character_ids"] == []
    assert settings["never_hide_character_ids"] == ["1:101"]


def test_settings_unhides_by_id_even_when_both_labels_match(qapp):
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QListWidget
    from src.pages.settings_page import SettingsPage

    calls = []
    subject = SimpleNamespace(
        hidden_list=QListWidget(),
        show_characters_requested=SimpleNamespace(emit=calls.append),
    )
    try:
        SettingsPage.set_hidden_character_items(subject, [((1, 101), "Sabrine"), ((2, 102), "Sabrine")])
        subject.hidden_list.setCurrentRow(1)
        SettingsPage._show_selected_characters(subject)
        assert calls == [[(2, 102)]]
    finally:
        subject.hidden_list.deleteLater()
