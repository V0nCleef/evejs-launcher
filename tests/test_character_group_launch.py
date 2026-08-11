"""Main-window integration tests for exact character-group launching."""
from __future__ import annotations

from PyQt6.QtWidgets import QApplication, QMainWindow

from src import config
from src.app import MainWindow
from src.core.db import Account, Character
from src.core.groups import CharacterGroup, GroupMember, TargetGroupState


class _Tracker:
    def __init__(self, running: set[str] | None = None) -> None:
        self.running = set(running or set())

    def is_account_running(self, username: str) -> bool:
        return username in self.running


def _accounts() -> list[Account]:
    return [
        Account(
            "account-a",
            1,
            "0",
            False,
            [Character(101, "First"), Character(102, "Second")],
        ),
        Account(
            "account-b",
            2,
            "0",
            False,
            [Character(201, "Third")],
        ),
    ]


def _window(qapp: QApplication) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = dict(config.DEFAULT_CONFIG)
    window._cfg.update(
        {
            "evejs_root": "C:/Fixture/EveJS",
            "client_path": "C:/Fixture/EVE/tq",
            "hide_test_characters": False,
        }
    )
    window._accounts = _accounts()
    window._tracker = _Tracker()
    window._pending_client_launches = set()
    window._launch_queue = None
    window._client_launch_thread = None
    window._effective_hidden_characters = lambda: set()
    return window


def test_group_launch_queues_exact_selected_characters(
    qapp: QApplication,
) -> None:
    window = _window(qapp)
    window._group_state = TargetGroupState(
        (
            CharacterGroup(
                "miners",
                "Miners",
                members=(GroupMember(1, 102), GroupMember(2, 201)),
            ),
        ),
        "miners",
    )
    callbacks: list[object] = []
    queued: list[tuple[list[tuple[Account, Character]], str | None]] = []
    window._ensure_server_if_needed = lambda callback: callbacks.append(callback) or True
    window._begin_client_launch_queue = (
        lambda candidates, *, group_name=None, **_kwargs: queued.append(
            (candidates, group_name)
        )
    )

    window._launch_all()
    callbacks[0]()

    assert [(account.username, char.name) for account, char in queued[0][0]] == [
        ("account-a", "Second"),
        ("account-b", "Third"),
    ]
    assert queued[0][1] == "Miners"
    window.deleteLater()


def test_all_visible_keeps_first_character_per_account_behavior(
    qapp: QApplication,
) -> None:
    window = _window(qapp)
    window._group_state = TargetGroupState()

    rows, error = window._batch_launch_rows(set())

    assert error is None
    assert [(account.username, char.name) for account, char in rows] == [
        ("account-a", "First"),
        ("account-b", "Third"),
    ]
    window.deleteLater()


def test_hidden_group_member_is_not_a_launch_candidate(
    qapp: QApplication,
) -> None:
    window = _window(qapp)
    window._group_state = TargetGroupState(
        (
            CharacterGroup(
                "miners",
                "Miners",
                members=(GroupMember(1, 102), GroupMember(2, 201)),
            ),
        ),
        "miners",
    )

    rows, error = window._batch_launch_rows({"Second"})

    assert error is None
    assert [(account.username, char.name) for account, char in rows] == [
        ("account-b", "Third")
    ]
    window.deleteLater()
