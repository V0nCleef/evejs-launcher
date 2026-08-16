"""Characters page for EveJS Launcher V2.

Layout
------
+-------------------------------------------------------------------+
|  CHARACTERS   (24)                       [Filter characters...]   |
+------------------------------------------------+------------------+
|  [card] [card] [card]                          |                  |
|  [card] [card] [card]      (3-col grid)        |   DetailPanel    |
|  [card] [card] [card]                          |     (280 px)     |
+------------------------------------------------+------------------+

Behaviour
---------
* ``refresh(accounts, hidden_characters, tracker)`` rebuilds the card grid
  diff-smart: cards that already exist are updated in place, new cards are
  added, removed characters are deleted.
* While accounts are loading, placeholder ``SkeletonCard`` widgets are shown.
* Typing in the search box filters cards in real time against character name,
  account username, and ship name.
* When no character is explicitly selected, the detail panel is hidden.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS as C, SEMANTIC_COLORS as S, Status
from src.core.dashboard import visible_character_rows
from src.core.db import Account, Character, _fmt_isk, _fmt_sp
from src.core.groups import TargetGroupState
from src.core.process_tracker import ProcessTracker
from src.core.runtime.data import native_data_selection
from src.core.runtime.portraits import (
    PortraitImageResult,
    PortraitRequest,
    PortraitTarget,
)
from src.ui.motion import MotionController
from src.utils.cache import PortraitCache
from src.widgets.character_card import CharacterCard
from src.widgets.deep_signal_background import operations_scene_path
from src.widgets.detail_panel import DetailPanel
from src.widgets.new_character_card import NewCharacterCard
from src.widgets.skeleton_card import SkeletonCard
from src.widgets.page_header import PageHeader
from src.workers.portrait_worker import PortraitLoadFailure, PortraitLoader

GRID_COLUMNS = 6
GRID_SPACING = 10
_SKELETON_COUNT = 6
_DETAIL_HEIGHT = 150
_ROSTER_SUBTITLE = "Inspect capsule telemetry, organise launch groups, and deploy a pilot."


class _StaticCharacterScene(QWidget):
    """One cached, permanently static character-page scene.

    This deliberately avoids the Operations background's richer procedural
    fallback and cross-page motion compatibility plumbing.  The Characters
    page only needs an immutable raster backdrop behind its roster.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._scene = QPixmap(str(operations_scene_path()))
        self._cache = QPixmap()

    def is_animating(self) -> bool:
        return False

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        if self._cache.size() != self.size():
            self._cache = QPixmap(max(1, self.width()), max(1, self.height()))
            self._cache.fill(QColor(S["background"]))
            painter = QPainter(self._cache)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if not self._scene.isNull():
                scaled = self._scene.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                excess_x = max(0, scaled.width() - self.width())
                excess_y = max(0, scaled.height() - self.height())
                source_x = round(excess_x * 0.70)
                source_y = round(excess_y * 0.48)
                painter.drawPixmap(
                    self.rect(),
                    scaled,
                    scaled.rect().adjusted(
                        source_x,
                        source_y,
                        -(excess_x - source_x),
                        -(excess_y - source_y),
                    ),
                )
            fade = QLinearGradient(0, 0, max(1, self.width()), 0)
            fade.setColorAt(0.0, QColor(3, 8, 14, 247))
            fade.setColorAt(0.48, QColor(3, 9, 16, 190))
            fade.setColorAt(0.82, QColor(3, 10, 17, 44))
            fade.setColorAt(1.0, QColor(3, 10, 17, 8))
            painter.fillRect(self.rect(), fade)
            painter.end()
        output = QPainter(self)
        output.drawPixmap(0, 0, self._cache)
        output.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._cache = QPixmap()
        super().resizeEvent(event)


