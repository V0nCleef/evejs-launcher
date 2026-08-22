"""Application-level Docker mod bridge and recreation guards."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QMainWindow, QMessageBox

from src import app as app_module
from src.app import MainWindow
from src.core.runtime.docker_compose import ContainerRecord
from src.core.runtime.docker_controller import (
    DockerLifecycleAction,
    DockerLifecycleResult,
)
from src.core.runtime.docker_mods import (
    DockerModApplyResult,
    apply_docker_mod_override,
    build_docker_mod_override,
    docker_mod_override_path,
    docker_mod_transaction_path,
    finalize_docker_mod_override,
)
from src.core.mod_runtime_state import MAX_LOADER_PAYLOAD_BYTES, ModRuntimeStateError
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.workers.docker_monitor import DockerObservation


class _FinishedThread:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:
        self.deleted = True


def _loader(root: Path, mod_name: str) -> None:
    loader = root / "mods" / mod_name / "loader.js"
    loader.parent.mkdir(parents=True, exist_ok=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")


def _window(root: Path, policy: str = "managed") -> MainWindow:
    compose_file = root / "compose.yaml"
    if not compose_file.exists():
        compose_file.write_text("services: {}\n", encoding="utf-8")
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": policy,
        "evejs_root": str(root),
        "docker_compose_file": str(compose_file),
        "docker_project_name": "fixture",
    }
    window._mods_page = type(
        "Mods",
        (),
        {
            "refresh_count": 0,
            "snapshots": [],
            "selected_loader_names": lambda _self: ("Fixture Mod",),
            "refresh_mods": lambda self: setattr(
                self,
                "refresh_count",
                self.refresh_count + 1,
            ),
            "set_mod_runtime_snapshot": lambda self, snapshot: self.snapshots.append(
                snapshot
            ),
        },
    )()
    window._restart_docker_monitor_for_compose_change = lambda: None
    window._close_in_progress = False
    window._monitor_generation = 0
    window._tracker = type("Tracker", (), {"running_count": 0})()
    window._apply_runtime_snapshot = lambda _snapshot: None
    window._current_mod_runtime_snapshot = None
    window._attested_docker_target_identity = None
    window._attested_docker_container_id = None
    window._docker_mod_quarantined_targets = {}
    return window


def _successful_recreate_result(window: MainWindow) -> DockerLifecycleResult:
    plan = window._pending_docker_mod_plan
    return DockerLifecycleResult(
        action=DockerLifecycleAction.RECREATE_GAME,
        succeeded=True,
        records={
            "server": ContainerRecord(
                service="server",
                name="fixture-server",
                short_id="a" * 12,
                state=ServiceState.ONLINE,
                health="healthy",
                exit_code=None,
                publishers=(),
                raw_state="running",
            )
        },
        target_identity="docker:fixture-target",
        server_node_options_sha256=hashlib.sha256(
            (plan.docker_node_options or "").encode("utf-8")
        ).hexdigest(),
        game_runtime_identity="b" * 64,
    )


def _set_current_recreated_runtime(
    window: MainWindow,
    result: DockerLifecycleResult,
) -> None:
    server = result.records["server"]  # type: ignore[index]
    window._runtime_snapshot = RuntimeSnapshot(
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
        running_clients=0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        game_container=server.short_id,
        target_identity=result.target_identity,
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
        game_runtime_identity=result.game_runtime_identity,
    )


def _record_started_lifecycle(
    lifecycle: list[tuple[DockerLifecycleAction, object]],
):
    """Simulate the worker's exact attach/finalize boundary in GUI tests."""

    def begin(action, **kwargs) -> bool:
        apply_result = kwargs.get("docker_mod_apply_result")
        if isinstance(apply_result, DockerModApplyResult):
            finalize_docker_mod_override(
                apply_result,
                policy=DockerControlPolicy.MANAGED,
            )
        lifecycle.append((action, kwargs.get("on_complete")))
        return True

    return begin


def test_docker_target_factory_appends_existing_mod_override(qapp, tmp_path: Path) -> None:
    _loader(tmp_path, "Fixture Mod")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    result = apply_docker_mod_override(
        tmp_path,
        ("Fixture Mod",),
        policy=DockerControlPolicy.MANAGED,
    )
    finalize_docker_mod_override(result, policy=DockerControlPolicy.MANAGED)
    window = _window(tmp_path)

    target = window._docker_log_target_factory()()

    assert target.override_files == (docker_mod_override_path(tmp_path),)


