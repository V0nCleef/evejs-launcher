"""Native MainWindow orchestration for per-start mod compatibility choices."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import app as app_module
from src import config
from src.app import MainWindow
from src.ui.mod_start_compatibility import ModStartCompatibilityChoice


def _write_loader(
    root: Path,
    mod_id: str,
    evejs_versions: list[str],
    *,
    requires: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Create one real schema-3 loader package under a disposable EveJS root."""
    folder = root / "mods" / mod_id
    folder.mkdir(parents=True)
    manifest_path = folder / "evejs-launcher.mod.json"
    manifest = {
        "schemaVersion": 3,
        "id": mod_id,
        "displayName": mod_id.replace("-", " ").title(),
        "version": "1.0.0",
        "kind": "loader",
        "restart": "game_server",
        "activation": {"strategy": "loader_rename"},
        "compatibility": {"evejsVersions": evejs_versions},
    }
    if requires:
        manifest["requires"] = list(requires)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    loader_path = folder / "loader.js"
    loader_path.write_text(f"// {mod_id} fixture payload\n", encoding="utf-8")
    return manifest_path, loader_path


def _root(path: Path, *, unsupported: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text('{"version":"0.12.9"}', encoding="utf-8")
    _write_loader(path, "fixture-mod", ["0.12.8"] if unsupported else ["0.12.9"])
    return path


class _Lease:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.released = False

    def release(self) -> None:
        self.released = True


def _window(root: Path) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._cfg.update(evejs_root=str(root), runtime_backend="native")
    window._server_proc = None
    window._market_proc = None
    window._server_intent = None
    window._market_intent = None
    window._server_error = None
    window._market_error = None
    window._lifecycle_thread = None
    window._lifecycle_worker = None
    window._mod_lifecycle_lease = None
    window._mod_lifecycle_lease_token = None
    window._mod_operation_request = None
    window._mod_operation_thread = None
    window._close_in_progress = False
    window._docker_mode = lambda: False
    window._lifecycle_active = lambda: (
        window._lifecycle_thread is not None
        or window._mod_operation_request is not None
    )
    window._publish_mod_runtime_snapshot = lambda _snapshot: None
    window._publish_cached_runtime = lambda: None
    window._announce_shipboard = lambda *_args, **_kwargs: None
    window._show_mod_compatibility_message = lambda _title, _message: None
    return window


def _fake_lease_factory(monkeypatch: pytest.MonkeyPatch) -> list[_Lease]:
    leases: list[_Lease] = []

    def acquire(root: str | Path) -> _Lease:
        lease = _Lease(Path(root))
        leases.append(lease)
        return lease

    monkeypatch.setattr(app_module, "acquire_mod_lifecycle_lease", acquire)
    return leases


def _capture_service_workers(window: MainWindow) -> list[object]:
    workers: list[object] = []

    def begin(worker: object, _handler: object) -> None:
        workers.append(worker)
        window._lifecycle_thread = object()
        window._lifecycle_result_received = False
        window._lifecycle_thread_finished = False

    window._begin_lifecycle_worker = begin
    return workers


def _wait_for(qapp: QApplication, predicate, *, timeout_ms: int = 5000) -> None:
    if predicate():
        return
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    poll.stop()
    assert predicate(), "Qt worker did not reach the expected terminal state"


def test_native_cancel_starts_no_services_and_releases_game_lease(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path / "evejs")
    window = _window(root)
    leases = _fake_lease_factory(monkeypatch)
    workers = _capture_service_workers(window)
    starts: list[str] = []
    window._choose_mod_start_compatibility = lambda *_args, **_kwargs: (
        ModStartCompatibilityChoice.CANCEL
    )
    monkeypatch.setattr(app_module, "start_game_server", lambda *_a, **_k: starts.append("game"))
    monkeypatch.setattr(app_module, "start_market_server", lambda *_a, **_k: starts.append("market"))

    try:
        assert not window._start_service_sequence(
            start_market=True,
            start_game=True,
            mode="modded",
            on_ready=None,
            error_title="Game Server Error",
        )
        qapp.processEvents()
        assert not workers
        assert not starts
        assert window._server_intent is None
        assert window._market_intent is None
        assert len(leases) == 1 and leases[0].released
        assert window._mod_lifecycle_lease is None
    finally:
        window._release_mod_lifecycle_lease()
        window.deleteLater()


def test_run_anyway_is_passed_to_one_native_start_and_asked_again_next_time(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path / "evejs")
    leases = _fake_lease_factory(monkeypatch)
    choices = iter(
        (
            ModStartCompatibilityChoice.RUN_ANYWAY,
            ModStartCompatibilityChoice.CANCEL,
        )
    )
    prompts: list[str] = []
    process = object()
    started: list[object] = []
    verified: list[object] = []
    monkeypatch.setattr(
        app_module,
        "start_game_server",
        lambda *_args, **_kwargs: started.append(process) or process,
    )

    first = _window(root)
    first._choose_mod_start_compatibility = lambda _results, version, **_kwargs: (
        prompts.append(version) or next(choices)
    )
    first._verify_native_mod_runtime = lambda _plan, received, consent: (
        verified.append((received, consent)) or SimpleNamespace()
    )
    first_workers = _capture_service_workers(first)
    second = _window(root)
    second._choose_mod_start_compatibility = first._choose_mod_start_compatibility
    second_workers = _capture_service_workers(second)

    try:
        assert first._start_service_sequence(
            start_market=False,
            start_game=True,
            mode="modded",
            on_ready=None,
            error_title="Game Server Error",
        )
        assert len(first_workers) == 1
        worker = first_workers[0]
        assert worker._start_game_fn(str(root), mode="modded") is process
        assert worker._game_runtime_validator(process) is not None
        assert started == [process]
        assert verified and verified[0][0] is process and verified[0][1] is not None

        # A separate start invocation has no stored bypass and prompts again.
        assert not second._start_service_sequence(
            start_market=False,
            start_game=True,
            mode="modded",
            on_ready=None,
            error_title="Game Server Error",
        )
        assert prompts == ["0.12.9", "0.12.9"]
        assert len(second_workers) == 0
        assert started == [process]
        assert len(leases) == 2
        assert not leases[0].released  # retained by the accepted start worker
        assert leases[1].released  # Cancel releases the second invocation's lease
    finally:
        first._release_mod_lifecycle_lease()
        second._release_mod_lifecycle_lease()
        first.deleteLater()
        second.deleteLater()


@pytest.mark.parametrize("change", ["manifest", "loader", "root", "backend"])
def test_native_run_anyway_rejects_selection_or_package_changes_during_dialog(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    root = _root(tmp_path / "evejs")
    other_root = tmp_path / "other-evejs"
    other_root.mkdir()
    manifest_path, loader_path = next(root.glob("mods/*/evejs-launcher.mod.json")), next(
        root.glob("mods/*/loader.js")
    )
    window = _window(root)
    leases = _fake_lease_factory(monkeypatch)
    workers = _capture_service_workers(window)
    messages: list[str] = []
    window._show_mod_compatibility_message = lambda _title, message: messages.append(message)
    starts: list[str] = []
    def choose_after_change(*_args, **_kwargs):
        _change_while_dialog_is_open(
            change,
            window,
            other_root,
            manifest_path,
            loader_path,
        )
        return ModStartCompatibilityChoice.RUN_ANYWAY

    window._choose_mod_start_compatibility = choose_after_change
    monkeypatch.setattr(
        app_module,
        "start_game_server",
        lambda *_a, **_k: starts.append("game"),
    )
    monkeypatch.setattr(
        app_module,
        "start_market_server",
        lambda *_a, **_k: starts.append("market"),
    )

    try:
        assert not window._start_service_sequence(
            start_market=True,
            start_game=True,
            mode="modded",
            on_ready=None,
            error_title="Game Server Error",
        )
        assert not workers
        assert not starts
        assert messages
        assert len(leases) == 1 and leases[0].released
        if change == "root":
            assert window._cfg["evejs_root"] == str(other_root)
        if change == "backend":
            assert window._docker_mode()
    finally:
        window._release_mod_lifecycle_lease()
        window.deleteLater()


def _change_while_dialog_is_open(
    change: str,
    window: MainWindow,
    other_root: Path,
    manifest_path: Path,
    loader_path: Path,
) -> bool:
    if change == "manifest":
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["compatibility"]["evejsVersions"] = ["0.12.9"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif change == "loader":
        loader_path.write_text("// payload changed while modal was open\n", encoding="utf-8")
    elif change == "root":
        window._cfg["evejs_root"] = str(other_root)
    elif change == "backend":
        window._docker_mode = lambda: True
    return True


def test_native_disable_resume_is_suppressed_when_window_close_is_pending(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path / "evejs")
    window = _window(root)
    mod = MainWindow._applicable_runtime_mods(str(root), backend="native")[0]
    continuations: list[bool] = []
    messages: list[str] = []

    class Coordinator:
        _refresh_pending = False

        def _context(self, _mod):
            return object()

        def run(self, operation, callback):
            self.operation = operation
            self.callback = callback
            window._mod_operation_request = object()
            return True

    coordinator = Coordinator()
    window._mod_coordinator = coordinator
    window._show_mod_compatibility_message = lambda _title, message: messages.append(message)
    monkeypatch.setattr(
        "src.core.mod_operations.change_mod_state",
        lambda _mod, _desired, _context: False,
    )

    try:
        assert window._queue_compatibility_mod_disable(
            str(root),
            "native",
            (mod,),
            continuation=lambda: continuations.append(True),
            error_title="Game Server Error",
        )
        outcome = coordinator.operation()
        window._close_in_progress = True
        coordinator.callback(
            SimpleNamespace(success=True, value=outcome, error=None)
        )
        # Match ModCoordinator's teardown: callback first, reservation release
        # second, then the queued continuation gets its turn on the GUI loop.
        window._mod_operation_request = None
        qapp.processEvents()
        assert not continuations
        assert not messages
        assert coordinator._refresh_pending
    finally:
        window.deleteLater()


def test_native_restart_cancel_leaves_launcher_owned_game_process_running(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path / "evejs")
    window = _window(root)
    leases = _fake_lease_factory(monkeypatch)

    class RunningProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    process = RunningProcess()
    window._server_proc = process
    window._server_process_alive = lambda: True
    window._native_game_running = lambda **_kwargs: pytest.fail(
        "an owned process should avoid external-process probing"
    )
    window._resolve_server_start = lambda: ("modded", None)
    window._choose_mod_start_compatibility = lambda *_args, **_kwargs: (
        ModStartCompatibilityChoice.CANCEL
    )
    stops: list[object] = []
    starts: list[object] = []
    window._run_stop_sequence = lambda **kwargs: stops.append(kwargs) or True
    window._start_service_sequence = lambda **kwargs: starts.append(kwargs) or True

    try:
        window._restart_server(allow_force_game_kill=False)
        assert window._server_proc is process
        assert process.poll() is None
        assert not stops
        assert not starts
        assert len(leases) == 1 and leases[0].released
    finally:
        window._release_mod_lifecycle_lease()
        window.deleteLater()
