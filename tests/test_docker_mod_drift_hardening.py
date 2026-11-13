"""Mod-drift regression controls adapted from Tokeiito's PR #4."""
from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QThread, Qt

from src.core import mod_inventory
from src.core.runtime.data import (
    DataSourceError,
    _map_roster_accounts,
)
from src.core.runtime import data as data_module
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord, PreflightReport, PreflightFailureKind
from src.core.runtime.data_diagnostics import DOCKER_MODS_UNAVAILABLE_MESSAGE
from src.core.runtime.docker_mods import DockerModBridgeError
from src.core.runtime.docker_setup import DockerSetupDraft, create_preflight_request
from src.core.service_status import DockerControlPolicy, RuntimeBackend, RuntimeSnapshot, ServiceState
from src.workers.db_worker import AccountLoader, DataLoadFailure
from src.workers.docker_monitor import DockerMonitor, DockerObservation
from src.workers.docker_preflight_worker import DockerPreflightWorker
from test_app_docker_observer import docker_window


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


def test_unexpected_worker_fault_keeps_private_values_out_of_logs(caplog) -> None:
    def factory():
        raise ValueError('{"accountName":"QA_PRIVATE_ACCOUNT","password":"QA_PRIVATE_PASSWORD"}')

    failures: list[DataLoadFailure] = []
    worker = AccountLoader(factory)
    worker.failed.connect(failures.append)
    with caplog.at_level(logging.WARNING):
        worker.run()

    assert [f.code for f in failures] == ["unexpected_error"]
    assert failures[0].message == "Character data could not be loaded safely."
    assert "QA_PRIVATE_ACCOUNT" not in caplog.text
    assert "QA_PRIVATE_PASSWORD" not in caplog.text


def test_mod_override_drift_becomes_an_actionable_data_error() -> None:
    def factory():
        raise DockerModBridgeError("The Docker loader directory for 'Fixture-0.1.0' is unavailable or unsafe.")

    with pytest.raises(DataSourceError) as caught:
        data_module.resolve_docker_data_target(factory)

    assert caught.value.code == "docker_mods_unavailable"
    assert caught.value.diagnostic.reason == "mods_unavailable"
    assert "Fixture-0.1.0" not in str(caught.value)
    assert "Mods" in str(caught.value)


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
    assert emitted[-1].mod_error and "Fixture-0.1.0" not in emitted[-1].mod_error
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
    worker.cleanup.connect(worker.deleteLater)
    worker.destroyed.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    thread.started.connect(worker.run)
    thread.start()
    assert thread.wait(2_000)
    qapp.processEvents()

    assert results[0].report.ok is True
    assert results[0].mod_warning and "Fixture-0.1.0" not in results[0].mod_warning
    thread.deleteLater()
    from PyQt6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(thread, QEvent.Type.DeferredDelete)


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

    notice = mod_inventory._moved_mod_notice(str(tmp_path), [Record()], [Mod()])
    assert "mods/Fixture-0.1.0" in notice and "mods/Fixture" in notice
    assert mod_inventory._moved_mod_notice(str(tmp_path), [], [Mod()]) == ""


def _snapshot(*, mod_error=None, game=ServiceState.ONLINE):
    from src.core.runtime.endpoints import Endpoint, RuntimeEndpoints
    def endpoint(service, target, port):
        return Endpoint(service, "127.0.0.1", port, target, "tcp")
    snapshot = RuntimeSnapshot(game, ServiceState.ONLINE, 0,
        backend=RuntimeBackend.DOCKER_COMPOSE, docker_control_policy=DockerControlPolicy.MANAGED,
        target_identity="docker:fixture", settings_identity="fixture-settings", monitor_generation=7,
        endpoints=RuntimeEndpoints(
            game=endpoint("server", 26000, 32600), image=endpoint("server", 26001, 32601),
            proxy=endpoint("server", 26002, 32602), assets=endpoint("server", 26003, 34443),
            xmpp=endpoint("server", 5222, 35222), market=endpoint("market", 40110, 40110)))
    return replace(snapshot, docker_mod_error=mod_error)


def _window(snapshot):
    return SimpleNamespace(
        _docker_mode=lambda: True, _lifecycle_active=lambda: False,
        _runtime_snapshot=snapshot, _monitor_generation=7,
        _docker_monitor_settings_identity=lambda: "fixture-settings",
    )


