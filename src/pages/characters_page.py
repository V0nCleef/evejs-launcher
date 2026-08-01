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
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.constants import Status
from src.core.dashboard import visible_character_rows
from src.core.db import Account, Character, _fmt_isk, _fmt_sp
from src.core.process_tracker import ProcessTracker
from src.core.runtime.data import native_data_selection
from src.core.runtime.portraits import (
    PortraitImageResult,
    PortraitRequest,
    PortraitTarget,
)
from src.utils.cache import PortraitCache
from src.widgets.character_card import CharacterCard
from src.widgets.detail_panel import DetailPanel
from src.widgets.skeleton_card import SkeletonCard
from src.workers.portrait_worker import PortraitLoadFailure, PortraitLoader

GRID_COLUMNS = 3
GRID_SPACING = 12
_SKELETON_COUNT = 6


class CharactersPage(QWidget):
    """Character grid with search and detail panel."""

    launch_character = pyqtSignal(str, str)  # username, char_name
    character_selected = pyqtSignal(str, str, int)  # username, char_name, char_id
    hide_character = pyqtSignal(str)  # character_name
    portrait_loads_idle = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Keyed by (username, char_id) — survives re-sorting and filtering.
        self._cards: dict[tuple[str, int], CharacterCard] = {}
        # Flat list of (username, Character) for layout.
        self._rotation_pool: list[tuple[str, Character]] = []
        self._selected_key: tuple[str, int] | None = None
        self._tracker: ProcessTracker | None = None
        self._animating: bool = False
        self._transition_group: QParallelAnimationGroup | None = None


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

        self._build_ui()
        self.show_skeletons()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
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
    def show_skeletons(self, count: int = _SKELETON_COUNT) -> None:
        """Replace the grid with skeleton placeholder cards."""
        self.cancel_portrait_loads(invalidate=True)
        self._clear_grid()
        self._cards.clear()
        for i in range(count):
            skeleton = SkeletonCard()
            self._grid.addWidget(skeleton, i // GRID_COLUMNS, i % GRID_COLUMNS)
        self.count_label.setText("Loading…")

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if isinstance(widget, SkeletonCard):
                    widget.stop_pulsing()
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

        total = len(desired)
        self.count_label.setText(f"({total})")

        # Show/hide the detail panel based on selection state.
        if self._selected_key is None:
            self.detail_panel.setMaximumWidth(0)
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
        """Re-position all cards in row-major order."""
        for card in self._cards.values():
            self._grid.removeWidget(card)
        for i, (username, char) in enumerate(self._rotation_pool):
            card = self._cards.get((username, char.char_id))
            if card is not None:
                self._grid.addWidget(card, i // GRID_COLUMNS, i % GRID_COLUMNS)

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

    # ── Grid transition animation ─────────────────────────────────────────────

    _TRANSITION_DURATION = 300  # ms

    def _animate_detail_transition(self, show: bool) -> None:
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
        if self._transition_group is not None and self._transition_group.state() == 1:  # Running
            self._transition_group.stop()
        self._finish_detail_transition()

    # ── Filtering ────────────────────────────────────────────────────────────
    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        visible = 0
        for (username, _char_id), card in self._cards.items():
            if not needle:
                match = True
            else:
                haystacks = (
                    username.lower(),
                    card.char_name.lower(),
                    (card.ship or "").lower(),
                )
                match = any(needle in h for h in haystacks)
            card.setVisible(match)
            if match:
                visible += 1
        total = len(self._cards)
        self.count_label.setText(
            f"({visible} / {total})" if needle else f"({total})"
        )

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
        panel_already_open = self.detail_panel.maximumWidth() > 0
        if not panel_already_open:
            self.detail_panel.show()
            self._animate_detail_transition(show=True)
        self._show_in_detail(username, char_id)
        self.character_selected.emit(username, char_name, char_id)

    def _on_detail_launch(self) -> None:
        username, char_name, _char_id = self.detail_panel.get_character()
        if username and char_name:
            self.launch_character.emit(username, char_name)

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
                    if isinstance(child, CharacterCard):
                        return False  # Let the card handle it
                    child = child.parentWidget()
                # Clicked on empty space — deselect
                self.clear_selection()
        return super().eventFilter(obj, event)
