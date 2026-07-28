"""Pure visible-character view shared by grid, bulk launch, and metrics."""
from __future__ import annotations

from collections.abc import Iterable

from .db import Account, Character


def visible_character_rows(
    accounts: Iterable[Account],
    hidden_characters: Iterable[str],
) -> list[tuple[Account, Character]]:
    """Return visible, non-banned character rows in account/character order."""

    hidden = set(hidden_characters)
    rows: list[tuple[Account, Character]] = []
    for account in accounts:
        if account.banned or getattr(account, "hidden", False):
            continue
        rows.extend(
            (account, character)
            for character in account.characters
            if character.name not in hidden
        )
    return rows


def visible_account_count(rows: Iterable[tuple[Account, Character]]) -> int:
    """Count accounts that own at least one row in the visible view."""

    return len({account.username for account, _character in rows})
