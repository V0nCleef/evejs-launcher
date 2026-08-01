"""Regression tests for dashboard counts derived from visible characters."""
from __future__ import annotations

from copy import deepcopy
import time

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.db import Account, Character


def _wait_for_data(qapp: QApplication, window: MainWindow) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not window._data_load_active():
            qapp.processEvents()
            return
        QTest.qWait(5)
    assert not window._data_load_active()


def _character(char_id: int, name: str) -> Character:
    return Character(char_id=char_id, name=name)


def _account(
    username: str,
    account_id: int,
    *characters: Character,
    banned: bool = False,
) -> Account:
    return Account(
        username=username,
        account_id=account_id,
        role="0",
        banned=banned,
        characters=list(characters),
    )


@pytest.mark.parametrize(
    ("accounts", "hidden", "expected_accounts", "expected_characters"),
    [
        (
            [_account("account-a", 1, _character(101, "Pilot One"), _character(102, "Pilot Two"))],
            ["Pilot Two"],
            1,
            1,
        ),
        (
            [_account("account-a", 1, _character(101, "Pilot One"), _character(102, "Pilot Two"))],
            ["Pilot One", "Pilot Two"],
            0,
            0,
        ),
        (
            [_account("account-a", 1, _character(101, "Pilot One"), banned=True)],
            [],
            0,
            0,
        ),
        (
            [_account("account-a", 1, _character(101, "Pilot One"), _character(102, "Pilot Two"))],
            [],
            1,
            2,
        ),
    ],
    ids=["one-hidden", "all-hidden", "banned", "multiple-characters"],
)
def test_home_counts_only_visible_non_banned_characters(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    accounts: list[Account],
    hidden: list[str],
    expected_accounts: int,
    expected_characters: int,
) -> None:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "C:/Games/EveJS",
            "client_path": "",
            "hidden_characters": list(hidden),
            "hide_test_characters": False,
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: accounts)
    monkeypatch.setattr(app_module.CharactersPage, "refresh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_for_data(qapp, window)
        assert window._home_page.accounts_card.value_label.text() == str(expected_accounts)
        assert window._home_page.characters_card.value_label.text() == str(expected_characters)
    finally:
        window.close()
        _wait_for_data(qapp, window)
        window.deleteLater()


def test_launch_all_requires_visible_account_and_complete_paths(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts = [_account("account-a", 1, _character(101, "Pilot One"))]
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": "C:/Games/EveJS",
            "client_path": "C:/Games/EVE/tq",
            "hide_test_characters": False,
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: accounts)
    monkeypatch.setattr(app_module.CharactersPage, "refresh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "is_server_running", lambda **_kwargs: False)

    window = MainWindow()
    try:
        window._status_timer.stop()
        window._prune_timer.stop()
        _wait_for_data(qapp, window)
        assert window._home_page.btn_launch_all.isEnabled() is True

        window._cfg["client_path"] = ""
        window._refresh_characters()
        _wait_for_data(qapp, window)

        assert window._home_page.btn_launch_all.isEnabled() is False
        assert "Configure" in window._home_page.btn_launch_all.toolTip()
    finally:
        window.close()
        _wait_for_data(qapp, window)
        window.deleteLater()