def test_fallback_observation_is_not_exposed_as_mod_ready_target():
    from src.app import MainWindow
    window = _window(_snapshot(mod_error=DOCKER_MODS_UNAVAILABLE_MESSAGE))
    assert MainWindow._current_observed_docker_target_identity(window) is None
    window._runtime_snapshot = _snapshot()
    assert MainWindow._current_observed_docker_target_identity(window) == "docker:fixture"


def test_fallback_cannot_authorize_client_endpoints(monkeypatch):
    from src.app import MainWindow
    from src.core.launcher import ClientLaunchContext
    def forbidden(cls, endpoints, *, target_identity, settings_identity, monitor_generation):
        pytest.fail("degraded observation reached client endpoint construction")
    monkeypatch.setattr(ClientLaunchContext, "from_docker", classmethod(forbidden))
    context, reason = MainWindow._resolve_client_launch_context(
        _window(_snapshot(mod_error=DOCKER_MODS_UNAVAILABLE_MESSAGE)))
    assert context is None and "Mods" in reason


def test_recovered_observation_restores_client_context():
    from src.app import MainWindow
    context, reason = MainWindow._resolve_client_launch_context(_window(_snapshot()))
    assert context is not None and not reason and context.target_identity == "docker:fixture"


def test_running_monitor_detects_new_mod_drift_and_recovers(tmp_path):
    plain = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    attached = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture", (tmp_path / "mods.yaml",))
    broken, calls = [False], []
    def primary():
        calls.append(True)
        if broken[0]:
            raise DockerModBridgeError('private-json={"password":"QA_PRIVATE"}')
        return attached
    monitor = DockerMonitor(primary, inspector_factory=_Inspector,
                            fallback_target_factory=lambda: plain, settings_identity="fixture-settings")
    observations = []
    monitor.observation_changed.connect(observations.append)
    monitor.observe_now()
    assert observations[-1].mod_error is None
    broken[0] = True
    monitor.observe_now()
    assert observations[-1].game is ServiceState.ONLINE and observations[-1].mod_error
    assert "QA_PRIVATE" not in observations[-1].mod_error
    broken[0] = False
    monitor.observe_now()
    assert observations[-1].mod_error is None and len(calls) == 3


def test_failed_repair_preflight_does_not_retain_fallback_authority(tmp_path):
    plain = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture")
    attached = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "fixture", (tmp_path / "mods.yaml",))
    broken = [True]
    def primary():
        if broken[0]:
            raise DockerModBridgeError("missing")
        return attached
    class Inspector(_Inspector):
        def preflight(self, target):
            if target is attached:
                return PreflightReport.failed(PreflightFailureKind.INSPECT_FAILED)
            return super().preflight(target)
    monitor = DockerMonitor(primary, inspector_factory=Inspector,
                            fallback_target_factory=lambda: plain, settings_identity="fixture-settings")
    observations = []
    monitor.observation_changed.connect(observations.append)
    monitor.observe_now()
    assert observations[-1].target_identity
    broken[0] = False
    monitor.observe_now()
    assert observations[-1].game is ServiceState.UNKNOWN
    assert observations[-1].target_identity is None and observations[-1].endpoints is None


def test_unrelated_monitor_error_does_not_activate_mod_fallback():
    fallbacks = []
    def primary():
        raise RuntimeError("fixture failure")
    monitor = DockerMonitor(primary, inspector_factory=_Inspector,
                            fallback_target_factory=lambda: fallbacks.append(True),
                            settings_identity="fixture-settings")
    observations = []
    monitor.observation_changed.connect(observations.append)
    monitor.observe_now()
    assert not fallbacks and observations[-1].game is ServiceState.UNKNOWN
    assert observations[-1].mod_error is None


def test_degraded_home_never_claims_all_systems_nominal(qapp):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from src.pages.home_page import HomePage
    page = HomePage()
    try:
        page.apply_runtime_snapshot(_snapshot(mod_error=DOCKER_MODS_UNAVAILABLE_MESSAGE))
        assert "MODS" in page.overall_status_label.text().upper()
        assert "Mods" in page.overall_detail_label.text()
        assert page._stack_action == "stop" and page.btn_start_servers.isEnabled()
        page.apply_runtime_snapshot(_snapshot())
        assert "NOMINAL" in page.overall_status_label.text().upper()
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(page, QEvent.Type.DeferredDelete)


