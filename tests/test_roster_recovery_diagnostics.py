"""Real parser/worker controls for the reported Docker roster regression."""
import json
import subprocess

import pytest

from src.core.runtime.character_display import DISPLAY_SCRIPT
from src.core.runtime.data import DockerExportDataSource, DataSourceError
from src.core.runtime.docker_cli import DockerCommandRunner
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord
from src.core.service_status import DockerControlPolicy, ServiceState
from src.workers.db_worker import AccountLoader


def payload():
    account = dict(accountId=1, accountName="fixture-account", banned=False, isGM=False)
    player = dict(characterId="140000001", characterName="Fixture Pilot", **account,
                  balance=123, skillPoints=456, shipTypeID=789, securityStatus=0.5)
    return dict(accounts=[account], players=[player])


def source(tmp_path, roster=None, display=None):
    roster = payload() if roster is None else roster
    display = {"players": []} if display is None else display
    def execute(argv, cwd, timeout):
        result = display if DISPLAY_SCRIPT in argv else roster
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, subprocess.CompletedProcess):
            return result
        return subprocess.CompletedProcess(argv, 0, result if isinstance(result, str) else json.dumps(result), "")
    return DockerExportDataSource(
        ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture-world"),
        ContainerRecord("server", "fixture-world-server-1", "0123456789ab",
                        ServiceState.ONLINE, "healthy", 0, (), True, "running"),
        DockerCommandRunner(executable="docker.exe", execute=execute),
        "docker:fixture", control_policy=DockerControlPolicy.MANAGED,
    )


@pytest.mark.parametrize("display", [
    "not JSON",
    {"players": [{"characterId": "invalid"}]},
    subprocess.CompletedProcess((), 1, "", "password=private-sentinel"),
    subprocess.TimeoutExpired("private-sentinel", 20),
])
def test_bad_enrichment_preserves_authoritative_roster(tmp_path, display, caplog):
    provider = source(tmp_path, display=display)
    accounts = provider.load_accounts()
    assert [(a.username, [c.char_id for c in a.characters]) for a in accounts] == [
        ("fixture-account", [140000001])]
    assert accounts[0].characters[0].isk == 123
    assert provider.warnings[0].stage == "display"
    assert "private-sentinel" not in caplog.text
    assert "stage=display" in caplog.text


@pytest.mark.parametrize("field,value", [
    ("balance", "private-sentinel"), ("skillPoints", "0"),
    ("shipTypeID", False), ("securityStatus", None),
    ("shipName", {"private": "private-sentinel"}),
    ("solarSystemName", ["private-sentinel"]),
])
def test_optional_roster_statistics_cannot_erase_identities(tmp_path, field, value):
    exported = payload()
    exported["players"][0][field] = value
    provider = source(tmp_path, roster=exported)
    accounts = provider.load_accounts()
    assert accounts[0].characters[0].char_id == 140000001
    assert any(w.field == field and w.stage == "roster" for w in provider.warnings)
    assert "private-sentinel" not in repr(provider.warnings)


@pytest.mark.parametrize("field,value", [
    ("characterId", "private-sentinel"), ("accountName", "wrong-account"),
    ("banned", "private-sentinel"), ("characterName", None),
])
def test_invalid_authoritative_identity_still_fails_closed(tmp_path, field, value):
    exported = payload()
    exported["players"][0][field] = value
    provider = source(tmp_path, roster=exported)
    with pytest.raises(DataSourceError) as failure:
        provider.load_accounts()
    assert failure.value.code == "malformed_export"
    assert failure.value.diagnostic.stage == "roster"
    assert failure.value.diagnostic.field == field
    assert "private-sentinel" not in str(failure.value)
    assert "private-sentinel" not in repr(failure.value.diagnostic)


def test_invalid_json_and_invalid_schema_have_different_safe_reasons(tmp_path):
    failures = []
    for roster in ("private-sentinel", {"players": []}):
        with pytest.raises(DataSourceError) as failure:
            source(tmp_path, roster=roster).load_accounts()
        failures.append(failure.value.diagnostic.reason)
    assert failures == ["invalid_json", "invalid_collection"]


def test_worker_delivers_warning_without_turning_success_into_failure(tmp_path):
    from src.core.runtime.data import RuntimeDataSelection
    provider = source(tmp_path, display="invalid JSON")
    worker = AccountLoader(lambda: RuntimeDataSelection(provider, None, "docker:fixture"))
    completed, failed = [], []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert not failed and len(completed[0].accounts) == 1
    assert completed[0].warnings[0].stage == "display"


def test_reload_clears_previous_warning(tmp_path):
    provider = source(tmp_path, display="invalid JSON")
    provider.load_accounts()
    assert provider.warnings
    provider.runner = source(tmp_path).runner
    provider.load_accounts()
    assert provider.warnings == ()


def test_valid_cards_remain_usable_with_statistics_warning(qapp):
    from PyQt6.QtCore import QEvent
    from src.core.db import Account, Character
    from src.core.process_tracker import ProcessTracker
    from src.pages.characters_page import CharactersPage
    page = CharactersPage()
    try:
        account = Account("fixture-account", 1, "0", False)
        account.characters = [Character(140000001, "Fixture Pilot")]
        page.refresh([account], [], ProcessTracker())
        page.set_data_warning("Characters loaded; some display statistics are unavailable.")
        assert page._cards and page._data_error == "" and page._data_warning
        page.set_data_warning("")
        assert page._cards and page._data_warning == ""
        page.set_data_error("Invalid roster identity.")
        page.set_data_warning("")
        assert page._data_error == "Invalid roster identity."
    finally:
        page.close()
        page.deleteLater()
        qapp.sendPostedEvents(page, QEvent.Type.DeferredDelete)


def test_character_detail_survives_failed_display_enrichment(tmp_path):
    exported = {"players": [], "selectedCharacterId": "140000001",
                "selectedPlayer": {"characterId": "140000001", "character": {"balance": 123}}}
    provider = source(tmp_path, roster=exported, display="invalid JSON")
    assert provider.get_character_detail(140000001) == {"balance": 123}
    assert provider.warnings[0].stage == "display"
