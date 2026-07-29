"""UI contract tests for the curated EveJS Tool Deck."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QPushButton

from src.core.tool_catalog import TOOL_CATEGORIES, supported_tool_definitions
from src.pages.tools_page import ToolCard, ToolsPage


def _install_all_wrappers(root: Path) -> None:
    for definition in supported_tool_definitions():
        wrapper = root / "tools" / definition.relative_entrypoint
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")


def _show_page(qapp: QApplication, page: ToolsPage, width: int = 756) -> None:
    page.resize(width, 560)
    page.show()
    qapp.processEvents()


def _close_page(page: ToolsPage) -> None:
    page.close()
    page.deleteLater()


def test_valid_root_renders_all_cards_under_ordered_category_sections(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        assert page.visible_tool_ids() == tuple(
            definition.id for definition in supported_tool_definitions()
        )
        assert page.visible_categories() == TOOL_CATEGORIES
        assert page.available_count_label.text() == "11 available"
        assert page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        for definition in supported_tool_definitions():
            card = page.card_for(definition.id)
            assert isinstance(card, ToolCard)
            assert card.name_label.text() == definition.name
            assert card.category_label.text() == definition.category.upper()
            assert card.source_label.text() == definition.source_folder
            assert card.description_label.text() == definition.description
            assert card.status_label.text() == "Ready"
            assert card.status_label.toolTip().endswith(
                str(definition.relative_entrypoint)
            )
            assert tuple(button.text() for button in card.action_buttons.values()) == tuple(
                action.label for action in definition.actions
            )
    finally:
        _close_page(page)


def test_action_buttons_have_a_widget_parent_before_native_size_measurement(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_all_wrappers(tmp_path)
    parentless_actions: list[str] = []
    original_size_hint = QPushButton.sizeHint

    def tracked_size_hint(button: QPushButton):
        if (
            button.objectName().startswith("toolAction-")
            and button.parentWidget() is None
        ):
            parentless_actions.append(button.objectName())
        return original_size_hint(button)

    monkeypatch.setattr(QPushButton, "sizeHint", tracked_size_hint)
    page = ToolsPage(str(tmp_path))

    try:
        assert parentless_actions == []
    finally:
        _close_page(page)


def test_server_config_editor_discloses_its_docker_runtime_requirement(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        card = page.card_for("server-config-editor")

        assert card.risk_label.text() == "Docker for containers"
        assert card.risk_label.isVisible()
        assert "Docker Desktop must be running" in card.description_label.text()
    finally:
        _close_page(page)


def test_search_and_category_filters_hide_nonmatching_cards_and_empty_sections(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        page.search_edit.setText("decompiling")
        qapp.processEvents()
        assert page.visible_tool_ids() == ("client-code-grabber",)
        assert page.visible_categories() == ("Client & Setup",)

        page.search_edit.clear()
        page.category_combo.setCurrentText("Market")
        qapp.processEvents()
        assert page.visible_tool_ids() == (
            "market-seed-builder",
            "market-seed-builder-gui",
            "tq-market-snapshot-seeder-v2",
            "rust-msvc-market-setup",
        )
        assert page.visible_categories() == ("Market",)
    finally:
        _close_page(page)


def test_refresh_re_resolves_wrapper_availability_in_the_existing_page(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    tools_root = tmp_path / "tools"
    tools_root.mkdir()
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        before = page.card_for("client-setup-wizard")
        assert before.status_label.text() == "Not installed"
        assert all(not button.isEnabled() for button in before.action_buttons.values())

        definition = supported_tool_definitions()[0]
        wrapper = tools_root / definition.relative_entrypoint
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("@echo off\n", encoding="utf-8")
        page.refresh_tools()
        qapp.processEvents()

        after = page.card_for("client-setup-wizard")
        assert after.status_label.text() == "Ready"
        assert all(button.isEnabled() for button in after.action_buttons.values())
        assert page.available_count_label.text() == "1 available"
    finally:
        _close_page(page)


def test_unchanged_refresh_reuses_existing_cards_without_visual_pop_in(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        existing_cards = page.tool_cards()

        page.refresh_tools()
        qapp.processEvents()

        assert page.tool_cards() == existing_cards
    finally:
        _close_page(page)


def test_refresh_button_keeps_the_configured_root(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        page.refresh_button.click()
        qapp.processEvents()

        assert page.available_count_label.text() == "11 available"
        assert page.visible_tool_ids()
        assert not page.empty_state.isVisible()
    finally:
        _close_page(page)


def test_missing_root_shows_deliberate_empty_state_and_routes_to_settings(
    qapp: QApplication,
) -> None:
    page = ToolsPage("")
    requested: list[bool] = []
    page.open_settings_requested.connect(lambda: requested.append(True))
    _show_page(qapp, page)

    try:
        assert page.empty_state.isVisible()
        assert "EveJS root" in page.empty_message_label.text()
        assert page.empty_settings_button.isVisible()
        page.empty_settings_button.click()
        assert requested == [True]
        assert page.visible_tool_ids() == ()
    finally:
        _close_page(page)


def test_missing_tools_folder_has_an_explanatory_empty_state(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        assert page.empty_state.isVisible()
        assert "supported tools folder" in page.empty_message_label.text().casefold()
        assert page.available_count_label.text() == "0 available"
    finally:
        _close_page(page)


def test_normal_and_preview_actions_emit_one_truthful_launch_request(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    requests: list[tuple[str, str, tuple[str, ...]]] = []
    page.launch_requested.connect(
        lambda tool, action: requests.append(
            (tool.definition.id, action.id, action.arguments)
        )
    )
    _show_page(qapp, page)

    try:
        page.card_for("client-setup-wizard").action_buttons["launch"].click()
        page.card_for("reset-local-databases").action_buttons["preview"].click()
        qapp.processEvents()

        assert requests == [
            ("client-setup-wizard", "launch", ()),
            ("reset-local-databases", "preview", ("/whatif",)),
        ]
    finally:
        _close_page(page)


def test_risky_actions_are_forwarded_to_the_central_launch_boundary(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    requests: list[tuple[str, str]] = []
    page.launch_requested.connect(
        lambda tool, action: requests.append((tool.definition.id, action.id))
    )
    _show_page(qapp, page)

    try:
        page.card_for("reset-local-databases").action_buttons["reset"].click()
        page.card_for("rust-msvc-market-setup").action_buttons["launch"].click()
        qapp.processEvents()

        assert requests == [
            ("reset-local-databases", "reset"),
            ("rust-msvc-market-setup", "launch"),
        ]
    finally:
        _close_page(page)


def test_reset_action_emits_no_reset_modifier(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    requests: list[tuple[str, tuple[str, ...]]] = []
    page.launch_requested.connect(
        lambda _tool, action: requests.append((action.id, action.arguments))
    )
    _show_page(qapp, page)

    try:
        page.card_for("reset-local-databases").action_buttons["reset"].click()
        qapp.processEvents()

        assert requests == [("reset", ())]
    finally:
        _close_page(page)


def test_minimum_layout_has_no_horizontal_scroll_and_reflows_to_one_column(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page, width=756)

    try:
        assert page.card_column_count == 2
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
        for card in page.tool_cards():
            for button in card.action_buttons.values():
                assert button.width() >= button.sizeHint().width()

        page.resize(560, 560)
        qapp.processEvents()
        assert page.card_column_count == 1
        assert page.scroll_area.horizontalScrollBar().maximum() == 0
    finally:
        _close_page(page)


def test_search_filters_refresh_and_actions_have_accessible_keyboard_contracts(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _install_all_wrappers(tmp_path)
    page = ToolsPage(str(tmp_path))
    _show_page(qapp, page)

    try:
        controls = (page.search_edit, page.category_combo, page.refresh_button)
        for control in controls:
            assert control.focusPolicy() != Qt.FocusPolicy.NoFocus
            assert control.accessibleName()
            assert control.accessibleDescription()

        for card in page.tool_cards():
            for button in card.action_buttons.values():
                assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
                assert button.accessibleName()
                assert button.accessibleDescription()
    finally:
        _close_page(page)
