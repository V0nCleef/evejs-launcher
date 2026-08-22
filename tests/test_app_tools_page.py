"""Integration tests for the Tools page in MainWindow."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from src import app as app_module
from src import config
from src.app import MainWindow
from src.constants import Page
from src.core.tool_catalog import ResolvedTool, ToolAction, supported_tool_definitions


def _install_all_wrappers(root: Path) -> None:
    for definition in supported_tool_definitions():
        wrapper = root / "tools" / definition.relative_entrypoint
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")


def _window_config(root: Path) -> dict:
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": str(root),
            "client_path": "",
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    return cfg


@pytest.fixture
def tools_window(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MainWindow:
    _install_all_wrappers(tmp_path)
    cfg = _window_config(tmp_path)
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    yield window
    window.deleteLater()


def test_main_window_stack_and_navigation_follow_page_enum_order(
    tools_window: MainWindow,
) -> None:
    assert tools_window._stack.count() == len(Page)
    assert tools_window._stack.widget(int(Page.HOME)) is tools_window._home_page
    assert tools_window._stack.widget(int(Page.CHARACTERS)) is tools_window._characters_page
    assert tools_window._stack.widget(int(Page.MODS)) is tools_window._mods_page
    assert tools_window._stack.widget(int(Page.TOOLS)) is tools_window._tools_page
    assert tools_window._stack.widget(int(Page.SETTINGS)) is tools_window._settings_page
    assert tools_window._nav.nav_group.button(int(Page.TOOLS)).text() == "Tools"


def test_tools_empty_state_routes_to_settings_and_syncs_navigation(
    tools_window: MainWindow,
) -> None:
    tools_window._switch_page(int(Page.TOOLS))

    tools_window._tools_page.open_settings_requested.emit()

    assert tools_window._stack.currentIndex() == int(Page.SETTINGS)
    assert tools_window._nav.btn_settings.isChecked()


def test_visiting_tools_refreshes_the_current_configured_root(
    tools_window: MainWindow,
) -> None:
    calls: list[str | None] = []
    tools_window._tools_page.refresh_tools = (
        lambda root=None: calls.append(None if root is None else str(root))
    )

    tools_window._switch_page(int(Page.TOOLS))

    assert calls == [str(tools_window._cfg["evejs_root"])]


def test_root_change_refreshes_tools_alongside_other_root_dependent_views(
    tools_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    new_root = tmp_path / "new-root"
    tools_calls: list[str] = []
    mods_calls: list[bool] = []
    cache_calls: list[str] = []
    tools_window._tools_page.set_evejs_root = lambda root: tools_calls.append(str(root))
    tools_window._mods_page.refresh_mods = lambda: mods_calls.append(True)
    tools_window._apply_runtime_settings = lambda: None
    tools_window._home_page.set_server_mode = lambda _mode: None
    tools_window._refresh_characters = lambda: None
    monkeypatch.setattr(
        app_module,
        "clear_solar_system_name_cache",
        lambda: cache_calls.append("systems"),
    )
    monkeypatch.setattr(
        app_module.PortraitCache,
        "clear",
        lambda: cache_calls.append("portraits"),
    )

    tools_window._on_settings_saved({"evejs_root": str(new_root)})

    assert tools_calls == [str(new_root)]
    # Withdraw target-bound runtime evidence immediately, then rescan once the
    # Mods page has switched to the replacement root.
    assert mods_calls == [True, True]
    assert cache_calls == ["systems", "portraits"]


def test_unrelated_settings_save_does_not_rescan_tools_root(
    tools_window: MainWindow,
) -> None:
    tools_calls: list[str] = []
    tools_window._tools_page.set_evejs_root = lambda root: tools_calls.append(str(root))
    tools_window._apply_runtime_settings = lambda: None
    tools_window._home_page.set_server_mode = lambda _mode: None
    tools_window._refresh_characters = lambda: None

    tools_window._on_settings_saved({"animations_enabled": False})

    assert tools_calls == []


def test_one_normal_tool_click_spawns_once_and_reports_launched(
    qapp: QApplication,
    tools_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def fake_launch(entrypoint: Path, arguments: tuple[str, ...]) -> object:
        calls.append((entrypoint, arguments))
        return object()

    monkeypatch.setattr(app_module, "launch_tool_wrapper", fake_launch)
    card = tools_window._tools_page.card_for("client-setup-wizard")

    card.action_buttons["launch"].click()
    qapp.processEvents()

    assert calls == [(card.tool.absolute_entrypoint, ())]
    assert card.status_label.text() == "Launched"
    assert tools_window._server_proc is None
    assert tools_window._market_proc is None


def test_central_handler_cancels_destructive_action_before_spawn(
    tools_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: calls.append((entrypoint, arguments)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    card = tools_window._tools_page.card_for("reset-local-databases")
    reset_action = next(
        action for action in card.tool.definition.actions if action.id == "reset"
    )

    tools_window._on_tool_launch_requested(card.tool, reset_action)

    assert calls == []


def test_central_handler_uses_curated_arguments_for_programmatic_action(
    tools_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: calls.append((entrypoint, arguments)),
    )
    card = tools_window._tools_page.card_for("client-setup-wizard")
    forged_action = ToolAction(
        id="launch",
        label="Forged launch",
        arguments=("/whatif & whoami",),
    )

    tools_window._on_tool_launch_requested(card.tool, forged_action)

    assert calls == [(card.tool.absolute_entrypoint, ())]


def test_central_handler_uses_current_curated_wrapper_path(
    tools_window: MainWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: calls.append((entrypoint, arguments)),
    )
    card = tools_window._tools_page.card_for("client-setup-wizard")
    action = card.tool.definition.actions[0]
    forged_tool = ResolvedTool(
        definition=card.tool.definition,
        absolute_entrypoint=tmp_path / "outside-tools" / "Run.bat",
        available=True,
    )

    tools_window._on_tool_launch_requested(forged_tool, action)

    assert calls == [(card.tool.absolute_entrypoint, ())]


def test_central_handler_rejects_action_not_in_curated_definition(
    tools_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: calls.append((entrypoint, arguments)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda _parent, title, message, *_args: warnings.append((title, message)),
    )
    card = tools_window._tools_page.card_for("client-setup-wizard")
    unknown_action = ToolAction(id="not-curated", label="Not curated")

    tools_window._on_tool_launch_requested(card.tool, unknown_action)

    assert calls == []
    assert warnings == [
        (
            "Unsupported Tool Action",
            "This tool action is not in the reviewed launcher catalog",
        )
    ]


def test_central_handler_confirmed_reset_launches_without_modifier(
    tools_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(
        app_module,
        "launch_tool_wrapper",
        lambda entrypoint, arguments: calls.append((entrypoint, arguments)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    card = tools_window._tools_page.card_for("reset-local-databases")
    reset_action = next(
        action for action in card.tool.definition.actions if action.id == "reset"
    )

    tools_window._on_tool_launch_requested(card.tool, reset_action)

    assert calls == [(card.tool.absolute_entrypoint, ())]


def test_tool_spawn_failure_is_logged_inline_and_shown_contextually(
    qapp: QApplication,
    tools_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[tuple[str, str]] = []

    def fail_launch(_entrypoint: Path, _arguments: tuple[str, ...]) -> object:
        raise RuntimeError("CreateProcess failed")

    monkeypatch.setattr(app_module, "launch_tool_wrapper", fail_launch)
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda _parent, title, message: errors.append((title, message)),
    )
    card = tools_window._tools_page.card_for("client-setup-wizard")

    card.action_buttons["launch"].click()
    qapp.processEvents()

    assert card.status_label.text() == "Launch failed"
    assert errors == [
        (
            "Tool Launch Failed",
            "Client Setup Wizard could not be launched.\n\nCreateProcess failed",
        )
    ]
