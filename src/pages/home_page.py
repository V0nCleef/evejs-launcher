"""Home page for EveJS Launcher V2.

Layout
------
+---------------------------------------------------------------+
|  HeroBanner (176 px)                                          |
+---------------------------------------------------------------+
|  [Accounts] [Characters] [Running Clients] [Server Status]    |
+---------------------------------------------------------------+
|  [Launch All]  [Start All Servers]  [Kill All]                |
+-------------------------------+-------------------------------+
|  Latest release               |  Compact Resources             |
+-------------------------------+-------------------------------+
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS
from src.core.groups import TargetGroupState
from src.core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
)
from src.widgets.hero_banner import HeroBanner

DISCORD_INVITE_URL = "https://discord.gg/HVTfKeqX3t"
_CHANGELOG_PATH = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"


def extract_latest_release(text: str, *, limit: int = 3) -> tuple[str, list[str]]:
    """Return the newest changelog heading and a capped list of its bullets."""
    lines = text.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("## v")),
        None,
    )
    if header_index is None:
        return "Latest release unavailable", []

    version = lines[header_index].removeprefix("## ").strip()
    highlights: list[str] = []
    for line in lines[header_index + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            highlights.append(line.removeprefix("- ").strip())
            if len(highlights) >= limit:
                break
    return version, highlights


class StatCard(QFrame):
    """Mini stat card showing a big number over a small label."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 26px; font-weight: 700;"
        )
        layout.addWidget(self.value_label)

        name_label = QLabel(label.upper())
        name_label.setProperty("class", "muted")
        layout.addWidget(name_label)

    def set_value(self, value: str | int) -> None:
        self.value_label.setText(str(value))


class ServerStatusCard(QFrame):
    """Compatibility name retained for imports during the Home transition."""


class ServiceRow(QFrame):
    """Keyboard-accessible service state row that opens its console."""

    activated = pyqtSignal(str)

    def __init__(
        self,
        service_key: str,
        label: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service_key = service_key
        self._state_text = "Offline"
        self._detail_text = ""
        self.setProperty("class", "serviceRow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{label} service status")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(7)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(12)
        layout.addWidget(self._dot)

        name_label = QLabel(label.upper())
        name_label.setProperty("class", "eyebrow")
        name_label.setFixedWidth(48)
        layout.addWidget(name_label)

        self._state_label = QLabel(self._state_text)
        self._state_label.setProperty("class", "serviceState")
        layout.addWidget(self._state_label)

        layout.addStretch()
        self._detail_label = QLabel()
        self._detail_label.setProperty("class", "muted")
        self._detail_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._detail_label)

        self.set_state(ServiceState.OFFLINE)

    @property
    def state_text(self) -> str:
        return self._state_text

    @property
    def detail_text(self) -> str:
        return self._detail_text

    def set_online(self, online: bool) -> None:
        self.set_state(ServiceState.ONLINE if online else ServiceState.OFFLINE)

    def set_state(
        self,
        state: ServiceState,
        *,
        pid: int | None = None,
        container: str | None = None,
        health: str | None = None,
        error: str | None = None,
    ) -> None:
        """Render a service lifecycle state and optional owned-process detail."""
        labels = {
            ServiceState.OFFLINE: ("Offline", COLORS["grey"]),
            ServiceState.STARTING: ("Starting…", COLORS["gold"]),
            ServiceState.ONLINE: ("Online", COLORS["green"]),
            ServiceState.STOPPING: ("Stopping…", COLORS["gold"]),
            ServiceState.FAILED: ("Failed", COLORS["red"]),
            ServiceState.UNKNOWN: ("Unknown", COLORS["gold"]),
        }
        self._state_text, color = labels[state]
        self._detail_text = error if state in {ServiceState.FAILED, ServiceState.UNKNOWN} and error else ""
        if not self._detail_text and pid is not None:
            self._detail_text = f"PID {pid}"
        if not self._detail_text and container:
            self._detail_text = f"Container {container}" + (f" ({health})" if health else "")

        self._dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self._state_label.setText(self._state_text)
        detail_display = self._detail_text
        if len(detail_display) > 30:
            detail_display = f"{detail_display[:27]}…"
        self._detail_label.setText(detail_display)
        self._detail_label.setToolTip(self._detail_text)
        self.setAccessibleDescription(
            f"{self._state_text}. {self._detail_text}".strip()
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Space,
        ):
            self.activated.emit(self._service_key)
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit(self._service_key)
        super().mouseReleaseEvent(event)