def test_degraded_sample_cannot_complete_pending_mod_verification():
    from src.app import MainWindow
    touched = []
    window = SimpleNamespace(_pending_docker_mod_observation_token=None,
        _retry_deferred_docker_verification=lambda observation, generation: touched.append(True))
    observation = DockerObservation(ServiceState.ONLINE, ServiceState.ONLINE,
                                    mod_error=DOCKER_MODS_UNAVAILABLE_MESSAGE)
    MainWindow._on_docker_mod_runtime_observation_sample(window, observation)
    assert not touched


def test_real_registered_legacy_folder_rename_is_reported_without_registry_edits(tmp_path, monkeypatch):
    from src.core.local_mod_packages import LocalModPackages
    from src.core.mod_manifest import scan_mods
    from src.core import mod_management
    def absent(_mod):
        raise mod_management.ModNotManagedError("No fixture installer enrollment")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", absent)
    root = tmp_path / "world"
    original = root / "mods" / "Fixture-0.1.0"
    original.mkdir(parents=True)
    loader_bytes = b"module.exports = {};\n"
    (original / "loader.js").write_bytes(loader_bytes)
    service = LocalModPackages(root)
    service.adopt(scan_mods(root)[0])
    before = service.registry_path.read_bytes()
    moved = original.with_name("Fixture")
    original.rename(moved)
    inventory = mod_inventory.load_mod_inventory(str(root))
    assert "mods/Fixture-0.1.0" in inventory.moved_mod_notice
    assert "missing" in inventory.moved_mod_notice.lower()
    assert service.registry_path.read_bytes() == before
    assert (moved / "loader.js").read_bytes() == loader_bytes


def test_moved_notice_does_not_guess_between_duplicate_ids(tmp_path):
    record = SimpleNamespace(status="installed", relative_path="mods/Old", mod_id="fixture")
    mods = []
    for name in ("One", "Two"):
        path = tmp_path / "mods" / name
        path.mkdir(parents=True)
        mods.append(SimpleNamespace(id="fixture", name="Fixture", path=path))
    notice = mod_inventory._moved_mod_notice(str(tmp_path), [record], mods)
    assert "mods/Old" in notice
    assert "the mod is now in" not in notice and "mods/One" not in notice


def test_missing_mod_notices_are_bounded(tmp_path):
    records = [SimpleNamespace(status="installed", relative_path=f"mods/Old-{i}", mod_id=f"fixture-{i}") for i in range(30)]
    notice = mod_inventory._moved_mod_notice(str(tmp_path), records, [])
    assert notice and len(notice) <= 2600 and len(notice.splitlines()) <= 6


def test_main_window_retires_fallback_authority_and_recovers_even_with_same_digest(docker_window, caplog):
    window = docker_window
    identity = window._docker_monitor_settings_identity()
    snapshot = replace(_snapshot(), settings_identity=identity,
                       monitor_generation=window._monitor_generation)
    window._runtime_snapshot = snapshot
    events = []
    window._accounts = [object()]
    window._data_selection = object()
    window._account_thread = None
    window._publish_mod_runtime_snapshot = lambda value: events.append("retire-mods")
    window._cancel_launch_queue = lambda: events.append("cancel-launches")
    window._cancel_data_loads = lambda: events.append("cancel-data")
    window._stop_docker_log_stream = lambda: events.append("stop-follower")
    window._refresh_characters = lambda: events.append("reload")
    window._apply_runtime_snapshot = lambda value: None
    observation = DockerObservation(ServiceState.ONLINE, ServiceState.ONLINE,
        target_identity=snapshot.target_identity, settings_identity=identity,
        monitor_generation=window._monitor_generation, endpoints=snapshot.endpoints,
        mod_error="QA_PRIVATE_ACCOUNT / QA_PRIVATE_PASSWORD")
    window._on_docker_observation(observation)
    assert window._runtime_snapshot.game is ServiceState.ONLINE
    assert window._runtime_snapshot.docker_mod_error == DOCKER_MODS_UNAVAILABLE_MESSAGE
    assert window._current_observed_docker_target_identity() is None
    assert not window._accounts and window._data_selection is None
    assert events == ["retire-mods", "cancel-launches", "cancel-data", "stop-follower", "reload"]
    assert "QA_PRIVATE" not in caplog.text
    events.clear()
    window._on_docker_observation(replace(observation, mod_error=None))
    assert window._runtime_snapshot.docker_mod_error is None
    assert window._current_observed_docker_target_identity() == snapshot.target_identity
    assert events == ["retire-mods", "cancel-launches", "cancel-data", "stop-follower", "reload"]


