"""Focused coverage for the Docker compatibility choice and continuation."""
from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QLabel, QMainWindow

from src import app as app_module
from src.app import MainWindow
from src.core.mod_evejs_compatibility import (
    EvejsModCompatibility,
    EvejsModCompatibilityStatus,
)
from src.core.runtime.docker_controller import DockerLifecycleAction
from src.core.mod_manifest import ActivationKind, Mod
from src.core.service_status import ServiceState
from src.ui.mod_start_compatibility import ModStartCompatibilityDialog


def test_docker_compatibility_dialog_discloses_client_disconnect(qapp) -> None:
    item = EvejsModCompatibility(
        mod_id="probe",
        mod_name="Compatibility Probe",
        mod_version="1.0.0",
        status=EvejsModCompatibilityStatus.UNSUPPORTED,
        installed_version="0.12.9",
        declared_versions=("0.12.8",),
        message="Unsupported declaration.",
    )
    dialog = ModStartCompatibilityDialog((item,), "0.12.9", docker_mode=True)
    dialog.show()
    qapp.processEvents()
    labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))

    assert "may still work" in labels
    assert "disconnects connected clients" in labels
    assert dialog.cancel_button.isDefault()
    assert dialog.cancel_button.hasFocus()
    assert not dialog.run_anyway_button.autoDefault()
    assert not dialog.disable_button.autoDefault()
    assert all(
        label.textFormat().name == "PlainText"
        for label in dialog.findChildren(QLabel)
    )
    dialog.close()


def _start_stack_window(qapp) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "runtime_backend": "docker_compose",
        "docker_control_policy": "managed",
        "evejs_root": ".",
    }
    window._docker_managed = lambda: True
    window._docker_mode = lambda: True
    window._lifecycle_active = lambda: False
    window._current_observed_docker_target_identity = lambda: "docker:observed"
    window._docker_target_identity = lambda: ("docker_compose", "managed", ".")
    window._docker_cached_snapshot = lambda: SimpleNamespace(
        market=ServiceState.OFFLINE
    )
    window._applicable_runtime_mods = lambda *_args, **_kwargs: ()
    return window


def test_disable_continuation_recreates_then_starts_market_and_keeps_callback(
    qapp,
) -> None:
    window = _start_stack_window(qapp)
    captured: dict[str, object] = {}
    completions: list[bool] = []
    window._prepare_mod_start_compatibility = lambda *_args, **kwargs: (
        captured.update(kwargs) or ("queued", None)
    )

    mod_apply_calls: list[dict[str, object]] = []

    def apply_after_disable(**kwargs):
        mod_apply_calls.append(kwargs)
        kwargs["_on_complete"](True)
        return True

    window._on_mods_apply_restart = apply_after_disable
    market_calls: list[dict[str, object]] = []

    def start_market(action, **kwargs):
        market_calls.append({"action": action, **kwargs})
        kwargs["on_complete"](True)
        return True

    window._begin_docker_lifecycle = start_market

    original_target = MainWindow._begin_docker_lifecycle
    assert original_target(
        window,
        DockerLifecycleAction.START_STACK,
        expected_target_identity="docker:observed",
        on_complete=completions.append,
        suppress_failure_dialog=True,
    )
    continuation = captured["continuation"]
    assert callable(continuation)
    continuation()

    assert mod_apply_calls[0]["_confirmed"] is True
    assert mod_apply_calls[0]["_expected_target_identity"] == "docker:observed"
    assert mod_apply_calls[0]["_suppress_failure_dialog"] is True
    assert market_calls[0]["action"] is DockerLifecycleAction.START_MARKET
    assert market_calls[0]["expected_target_identity"] == "docker:observed"
    assert market_calls[0]["suppress_failure_dialog"] is True
    assert completions == [True]
    window.deleteLater()


def test_disable_continuation_reports_recreate_failure_to_original_callback(qapp) -> None:
    window = _start_stack_window(qapp)
    captured: dict[str, object] = {}
    completions: list[bool] = []
    window._prepare_mod_start_compatibility = lambda *_args, **kwargs: (
        captured.update(kwargs) or ("queued", None)
    )
    window._begin_docker_lifecycle = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("Market must not start after failure"))
    )

    def apply_after_disable(**kwargs):
        kwargs["_on_complete"](False)
        return True

    window._on_mods_apply_restart = apply_after_disable
    assert MainWindow._begin_docker_lifecycle(
        window,
        DockerLifecycleAction.START_STACK,
        on_complete=completions.append,
    )
    continuation = captured["continuation"]
    assert callable(continuation)
    continuation()

    assert completions == [False]
    window.deleteLater()


def test_disable_batch_runs_affected_mods_in_reverse_runtime_order_after_teardown(
    qapp,
    monkeypatch,
    tmp_path,
) -> None:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {"runtime_backend": "native", "evejs_root": str(tmp_path)}
    window._docker_mode = lambda: False
    window._close_in_progress = False

    class Coordinator:
        _refresh_pending = False

        def __init__(self):
            self.operation = None
            self.callback = None
            self.busy = True

        def _context(self, mod):
            return mod.id

        def run(self, operation, callback):
            self.operation = operation
            self.callback = callback
            return True

    coordinator = Coordinator()
    window._mod_coordinator = coordinator
    order: list[str] = []
    monkeypatch.setattr(
        "src.core.mod_operations.change_mod_state",
        lambda mod, enabled, context: order.append(mod.id) or False,
    )
    scheduled: list[object] = []
    monkeypatch.setattr(app_module.QTimer, "singleShot", lambda _ms, callback: scheduled.append(callback))
    messages: list[str] = []
    window._show_mod_compatibility_message = lambda _title, message: messages.append(message)
    resumed: list[str] = []
    base = Mod("Unsupported base", tmp_path / "base", True, id="base")
    dependent = Mod("Unsupported dependent", tmp_path / "dependent", True, id="dependent")

    assert window._queue_compatibility_mod_disable(
        str(tmp_path),
        "native",
        (base, dependent),  # runtime order is dependencies first
        continuation=lambda: resumed.append("start"),
        error_title="Game Start",
    )
    result = coordinator.operation()
    coordinator.callback(SimpleNamespace(success=True, value=result, error=""))

    assert order == ["dependent", "base"]
    assert coordinator._refresh_pending is True
    assert resumed == []
    assert len(scheduled) == 1
    coordinator.busy = False  # ModCoordinator has cleared its reservation.
    scheduled[0]()

    assert resumed == ["start"]
    assert messages == []
    window.deleteLater()