class ServicesCard(QFrame):
    """Operational card showing Game and Market independently."""

    console_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(104)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(1)

        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 6, 0)
        title = QLabel("SERVICES")
        title.setProperty("class", "eyebrow")
        header.addWidget(title)
        header.addStretch()
        self.mode_label = QLabel("ASK ON START")
        self.mode_label.setProperty("class", "muted")
        header.addWidget(self.mode_label)
        layout.addLayout(header)

        self.game_row = ServiceRow("server", "Game")
        self.market_row = ServiceRow("market", "Market")
        self.game_row.activated.connect(self.console_requested.emit)
        self.market_row.activated.connect(self.console_requested.emit)
        layout.addWidget(self.game_row)
        layout.addWidget(self.market_row)

    def set_mode(self, label: str) -> None:
        self.mode_label.setText(label.upper())

    def apply_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self.game_row.set_state(
            snapshot.game,
            pid=snapshot.game_pid,
            container=snapshot.game_container,
            health=snapshot.game_health,
            error=snapshot.game_error,
        )
        self.market_row.set_state(
            snapshot.market,
            pid=snapshot.market_pid,
            container=snapshot.market_container,
            health=snapshot.market_health,
            error=snapshot.market_error,
        )


class LatestReleaseCard(QFrame):
    """Compact summary of the newest release rather than the full archive."""

    view_full_changelog_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(164)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("LATEST RELEASE")
        title.setProperty("class", "sectionTitle")
        layout.addWidget(title)

        self.version_label = QLabel()
        self.version_label.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 15px; font-weight: 700;"
        )
        layout.addWidget(self.version_label)

        self.highlights_label = QLabel()
        self.highlights_label.setProperty("class", "muted")
        self.highlights_label.setWordWrap(True)
        layout.addWidget(self.highlights_label, stretch=1)

        view_button = QPushButton("View Full Changelog")
        view_button.setProperty("class", "ghost")
        view_button.setCursor(Qt.CursorShape.PointingHandCursor)
        view_button.setFixedHeight(28)
        view_button.clicked.connect(self.view_full_changelog_requested.emit)
        layout.addWidget(view_button, alignment=Qt.AlignmentFlag.AlignLeft)

    def set_release(self, version: str, highlights: list[str]) -> None:
        """Render a bounded release summary suitable for the dashboard."""
        self.version_label.setText(version)
        self.highlights_label.setText(
            "\n".join(f"• {highlight}" for highlight in highlights)
            or "No release highlights are available."
        )


class ResourcesCard(QFrame):
    """Compact community, release, and diagnostic shortcuts."""

    console_requested = pyqtSignal(str)
    changelog_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(164)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        title = QLabel("RESOURCES")
        title.setProperty("class", "sectionTitle")
        layout.addWidget(title)

        blurb = QLabel("Community, release notes, and service diagnostics.")
        blurb.setProperty("class", "muted")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        links = QHBoxLayout()
        links.setSpacing(6)
        self.btn_discord = self._make_button("Discord")
        self.btn_discord.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(DISCORD_INVITE_URL))
        )
        self.btn_changelog = self._make_button("Changelog")
        self.btn_changelog.clicked.connect(self.changelog_requested.emit)
        links.addWidget(self.btn_discord)
        links.addWidget(self.btn_changelog)
        layout.addLayout(links)

        consoles = QHBoxLayout()
        consoles.setSpacing(6)
        self.btn_game_console = self._make_button("Game Console")
        self.btn_market_console = self._make_button("Market Console")
        self.btn_game_console.clicked.connect(
            lambda: self.console_requested.emit("server")
        )
        self.btn_market_console.clicked.connect(
            lambda: self.console_requested.emit("market")
        )
        consoles.addWidget(self.btn_game_console)
        consoles.addWidget(self.btn_market_console)
        layout.addLayout(consoles)

    @staticmethod
    def _make_button(label: str) -> QPushButton:
        button = QPushButton(label)
        button.setProperty("class", "compactGhost")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(32)
        return button