@pytest.mark.parametrize("policy", list(DockerControlPolicy))
def test_real_widgets_preserve_only_explicit_stop_during_mod_drift(qapp, policy):
    from PyQt6.QtCore import QCoreApplication, QEvent
    from test_app_docker_lifecycle import _real_widget_window
    window = _real_widget_window()
    try:
        online = replace(_snapshot(mod_error=DOCKER_MODS_UNAVAILABLE_MESSAGE), docker_control_policy=policy)
        window._apply_runtime_snapshot(online)
        assert window._nav.btn_server.isEnabled() is (policy is DockerControlPolicy.MANAGED)
        assert window._home_page.btn_start_servers.isEnabled() is (policy is DockerControlPolicy.MANAGED)
        offline = replace(online, game=ServiceState.OFFLINE, market=ServiceState.OFFLINE)
        window._apply_runtime_snapshot(offline)
        assert not window._nav.btn_server.isEnabled() and not window._nav.btn_market.isEnabled()
        assert not window._home_page.btn_start_servers.isEnabled()
        window._apply_runtime_snapshot(replace(offline, docker_mod_error=None))
        assert window._nav.btn_server.isEnabled() is (policy is DockerControlPolicy.MANAGED)
    finally:
        # This bare-window fixture creates no timers or service workers.
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def test_real_mod_override_fallback_and_repair_are_read_only(tmp_path):
    from src.core.runtime.docker_mods import attach_docker_mod_override, build_docker_mod_override, docker_mod_override_path
    world = tmp_path / "world"
    folder = world / "mods" / "Fixture"
    folder.mkdir(parents=True)
    (folder / "loader.js").write_bytes(b"module.exports = {};\n")
    compose = world / "compose.yaml"
    compose.write_text("services: {}\n")
    override = docker_mod_override_path(world)
    override.parent.mkdir(parents=True)
    override.write_text(build_docker_mod_override(world, ("OldFixture",)).content, encoding="utf-8")
    def files():
        return {p.relative_to(world).as_posix(): p.read_bytes() for p in world.rglob("*") if p.is_file()}
    plain = ComposeTarget(compose, world, "fixture")
    monitor = DockerMonitor(lambda: attach_docker_mod_override(plain), inspector_factory=_Inspector,
        fallback_target_factory=lambda: plain, settings_identity="fixture-settings")
    observations = []
    monitor.observation_changed.connect(observations.append)
    before = files()
    monitor.observe_now()
    assert observations[-1].mod_error and observations[-1].game is ServiceState.ONLINE
    assert files() == before
    fallback_identity = observations[-1].target_identity
    override.write_text(build_docker_mod_override(world, ("Fixture",)).content, encoding="utf-8")
    repaired = files()
    monitor.observe_now()
    assert observations[-1].mod_error is None
    assert observations[-1].target_identity != fallback_identity
    assert files() == repaired


@pytest.mark.parametrize("image_error", [False, True])
def test_known_mod_failures_cross_worker_boundary_as_categories_only(image_error, caplog):
    from src.core.runtime.docker_mod_images import DockerModImageError
    error_type = DockerModImageError if image_error else DockerModBridgeError
    def target():
        raise error_type('{"accountName":"QA_PRIVATE_ACCOUNT","password":"QA_PRIVATE_PASSWORD"}')
    failures = []
    worker = AccountLoader(lambda: data_module.resolve_docker_data_target(target))
    worker.failed.connect(failures.append)
    worker.run()
    assert failures[0].code == "docker_mods_unavailable"
    assert failures[0].diagnostic.stage == "mods" and failures[0].diagnostic.reason == "mods_unavailable"
    assert failures[0].message == DOCKER_MODS_UNAVAILABLE_MESSAGE
    assert "QA_PRIVATE" not in caplog.text and "QA_PRIVATE" not in repr(failures)


def test_optional_preflight_fields_do_not_hide_mod_warning():
    from src.core.runtime.docker_setup import DockerPreflightResult
    from src.pages.settings_page import SettingsPage
    result = DockerPreflightResult(1, "fixture", PreflightReport(True, ("ready",)),
                                   DOCKER_MODS_UNAVAILABLE_MESSAGE)
    text = SettingsPage._format_docker_preflight_success(result)
    assert "valid" in text.lower() and "Needs attention" in text and "Mods" in text