class CharactersPage(QWidget):
    """Character grid with search and detail panel."""

    launch_character = pyqtSignal(str, str, int)  # username, char_name, char_id
    character_selected = pyqtSignal(str, str, int)  # username, char_name, char_id
    hide_character = pyqtSignal(str)  # character_name
    delete_character_requested = pyqtSignal(str, str, int)
    delete_account_requested = pyqtSignal(str, str, int)
    new_character_requested = pyqtSignal()
    group_selection_changed = pyqtSignal(object)  # group ID or None for All Visible
    launch_group_requested = pyqtSignal()
    cancel_group_launches_requested = pyqtSignal()
    manage_groups_requested = pyqtSignal(object)  # optional focused character ID
    portrait_loads_idle = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Keyed by (username, char_id) — survives re-sorting and filtering.
        self._cards: dict[tuple[str, int], CharacterCard] = {}
        self._new_character_card: NewCharacterCard | None = None
        # Flat list of (username, Character) for layout.
        self._rotation_pool: list[tuple[str, Character]] = []
        self._selected_key: tuple[str, int] | None = None
        self._tracker: ProcessTracker | None = None
        self._motion = MotionController(parent=self)
        self._animating: bool = False
        self._transition_group: QParallelAnimationGroup | None = None
        self._grid_columns = GRID_COLUMNS
        self._compact_controls = False
        self._reflow_pending = False
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(0)
        self._reflow_timer.timeout.connect(self._perform_deferred_reflow)

        self._portrait_threads: dict[
            QThread,
            tuple[PortraitLoader, PortraitRequest],
        ] = {}
        self._portrait_tokens: dict[tuple[str, int], object] = {}
        self._portrait_target: PortraitTarget | None = None
        self._portrait_generation = 0
        self._evejs_root: str = ""
        self._launch_available = True
        self._launch_unavailable_reason = ""
        self._launching_accounts: dict[str, str] = {}
        self._character_creation_available = True
        self._character_creation_reason = ""
        self._data_error = ""
        self._group_state = TargetGroupState()
        self._group_launch_available = False
        self._group_launch_reason = "No visible accounts available"
        self._group_launch_ready_count = 0
        self._group_launch_in_progress = False

        self._build_ui()
        self.set_group_state(TargetGroupState())
        self.show_skeletons()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Build the static Deep Signal roster and responsive command rail."""
        self.setProperty("deepSignal", True)
        self.setAccessibleName("Characters")

        layers = QStackedLayout(self)
        layers.setContentsMargins(0, 0, 0, 0)
        layers.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.signal_background = _StaticCharacterScene(self)
        layers.addWidget(self.signal_background)

        self._foreground = QWidget(self)
        self._foreground.setProperty("deepSignal", True)
        layers.addWidget(self._foreground)
        layers.setCurrentWidget(self._foreground)

        root = QVBoxLayout(self._foreground)
        root.setContentsMargins(24, 14, 24, 14)
        root.setSpacing(10)

        self.page_header = PageHeader(
            "CHARACTERS",
            _ROSTER_SUBTITLE,
            "DEEP SIGNAL // CAPSULE REGISTRY",
            self._foreground,
        )
        self.count_label = QLabel("")
        self.count_label.setProperty("class", "signalPill")
        self.count_label.setAccessibleName("Visible character count")
        self.page_header.add_action(self.count_label)
        root.addWidget(self.page_header)

        self.controls_panel = QFrame(self._foreground)
        self.controls_panel.setProperty("class", "glassPanel")
        self.controls_panel.setProperty("variant", "quiet")
        self.controls_panel.setProperty("deepSignal", True)
        self._controls_grid = QGridLayout(self.controls_panel)
        self._controls_grid.setContentsMargins(10, 8, 10, 8)
        self._controls_grid.setHorizontalSpacing(8)
        self._controls_grid.setVerticalSpacing(7)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search character, account, or ship…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(120)
        self.search_edit.setFixedHeight(38)
        self.search_edit.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.search_edit.setAccessibleName("Search characters")
        self.search_edit.setToolTip("Filter by character, account, or active ship")
        self.search_edit.textChanged.connect(self._apply_filter)

        self.status_combo = QComboBox()
        self.status_combo.setMinimumWidth(90)
        self.status_combo.setFixedHeight(38)
        self.status_combo.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.status_combo.setAccessibleName("Character status filter")
        self.status_combo.setToolTip("Show characters in one launch state")
        self.status_combo.addItem("ALL STATUS", None)
        self.status_combo.addItem("READY", Status.READY)
        self.status_combo.addItem("RUNNING", Status.RUNNING)
        self.status_combo.addItem("LAUNCHING", Status.LAUNCHING)
        self.status_combo.addItem("WAITING", Status.SAME_ACCOUNT_ONLINE)
        self.status_combo.addItem("ERROR", Status.ERROR)
        self.status_combo.currentIndexChanged.connect(
            lambda _index: self._apply_filter(self.search_edit.text())
        )

        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(120)
        self.group_combo.setFixedHeight(38)
        self.group_combo.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.group_combo.setAccessibleName("Launch group selector")
        self.group_combo.setToolTip("Choose the roster group to display and launch")
        self.group_combo.currentIndexChanged.connect(self._on_group_combo_changed)

        self.launch_group_button = QPushButton("LAUNCH ALL")
        self.launch_group_button.setMinimumWidth(120)
        self.launch_group_button.setFixedHeight(38)
        self.launch_group_button.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.launch_group_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_group_button.setAccessibleName("Launch selected character group")
        self.launch_group_button.setStyleSheet(
            f"QPushButton {{ background-color: rgba(105, 72, 0, 232); "
            f"color: #FFE39A; border: 1px solid {C['gold']}; border-radius: 4px; "
            "padding: 0 14px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover:enabled {{ background-color: {C['gold']}; "
            f"color: {C['void_black']}; }}"
            "QPushButton:focus { border: 2px solid #FFF2C3; }"
            f"QPushButton:disabled {{ background-color: rgba(10, 22, 32, 210); "
            f"color: {S['text_muted']}; border-color: {S['border']}; }}"
        )
        self.launch_group_button.clicked.connect(self._emit_group_launch_action)

        self.manage_groups_button = QPushButton("MANAGE GROUPS")
        self.manage_groups_button.setProperty("class", "signalSecondary")
        self.manage_groups_button.setMinimumWidth(120)
        self.manage_groups_button.setFixedHeight(38)
        self.manage_groups_button.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding,
            QSizePolicy.Policy.Fixed,
        )
        self.manage_groups_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.manage_groups_button.setAccessibleName("Manage character groups")
        self.manage_groups_button.clicked.connect(
            lambda: self.manage_groups_requested.emit(None)
        )
        self._layout_controls(compact=self.width() < 900)
        root.addWidget(self.controls_panel)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setAccessibleName("Character roster")

        self._grid_container = QWidget()
        self._grid_container.setProperty("deepSignal", True)
        self._grid_container.installEventFilter(self)
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._scroll.setWidget(self._grid_container)
        root.addWidget(self._scroll, stretch=1)

        self.detail_panel = DetailPanel()
        self.detail_panel.launch_clicked.connect(self._on_detail_launch)
        self.detail_panel.hide_clicked.connect(self._on_detail_hide)
        self.detail_panel.setMaximumHeight(0)
        self.detail_panel.hide()
        root.addWidget(self.detail_panel)

    def _build_legacy_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header row
        header = QHBoxLayout()
        header.setSpacing(12)

        title = QLabel("CHARACTERS")
        title.setProperty("class", "title")
        header.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setProperty("class", "secondary")
        header.addWidget(self.count_label)

        header.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter characters...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(260)
        self.search_edit.textChanged.connect(self._apply_filter)
        header.addWidget(self.search_edit)

        root.addLayout(header)

        # Group view / bulk-launch toolbar. The built-in All Visible entry
        # preserves the launcher's existing Launch All behavior.
        group_bar = QHBoxLayout()
        group_bar.setSpacing(10)
        group_label = QLabel("LAUNCH GROUP")
        group_label.setProperty("class", "muted")
        group_bar.addWidget(group_label)

        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(220)
        self.group_combo.setMaximumWidth(320)
        self.group_combo.currentIndexChanged.connect(self._on_group_combo_changed)
        group_bar.addWidget(self.group_combo)

        self.launch_group_button = QPushButton("LAUNCH ALL")
        self.launch_group_button.setProperty("class", "primary")
        self.launch_group_button.setFixedHeight(36)
        self.launch_group_button.clicked.connect(self._emit_group_launch_action)
        group_bar.addWidget(self.launch_group_button)

        self.manage_groups_button = QPushButton("MANAGE GROUPS")
        self.manage_groups_button.setProperty("class", "secondary")
        self.manage_groups_button.setFixedHeight(36)
        self.manage_groups_button.clicked.connect(
            lambda: self.manage_groups_requested.emit(None)
        )
        group_bar.addWidget(self.manage_groups_button)
        group_bar.addStretch()
        root.addLayout(group_bar)

        # Content row: grid (left) + detail panel (right)
        content = QHBoxLayout()
        content.setSpacing(12)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self._grid_container = QWidget()
        self._grid_container.installEventFilter(self)  # catch clicks on empty space
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._grid_container)
        content.addWidget(self._scroll, stretch=1)

        self.detail_panel = DetailPanel()
        self.detail_panel.launch_clicked.connect(self._on_detail_launch)
        self.detail_panel.hide_clicked.connect(self._on_detail_hide)
        # Start collapsed — animated open when a character is selected.
        self.detail_panel.setMaximumWidth(0)
        self.detail_panel.show()
        content.addWidget(self.detail_panel)

        root.addLayout(content, stretch=1)

    # ── Loading placeholders ─────────────────────────────────────────────────
    def _layout_controls(self, *, compact: bool) -> None:
        """Reflow the same controls into one or two rows without rebuilding."""
        self._compact_controls = bool(compact)
        widgets = (
            self.search_edit,
            self.status_combo,
            self.group_combo,
            self.launch_group_button,
            self.manage_groups_button,
        )
        for widget in widgets:
            self._controls_grid.removeWidget(widget)
        for column in range(6):
            self._controls_grid.setColumnStretch(column, 0)

        # ``Ignored`` makes QGridLayout disregard a control's size hint.  The
        # zero-stretch action columns then collapse to a few pixels while the
        # buttons keep their widget minimums and paint outside their cells.
        # Keep every cell's minimum contribution in both responsive modes so
        # controls can reflow without overlapping or being clipped.
        horizontal_policy = QSizePolicy.Policy.MinimumExpanding
        for widget in widgets:
            widget.setSizePolicy(
                horizontal_policy,
                QSizePolicy.Policy.Fixed,
            )

        if compact:
            self._controls_grid.addWidget(self.search_edit, 0, 0, 1, 3)
            self._controls_grid.addWidget(self.status_combo, 0, 3)
            self._controls_grid.addWidget(self.group_combo, 1, 0, 1, 2)
            self._controls_grid.addWidget(self.launch_group_button, 1, 2)
            self._controls_grid.addWidget(self.manage_groups_button, 1, 3)
            self._controls_grid.setColumnStretch(0, 2)
            self._controls_grid.setColumnStretch(1, 2)
        else:
            self._controls_grid.addWidget(self.search_edit, 0, 0)
            self._controls_grid.addWidget(self.status_combo, 0, 1)
            self._controls_grid.addWidget(self.group_combo, 0, 2)
            self._controls_grid.addWidget(self.launch_group_button, 0, 3)
            self._controls_grid.addWidget(self.manage_groups_button, 0, 4)
            self._controls_grid.setColumnStretch(0, 3)
            self._controls_grid.setColumnStretch(2, 2)
        self.controls_panel.updateGeometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_controls_grid"):
            compact = event.size().width() < 900
            if compact != self._compact_controls:
                self._layout_controls(compact=compact)
        if hasattr(self, "_grid") and not self._reflow_pending:
            self._reflow_pending = True
            self._reflow_timer.start()

    def _perform_deferred_reflow(self) -> None:
        self._reflow_pending = False
        if hasattr(self, "_grid") and not self._animating:
            if not self._relayout_skeletons():
                self._relayout_grid()

    def show_skeletons(self, count: int = _SKELETON_COUNT) -> None:
        """Replace the grid with skeleton placeholder cards."""
        self.cancel_portrait_loads(invalidate=True)
        self._clear_grid()
        self._cards.clear()
        for i in range(count):
            skeleton = SkeletonCard()
            self._grid.addWidget(skeleton, i // 3, i % 3)
        self._relayout_skeletons()
        self.count_label.setText("Loading…")

    def _relayout_skeletons(self) -> bool:
        skeletons = [
            self._grid.itemAt(index).widget()
            for index in range(self._grid.count())
            if isinstance(self._grid.itemAt(index).widget(), SkeletonCard)
        ]
        if not skeletons:
            return False
        available = self._scroll.viewport().width()
        if available <= 0:
            available = max(320, self.width() - 48)
        columns = max(
            1,
            min(GRID_COLUMNS, (available + GRID_SPACING) // (220 + GRID_SPACING)),
        )
        for skeleton in skeletons:
            self._grid.removeWidget(skeleton)
        for index, skeleton in enumerate(skeletons):
            self._grid.addWidget(
                skeleton,
                index // columns,
                index % columns,
            )
        return True

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if isinstance(widget, SkeletonCard):
                    widget.stop_pulsing()
                if widget is self._new_character_card:
                    self._new_character_card = None
                widget.deleteLater()

    # ── Refresh (diff-smart) ─────────────────────────────────────────────────
    def refresh(
        self,
        accounts: list[Account],
        hidden_characters: list[str],
        tracker: ProcessTracker,
        evejs_root: str = "",
        portrait_target: PortraitTarget | None = None,
    ) -> None:
        """Rebuild the card grid from *accounts*.

        Diff-smart: existing cards are updated in place, cards for removed
        characters are deleted, and only genuinely new characters get new
        widgets.

        Cancels any in-progress detail-panel transition to avoid conflicts.
        """
        self._cancel_transition()
        self._tracker = tracker
        self._evejs_root = evejs_root
        if portrait_target is None and evejs_root:
            portrait_target = PortraitTarget(
                target_identity=native_data_selection(evejs_root).target_identity,
                native_root=Path(evejs_root),
            )
        target_changed = portrait_target != self._portrait_target
        if target_changed:
            self.cancel_portrait_loads(invalidate=True)
            self._portrait_target = portrait_target
            for card in self._cards.values():
                card.set_portrait(None)
                card.set_skeleton()
        hidden = set(hidden_characters)

        # Use the same pure visible-character view as Home and Launch All.
        desired = [
            (account.username, character)
            for account, character in visible_character_rows(accounts, hidden)
        ]

        desired_keys = {(u, c.char_id) for u, c in desired}

        # First refresh after skeletons: clear any placeholder widgets.
        self._remove_skeletons()
        self._ensure_new_character_card()

        # Remove cards that no longer exist.
        for key in list(self._cards):
            if key not in desired_keys:
                card = self._cards.pop(key)
                self._portrait_tokens.pop(key, None)
                self._grid.removeWidget(card)
                card.deleteLater()
                if self._selected_key == key:
                    self._selected_key = None

        # Add or update cards.
        for username, char in desired:
            key = (username, char.char_id)
            card = self._cards.get(key)
            if card is None:
                card = CharacterCard(
                    username=username,
                    char_name=char.name,
                    char_id=char.char_id,
                    isk=_fmt_isk(char.isk),
                    ship=char.ship_name or "—",
                    sp=_fmt_sp(char.skill_points),
                    location=char.location or "—",
                    sec_status=f"{char.security_status:.1f}",
                    parent=self._grid_container,
                )
                card.launched.connect(self.launch_character.emit)
                card.selected.connect(self._on_card_selected)
                card.hide_requested.connect(self._on_card_hide_requested)
                card.manage_groups_requested.connect(
                    lambda _username, _name, character_id: (
                        self.manage_groups_requested.emit(character_id)
                    )
                )
                card.delete_character_requested.connect(
                    self.delete_character_requested.emit
                )
                card.delete_account_requested.connect(
                    self.delete_account_requested.emit
                )
                self._cards[key] = card
            card.set_status(self._status_for(username, char))
            card.set_launch_available(
                self._launch_available,
                self._launch_unavailable_reason,
            )
            if (
                self._portrait_target is not None
                and (target_changed or card._portrait_pixmap is None)
                and key not in self._portrait_tokens
            ):
                self._load_portrait_for_card(card)

        self._rotation_pool = desired
        self._relayout_grid()
        self._apply_filter(self.search_edit.text())

        # Show/hide the detail panel based on selection state.
        if self._selected_key is None:
            self.detail_panel.setMaximumHeight(0)
            self.detail_panel.hide()
        # If a character is currently selected but no longer in the pool
        # (e.g. hidden or removed), clear selection.
        elif self._selected_key not in desired_keys:
            self.clear_selection()

    def _remove_skeletons(self) -> None:
        """Delete any SkeletonCard placeholders left in the grid."""
        for i in reversed(range(self._grid.count())):
            item = self._grid.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, SkeletonCard):
                self._grid.removeWidget(widget)
                widget.stop_pulsing()
                widget.deleteLater()

    def _ensure_new_character_card(self) -> None:
        if self._new_character_card is not None:
            return
        card = NewCharacterCard(self._grid_container)
        card.requested.connect(self.new_character_requested.emit)
        card.set_available(
            self._character_creation_available,
            self._character_creation_reason,
        )
        self._new_character_card = card

    def _status_for(self, username: str, char: Character) -> Status:
        """Compute the display status for a character card."""
        pending_character = self._launching_accounts.get(username)
        if pending_character == char.name:
            return Status.LAUNCHING
        if pending_character is not None:
            return Status.SAME_ACCOUNT_ONLINE
        if self._tracker is None:
            return Status.READY
        running_char = self._tracker.get_running_character(username)
        if running_char == char.name:
            # Still in the launch window (EVE takes 10-15s to show its window)
            if self._tracker.is_account_launching(username):
                return Status.LAUNCHING
            return Status.RUNNING
        if running_char is not None:
            return Status.SAME_ACCOUNT_ONLINE
        return Status.READY

    def _relayout_grid(self) -> None:
        """Re-position visible cards using the current viewport width."""
        available = self._scroll.viewport().width()
        if available <= 0:
            available = max(320, self.width() - 48)
        minimum = 156
        columns = max(
            1,
            min(
                GRID_COLUMNS,
                (available + GRID_SPACING) // (minimum + GRID_SPACING),
            ),
        )
        card_width = min(
            CharacterCard.CARD_MAX_WIDTH,
            max(
                minimum,
                (available - GRID_SPACING * (columns - 1)) // columns,
            ),
        )
        self._grid_columns = int(columns)

        if self._new_character_card is not None:
            self._grid.removeWidget(self._new_character_card)
        for card in self._cards.values():
            self._grid.removeWidget(card)

        visible_widgets: list[QWidget] = []
        if (
            self._new_character_card is not None
            and not self._new_character_card.isHidden()
        ):
            self._new_character_card.setFixedSize(
                int(card_width),
                CharacterCard.CARD_HEIGHT,
            )
            visible_widgets.append(self._new_character_card)
        for username, char in self._rotation_pool:
            card = self._cards.get((username, char.char_id))
            if card is not None and not card.isHidden():
                card.setFixedWidth(int(card_width))
                visible_widgets.append(card)

        for index, widget in enumerate(visible_widgets):
            self._grid.addWidget(
                widget,
                index // self._grid_columns,
                index % self._grid_columns,
            )
        for column in range(GRID_COLUMNS):
            self._grid.setColumnStretch(column, 0)

    def _load_portrait_for_card(self, card: CharacterCard) -> None:
        """Start an async portrait load for a character card."""
        target = self._portrait_target
        if target is None:
            return
        key = (card.username, card.char_id)
        token = object()
        request = PortraitRequest(
            target_identity=target.target_identity,
            character_id=card.char_id,
            size=128,
            generation=self._portrait_generation,
            token=token,
            settings_identity=target.settings_identity,
            monitor_generation=target.monitor_generation,
        )
        cache_key = PortraitCache.key(request)
        cached = PortraitCache.get(cache_key)
        self._portrait_tokens[key] = token
        if cached is not None:
            self._apply_portrait_result(key, request, cached)
            return

        thread = QThread(self)
        loader = PortraitLoader(target, request)
        loader.moveToThread(thread)
        thread.started.connect(loader.run)
        loader.loaded.connect(self._on_portrait_loaded)
        loader.failed.connect(self._on_portrait_failed)
        loader.cleanup.connect(
            loader.deleteLater,
            Qt.ConnectionType.DirectConnection,
        )
        loader.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_portrait_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._portrait_threads[thread] = (loader, request)
        thread.start()

    @pyqtSlot(object)
    def _on_portrait_loaded(self, result: PortraitImageResult) -> None:
        """Convert validated QImage to QPixmap only on the GUI thread."""
        request = result.request
        key = self._portrait_key_for_token(request.token)
        if (
            key is None
            or self._portrait_target is None
            or request.generation != self._portrait_generation
            or request.target_identity != self._portrait_target.target_identity
            or request.settings_identity != self._portrait_target.settings_identity
            or request.monitor_generation != self._portrait_target.monitor_generation
            or self._portrait_tokens.get(key) is not request.token
            or key not in self._cards
        ):
            return
        pixmap = QPixmap.fromImage(result.image)
        if pixmap.isNull():
            return
        PortraitCache.put(PortraitCache.key(request), pixmap)
        self._apply_portrait_result(key, request, pixmap)

    @pyqtSlot(object)
    def _on_portrait_failed(self, failure: PortraitLoadFailure) -> None:
        key = self._portrait_key_for_token(failure.request.token)
        if key is not None and self._portrait_tokens.get(key) is failure.request.token:
            self._portrait_tokens.pop(key, None)

    def _apply_portrait_result(
        self,
        key: tuple[str, int],
        request: PortraitRequest,
        pixmap: QPixmap,
    ) -> None:
        if self._portrait_tokens.get(key) is not request.token:
            return
        self._portrait_tokens.pop(key, None)
        card = self._cards.get(key)
        if card is None:
            return
        card.set_portrait(pixmap)
        if self._selected_key == key:
            self.detail_panel.set_portrait(pixmap)

    def _portrait_key_for_token(self, token: object) -> tuple[str, int] | None:
        for key, current in self._portrait_tokens.items():
            if current is token:
                return key
        return None

    @pyqtSlot()
    def _on_portrait_thread_finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, QThread) or thread not in self._portrait_threads:
            return
        self._portrait_threads.pop(thread, None)
        thread.deleteLater()
        if not self._portrait_threads:
            self.portrait_loads_idle.emit()

    def cancel_portrait_loads(self, *, invalidate: bool = False) -> None:
        """Cancel delivery without waiting for bounded I/O to return."""
        if invalidate:
            self._portrait_generation += 1
            self._portrait_tokens.clear()
        for loader, _request in self._portrait_threads.values():
            loader.request_cancel()

    def portrait_loads_active(self) -> bool:
        return bool(self._portrait_threads)

    def invalidate_portrait_target(self) -> None:
        """Immediately remove images attributed to a superseded runtime."""
        self.cancel_portrait_loads(invalidate=True)
        self._portrait_target = None
        for card in self._cards.values():
            card.set_portrait(None)
            card.set_skeleton()
        self.detail_panel.set_portrait(None)

    def set_launch_available(self, enabled: bool, reason: str = "") -> None:
        """Apply backend launch capability without disabling character browsing."""
        self._launch_available = bool(enabled)
        self._launch_unavailable_reason = "" if enabled else reason
        for card in self._cards.values():
            card.set_launch_available(enabled, reason)
        self.detail_panel.set_launch_available(enabled, reason)

    def set_group_state(self, state: TargetGroupState) -> None:
        """Synchronize the group selector and current grid filter."""
        self._group_state = state
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("All Visible Characters", None)
        selected_index = 0
        for index, group in enumerate(state.groups, start=1):
            self.group_combo.addItem(
                f"{group.name} ({len(group.members)})",
                group.group_id,
            )
            if group.group_id == state.selected_group_id:
                selected_index = index
        self.group_combo.setCurrentIndex(selected_index)
        self.group_combo.blockSignals(False)
        self._update_group_launch_button()
        self._apply_filter(self.search_edit.text())

    def set_group_management_available(
        self,
        enabled: bool,
        reason: str = "",
    ) -> None:
        """Enable group editing only when an attributed data source is loaded."""
        self.manage_groups_button.setEnabled(enabled)
        self.manage_groups_button.setToolTip("" if enabled else reason)

    def set_group_launch_available(
        self,
        enabled: bool,
        ready_count: int = 0,
        reason: str = "",
    ) -> None:
        """Apply shared batch-launch eligibility to the Characters toolbar."""
        if self._group_launch_in_progress:
            return
        self._group_launch_available = bool(enabled)
        self._group_launch_ready_count = max(0, int(ready_count))
        self._group_launch_reason = "" if enabled else reason
        self._update_group_launch_button()

    def set_group_launch_progress(
        self,
        attempted: int,
        total: int,
        succeeded: int,
        group_name: str | None = None,
    ) -> None:
        """Turn the bulk action into a queue cancellation control."""
        self._group_launch_in_progress = True
        self.group_combo.setEnabled(False)
        label = f" {group_name.upper()}" if group_name else ""
        self.launch_group_button.setText(
            f"LAUNCHING{label} {attempted} OF {total}…"
        )
        self.launch_group_button.setEnabled(True)
        self.launch_group_button.setToolTip(
            "Cancel remaining queued launches; clients already started will continue"
        )

    def finish_group_launch_progress(self) -> None:
        """Restore group controls after the serial queue completes."""
        self._group_launch_in_progress = False
        self.group_combo.setEnabled(True)
        self._update_group_launch_button()

    def _selected_group(self):
        return self._group_state.selected_group

    def _selected_group_member_ids(self) -> set[int] | None:
        group = self._selected_group()
        if group is None:
            return None
        return {member.character_id for member in group.members}

    def _update_group_launch_button(self) -> None:
        if self._group_launch_in_progress:
            return
        group = self._selected_group()
        target = group.name.upper() if group is not None else "ALL"
        count = self._group_launch_ready_count
        self.launch_group_button.setText(f"LAUNCH {target} ({count})")
        self.launch_group_button.setEnabled(self._group_launch_available)
        self.launch_group_button.setToolTip(
            "Launch every ready character in this group"
            if self._group_launch_available and group is not None
            else (
                "Launch every eligible visible account"
                if self._group_launch_available
                else self._group_launch_reason
            )
        )

    def _on_group_combo_changed(self, index: int) -> None:
        group_id = self.group_combo.itemData(index)
        self.group_selection_changed.emit(group_id)

    def _emit_group_launch_action(self) -> None:
        if self._group_launch_in_progress:
            self.cancel_group_launches_requested.emit()
        else:
            self.launch_group_requested.emit()

    def set_data_error(self, message: str) -> None:
        """Distinguish an unreadable roster from a genuinely empty one.

        Without this, a failed EveJS read renders exactly like a store with no
        characters, so the launcher appears to have lost pilots that are still
        present.  The message is already bounded and redacted by the worker.
        """
        message = str(message or "").strip()
        if message == self._data_error:
            return
        self._data_error = message
        self.count_label.setToolTip(message)
        self.count_label.setProperty("state", "error" if message else "")
        self.count_label.style().unpolish(self.count_label)
        self.count_label.style().polish(self.count_label)
        self.page_header.set_subtitle(message or _ROSTER_SUBTITLE)
        self._apply_filter(self.search_edit.text())

    def set_character_creation_available(
        self,
        enabled: bool,
        reason: str = "",
    ) -> None:
        """Enable the Native-only new-character tile independently of launch."""
        self._character_creation_available = bool(enabled)
        self._character_creation_reason = "" if enabled else reason
        if self._new_character_card is not None:
            self._new_character_card.set_available(enabled, reason)

    def set_account_launching(
        self,
        username: str,
        character_name: str,
        launching: bool,
    ) -> None:
        """Apply launch-pending state immediately and preserve it across refreshes."""
        if launching:
            self._launching_accounts[username] = character_name
        elif self._launching_accounts.get(username) == character_name:
            self._launching_accounts.pop(username, None)

        for (card_username, _char_id), card in self._cards.items():
            if card_username == username:
                char = self._find_character(card_username, card.char_id)
                if char is not None:
                    card.set_status(self._status_for(card_username, char))

        selected_username, selected_character, _char_id = (
            self.detail_panel.get_character()
        )
        self.detail_panel.set_launch_pending(
            self._launching_accounts.get(selected_username) == selected_character
        )
        if self.status_combo.currentData() is not None:
            self._apply_filter(self.search_edit.text())

    # ── Grid transition animation ─────────────────────────────────────────────

    _TRANSITION_DURATION = 300  # ms

    @property
    def animations_enabled(self) -> bool:
        """Whether the character detail command pane may transition."""
        return self._motion.animations_enabled

    def set_animations_enabled(self, enabled: bool) -> None:
        """Apply reduced motion immediately, settling any active transition."""
        self._motion.set_reduced_motion(not bool(enabled))
        if not self._motion.animations_enabled:
            self._cancel_transition()

    def _animate_detail_transition(self, show: bool) -> None:
        """Reveal the selected detail command pane without moving the backdrop."""
        if self._transition_group is not None:
            self._transition_group.stop()
            self._transition_group.deleteLater()
            self._transition_group = None

        if not self._motion.animations_enabled:
            self._settle_detail_transition(show)
            return

        start_height = self.detail_panel.maximumHeight()
        end_height = _DETAIL_HEIGHT if show else 0
        if show:
            self.detail_panel.show()
        if start_height == end_height:
            self.detail_panel.setMaximumHeight(end_height)
            if not show:
                self.detail_panel.hide()
                self.detail_panel.show_empty()
            return

        self._animating = True
        group = QParallelAnimationGroup(self)
        panel_animation = QPropertyAnimation(
            self.detail_panel,
            b"maximumHeight",
            group,
        )
        panel_animation.setStartValue(start_height)
        panel_animation.setEndValue(end_height)
        self._motion.configure(
            panel_animation,
            "page",
            QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic,
        )
        group.addAnimation(panel_animation)
        group.finished.connect(self._finish_detail_transition)
        self._transition_group = group
        group.start()

    def _animate_detail_transition_legacy(self, show: bool) -> None:
        """Animate cards repositioning + detail panel sliding in/out."""

        # Debounce: skip if an animation is already in progress.
        if self._animating:
            return

        # Nothing to animate if all cards are filtered out.
        visible_cards = {
            key: card
            for key, card in self._cards.items()
            if card.isVisible()
        }
        if not visible_cards:
            self.detail_panel.setMaximumWidth(280 if show else 0)
            return

        self._animating = True

        # ── 1. Snapshot current card geometries ──────────────────────────
        old_geos: dict[tuple[str, int], QRect] = {}
        for key, card in visible_cards.items():
            old_geos[key] = QRect(card.geometry())

        # ── 2. Hide all cards so the user doesn't see the "snap" ────────
        for card in self._cards.values():
            card.hide()

        # ── 3. Disable panel paints, snap to target, capture new card positions
        self.detail_panel.setUpdatesEnabled(False)
        self.detail_panel.setMaximumWidth(280 if show else 0)
        QApplication.instance().processEvents()

        self._relayout_grid()
        QApplication.instance().processEvents()

        new_geos: dict[tuple[str, int], QRect] = {}
        for key, card in visible_cards.items():
            if key in old_geos:
                new_geos[key] = QRect(card.geometry())

        # ── 4. Remove cards from grid, snap panel back to start ─────────
        for card in self._cards.values():
            self._grid.removeWidget(card)

        self.detail_panel.setMaximumWidth(0 if show else 280)
        QApplication.instance().processEvents()
        self.detail_panel.setUpdatesEnabled(True)

        # ── 5. Show cards at their old positions ────────────────────────
        for key, card in visible_cards.items():
            if key in old_geos:
                card.move(old_geos[key].topLeft())
                card.resize(old_geos[key].size())
            card.show()

        # ── 6. Build animation group ────────────────────────────────────
        group = QParallelAnimationGroup()
        group.setObjectName("detailTransition")
        DUR = self._TRANSITION_DURATION

        for key, card in visible_cards.items():
            if key in old_geos and key in new_geos:
                if old_geos[key] != new_geos[key]:
                    anim = QPropertyAnimation(card, b"geometry")
                    anim.setDuration(DUR)
                    anim.setStartValue(old_geos[key])
                    anim.setEndValue(new_geos[key])
                    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
                    group.addAnimation(anim)

        # Detail panel width slides in/out
        panel_anim = QPropertyAnimation(self.detail_panel, b"maximumWidth")
        panel_anim.setDuration(DUR)
        panel_anim.setStartValue(0 if show else 280)
        panel_anim.setEndValue(280 if show else 0)
        panel_anim.setEasingCurve(
            QEasingCurve.Type.OutCubic if show else QEasingCurve.Type.InCubic
        )
        group.addAnimation(panel_anim)

        # ── 7. On finish, restore grid layout ───────────────────────────
        group.finished.connect(self._finish_detail_transition)
        self._transition_group = group
        group.start()

    def _finish_detail_transition(self) -> None:
        """Settle the compact detail pane after its height transition."""
        self._animating = False
        group = self._transition_group
        self._transition_group = None
        if group is not None:
            group.deleteLater()
        if self.detail_panel.maximumHeight() <= 0:
            self.detail_panel.setMaximumHeight(0)
            self.detail_panel.hide()
            self.detail_panel.show_empty()
        else:
            self.detail_panel.setMaximumHeight(_DETAIL_HEIGHT)
            self.detail_panel.show()
        self._relayout_grid()

    def _finish_detail_transition_legacy(self) -> None:
        """Called when the card/detail-panel animation group finishes."""
        self._animating = False

        # Re-add all visible cards to the grid layout so future layout
        # changes (resize, filter) work normally.
        for _, card in self._cards.items():
            self._grid.removeWidget(card)
        self._relayout_grid()

        if self._transition_group is not None:
            self._transition_group.deleteLater()
            self._transition_group = None

        # Hide panel completely when collapsed so it doesn't eat events.
        if self.detail_panel.maximumWidth() == 0:
            self.detail_panel.hide()
        else:
            self.detail_panel.show()

    def _cancel_transition(self) -> None:
        """Stop any in-progress transition and jump to the final state."""
        self._settle_detail_transition(self._selected_key is not None)

    def _settle_detail_transition(self, show: bool) -> None:
        """Stop transition ownership and render its deterministic end state."""
        group = self._transition_group
        self._transition_group = None
        if group is not None:
            group.stop()
            group.deleteLater()
        self._animating = False
        target = _DETAIL_HEIGHT if show else 0
        self.detail_panel.setMaximumHeight(target)
        if show:
            self.detail_panel.show()
        else:
            self.detail_panel.hide()
            self.detail_panel.show_empty()
        self._relayout_grid()

    # ── Filtering ────────────────────────────────────────────────────────────
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        group_member_ids = self._selected_group_member_ids()
        status_filter = self.status_combo.currentData()
        if self._new_character_card is not None:
            self._new_character_card.setVisible(
                status_filter is None
                and (
                    not needle
                    or any(
                        needle in value
                        for value in (
                            "new character",
                            "create character",
                            "new account",
                        )
                    )
                )
            )
        visible = 0
        for (username, _char_id), card in self._cards.items():
            in_group = (
                group_member_ids is None or card.char_id in group_member_ids
            )
            in_status = status_filter is None or card._status == status_filter
            if not needle:
                match = in_group and in_status
            else:
                haystacks = (
                    username.lower(),
                    card.char_name.lower(),
                    (card.ship or "").lower(),
                )
                match = (
                    in_group
                    and in_status
                    and any(needle in h for h in haystacks)
                )
            card.setVisible(match)
            if match:
                visible += 1
        total = len(self._cards)
        group_total = (
            total
            if group_member_ids is None
            else sum(card.char_id in group_member_ids for card in self._cards.values())
        )
        if self._data_error and not self._cards:
            self.count_label.setText("DATA UNAVAILABLE")
        elif needle or status_filter is not None:
            self.count_label.setText(f"({visible} / {group_total})")
        elif group_member_ids is not None:
            self.count_label.setText(f"({group_total} of {total})")
        else:
            self.count_label.setText(f"({total})")

        if self._selected_key is not None:
            selected_card = self._cards.get(self._selected_key)
            if selected_card is not None and selected_card.isHidden():
                self.clear_selection()
        self._relayout_grid()

    # ── Selection ─────────────────────────────────────────────────────
    def _on_card_selected(self, username: str, char_name: str, char_id: int) -> None:
        new_key = (username, char_id)

        # Toggle: clicking the already-selected card deselects it
        if self._selected_key == new_key:
            self.clear_selection()
            return

        # Deselect previous
        if self._selected_key is not None:
            prev_card = self._cards.get(self._selected_key)
            if prev_card is not None:
                prev_card.set_selected(False)

        self._selected_key = new_key

        # Select new
        card = self._cards.get(new_key)
        if card is not None:
            card.set_selected(True)

        # If the panel is already visible (switching between cards), just
        # swap the content without re-animating the panel.
        panel_already_open = self.detail_panel.maximumHeight() > 0
        if not panel_already_open:
            self.detail_panel.show()
            self._animate_detail_transition(show=True)
        self._show_in_detail(username, char_id)
        self.character_selected.emit(username, char_name, char_id)

    def _on_detail_launch(self) -> None:
        username, char_name, char_id = self.detail_panel.get_character()
        if username and char_name and char_id > 0:
            self.launch_character.emit(username, char_name, char_id)

    def _show_in_detail(self, username: str, char_id: int) -> None:
        char = self._find_character(username, char_id)
        if char is None:
            return
        card = self._cards.get((username, char_id))
        portrait = card._portrait_pixmap if card is not None else None
        self.detail_panel.show_character(
            username,
            char.name,
            char.char_id,
            portrait,
            {
                "ISK": card.isk if card is not None else "—",
                "SP": card.sp if card is not None else "—",
                "Ship": card.ship if card is not None else "—",
                "Location": card.location if card is not None else "—",
                "Sec Status": card.sec_status if card is not None else "—",
            },
        )
        self.detail_panel.set_launch_pending(
            self._launching_accounts.get(username) == char.name
        )

    def apply_character_detail(
        self,
        username: str,
        char_id: int,
        detail: dict,
    ) -> bool:
        """Apply a current worker result only to the still-selected character."""
        key = (username, char_id)
        if self._selected_key != key:
            return False
        char = self._find_character(username, char_id)
        card = self._cards.get(key)
        if char is None or card is None:
            return False

        balance = detail.get("balance")
        skill_points = detail.get("skillPoints")
        security_status = detail.get("securityStatus")
        ship_name = detail.get("shipName")
        location = detail.get("solarSystemName")
        stats = {
            "ISK": _fmt_isk(int(balance))
            if isinstance(balance, (int, float)) and not isinstance(balance, bool)
            else card.isk,
            "SP": _fmt_sp(int(skill_points))
            if isinstance(skill_points, (int, float))
            and not isinstance(skill_points, bool)
            else card.sp,
            "Ship": ship_name if isinstance(ship_name, str) and ship_name else card.ship,
            "Location": location
            if isinstance(location, str) and location
            else card.location,
            "Sec Status": f"{float(security_status):.1f}"
            if isinstance(security_status, (int, float))
            and not isinstance(security_status, bool)
            else card.sec_status,
        }
        self.detail_panel.show_character(
            username,
            char.name,
            char.char_id,
            card._portrait_pixmap,
            stats,
        )
        self.detail_panel.set_launch_pending(
            self._launching_accounts.get(username) == char.name
        )
        return True

    def _find_character(self, username: str, char_id: int) -> Character | None:
        for u, char in self._rotation_pool:
            if u == username and char.char_id == char_id:
                return char
        return None

    def clear_selection(self) -> None:
        """Deselect and animate the detail panel closed."""
        if self._selected_key is not None:
            prev_card = self._cards.get(self._selected_key)
            if prev_card is not None:
                prev_card.set_selected(False)
        self._selected_key = None
        self._animate_detail_transition(show=False)

    def _on_detail_hide(self) -> None:
        """Hide the character currently shown in the detail panel."""
        _username, char_name, _char_id = self.detail_panel.get_character()
        if char_name:
            self.hide_character.emit(char_name)

    def _on_card_hide_requested(self, character_name: str) -> None:
        """Hide a character from its card's overflow menu."""
        self.hide_character.emit(character_name)

    # ── Click on empty grid space → deselect ───────────────────────────────
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self.search_edit.text():
                self.search_edit.clear()
            elif self._selected_key is not None:
                self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._grid_container and event.type() == event.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                # Check if the click landed on a CharacterCard — if so, let it through
                pos = event.position().toPoint()
                child = self._grid_container.childAt(pos)
                if child is None:
                    self.clear_selection()
                    return False
                # Walk up to see if the clicked widget is inside a CharacterCard
                while child is not None:
                    if isinstance(child, (CharacterCard, NewCharacterCard)):
                        return False  # Let the card handle it
                    child = child.parentWidget()
                # Clicked on empty space — deselect
                self.clear_selection()
        return super().eventFilter(obj, event)