class HomePage(QWidget):
    """Landing page with operational metrics, actions, and compact resources."""

    launch_all_clicked = pyqtSignal()
    cancel_launches_clicked = pyqtSignal()
    group_selection_changed = pyqtSignal(object)  # group ID or None
    manage_groups_requested = pyqtSignal()
    start_servers_clicked = pyqtSignal()
    stop_servers_clicked = pyqtSignal()
    kill_all_clicked = pyqtSignal()
    console_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stack_action = "start"
        self._launch_in_progress = False
        self._group_state = TargetGroupState()
        self._launch_available = True
        self._launch_unavailable_reason = ""
        self._launch_ready_count = 0
        self._build_ui()
        self.set_group_state(TargetGroupState())
        self._load_latest_release()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 0, 16, 16)
        root.setSpacing(12)

        # Hero banner
        self.hero = HeroBanner(self)
        self.hero.setFixedHeight(HeroBanner.HEIGHT)
        root.addWidget(self.hero)

        # Stats row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.accounts_card = StatCard("Accounts")
        self.characters_card = StatCard("Characters")
        self.running_card = StatCard("Running Clients")
        self.services_card = ServicesCard()
        self.services_card.console_requested.connect(self.console_requested.emit)
        # Compatibility alias for the former single-server card API.
        self.server_card = self.services_card.game_row
        for card in (
            self.accounts_card,
            self.characters_card,
            self.running_card,
            self.services_card,
        ):
            stats_row.addWidget(card)
        root.addLayout(stats_row)

        # Quick actions row
        actions = QHBoxLayout()
        actions.setSpacing(12)

        launch_box = QWidget()
        launch_box.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        launch_layout = QHBoxLayout(launch_box)
        launch_layout.setContentsMargins(0, 0, 0, 0)
        launch_layout.setSpacing(6)

        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(130)
        self.group_combo.setMaximumWidth(190)
        self.group_combo.setFixedHeight(48)
        self.group_combo.currentIndexChanged.connect(self._on_group_combo_changed)
        launch_layout.addWidget(self.group_combo)

        self.btn_launch_all = QPushButton("Launch All")
        self.btn_launch_all.setProperty("class", "primary")
        self.btn_launch_all.setFixedHeight(48)
        self.btn_launch_all.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_launch_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch_all.clicked.connect(self._emit_launch_action)
        launch_layout.addWidget(self.btn_launch_all, stretch=1)
        actions.addWidget(launch_box)

        self.btn_start_servers = QPushButton("Start Stack")
        self.btn_start_servers.setProperty("class", "secondary")
        self.btn_start_servers.setFixedHeight(48)
        self.btn_start_servers.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_start_servers.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_servers.clicked.connect(self._emit_stack_action)
        actions.addWidget(self.btn_start_servers)

        self.btn_kill_all = QPushButton("Kill All Clients")
        self.btn_kill_all.setProperty("class", "dangerOutline")
        self.btn_kill_all.setFixedHeight(48)
        self.btn_kill_all.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_kill_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kill_all.clicked.connect(self.kill_all_clicked.emit)
        actions.addWidget(self.btn_kill_all)

        root.addLayout(actions)

        # Compact lower row: latest release + fast operational resources.
        lower_row = QHBoxLayout()
        lower_row.setSpacing(12)
        self.release_card = LatestReleaseCard()
        self.resources_card = ResourcesCard()
        self.release_card.view_full_changelog_requested.connect(self._open_full_changelog)
        self.resources_card.changelog_requested.connect(self._open_full_changelog)
        self.resources_card.console_requested.connect(self.console_requested.emit)
        lower_row.addWidget(self.release_card, stretch=3)
        lower_row.addWidget(self.resources_card, stretch=2)
        root.addLayout(lower_row)
        root.addStretch(1)

    # ── Data ─────────────────────────────────────────────────────────────────
    def _load_latest_release(self) -> None:
        """Load only the latest release highlights into the compact summary."""
        try:
            if _CHANGELOG_PATH.exists():
                text = _CHANGELOG_PATH.read_text(encoding="utf-8")
                version, highlights = extract_latest_release(text)
            else:
                version, highlights = "Latest release unavailable", []
        except OSError:
            version, highlights = "Latest release unavailable", []
        self.release_card.set_release(version, highlights)

    @staticmethod
    def _open_full_changelog() -> None:
        """Open the local full changelog only when the user asks for it."""
        if _CHANGELOG_PATH.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_CHANGELOG_PATH)))

    # ── Public API ───────────────────────────────────────────────────────────
    def set_stats(
        self,
        accounts: int,
        characters: int,
        running_clients: int,
        server_online: bool,
    ) -> None:
        """Update the four stat cards."""
        self.accounts_card.set_value(accounts)
        self.characters_card.set_value(characters)
        self.running_card.set_value(running_clients)
        self.server_card.set_online(server_online)

    def set_character_stats(self, accounts: int, characters: int) -> None:
        """Update account/character metrics without disturbing runtime state."""
        self.accounts_card.set_value(accounts)
        self.characters_card.set_value(characters)

    def set_server_online(self, online: bool) -> None:
        """Update only the server status mini card."""
        self.server_card.set_online(online)

    def set_server_state(self, state: ServiceState) -> None:
        """Update only the game-service lifecycle state."""
        self.server_card.set_state(state)

    def set_server_mode(self, label: str) -> None:
        """Show the configured server-mode policy without exposing its path."""
        self.services_card.set_mode(label)

    def set_group_state(self, state: TargetGroupState) -> None:
        """Synchronize the quick-launch selector with the Characters page."""
        self._group_state = state
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("All Visible", None)
        selected_index = 0
        for index, group in enumerate(state.groups, start=1):
            self.group_combo.addItem(
                f"{group.name} ({len(group.members)})",
                group.group_id,
            )
            if group.group_id == state.selected_group_id:
                selected_index = index
        self.group_combo.insertSeparator(self.group_combo.count())
        self.group_combo.addItem("Manage Groups…", "__manage_groups__")
        self.group_combo.setCurrentIndex(selected_index)
        self.group_combo.blockSignals(False)
        self._restore_launch_button()

    def set_launch_available(
        self,
        available: bool,
        reason: str = "",
        ready_count: int | None = None,
    ) -> None:
        """Enable Launch All only when at least one account can be launched."""
        if self._launch_in_progress:
            return
        self._launch_available = bool(available)
        self._launch_unavailable_reason = "" if available else reason
        if ready_count is not None:
            self._launch_ready_count = max(0, int(ready_count))
        self._restore_launch_button()

    def set_launch_progress(
        self,
        attempted: int,
        total: int,
        succeeded: int,
        group_name: str | None = None,
    ) -> None:
        """Make Launch All a cancellation control while its serial queue runs."""
        self._launch_in_progress = True
        self.group_combo.setEnabled(False)
        prefix = f"Launching {group_name}" if group_name else "Launching"
        self.btn_launch_all.setText(f"{prefix} {attempted} of {total}…")
        self.btn_launch_all.setEnabled(True)
        self.btn_launch_all.setToolTip(
            "Cancel remaining queued launches; clients already started will continue running"
        )

    def finish_launch_progress(
        self,
        attempted: int,
        succeeded: int,
        cancelled: bool,
    ) -> None:
        """Restore the primary action after its serial launch queue finishes."""
        self._launch_in_progress = False
        self.group_combo.setEnabled(True)
        self._restore_launch_button()
        if cancelled:
            self.btn_launch_all.setToolTip(
                f"Cancelled after launching {succeeded} of {attempted} account(s)"
            )

    def _restore_launch_button(self) -> None:
        if self._launch_in_progress:
            return
        group = self._group_state.selected_group
        if group is None:
            self.btn_launch_all.setText("Launch All")
        else:
            self.btn_launch_all.setText(
                f"Launch {group.name} ({self._launch_ready_count})"
            )
        self.btn_launch_all.setEnabled(self._launch_available)
        if not self._launch_available:
            tooltip = self._launch_unavailable_reason
        elif group is None:
            tooltip = "Launch every eligible visible account"
        else:
            tooltip = f"Launch every ready character in {group.name}"
        self.btn_launch_all.setToolTip(tooltip)

    def _on_group_combo_changed(self, index: int) -> None:
        value = self.group_combo.itemData(index)
        if value == "__manage_groups__":
            self.manage_groups_requested.emit()
            self.set_group_state(self._group_state)
            return
        self.group_selection_changed.emit(value)

    def apply_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        """Apply the authoritative runtime observation to Home."""
        self.running_card.set_value(snapshot.running_clients)
        self.services_card.apply_snapshot(snapshot)
        self._update_stack_action(snapshot)
        has_running_clients = snapshot.running_clients > 0
        self.btn_kill_all.setEnabled(has_running_clients)
        self.btn_kill_all.setToolTip(
            "Terminate every running EVE client"
            if has_running_clients
            else "No EVE clients are running"
        )

    def _update_stack_action(self, snapshot: RuntimeSnapshot) -> None:
        """Describe the next safe stack operation from the shared snapshot."""
        if snapshot.backend is RuntimeBackend.DOCKER_COMPOSE:
            states = {snapshot.game, snapshot.market}
            if snapshot.docker_control_policy is DockerControlPolicy.CONNECT_ONLY:
                label, enabled, tooltip = (
                    "Docker Stack (observing)", False,
                    "Connect-only Docker mode cannot change containers.",
                )
            elif ServiceState.STARTING in states:
                label, enabled, tooltip = "Starting…", False, "Services are starting"
            elif ServiceState.STOPPING in states:
                label, enabled, tooltip = "Stopping…", False, "Services are stopping"
            elif ServiceState.UNKNOWN in states:
                label, enabled, tooltip = "Docker unavailable", False, "Docker state is unavailable"
            elif ServiceState.ONLINE in states:
                label, enabled, tooltip = "Stop Stack", True, "Stop all Docker Compose services"
                self._stack_action = "stop"
            elif ServiceState.FAILED in states:
                label, enabled, tooltip = "Retry Stack", True, "Retry failed services"
                self._stack_action = "start"
            else:
                label, enabled, tooltip = "Start Stack", True, "Start the Docker Compose stack"
                self._stack_action = "start"
            if not enabled:
                self._stack_action = "none"
            self.btn_start_servers.setText(label)
            self.btn_start_servers.setEnabled(enabled)
            self.btn_start_servers.setToolTip(tooltip)
            return
        services = (
            (snapshot.game, snapshot.game_owned),
            (snapshot.market, snapshot.market_owned),
        )
        states = {state for state, _owned in services}
        external_online = any(
            state is ServiceState.ONLINE and not owned
            for state, owned in services
        )
        managed_online = any(
            state is ServiceState.ONLINE and owned
            for state, owned in services
        )
        if ServiceState.STOPPING in states:
            label, enabled, tooltip = "Stopping…", False, "Services are stopping"
        elif ServiceState.STARTING in states:
            label, enabled, tooltip = "Starting…", False, "Services are starting"
        elif ServiceState.FAILED in states:
            label = "Retry Managed Services" if external_online else "Retry Stack"
            enabled = True
            tooltip = (
                "The external service will remain running"
                if external_online
                else "Retry failed services"
            )
            self._stack_action = "start"
        elif managed_online:
            label = "Stop Managed Services" if external_online else "Stop Stack"
            enabled = True
            tooltip = (
                "Only services started by this launcher can be stopped; "
                "external services must be stopped from their original console"
                if external_online
                else "Stop all services started by this launcher"
            )
            self._stack_action = "stop"
        elif external_online:
            if ServiceState.OFFLINE in states:
                label, enabled = "Start Managed Services", True
                tooltip = "The external service will remain running"
                self._stack_action = "start"
            else:
                label, enabled = "Managed Externally", False
                tooltip = (
                    "All online services were started outside this launcher; "
                    "stop them from their original console"
                )
                self._stack_action = "none"
        else:
            label, enabled, tooltip = "Start Stack", True, "Start the service stack"
            self._stack_action = "start"
        if not enabled:
            self._stack_action = "none"
        self.btn_start_servers.setText(label)
        self.btn_start_servers.setEnabled(enabled)
        self.btn_start_servers.setToolTip(tooltip)

    def _emit_stack_action(self) -> None:
        if self._stack_action == "stop":
            self.stop_servers_clicked.emit()
        elif self._stack_action == "start":
            self.start_servers_clicked.emit()

    def _emit_launch_action(self) -> None:
        if self._launch_in_progress:
            self.cancel_launches_clicked.emit()
        else:
            self.launch_all_clicked.emit()
