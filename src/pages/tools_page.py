"""Searchable Tool Deck for curated utilities in an EveJS installation."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.constants import SPACING
from src.core.tool_catalog import (
    TOOL_CATEGORIES,
    ResolvedTool,
    ResolvedToolAction,
    ToolDispatchKind,
    filter_tools,
    resolve_tools,
)
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.widgets.page_header import PageHeader


_ROOT_STATE_REASONS = {
    "Set the EveJS root in Settings",
    "Configured EveJS root was not found",
    "Supported tools folder was not found",
}


class ToolCard(QFrame):
    """Presentation and actions for one resolved catalog entry."""

    action_requested = pyqtSignal(object, object)

    def __init__(self, tool: ResolvedTool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tool = tool
        definition = tool.definition
        self.setObjectName(f"toolCard-{definition.id}")
        self.setProperty("class", "toolCard")
        self.setProperty("accent", definition.accent_role)
        self.setAccessibleName(definition.name)
        self.setAccessibleDescription(definition.description)
        # Keep the two-column layout shrinkable so the page receives a resize
        # event and can reflow before a hidden horizontal overflow develops.
        self.setMinimumWidth(240)
        self.setMinimumHeight(222)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self._feedback_action_id = ""
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.setInterval(2_500)
        self._feedback_timer.timeout.connect(self._restore_ready_state)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)

        icon_plate = QFrame()
        icon_plate.setProperty("class", "toolIconPlate")
        icon_plate.setProperty("accent", definition.accent_role)
        icon_plate.setFixedSize(42, 42)
        icon_layout = QVBoxLayout(icon_plate)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel(definition.icon)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setProperty("class", "toolIcon")
        icon_layout.addWidget(icon_label)
        top.addWidget(icon_plate, alignment=Qt.AlignmentFlag.AlignTop)

        title_column = QVBoxLayout()
        title_column.setSpacing(3)
        self.name_label = QLabel(definition.name)
        self.name_label.setProperty("class", "toolName")
        self.name_label.setWordWrap(True)
        title_column.addWidget(self.name_label)

        metadata = QHBoxLayout()
        metadata.setSpacing(6)
        self.category_label = QLabel(definition.category.upper())
        self.category_label.setProperty("class", "toolCategoryPill")
        metadata.addWidget(self.category_label)
        self.source_label = QLabel(definition.source_folder)
        self.source_label.setProperty("class", "toolSource")
        metadata.addWidget(self.source_label)
        metadata.addStretch()
        title_column.addLayout(metadata)
        top.addLayout(title_column, stretch=1)
        root.addLayout(top)

        self.description_label = QLabel(definition.description)
        self.description_label.setProperty("class", "toolDescription")
        self.description_label.setWordWrap(True)
        self.description_label.setToolTip(definition.description)
        self.description_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.MinimumExpanding,
        )
        root.addWidget(self.description_label)

        root.addStretch()

        state_row = QHBoxLayout()
        state_row.setSpacing(6)
        self.status_dot = QLabel("●")
        self.status_dot.setProperty("class", "toolAvailabilityDot")
        self.status_label = QLabel()
        self.status_label.setProperty("class", "toolAvailability")
        state_row.addWidget(self.status_dot)
        state_row.addWidget(self.status_label)

        self.risk_label = QLabel(definition.prerequisite_label)
        self.risk_label.setProperty("class", "toolRiskBadge")
        self.risk_label.setProperty("risk", self._badge_risk())
        self.risk_label.setVisible(bool(definition.prerequisite_label))
        state_row.addStretch()
        state_row.addWidget(self.risk_label)
        root.addLayout(state_row)

        many_actions = len(tool.actions) > 2
        action_layout: QHBoxLayout | QGridLayout
        if many_actions:
            action_layout = QGridLayout()
            action_layout.setHorizontalSpacing(8)
            action_layout.setVerticalSpacing(6)
        else:
            action_layout = QHBoxLayout()
            action_layout.setSpacing(8)
            action_layout.addStretch()
        self.action_buttons: dict[str, QPushButton] = {}
        for index, action in enumerate(tool.actions):
            # Parent before asking Qt for a native size hint. A parentless
            # QPushButton can briefly materialize as a top-level Windows
            # window while a visible Tool Deck is being rebuilt.
            button = QPushButton(action.label, self)
            button.setObjectName(f"toolAction-{definition.id}-{action.id}")
            button.setProperty("class", self._action_class(action))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setEnabled(action.available)
            button.setAccessibleName(f"{action.label} {definition.name}")
            button.setAccessibleDescription(
                (
                    f"{action.label} via the selected Docker Compose target"
                    if action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE
                    else f"{action.label} via {definition.relative_entrypoint}"
                )
            )
            button.setToolTip(
                (
                    f"{action.label}: selected Docker Compose target"
                    if action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE
                    else f"{action.label}: {definition.relative_entrypoint}"
                )
                if action.available
                else action.unavailable_reason
            )
            button.clicked.connect(
                lambda _checked=False, selected=action: self.action_requested.emit(
                    self.tool,
                    selected,
                )
            )
            if many_actions:
                button.setMinimumWidth(0)
                button.setSizePolicy(
                    QSizePolicy.Policy.Ignored,
                    QSizePolicy.Policy.Fixed,
                )
            else:
                button.setMinimumWidth(button.sizeHint().width())
            self.action_buttons[action.id] = button
            if many_actions:
                action_layout.addWidget(button, index, 0)
            else:
                action_layout.addWidget(button)
        root.addLayout(action_layout)

        self._restore_ready_state()

    def _badge_risk(self) -> str:
        label = self.tool.definition.prerequisite_label.casefold()
        if "destructive" in label:
            return "destructive"
        if "administrator" in label or "system" in label:
            return "system"
        return "caution"

    @staticmethod
    def _action_class(action: ResolvedToolAction) -> str:
        if action.risk_level == "destructive":
            return "toolDanger"
        if action.id in {
            "preview",
            "status",
            "backups",
            "presets",
            "snapshot-info",
        }:
            return "toolSecondary"
        return "toolPrimary"

    def set_launch_result(
        self,
        action_id: str,
        *,
        success: bool,
        message: str = "",
        completed: bool = False,
    ) -> None:
        """Show truthful wrapper-spawn or managed-operation feedback."""
        self._feedback_timer.stop()
        self._feedback_action_id = action_id
        button = self.action_buttons.get(action_id)
        if success:
            success_text = "Completed" if completed else "Launched"
            self.status_label.setText(success_text)
            self.status_label.setProperty("state", "launched")
            self.status_dot.setProperty("state", "launched")
            self.status_label.setToolTip(message or "Tool wrapper launched")
            if button is not None:
                button.setText(success_text)
        else:
            self.status_label.setText(
                "Operation failed" if completed else "Launch failed"
            )
            self.status_label.setProperty("state", "error")
            self.status_dot.setProperty("state", "error")
            self.status_label.setToolTip(message or "Tool wrapper could not be launched")
            if button is not None:
                button.setText("Failed")
        self._refresh_dynamic_style()
        self._feedback_timer.start()

    def _restore_ready_state(self) -> None:
        definition = self.tool.definition
        status = "Ready" if self.tool.available else self.tool.unavailable_reason
        self.status_label.setText(status)
        state = "ready" if self.tool.available else "missing"
        self.status_label.setProperty("state", state)
        self.status_dot.setProperty("state", state)
        self.status_label.setToolTip(f"{status}\n{definition.relative_entrypoint}")

        for action in self.tool.actions:
            button = self.action_buttons.get(action.id)
            if button is not None:
                button.setText(action.label)
                button.setEnabled(action.available)
        self._feedback_action_id = ""
        self._refresh_dynamic_style()

    def _refresh_dynamic_style(self) -> None:
        for widget in (self.status_dot, self.status_label, self.risk_label):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()


class ToolCategorySection(QWidget):
    """One semantic category heading and responsive card grid."""

    def __init__(
        self,
        category: str,
        cards: list[ToolCard],
        columns: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        self.setAccessibleName("Tool Deck")
        self.setAccessibleDescription(
            "Search and launch reviewed utilities for the selected EveJS runtime."
        )
        self.category = category
        self.cards = cards
        self.setObjectName(f"toolSection-{category.casefold().replace(' & ', '-').replace(' ', '-')}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        heading_row = QHBoxLayout()
        self.heading_label = QLabel(category.upper())
        self.heading_label.setProperty("class", "toolSectionTitle")
        heading_row.addWidget(self.heading_label)
        heading_row.addStretch()
        noun = "tool" if len(cards) == 1 else "tools"
        self.count_label = QLabel(f"{len(cards)} {noun}")
        self.count_label.setProperty("class", "toolSectionCount")
        heading_row.addWidget(self.count_label)
        root.addLayout(heading_row)

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        root.addLayout(self.grid)
        self.set_columns(columns)

    def set_columns(self, columns: int) -> None:
        """Reflow existing cards without recreating their widgets."""
        while self.grid.count():
            self.grid.takeAt(0)
        for column in range(2):
            self.grid.setColumnStretch(column, 0)
        for index, card in enumerate(self.cards):
            row, column = divmod(index, columns)
            self.grid.addWidget(card, row, column)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)


class ToolsPage(QWidget):
    """Grouped, searchable catalog of supported external EveJS utilities."""

    open_settings_requested = pyqtSignal()
    launch_requested = pyqtSignal(object, object)

    _TWO_COLUMN_MIN_WIDTH = 640

    def __init__(
        self,
        evejs_root: str | Path = "",
        parent: QWidget | None = None,
        *,
        backend: RuntimeBackend = RuntimeBackend.NATIVE,
        docker_policy: DockerControlPolicy = DockerControlPolicy.CONNECT_ONLY,
        compose_file: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._evejs_root = str(evejs_root or "")
        self._runtime_backend = backend
        self._docker_policy = docker_policy
        self._compose_file = str(compose_file or "")
        self._resolved_tools: tuple[ResolvedTool, ...] = ()
        self._cards: dict[str, ToolCard] = {}
        self._sections: dict[str, ToolCategorySection] = {}
        self.card_column_count = 2
        self._build_ui()
        self._update_context_presentation()
        self.refresh_tools()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            SPACING["xl"],
            SPACING["lg"],
            SPACING["xl"],
            SPACING["lg"],
        )
        root.setSpacing(SPACING["md"])

        self.page_header = PageHeader(
            "TOOL DECK",
            "Reviewed utilities resolved against the selected EveJS runtime.",
            "SYSTEM UTILITIES",
            self,
        )

        self.runtime_context_label = QLabel()
        self.runtime_context_label.setProperty("class", "toolRuntimePill")
        self.runtime_context_label.setAccessibleName("Tool runtime context")
        self.page_header.add_action(self.runtime_context_label)

        self.available_count_label = QLabel("0 available")
        self.available_count_label.setProperty("class", "toolAvailableCount")
        self.available_count_label.setAccessibleName("Available tool count")
        self.page_header.add_action(self.available_count_label)

        self.refresh_button = QPushButton("REFRESH")
        self.refresh_button.setProperty("class", "signalSecondary")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setAccessibleName("Refresh tools")
        self.refresh_button.setAccessibleDescription(
            "Rescan supported wrappers in the configured EveJS tools folder"
        )
        self.refresh_button.clicked.connect(lambda _checked=False: self.refresh_tools())
        self.page_header.add_action(self.refresh_button)
        root.addWidget(self.page_header)

        self.filter_rail = QFrame(self)
        self.filter_rail.setProperty("class", "toolFilterRail")
        filters = QHBoxLayout(self.filter_rail)
        filters.setContentsMargins(14, 10, 14, 10)
        filters.setSpacing(SPACING["md"])

        search_block = QVBoxLayout()
        search_block.setSpacing(3)
        search_label = QLabel("SEARCH TELEMETRY")
        search_label.setProperty("class", "toolFilterLabel")
        search_block.addWidget(search_label)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search tools…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(0)
        self.search_edit.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.search_edit.setAccessibleName("Search tools")
        self.search_edit.setAccessibleDescription(
            "Filter by tool name, description, category, or source folder"
        )
        self.search_edit.textChanged.connect(self._render_tools)
        search_block.addWidget(self.search_edit)
        filters.addLayout(search_block, stretch=1)

        category_block = QVBoxLayout()
        category_block.setSpacing(3)
        category_label = QLabel("SYSTEM GROUP")
        category_label.setProperty("class", "toolFilterLabel")
        category_block.addWidget(category_label)
        self.category_combo = QComboBox()
        self.category_combo.addItem("All categories")
        self.category_combo.addItems(TOOL_CATEGORIES)
        self.category_combo.setMinimumWidth(164)
        self.category_combo.setAccessibleName("Tool category")
        self.category_combo.setAccessibleDescription(
            "Show all tools or one semantic tool category"
        )
        self.category_combo.currentTextChanged.connect(self._render_tools)
        category_block.addWidget(self.category_combo)
        filters.addLayout(category_block)
        root.addWidget(self.filter_rail)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_area.setAccessibleName("Reviewed tool catalog")

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 4, 0)
        self._content_layout.setSpacing(16)

        self.empty_state = QFrame()
        self.empty_state.setProperty("class", "toolEmptyState")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(24, 48, 24, 48)
        empty_layout.setSpacing(10)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_icon = QLabel("◇")
        empty_icon.setProperty("class", "toolEmptyIcon")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)
        self.empty_title_label = QLabel("TOOLS UNAVAILABLE")
        self.empty_title_label.setProperty("class", "toolEmptyTitle")
        self.empty_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title_label)
        self.empty_message_label = QLabel()
        self.empty_message_label.setProperty("class", "secondary")
        self.empty_message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message_label.setWordWrap(True)
        self.empty_message_label.setMaximumWidth(520)
        empty_layout.addWidget(self.empty_message_label)
        self.empty_settings_button = QPushButton("Open Settings")
        self.empty_settings_button.setProperty("class", "signalSecondary")
        self.empty_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.empty_settings_button.setAccessibleName("Open Settings")
        self.empty_settings_button.setAccessibleDescription(
            "Open Settings to choose an EveJS installation"
        )
        self.empty_settings_button.clicked.connect(self.open_settings_requested.emit)
        empty_layout.addWidget(
            self.empty_settings_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self._content_layout.addWidget(self.empty_state)

        self.no_results_label = QLabel("No tools match the current filters.")
        self.no_results_label.setProperty("class", "secondary")
        self.no_results_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_results_label.setWordWrap(True)
        self.no_results_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self._content_layout.addWidget(self.no_results_label)
        self._content_layout.addStretch()

        self.scroll_area.setWidget(self._content)
        root.addWidget(self.scroll_area, stretch=1)

    def _update_context_presentation(self) -> None:
        if self._runtime_backend is RuntimeBackend.NATIVE:
            text, state = "NATIVE HOST", "ready"
        elif self._docker_policy is DockerControlPolicy.MANAGED:
            text, state = "DOCKER · MANAGED", "online"
        else:
            text, state = "DOCKER · CONNECT ONLY", "idle"
        self.runtime_context_label.setText(text)
        self.runtime_context_label.setProperty("state", state)
        style = self.runtime_context_label.style()
        style.unpolish(self.runtime_context_label)
        style.polish(self.runtime_context_label)
        self.runtime_context_label.update()

    # ── Data and filtering ────────────────────────────────────────────────────
    def refresh_tools(self, evejs_root: str | Path | None = None) -> None:
        """Re-resolve availability without recreating the page itself."""
        if evejs_root is not None:
            self._evejs_root = str(evejs_root or "")
        resolved_tools = resolve_tools(
            self._evejs_root,
            backend=self._runtime_backend,
            docker_policy=self._docker_policy,
            compose_file=self._compose_file,
        )
        available = sum(tool.available for tool in resolved_tools)
        self.available_count_label.setText(f"{available} available")
        self.available_count_label.setProperty(
            "state",
            "online" if available else "idle",
        )
        count_style = self.available_count_label.style()
        count_style.unpolish(self.available_count_label)
        count_style.polish(self.available_count_label)
        self.available_count_label.update()
        if resolved_tools == self._resolved_tools:
            return
        self._resolved_tools = resolved_tools
        self._render_tools()

    def set_evejs_root(self, evejs_root: str | Path) -> None:
        """Update the configured root and immediately refresh availability."""
        self.refresh_tools(evejs_root)

    def set_runtime_context(
        self,
        backend: RuntimeBackend,
        docker_policy: DockerControlPolicy,
        compose_file: str | Path | None = None,
    ) -> None:
        """Resolve cards and actions for the selected runtime capability."""
        context = (backend, docker_policy, str(compose_file or ""))
        if context == (
            self._runtime_backend,
            self._docker_policy,
            self._compose_file,
        ):
            return
        self._runtime_backend, self._docker_policy, self._compose_file = context
        self._update_context_presentation()
        self.refresh_tools()

    def _render_tools(self, *_args: object) -> None:
        self._clear_sections()
        root_reason = self._root_state_reason()
        if root_reason:
            self._show_empty_state(root_reason)
            return

        self.empty_state.hide()
        filtered = filter_tools(
            self._resolved_tools,
            self.search_edit.text(),
            self.category_combo.currentText(),
        )
        self.no_results_label.setVisible(not filtered)
        if not filtered:
            return

        grouped = {
            category: [
                tool for tool in filtered if tool.definition.category == category
            ]
            for category in TOOL_CATEGORIES
        }
        insert_at = self._content_layout.count() - 1
        for category in TOOL_CATEGORIES:
            tools = grouped[category]
            if not tools:
                continue
            cards: list[ToolCard] = []
            for tool in tools:
                card = ToolCard(tool)
                card.action_requested.connect(self._on_action_requested)
                self._cards[tool.definition.id] = card
                cards.append(card)
            section = ToolCategorySection(
                category,
                cards,
                self.card_column_count,
            )
            self._sections[category] = section
            self._content_layout.insertWidget(insert_at, section)
            insert_at += 1

    def _clear_sections(self) -> None:
        for section in self._sections.values():
            section.setParent(None)
            section.deleteLater()
        self._sections.clear()
        self._cards.clear()
        self.no_results_label.hide()

    def _root_state_reason(self) -> str:
        reasons = {tool.unavailable_reason for tool in self._resolved_tools}
        if len(reasons) == 1:
            reason = next(iter(reasons))
            if reason in _ROOT_STATE_REASONS:
                return reason
        return ""

    def _show_empty_state(self, reason: str) -> None:
        messages = {
            "Set the EveJS root in Settings": (
                "Choose an EveJS root in Settings to discover its supported tools."
            ),
            "Configured EveJS root was not found": (
                "The configured EveJS root could not be found. Choose the current "
                "installation folder in Settings."
            ),
            "Supported tools folder was not found": (
                "This EveJS installation has no supported tools folder. Check the "
                "selected root or use a supported EveJS version."
            ),
        }
        self.empty_message_label.setText(messages[reason])
        self.empty_state.show()
        self.no_results_label.hide()

    # ── Actions and feedback ──────────────────────────────────────────────────
    def _on_action_requested(
        self,
        tool: ResolvedTool,
        action: ResolvedToolAction,
    ) -> None:
        current_tool = next(
            (
                candidate
                for candidate in self._resolved_tools
                if candidate.definition.id == tool.definition.id
            ),
            None,
        )
        if current_tool is None or current_tool != tool:
            return
        canonical = next(
            (
                candidate
                for candidate in current_tool.actions
                if candidate.id == action.id
            ),
            None,
        )
        if (
            canonical is None
            or canonical != action
            or not canonical.available
            or canonical.dispatch_kind is ToolDispatchKind.UNAVAILABLE
        ):
            return
        self.launch_requested.emit(current_tool, canonical)

    def set_launch_result(
        self,
        tool_id: str,
        action_id: str,
        *,
        success: bool,
        message: str = "",
        completed: bool = False,
    ) -> None:
        """Apply a reviewed action result to a visible card."""
        card = self._cards.get(tool_id)
        if card is not None:
            card.set_launch_result(
                action_id,
                success=success,
                message=message,
                completed=completed,
            )

    # ── Testable presentation state ───────────────────────────────────────────
    def card_for(self, tool_id: str) -> ToolCard:
        return self._cards[tool_id]

    def tool_cards(self) -> tuple[ToolCard, ...]:
        return tuple(self._cards.values())

    def visible_tool_ids(self) -> tuple[str, ...]:
        return tuple(self._cards)

    def visible_categories(self) -> tuple[str, ...]:
        return tuple(self._sections)

    # ── Responsive layout ─────────────────────────────────────────────────────
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_card_columns)

    def _update_card_columns(self) -> None:
        width = self.scroll_area.viewport().width()
        columns = 2 if width >= self._TWO_COLUMN_MIN_WIDTH else 1
        if columns == self.card_column_count:
            return
        self.card_column_count = columns
        for section in self._sections.values():
            section.set_columns(columns)
