"""Per-installation character visibility based on durable roster identities."""
from __future__ import annotations

from collections.abc import Iterable

from .db import Account, Character

CharacterIdentity = tuple[int, int]
VisibilityRef = str | CharacterIdentity


def visibility_ids(values: Iterable[str]) -> set[CharacterIdentity]:
    identities = set()
    for value in values:
        if not isinstance(value, str):
            continue
        account, separator, character = value.partition(":")
        if separator and all(
            part.isascii() and part.isdecimal() and 0 < len(part) <= 16
            for part in (account, character)
        ):
            key = int(account), int(character)
            if key[0] > 0 and key[1] > 0:
                identities.add(key)
    return identities


def store_visibility_ids(settings: dict, field: str, values: set[CharacterIdentity]) -> None:
    settings[field] = [f"{account}:{character}" for account, character in sorted(values)]


def change_visibility(settings: dict, ref: VisibilityRef, *, hidden: bool) -> None:
    if isinstance(ref, tuple):
        hidden_ids = visibility_ids(settings.get("hidden_character_ids", []))
        shown_ids = visibility_ids(settings.get("never_hide_character_ids", []))
        (hidden_ids if hidden else shown_ids).add(ref)
        (shown_ids if hidden else hidden_ids).discard(ref)
        store_visibility_ids(settings, "hidden_character_ids", hidden_ids)
        store_visibility_ids(settings, "never_hide_character_ids", shown_ids)
        return
    hidden_names = set(settings.get("hidden_characters", []))
    shown_names = set(settings.get("never_hide_characters", []))
    if settings.get("character_visibility_initialized", False):
        # Retire the unresolved legacy entry without attaching it to a future pilot.
        hidden_names.discard(ref)
    else:
        (hidden_names if hidden else shown_names).add(ref)
        (shown_names if hidden else hidden_names).discard(ref)
    settings["hidden_characters"] = sorted(hidden_names)
    settings["never_hide_characters"] = sorted(shown_names)


def migrate_legacy_visibility(settings: dict, accounts: Iterable[Account]) -> bool:
    """Pin all affected pilots from a current, complete roster once."""
    if settings.get("character_visibility_initialized", False):
        return False
    hidden = set(settings.get("hidden_characters", []))
    shown = set(settings.get("never_hide_characters", []))
    if not (hidden or shown):
        return False
    by_name: dict[str, set[CharacterIdentity]] = {}
    for account in accounts:
        for character in account.characters:
            if account.account_id > 0 and character.char_id > 0:
                by_name.setdefault(character.name, set()).add((account.account_id, character.char_id))
    stored_hidden = visibility_ids(settings.get("hidden_character_ids", []))
    stored_shown = visibility_ids(settings.get("never_hide_character_ids", []))
    legacy_hidden = set().union(*(by_name.get(name, set()) for name in hidden))
    legacy_shown = set().union(*(by_name.get(name, set()) for name in shown))
    store_visibility_ids(settings, "hidden_character_ids", stored_hidden | (legacy_hidden - stored_shown))
    store_visibility_ids(settings, "never_hide_character_ids", stored_shown | (legacy_shown - stored_hidden))
    settings["hidden_characters"] = sorted(hidden - by_name.keys())
    settings["never_hide_characters"] = sorted(shown - by_name.keys())
    settings["character_visibility_initialized"] = True
    return True


def is_character_hidden(account: Account, character: Character, hidden: set[VisibilityRef]) -> bool:
    return character.name in hidden or (account.account_id, character.char_id) in hidden
