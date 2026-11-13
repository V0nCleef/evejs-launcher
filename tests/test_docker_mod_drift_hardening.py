"""A stale mod override or case-variant account must not blank the main screen."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PyQt6.QtCore import QThread, Qt

from src.core.mod_inventory import _moved_mod_notice
from src.core.runtime.data import (
    DataSourceError,
    _map_roster_accounts,
    resolve_docker_data_target,
)
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord, PreflightReport
from src.core.runtime.docker_mods import DockerModBridgeError
from src.core.runtime.docker_setup import DockerSetupDraft, create_preflight_request
from src.core.service_status import ServiceState
from src.workers.db_worker import AccountLoader, DataLoadFailure
from src.workers.docker_monitor import DockerMonitor, DockerObservation
from src.workers.docker_preflight_worker import DockerPreflightWorker


def _player(char_id: str, account_id: int, account_name: str) -> dict:
    return dict(
        characterId=char_id,
        characterName=f"Pilot {char_id}",
        accountId=account_id,
        accountName=account_name,
        banned=False,
        isGM=False,
    )


def test_roster_keeps_an_empty_account_whose_name_differs_only_by_case() -> None:
    payload = {
        "accounts": [
            {"accountId": 1, "accountName": "Fixture"},
            {"accountId": 2, "accountName": "fixture"},
            {"accountId": 3, "accountName": "other"},
        ],
        "players": [_player("10", 1, "Fixture"), _player("11", 3, "other")],
    }
    accounts = _map_roster_accounts(payload)

    assert sorted((a.account_id, a.username, [c.char_id for c in a.characters]) for a in accounts) == [
        (1, "Fixture", [10]),
        (2, "fixture", []),
        (3, "other", [11]),
    ]


@pytest.mark.parametrize(
    "accounts",
    [
        [{"accountId": 1, "accountName": "a"}, {"accountId": 1, "accountName": "b"}],
        [{"accountId": 1, "accountName": "same"}, {"accountId": 2, "accountName": "same"}],
    ],
)
def test_roster_still_rejects_duplicate_ids_and_exact_names(accounts) -> None:
    with pytest.raises(DataSourceError):
        _map_roster_accounts({"accounts": accounts, "players": []})


def test_unexpected_worker_fault_is_logged_with_its_class(caplog) -> None:
    class Boom(RuntimeError):
        pass

    def factory():
        raise Boom("fixture detail")

    failures: list[DataLoadFailure] = []
    worker = AccountLoader(factory)
    worker.failed.connect(failures.append)
    with caplog.at_level(logging.WARNING, logger="src.workers.db_worker"):
        worker.run()

    assert [f.code for f in failures] == ["unexpected_error"]
    # The GUI text stays generic; the local log names the real fault.
    assert "Boom" not in failures[0].message
    assert "Boom: fixture detail" in caplog.text


def test_mod_override_drift_becomes_an_actionable_data_error() -> None:
    def factory():
        raise DockerModBridgeError("The Docker loader directory for 'Fixture-0.1.0' is unavailable or unsafe.")

    with pytest.raises(DataSourceError) as caught:
        resolve_docker_data_target(factory)

    assert caught.value.code == "docker_mods_unavailable"
    assert caught.value.diagnostic.reason == "mods_unavailable"
    assert "Fixture-0.1.0" in str(caught.value)
    assert "apply them again" in str(caught.value)


class _Inspector:
    def __init__(self) -> None:
        self.targets: list[object] = []
        records = {
            "server": ContainerRecord("server", "game", "game-id", ServiceState.ONLINE, "healthy", None, ()),
            "market": ContainerRecord("market", "market", "market-id", ServiceState.ONLINE, "healthy", None, ()),
        }
        self.report = PreflightReport(True, (), config=None, records=records)

    def preflight(self, target):
        self.targets.append(target)
        return self.report

    def status(self, target):
        self.targets.append(target)
        return self.report.records

    def container_runtime_identity(self, target, record):
        return None


def test_monitor_reports_running_project_while_mods_are_stale_then_recovers(tmp_path: Path) -> None:
    plain = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    with_mods = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture", (tmp_path / "mods.yaml",))
    broken = {"value": True}

    def primary():
        if broken["value"]:
            raise DockerModBridgeError("The Docker loader directory for 'Fixture-0.1.0' is unavailable or unsafe.")
        return with_mods

    inspector = _Inspector()
    monitor = DockerMonitor(
        primary,
        inspector_factory=lambda: inspector,
        fallback_target_factory=lambda: plain,
        settings_identity="fixture-settings",
    )
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)

    monitor.observe_now()
    assert emitted[-1].game is ServiceState.ONLINE
    assert "Fixture-0.1.0" in (emitted[-1].mod_error or "")
    assert inspector.targets[-1] is plain

    broken["value"] = False
    monitor.observe_now()
    assert emitted[-1].game is ServiceState.ONLINE
    assert emitted[-1].mod_error is None
    assert inspector.targets[-1] is with_mods


def test_monitor_without_fallback_still_fails_closed() -> None:
    def primary():
        raise DockerModBridgeError("stale")

    monitor = DockerMonitor(primary, inspector_factory=_Inspector, settings_identity="fixture-settings")
    emitted: list[DockerObservation] = []
    monitor.observation_changed.connect(emitted.append)
    monitor.observe_now()
    assert emitted[-1].game is ServiceState.UNKNOWN


def test_docker_setup_test_warns_about_stale_mod_override(qapp, tmp_path: Path, monkeypatch) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    request = create_preflight_request(
        DockerSetupDraft(str(tmp_path), str(compose), "fixture", "connect_only", True, ""),
        token=3,
    )

    def stale(_target):
        raise DockerModBridgeError("The Docker loader directory for 'Fixture-0.1.0' is unavailable or unsafe.")

    monkeypatch.setattr("src.workers.docker_preflight_worker.attach_docker_mod_override", stale)

    class Inspector:
        def preflight(self, _target):
            return PreflightReport(True, ("ready",))

    results: list[object] = []
    thread = QThread()
    worker = DockerPreflightWorker(request, inspector_factory=Inspector)
    worker.moveToThread(thread)
    worker.completed.connect(results.append)
    worker.cleanup.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    thread.started.connect(worker.run)
    thread.start()
    assert thread.wait(2_000)
    qapp.processEvents()

    assert results[0].report.ok is True
    assert "Fixture-0.1.0" in results[0].mod_warning


def test_moved_mod_notice_names_old_and_new_folder(tmp_path: Path) -> None:
    (tmp_path / "mods" / "Fixture").mkdir(parents=True)

    class Record:
        status = "installed"
        relative_path = "mods/Fixture-0.1.0"
        mod_id = "fixtureMod"

    class Mod:
        id = "fixtureMod"
        name = "Fixture Mod"
        path = tmp_path / "mods" / "Fixture"

    notice = _moved_mod_notice(str(tmp_path), [Record()], [Mod()])
    assert "mods/Fixture-0.1.0" in notice and "mods/Fixture" in notice
    assert _moved_mod_notice(str(tmp_path), [], [Mod()]) == ""