def test_docker_log_target_factory_rejects_drifted_owned_override(
    qapp,
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    path = docker_mod_override_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(
        build_docker_mod_override(tmp_path, ("Fixture Mod",)).content.encode("utf-8")
        + b"# not launcher-rendered\n"
    )
    window = _window(tmp_path)

    with pytest.raises(ValueError, match="exact launcher renderer"):
        window._docker_log_target_factory()()


def test_authorized_lifecycle_factory_finalizes_only_after_exact_attach(
    qapp,
    tmp_path: Path,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    result = apply_docker_mod_override(
        tmp_path,
        ("Fixture Mod",),
        policy=DockerControlPolicy.MANAGED,
    )
    window = _window(tmp_path)

    target = window._docker_lifecycle_target_factory(
        docker_mod_apply_result=result,
    )()

    assert target.override_files == (docker_mod_override_path(tmp_path),)
    assert not docker_mod_transaction_path(tmp_path).exists()


def test_connect_only_mod_apply_rejects_before_filesystem_or_lifecycle(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path, "connect_only")
    denials: list[str] = []
    window._docker_unavailable = denials.append
    window._begin_docker_lifecycle = lambda _action: pytest.fail("lifecycle mutation")
    monkeypatch.setattr(
        app_module,
        "apply_docker_mod_override",
        lambda *_args, **_kwargs: pytest.fail("Compose-state mutation"),
    )

    window._on_mods_apply_restart()

    assert denials == ["Connect-only Docker mode cannot change mod or Compose state."]
    assert not docker_mod_override_path(tmp_path).exists()


def test_active_lifecycle_blocks_managed_mod_override_before_mutation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    window._lifecycle_thread = object()
    notices: list[str] = []
    window._docker_unavailable = notices.append
    window._restart_docker_monitor_for_compose_change = lambda: pytest.fail(
        "monitor restart"
    )
    window._begin_docker_lifecycle = lambda _action: pytest.fail(
        "lifecycle mutation"
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: pytest.fail("confirmation after occupied slot"),
    )
    monkeypatch.setattr(
        app_module,
        "apply_docker_mod_override",
        lambda *_args, **_kwargs: pytest.fail("Compose-state mutation"),
    )

    window._on_mods_apply_restart()

    assert notices == [
        "Another service or Docker tool operation is already running."
    ]
    assert not docker_mod_override_path(tmp_path).exists()


def test_managed_mod_apply_requires_confirmation_before_override_write(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[DockerLifecycleAction] = []
    window._begin_docker_lifecycle = (
        lambda action, **_kwargs: lifecycle.append(action) or True
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    window._on_mods_apply_restart()

    assert lifecycle == []
    assert not docker_mod_override_path(tmp_path).exists()


def test_managed_mod_apply_writes_override_then_requests_exact_recreation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    window._begin_docker_lifecycle = _record_started_lifecycle(lifecycle)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window._on_mods_apply_restart()

    assert docker_mod_override_path(tmp_path).is_file()
    assert len(lifecycle) == 1
    assert lifecycle[0][0] is DockerLifecycleAction.RECREATE_GAME
    completion = lifecycle[0][1]
    assert callable(completion)
    assert window._mod_lifecycle_lease is not None
    result = _successful_recreate_result(window)
    window._pending_docker_mod_lifecycle_result = result
    _set_current_recreated_runtime(window, result)
    completion(True)
    assert window._current_mod_runtime_snapshot is not None
    assert window._current_mod_runtime_snapshot.runtime_identity == "b" * 64
    assert window._attested_docker_target_identity == "docker:fixture-target"
    assert window._attested_docker_container_id == "a" * 12
    assert window._mod_lifecycle_lease is None
    assert window._mods_page.snapshots[-1] == window._current_mod_runtime_snapshot


@pytest.mark.parametrize("with_prior_override", [False, True])
def test_managed_mod_apply_fingerprints_before_override_mutation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_prior_override: bool,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    path = docker_mod_override_path(tmp_path)
    if with_prior_override:
        prior = apply_docker_mod_override(
            tmp_path,
            ("Fixture Mod",),
            policy=DockerControlPolicy.MANAGED,
        )
        finalize_docker_mod_override(prior, policy=DockerControlPolicy.MANAGED)
        before = path.read_bytes()
    else:
        before = None
    loader = tmp_path / "mods" / "Fixture Mod" / "loader.js"
    loader.write_bytes(b"x" * (MAX_LOADER_PAYLOAD_BYTES + 1))
    window = _window(tmp_path)
    failures: list[str] = []
    window._begin_docker_lifecycle = lambda *_args, **_kwargs: pytest.fail(
        "lifecycle started after failed preflight"
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()

    assert (path.read_bytes() if path.exists() else None) == before
    assert window._mod_lifecycle_lease is None
    assert failures and "byte limit" in failures[-1]


def test_post_write_plan_failure_withdraws_new_override_before_lifecycle(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    failures: list[str] = []
    real_builder = app_module.build_mod_runtime_plan

    def fail_final_plan(*args, **kwargs):
        if kwargs.get("docker_override_material") is None:
            raise ModRuntimeStateError("simulated post-write fingerprint failure")
        return real_builder(*args, **kwargs)

    window._begin_docker_lifecycle = lambda *_args, **_kwargs: pytest.fail(
        "lifecycle started after failed final freeze"
    )
    monkeypatch.setattr(app_module, "build_mod_runtime_plan", fail_final_plan)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()

    assert not docker_mod_override_path(tmp_path).exists()
    assert window._mod_lifecycle_lease is None
    assert failures and "simulated post-write" in failures[-1]


def test_post_write_failure_and_failed_rollback_leave_durable_attach_block(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    window = _window(tmp_path)
    failures: list[str] = []
    real_builder = app_module.build_mod_runtime_plan

    def fail_final_plan(*args, **kwargs):
        if kwargs.get("docker_override_material") is None:
            raise ModRuntimeStateError("simulated post-write plan rejection")
        return real_builder(*args, **kwargs)

    window._begin_docker_lifecycle = lambda *_args, **_kwargs: pytest.fail(
        "lifecycle started after rejected final plan"
    )
    monkeypatch.setattr(app_module, "build_mod_runtime_plan", fail_final_plan)
    monkeypatch.setattr(
        app_module,
        "rollback_docker_mod_override",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("simulated rollback denial")
        ),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()

    assert docker_mod_override_path(tmp_path).is_file()
    assert docker_mod_transaction_path(tmp_path).is_file()
    assert failures and "blocked until" in failures[-1]
    with pytest.raises(ValueError, match="unfinished"):
        window._docker_log_target_factory()()


def test_lifecycle_start_failure_withdraws_new_override(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    failures: list[str] = []
    window._begin_docker_lifecycle = lambda *_args, **_kwargs: False
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()

    assert not docker_mod_override_path(tmp_path).exists()
    assert window._pending_docker_mod_plan is None
    assert window._mod_lifecycle_lease is None
    assert failures and "prior override was restored" in failures[-1]


def test_lifecycle_start_exception_withdraws_new_override_and_cleans_state(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    failures: list[str] = []

    def raise_start(*_args, **_kwargs) -> bool:
        raise RuntimeError("simulated worker startup exception")

    window._begin_docker_lifecycle = raise_start
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()

    assert not docker_mod_override_path(tmp_path).exists()
    assert not docker_mod_transaction_path(tmp_path).exists()
    assert window._pending_docker_mod_plan is None
    assert window._mod_lifecycle_lease is None
    assert failures and "simulated worker startup" in failures[-1]


def test_actual_qthread_start_exception_restores_docker_snapshot_and_metadata(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    prior = RuntimeSnapshot(
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
        running_clients=0,
        backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_control_policy=DockerControlPolicy.MANAGED,
        target_identity="docker:fixture-target",
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
    )
    prior_mod_runtime = object()
    applied: list[RuntimeSnapshot] = []
    window._runtime_snapshot = prior
    window._current_mod_runtime_snapshot = prior_mod_runtime
    window._attested_docker_target_identity = "docker:fixture-target"
    window._attested_docker_container_id = "a" * 12
    window._lifecycle_thread = None
    window._apply_runtime_snapshot = applied.append

    class FailingStartThread(QThread):
        def start(self, *args, **kwargs) -> None:  # type: ignore[override]
            del args, kwargs
            raise RuntimeError("simulated QThread start failure")

    monkeypatch.setattr(app_module, "QThread", FailingStartThread)

    with pytest.raises(RuntimeError, match="simulated QThread start failure"):
        window._begin_docker_lifecycle(DockerLifecycleAction.START_GAME)

    assert window._runtime_snapshot == prior
    assert applied[-1] == prior
    assert window._current_mod_runtime_snapshot is prior_mod_runtime
    assert window._mods_page.snapshots[-1] is prior_mod_runtime
    assert window._attested_docker_target_identity == "docker:fixture-target"
    assert window._attested_docker_container_id == "a" * 12
    assert window._lifecycle_thread is None
    assert window._lifecycle_worker is None
    assert window._docker_lifecycle_snapshot is None
    assert window._docker_lifecycle_generation is None
    assert window._docker_lifecycle_target is None
    assert window._docker_lifecycle_action is None
    assert window._docker_lifecycle_observed_target is None
    assert window._docker_lifecycle_completion is None
    assert window._docker_lifecycle_suppress_failure_dialog is False


def test_lifecycle_start_failure_refuses_rollback_over_external_drift(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    window = _window(tmp_path)
    failures: list[str] = []
    path = docker_mod_override_path(tmp_path)

    def drift_then_fail(*_args, **_kwargs) -> bool:
        path.write_bytes(path.read_bytes() + b"# external drift\n")
        return False

    window._begin_docker_lifecycle = drift_then_fail
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()

    assert path.read_bytes().endswith(b"# external drift\n")
    assert failures and "blocked until" in failures[-1]
    with pytest.raises(ValueError):
        window._docker_log_target_factory()()


def test_unchanged_managed_mod_override_retries_recreation(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    window._begin_docker_lifecycle = _record_started_lifecycle(lifecycle)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    window._on_mods_apply_restart()
    first_completion = lifecycle[-1][1]
    assert callable(first_completion)
    first_result = _successful_recreate_result(window)
    window._pending_docker_mod_lifecycle_result = first_result
    _set_current_recreated_runtime(window, first_result)
    first_completion(True)

    window._on_mods_apply_restart()

    assert [action for action, _callback in lifecycle] == [
        DockerLifecycleAction.RECREATE_GAME,
        DockerLifecycleAction.RECREATE_GAME,
    ]
    second_completion = lifecycle[-1][1]
    assert callable(second_completion)
    second_result = _successful_recreate_result(window)
    window._pending_docker_mod_lifecycle_result = second_result
    _set_current_recreated_runtime(window, second_result)
    second_completion(True)
    assert window._mod_lifecycle_lease is None


def test_unverified_recreation_keeps_lock_through_corrective_stop(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    failures: list[str] = []
    window._begin_docker_lifecycle = _record_started_lifecycle(lifecycle)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()
    recreate_completion = lifecycle[-1][1]
    assert callable(recreate_completion)
    window._pending_docker_mod_lifecycle_result = replace(
        _successful_recreate_result(window),
        game_runtime_identity=None,
    )
    recreate_completion(True)

    assert [action for action, _callback in lifecycle] == [
        DockerLifecycleAction.RECREATE_GAME,
        DockerLifecycleAction.STOP_GAME,
    ]
    assert window._mod_lifecycle_lease is not None
    assert window._pending_docker_mod_plan is not None
    assert failures and "being stopped" in failures[0]

    stop_completion = lifecycle[-1][1]
    assert callable(stop_completion)
    stop_completion(True)

    assert window._mod_lifecycle_lease is None
    assert window._pending_docker_mod_plan is None


def test_corrective_stop_start_exception_releases_mod_transaction(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    failures: list[tuple[str, str]] = []
    window._begin_docker_lifecycle = _record_started_lifecycle(lifecycle)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, title, message: failures.append((title, message)),
    )

    window._on_mods_apply_restart()
    recreate_completion = lifecycle[-1][1]
    assert callable(recreate_completion)
    window._pending_docker_mod_lifecycle_result = replace(
        _successful_recreate_result(window),
        game_runtime_identity=None,
    )

    def fail_corrective_start(*_args, **_kwargs) -> bool:
        raise RuntimeError("simulated corrective QThread start failure")

    window._begin_docker_lifecycle = fail_corrective_start
    recreate_completion(True)

    assert window._pending_docker_mod_plan is None
    assert window._pending_docker_mod_apply_result is None
    assert window._mod_lifecycle_lease is None
    assert failures[0][0] == "Docker Mod Verification Failed"
    assert failures[-1][0] == "Docker Corrective Stop Failed"
    assert "simulated corrective QThread start failure" in failures[-1][1]


def test_recreate_completion_rejects_container_restarted_after_result(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    failures: list[str] = []
    window._begin_docker_lifecycle = _record_started_lifecycle(lifecycle)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )
    monkeypatch.setattr(
        app_module,
        "write_mod_runtime_snapshot",
        lambda *_args: pytest.fail("stale runtime evidence was persisted"),
    )

    window._on_mods_apply_restart()
    recreate_completion = lifecycle[-1][1]
    assert callable(recreate_completion)
    result = _successful_recreate_result(window)
    window._pending_docker_mod_lifecycle_result = result
    _set_current_recreated_runtime(window, result)

    # Compose may retain the short ID across an external restart. The monitor's
    # privacy-safe id+StartedAt digest is what distinguishes this runtime.
    window._on_docker_observation(
        DockerObservation(
            game=ServiceState.ONLINE,
            market=ServiceState.ONLINE,
            game_identity="a" * 12,
            game_runtime_identity="c" * 64,
            target_identity=result.target_identity,
            settings_identity=window._docker_monitor_settings_identity(),
            monitor_generation=window._monitor_generation,
        ),
        window._monitor_generation,
    )
    recreate_completion(True)

    assert [action for action, _callback in lifecycle] == [
        DockerLifecycleAction.RECREATE_GAME,
        DockerLifecycleAction.STOP_GAME,
    ]
    assert window._current_mod_runtime_snapshot is None
    assert window._mod_lifecycle_lease is not None
    assert failures and "changed before" in failures[0]


def test_fresh_post_result_replacement_enters_corrective_stop_path(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    failures: list[str] = []
    window._begin_docker_lifecycle = _record_started_lifecycle(lifecycle)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )
    monkeypatch.setattr(
        app_module,
        "write_mod_runtime_snapshot",
        lambda *_args: pytest.fail("replacement runtime evidence was persisted"),
    )
    window._on_mods_apply_restart()
    recreate_completion = lifecycle[-1][1]
    assert callable(recreate_completion)
    result = _successful_recreate_result(window)
    window._pending_docker_mod_lifecycle_result = result
    _set_current_recreated_runtime(window, result)

    token = object()
    window._pending_docker_mod_observation_token = token
    window._pending_docker_mod_observation_floor_ns = 100
    window._pending_docker_mod_observation_completion = recreate_completion
    lifecycle_thread = _FinishedThread()
    window._lifecycle_thread = lifecycle_thread
    window._lifecycle_worker = object()
    window._lifecycle_result_received = False
    window._lifecycle_thread_finished = True
    window._lifecycle_after_thread_callback = None
    replacement = DockerObservation(
        game=ServiceState.ONLINE,
        market=ServiceState.ONLINE,
        # A same-short-ID restart is still a different runtime because the
        # inspect-derived full-ID+StartedAt+environment digest changed.
        game_identity="a" * 12,
        game_runtime_identity="c" * 64,
        target_identity=result.target_identity,
        settings_identity=window._docker_monitor_settings_identity(),
        monitor_generation=window._monitor_generation,
        sample_started_monotonic_ns=101,
    )

    window._on_docker_mod_runtime_observation_sample(
        replacement,
        window._monitor_generation,
    )

    assert [action for action, _callback in lifecycle] == [
        DockerLifecycleAction.RECREATE_GAME,
        DockerLifecycleAction.STOP_GAME,
    ]
    assert lifecycle_thread.deleted
    assert window._current_mod_runtime_snapshot is None
    assert window._mod_lifecycle_lease is not None
    assert window._pending_docker_mod_plan is not None
    assert failures and "being stopped" in failures[0]


def test_unconsumed_recreate_target_failure_rolls_back_and_clears_marker(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _loader(tmp_path, "Fixture Mod")
    window = _window(tmp_path)
    lifecycle: list[tuple[DockerLifecycleAction, object]] = []
    failures: list[str] = []
    # The QThread starts, but its deferred target factory fails before it can
    # validate/finalize the Apply result or run any Docker command.
    window._begin_docker_lifecycle = lambda action, **kwargs: (
        lifecycle.append((action, kwargs.get("on_complete"))) or True
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: failures.append(message),
    )

    window._on_mods_apply_restart()
    completion = lifecycle[-1][1]
    assert callable(completion)
    assert docker_mod_transaction_path(tmp_path).is_file()

    completion(False)

    assert not docker_mod_override_path(tmp_path).exists()
    assert not docker_mod_transaction_path(tmp_path).exists()
    assert window._pending_docker_mod_plan is None
    assert window._mod_lifecycle_lease is None
    assert failures and "prior state was restored" in failures[-1]
