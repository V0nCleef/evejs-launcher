"""Qt-safe runtime data worker tests."""
from __future__ import annotations

from dataclasses import dataclass

from src.core.db import Account
from src.core.runtime.data import DataSourceError, RuntimeDataSelection
from src.workers.db_worker import (
    AccountLoadResult,
    AccountLoader,
    CharacterDetailResult,
    CharacterDetailLoader,
    DataLoadFailure,
)


@dataclass
class FakeSource:
    accounts: list[Account]
    detail: dict | None = None
    failure: DataSourceError | None = None

    def load_accounts(self) -> list[Account]:
        if self.failure is not None:
            raise self.failure
        return self.accounts

    def get_character_detail(self, char_id: int) -> dict | None:
        if self.failure is not None:
            raise self.failure
        return self.detail


def _selection(source: FakeSource) -> RuntimeDataSelection:
    return RuntimeDataSelection(source, None, "fixture-target")


def test_account_loader_emits_selection_and_accounts() -> None:
    account = Account("fixture-account", 501, "0", False)
    worker = AccountLoader(lambda: _selection(FakeSource([account])))
    completed: list[AccountLoadResult] = []
    cleaned: list[bool] = []
    worker.completed.connect(completed.append)
    worker.cleanup.connect(lambda: cleaned.append(True))

    worker.run()

    assert completed == [
        AccountLoadResult(
            RuntimeDataSelection(completed[0].selection.data_source, None, "fixture-target"),
            (account,),
        )
    ]
    assert cleaned == [True]


def test_character_detail_loader_uses_selected_runtime_source() -> None:
    detail = {"characterName": "Fixture Pilot"}
    worker = CharacterDetailLoader(
        lambda: _selection(FakeSource([], detail=detail)),
        900000001,
    )
    completed: list[CharacterDetailResult] = []
    worker.completed.connect(completed.append)

    worker.run()

    assert completed[0].character_id == 900000001
    assert completed[0].detail == detail
    assert completed[0].selection.target_identity == "fixture-target"


def test_worker_failure_is_structured_and_private_safe() -> None:
    private_value = "synthetic-private-value"
    failure = DataSourceError(
        "export_failed",
        f"Export failed with password={private_value}",
    )
    worker = AccountLoader(lambda: _selection(FakeSource([], failure=failure)))
    failures: list[DataLoadFailure] = []
    worker.failed.connect(failures.append)

    worker.run()

    assert failures[0].code == "export_failed"
    assert private_value not in failures[0].message
    assert len(failures[0].message) <= 160


def test_cancelled_worker_emits_no_stale_result() -> None:
    worker = AccountLoader(lambda: _selection(FakeSource([])))
    completed: list[AccountLoadResult] = []
    cleaned: list[bool] = []
    worker.completed.connect(completed.append)
    worker.cleanup.connect(lambda: cleaned.append(True))
    worker.request_cancel()

    worker.run()

    assert completed == []
    assert cleaned == [True]
