"""Application propagation contracts for backend-aware Mods and Tool Deck pages."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QMainWindow

from src import config
from src.app import MainWindow
from src.i18n import set_language
from src.core.runtime.docker_tools import DockerToolAction
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.core.tool_catalog import ToolDispatchKind, supported_tool_definitions
from src.pages.mods_page import ModsPage
from src.pages.tools_page import ToolsPage
from src.widgets.nav_panel import NavPanel
from src.widgets.status_bar import StatusBar


class _Button:
    def __init__(self) -> None:
        self.enabled = True
        self.text = ""
        self.tooltip = ""

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setText(self, text: str) -> None:
        self.text = text

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip


class _Nav:
    def __init__(self) -> None:
        self.btn_server = _Button()
        self.btn_market = _Button()
        self.btn_characters = _Button()
        self.btn_mods = _Button()
        self.btn_tools = _Button()
        self.btn_kill_all = _Button()

    def set_badge_count(self, *_args: object) -> None:
        pass


class _Status:
    def set_server_state(self, *_args: object, **_kwargs: object) -> None:
        pass

    def set_market_state(self, *_args: object, **_kwargs: object) -> None:
        pass

    def set_client_count(self, *_args: object) -> None:
        pass


class _Home:
    def __init__(self) -> None:
        self.btn_start_servers = _Button()
        self.btn_stop_servers = _Button()

    def apply_runtime_snapshot(self, _snapshot: RuntimeSnapshot) -> None:
        pass

    def set_server_mode(self, _mode: str) -> None:
        pass


class _Characters:
    def set_launch_available(self, *_args: object) -> None:
        pass


class _PageRecorder:
    def __init__(self) -> None:
        self.roots: list[str] = []
        self.contexts: list[tuple[object, ...]] = []

    def set_evejs_root(self, root: str | Path) -> None:
        self.roots.append(str(root))

    def set_runtime_context(
        self,
        *context: object,
        compose_file: str | Path | None = None,
    ) -> None:
        if compose_file is not None:
            context = (*context, str(compose_file))
        self.contexts.append(context)



def _window(root: Path) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._cfg.update(
        {
            "evejs_root": str(root),
            "docker_compose_file": str(root / "compose.yaml"),
            "docker_control_policy": "connect_only",
            "runtime_backend": "native",
        }
    )
    window._nav = _Nav()
    window._status_bar = _Status()
    window._home_page = _Home()
    window._characters_page = _Characters()
    window._mods_page = _PageRecorder()
    window._tools_page = _PageRecorder()
    window._monitor_generation = 0
    return window



def _close_window(window: MainWindow) -> None:
    window.deleteLater()
    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)



def _snapshot(
    backend: RuntimeBackend,
    policy: DockerControlPolicy = DockerControlPolicy.CONNECT_ONLY,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        ServiceState.OFFLINE,
        ServiceState.OFFLINE,
        0,
        backend=backend,
        docker_control_policy=policy,
    )



def _install_fixture(root: Path) -> None:
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    loader = root / "mods" / "Fixture Mod" / "loader.js"
    loader.parent.mkdir(parents=True)
    loader.write_text("module.exports = {};\n", encoding="utf-8")
    for definition in supported_tool_definitions():
        wrapper = root / "tools" / definition.relative_entrypoint
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")



def test_native_snapshot_pushes_native_page_capabilities_and_keeps_navigation(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)

    try:
        window._apply_runtime_snapshot(_snapshot(RuntimeBackend.NATIVE))

        assert window._mods_page.roots == [str(tmp_path)]
        assert window._mods_page.contexts == [
            (RuntimeBackend.NATIVE, DockerControlPolicy.CONNECT_ONLY)
        ]
        assert window._tools_page.roots == [str(tmp_path)]
        assert window._tools_page.contexts == [
            (
                RuntimeBackend.NATIVE,
                DockerControlPolicy.CONNECT_ONLY,
                str(tmp_path / "compose.yaml"),
            )
        ]
        assert window._nav.btn_mods.enabled
        assert window._nav.btn_tools.enabled
    finally:
        _close_window(window)



def test_managed_docker_snapshot_pushes_compose_capabilities_and_keeps_navigation(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_fixture(tmp_path)
    window = _window(tmp_path)
    window._cfg.update(
        {
            "runtime_backend": "docker_compose",
            "docker_control_policy": "managed",
        }
    )
    window._mods_page = ModsPage(window)
    window._tools_page = ToolsPage(str(tmp_path), parent=window)

    try:
        window._apply_runtime_snapshot(
            _snapshot(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.MANAGED)
        )

        assert window._nav.btn_mods.enabled
        assert window._nav.btn_tools.enabled
        assert window._mods_page._runtime_backend is RuntimeBackend.DOCKER_COMPOSE
        assert window._mods_page._docker_policy is DockerControlPolicy.MANAGED
        database_action = window._tools_page.card_for(
            "local-database-creator"
        ).tool.actions[0]
        assert database_action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE
        assert database_action.docker_action is DockerToolAction.INITIALIZE_DATABASE
    finally:
        _close_window(window)



def test_connect_only_snapshot_keeps_pages_navigable_but_mutation_controls_disabled(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_fixture(tmp_path)
    window = _window(tmp_path)
    window._cfg["runtime_backend"] = "docker_compose"
    window._mods_page = ModsPage(window)
    window._tools_page = ToolsPage(str(tmp_path), parent=window)

    try:
        window._apply_runtime_snapshot(
            _snapshot(
                RuntimeBackend.DOCKER_COMPOSE,
                DockerControlPolicy.CONNECT_ONLY,
            )
        )

        assert window._nav.btn_mods.enabled
        assert window._nav.btn_tools.enabled
        assert not window._mods_page.apply_btn.isEnabled()
        assert all(not row.toggle.isEnabled() for row in window._mods_page._rows)
        database = window._tools_page.card_for("local-database-creator")
        assert not database.tool.available
        assert all(not button.isEnabled() for button in database.action_buttons.values())
        safe_client = window._tools_page.card_for("client-setup-wizard")
        assert safe_client.action_buttons["launch"].isEnabled()
    finally:
        _close_window(window)



def test_repeated_identical_snapshots_do_not_resync_runtime_pages(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    snapshot = _snapshot(RuntimeBackend.NATIVE)

    try:
        window._apply_runtime_snapshot(snapshot)
        window._apply_runtime_snapshot(snapshot)

        assert len(window._mods_page.roots) == 1
        assert len(window._mods_page.contexts) == 1
        assert len(window._tools_page.roots) == 1
        assert len(window._tools_page.contexts) == 1
    finally:
        _close_window(window)



def test_root_change_updates_both_runtime_pages(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    window = _window(first_root)
    snapshot = _snapshot(RuntimeBackend.NATIVE)

    try:
        window._apply_runtime_snapshot(snapshot)
        window._cfg["evejs_root"] = str(second_root)
        window._apply_runtime_snapshot(snapshot)

        assert window._mods_page.roots == [str(first_root), str(second_root)]
        assert window._tools_page.roots == [str(first_root), str(second_root)]
    finally:
        _close_window(window)



def test_backend_policy_and_compose_changes_refresh_tool_deck_context(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)

    try:
        window._apply_runtime_snapshot(_snapshot(RuntimeBackend.NATIVE))
        replacement = tmp_path / "compose.replacement.yaml"
        window._cfg["docker_compose_file"] = str(replacement)
        window._apply_runtime_snapshot(
            _snapshot(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.MANAGED)
        )
        window._apply_runtime_snapshot(
            _snapshot(
                RuntimeBackend.DOCKER_COMPOSE,
                DockerControlPolicy.CONNECT_ONLY,
            )
        )

        assert window._tools_page.contexts == [
            (
                RuntimeBackend.NATIVE,
                DockerControlPolicy.CONNECT_ONLY,
                str(tmp_path / "compose.yaml"),
            ),
            (
                RuntimeBackend.DOCKER_COMPOSE,
                DockerControlPolicy.MANAGED,
                str(replacement),
            ),
            (
                RuntimeBackend.DOCKER_COMPOSE,
                DockerControlPolicy.CONNECT_ONLY,
                str(replacement),
            ),
        ]
    finally:
        _close_window(window)



def test_unrelated_settings_change_does_not_rescan_or_resync_runtime_pages(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._runtime_snapshot = _snapshot(RuntimeBackend.NATIVE)
    window._settings_generation = 0
    window._apply_runtime_settings = lambda: None
    window._effective_server_mode_label = lambda: "Fixture"
    window._refresh_characters = lambda: None

    try:
        window._apply_runtime_snapshot(window._runtime_snapshot)
        before = (
            len(window._mods_page.roots),
            len(window._mods_page.contexts),
            len(window._tools_page.roots),
            len(window._tools_page.contexts),
        )

        window._on_settings_saved({"animations_enabled": False})

        assert before == (1, 1, 1, 1)
        assert (
            len(window._mods_page.roots),
            len(window._mods_page.contexts),
            len(window._tools_page.roots),
            len(window._tools_page.contexts),
        ) == before
    finally:
        _close_window(window)


def test_language_selection_persists_and_refreshes_cached_runtime_labels(
    qapp: QApplication,
    monkeypatch,
) -> None:
    set_language("en")
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._runtime_snapshot = _snapshot(RuntimeBackend.NATIVE)
    window._nav = NavPanel(window)
    window._status_bar = StatusBar(window)
    saved: list[dict] = []
    applied: list[RuntimeSnapshot] = []
    monkeypatch.setattr(config, "save", lambda cfg: saved.append(deepcopy(cfg)))
    window._apply_runtime_snapshot = applied.append
    window._status_bar.language_changed.connect(window._on_language_changed)

    try:
        window._status_bar.language_combo.setCurrentIndex(
            window._status_bar.language_combo.findData("zh_CN")
        )
        qapp.processEvents()

        assert window._cfg["language"] == "zh_CN"
        assert saved[-1]["language"] == "zh_CN"
        assert applied == [window._runtime_snapshot]
        assert window._nav.btn_home.text() == "首页"
    finally:
        set_language("en")
        _close_window(window)


def test_language_selection_still_applies_when_config_save_fails(
    qapp: QApplication,
    monkeypatch,
) -> None:
    set_language("en")
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._runtime_snapshot = _snapshot(RuntimeBackend.NATIVE)
    window._nav = NavPanel(window)
    window._status_bar = StatusBar(window)
    applied: list[RuntimeSnapshot] = []

    def fail_save(_cfg: dict) -> None:
        raise OSError("simulated read-only profile")

    monkeypatch.setattr(config, "save", fail_save)
    window._apply_runtime_snapshot = applied.append

    try:
        window._on_language_changed("zh_CN")

        assert window._cfg["language"] == "zh_CN"
        assert applied == [window._runtime_snapshot]
        assert window._nav.btn_home.text() == "首页"
    finally:
        set_language("en")
        _close_window(window)
