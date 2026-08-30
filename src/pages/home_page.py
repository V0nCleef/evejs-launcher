"""Deep Signal Operations page for EveJS Launcher V2.

The presentation deliberately keeps the original Home public API. Runtime
state still arrives exclusively through :class:`RuntimeSnapshot`; this module
only renders that already-authoritative observation.
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
    QStackedLayout,
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
from src.i18n import (
    format_ui_phrase,
    translate_ui_phrase,
)
from src.ui.motion import MotionController
from src.widgets.hero_banner import HeroBanner
from src.widgets.deep_signal_background import DeepSignalBackground
from src.widgets.docking_traffic_overlay import DockingTrafficOverlay
from src.widgets.page_header import PageHeader
from src.widgets.status_ring import StatusRing
from src.widgets.ui_translation import (
    register_translatable_widget_tree,
    set_translatable_accessible_name,
    set_translatable_text,
    set_translatable_text_template,
    set_translatable_tooltip,
    set_translatable_tooltip_template,
)

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
        self.setProperty("class", "signalMetric")
        self.setProperty("deepSignal", True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        self.value_label = QLabel("—")
        self.value_label.setProperty("class", "metricValue")
        layout.addWidget(self.value_label)

        name_label = QLabel(label.upper())
        name_label.setProperty("class", "muted")
        layout.addWidget(name_label)

    def set_value(self, value: str | int) -> None:
        self.value_label.setText(str(value))


class ServerStatusCard(QFrame):
    """Compatibility name retained for imports during the Home transition."""


class ServiceRow(QFrame):
    """Standalone keyboard-accessible service instrument."""

    activated = pyqtSignal(str)

    def __init__(
        self,
        service_key: str,
        label: str,
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
    ) -> None:
        super().__init__(parent)
        self._service_key = service_key
        self._state_text = "Offline"
        self._detail_text = ""
        self.setProperty("class", "signalInstrument")
        self.setProperty("deepSignal", True)
        self.setFixedHeight(132)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        set_translatable_accessible_name(
            self,
            f"{label} service status",
            allow_templates=True,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._ring = StatusRing(
            label,
            "OFF",
            state=ServiceState.OFFLINE,
            motion_controller=motion_controller,
        )
        self._ring.setFixedSize(80, 80)
        self._ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._ring)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 8, 0, 8)
        copy.setSpacing(4)
        name_line = QHBoxLayout()
        name_line.setContentsMargins(0, 0, 0, 0)
        name_line.setSpacing(5)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(12)
        name_line.addWidget(self._dot)

        self._name_label = QLabel(label.upper())
        self._name_label.setProperty("class", "signalInstrumentName")
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        name_line.addWidget(self._name_label)
        name_line.addStretch()
        copy.addLayout(name_line)

        self._state_label = QLabel(self._state_text)
        self._state_label.setProperty("class", "signalInstrumentState")
        self._state_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        copy.addWidget(self._state_label)

        self._detail_label = QLabel()
        self._detail_label.setProperty("class", "muted")
        self._detail_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._detail_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        copy.addWidget(self._detail_label)
        copy.addStretch()
        layout.addLayout(copy, 1)

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
            ServiceState.OFFLINE: ("Offline", COLORS["red"]),
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
        self._state_label.setStyleSheet(f"color: {color};")
        self.setProperty("statusState", state.value)
        self._state_label.setProperty("statusState", state.value)
        ring_values = {
            ServiceState.OFFLINE: "OFF",
            ServiceState.STARTING: "INIT",
            ServiceState.ONLINE: "ON",
            ServiceState.STOPPING: "STOP",
            ServiceState.FAILED: "ERR",
            ServiceState.UNKNOWN: "?",
        }
        self._ring.set_state(
            state,
            value=ring_values[state],
            detail="",
            progress=1.0 if state is ServiceState.ONLINE else 0.72,
        )
        set_translatable_text(self._state_label, self._state_text)
        set_translatable_tooltip(self._detail_label, self._detail_text)
        self._render_detail()
        self.setAccessibleDescription(
            f"{translate_ui_phrase(self._state_text)}. "
            f"{translate_ui_phrase(self._detail_text)}".strip()
        )

    def _render_detail(self) -> None:
        available = self._detail_label.contentsRect().width()
        rendered = translate_ui_phrase(self._detail_text)
        if available <= 0:
            self._detail_label.setText(rendered)
            return
        self._detail_label.setText(
            self._detail_label.fontMetrics().elidedText(
                rendered,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render_detail()

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
    """Compatibility controller for the two visible service instruments."""

    console_requested = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode_label = QLabel("ASK ON START")
        self.mode_label.setProperty("class", "muted")
        self.mode_label.setParent(self)
        self.mode_label.hide()

        # These instruments are reparented into Home's three-column signal rail.
        self.game_row = ServiceRow(
            "server",
            "Game",
            None,
            motion_controller=motion_controller,
        )
        self.market_row = ServiceRow(
            "market",
            "Market",
            None,
            motion_controller=motion_controller,
        )
        self.game_row.activated.connect(self.console_requested.emit)
        self.market_row.activated.connect(self.console_requested.emit)

    def set_mode(self, label: str) -> None:
        set_translatable_text(self.mode_label, label.upper())

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


class ClientSignalCard(QFrame):
    """Third truthful signal instrument driven by the observed client count."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        motion_controller: MotionController | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("class", "signalInstrument")
        self.setProperty("deepSignal", True)
        self.setFixedHeight(132)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.setAccessibleName("Clients status")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._ring = StatusRing(
            "Clients",
            "0",
            state=ServiceState.OFFLINE,
            motion_controller=motion_controller,
        )
        self._ring.setFixedSize(80, 80)
        layout.addWidget(self._ring)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 8, 0, 8)
        copy.setSpacing(4)

        name_line = QHBoxLayout()
        name_line.setContentsMargins(0, 0, 0, 0)
        name_line.setSpacing(5)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(12)
        name_line.addWidget(self._dot)

        self._name_label = QLabel("CLIENTS")
        self._name_label.setProperty("class", "signalInstrumentName")
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        name_line.addWidget(self._name_label)
        name_line.addStretch()
        copy.addLayout(name_line)

        self._state_label = QLabel("NONE RUNNING")
        self._state_label.setProperty("class", "signalInstrumentState")
        self._state_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        copy.addWidget(self._state_label)

        self._detail_label = QLabel("CAPSULES IDLE")
        self._detail_label.setProperty("class", "muted")
        self._detail_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        copy.addWidget(self._detail_label)
        copy.addStretch()
        layout.addLayout(copy, 1)

        # Compatibility metric consumed by controller and dashboard tests.
        self.value_label = QLabel("0", self)
        self.value_label.hide()
        self.set_value(0)

    def set_value(self, value: str | int) -> None:
        count = max(0, int(value))
        self.value_label.setText(str(count))
        state = ServiceState.ONLINE if count > 0 else ServiceState.OFFLINE
        color = COLORS["green"] if count > 0 else COLORS["red"]
        self._ring.set_state(
            state,
            value=count,
            detail="",
            progress=1.0 if count > 0 else 0.72,
        )
        set_translatable_text_template(
            self._state_label,
            f"{count} RUNNING" if count > 0 else "NONE RUNNING"
        )
        self._dot.setStyleSheet(f"color: {color}; font-size: 14px;")
        self._state_label.setStyleSheet(f"color: {color};")
        self.setProperty("statusState", state.value)
        self._state_label.setProperty("statusState", state.value)
        set_translatable_text(
            self._detail_label,
            "CAPSULE LINK ACTIVE" if count > 0 else "CAPSULES IDLE"
        )
        self.setAccessibleDescription(
            format_ui_phrase("{count} EVE client(s) running", count=count)
        )


class ActivityRow(QFrame):
    """One bounded telemetry transition in the Recent Activity feed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "activityRow")
        self.setFixedHeight(25)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        self.time_label = QLabel("--:--")
        self.time_label.setProperty("class", "activityTime")
        self.time_label.setFixedWidth(48)
        layout.addWidget(self.time_label)

        self.message_label = QLabel("Awaiting runtime transition")
        self.message_label.setProperty("class", "activityMessage")
        self.message_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        layout.addWidget(self.message_label, 1)

        self.state_label = QLabel("WAIT")
        self.state_label.setProperty("class", "activityState")
        self.state_label.setProperty("state", "idle")
        self.state_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.state_label.setFixedWidth(68)
        layout.addWidget(self.state_label)
        self._full_message = self.message_label.text()

    def set_entry(self, timestamp: str, message: str, state: str, label: str) -> None:
        self.time_label.setText(timestamp)
        self._full_message = message
        set_translatable_tooltip(self.message_label, message)
        set_translatable_text(self.state_label, label.upper())
        self.state_label.setProperty("state", state)
        style = self.state_label.style()
        style.unpolish(self.state_label)
        style.polish(self.state_label)
        self._render_message()

    def clear_entry(self) -> None:
        self.time_label.setText("--:--")
        self._full_message = "Awaiting runtime transition"
        self.message_label.setToolTip("")
        set_translatable_text(self.state_label, "WAIT")
        self.state_label.setProperty("state", "idle")
        style = self.state_label.style()
        style.unpolish(self.state_label)
        style.polish(self.state_label)
        self._render_message()

    def _render_message(self) -> None:
        rendered = translate_ui_phrase(self._full_message)
        available = self.message_label.contentsRect().width()
        if available <= 0:
            self.message_label.setText(rendered)
            return
        self.message_label.setText(
            self.message_label.fontMetrics().elidedText(
                rendered,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render_message()


class RecentActivityCard(QFrame):
    """Four-row feed populated strictly from RuntimeSnapshot observations."""

    _SERVICE_MESSAGES = {
        ServiceState.OFFLINE: ("offline", "OFFLINE", "service offline"),
        ServiceState.STARTING: ("warning", "STARTING", "launch sequence started"),
        ServiceState.ONLINE: ("online", "ONLINE", "readiness signal online"),
        ServiceState.STOPPING: ("warning", "STOPPING", "shutdown in progress"),
        ServiceState.FAILED: ("danger", "FAILED", "service reported a failure"),
        ServiceState.UNKNOWN: ("warning", "UNKNOWN", "telemetry unavailable"),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "recentActivity")
        self.setProperty("deepSignal", True)
        self.setMinimumHeight(142)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._entries: list[tuple[str, str, str, str]] = []
        self._last_signature: tuple[ServiceState, ServiceState, int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(1)

        self.header_layout = QHBoxLayout()
        self.header_layout.setContentsMargins(4, 0, 2, 4)
        title = QLabel("RECENT ACTIVITY")
        title.setProperty("class", "sectionTitle")
        self.header_layout.addWidget(title)
        self.header_layout.addStretch()
        layout.addLayout(self.header_layout)

        self.activity_rows = [ActivityRow(self) for _ in range(4)]
        for row in self.activity_rows:
            layout.addWidget(row)
        layout.addStretch(1)

    def add_header_action(self, widget: QWidget) -> None:
        self.header_layout.addWidget(widget)

    def record_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        signature = (snapshot.game, snapshot.market, snapshot.running_clients)
        if signature == self._last_signature:
            return

        timestamp = snapshot.checked_at.astimezone().strftime("%H:%M")
        if self._last_signature is None:
            message = (
                f"Game {snapshot.game.value} · Market {snapshot.market.value} · "
                f"{snapshot.running_clients} client"
                f"{'s' if snapshot.running_clients != 1 else ''}"
            )
            if ServiceState.FAILED in (snapshot.game, snapshot.market):
                state = "danger"
            elif any(
                service in {
                    ServiceState.STARTING,
                    ServiceState.STOPPING,
                    ServiceState.UNKNOWN,
                }
                for service in (snapshot.game, snapshot.market)
            ):
                state = "warning"
            elif snapshot.game is ServiceState.ONLINE and snapshot.market is ServiceState.ONLINE:
                state = "online"
            else:
                state = "idle"
            self._append(timestamp, message, state, "SYNC")
        else:
            previous_game, previous_market, previous_clients = self._last_signature
            transitions: list[tuple[str, str, str]] = []
            if snapshot.game is not previous_game:
                state, label, message = self._SERVICE_MESSAGES[snapshot.game]
                transitions.append((f"Game {message}", state, label))
            if snapshot.market is not previous_market:
                state, label, message = self._SERVICE_MESSAGES[snapshot.market]
                transitions.append((f"Market {message}", state, label))
            if snapshot.running_clients != previous_clients:
                count = snapshot.running_clients
                transitions.append((
                    f"{count} EVE client{'s' if count != 1 else ''} running",
                    "online" if count > 0 else "idle",
                    "CLIENTS",
                ))
            for message, state, label in transitions:
                self._append(timestamp, message, state, label)

        self._last_signature = signature

    def _append(self, timestamp: str, message: str, state: str, label: str) -> None:
        self._entries.insert(0, (timestamp, message, state, label))
        del self._entries[4:]
        self._render_entries()

    def _render_entries(self) -> None:
        for index, row in enumerate(self.activity_rows):
            if index < len(self._entries):
                row.set_entry(*self._entries[index])
            else:
                row.clear_entry()

    def retranslate_ui(self) -> None:
        """Refresh retained activity entries without inventing new events."""
        self._render_entries()

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(entry[1] for entry in self._entries)


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
        if highlights:
            # Release-note content belongs to the publisher and stays verbatim.
            self.highlights_label.setText(
                "\n".join(f"• {highlight}" for highlight in highlights)
            )
        else:
            set_translatable_text(
                self.highlights_label,
                "No release highlights are available.",
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
        # One page-owned policy keeps all live status instruments synchronized
        # and is destroyed with this page (never retained process-globally).
        self._motion = MotionController(parent=self)
        self._stack_action = "start"
        self._launch_in_progress = False
        self._launch_progress: tuple[int, int, int, str | None] | None = None
        self._group_state = TargetGroupState()
        self._launch_available = True
        self._launch_unavailable_reason = ""
        self._launch_ready_count = 0
        self._build_ui()
        register_translatable_widget_tree(self)
        self.set_group_state(TargetGroupState())
        self._load_latest_release()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        self.setProperty("deepSignal", True)
        layers = QStackedLayout(self)
        layers.setContentsMargins(0, 0, 0, 0)
        layers.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.signal_background = DeepSignalBackground(
            self,
            motion_controller=self._motion,
        )
        layers.addWidget(self.signal_background)

        # Traffic is a transparent, click-through layer.  The orbital raster
        # beneath it remains permanently static, while controls stay above it.
        self.traffic_overlay = DockingTrafficOverlay(
            self,
            motion_controller=self._motion,
        )
        layers.addWidget(self.traffic_overlay)

        self._foreground = QWidget(self)
        self._foreground.setProperty("deepSignal", True)
        layers.addWidget(self._foreground)
        layers.setCurrentWidget(self._foreground)

        # Keep the pre-redesign object graph alive for controllers and plugins,
        # but make it impossible for those widgets to consume command-surface
        # geometry.  These remain real, functional objects rather than mocks.
        self._compatibility_store = QWidget(self)
        self._compatibility_store.setObjectName("homeCompatibilityStore")
        self._compatibility_store.hide()
        self.hero = HeroBanner(self._compatibility_store)
        self.accounts_card = StatCard("Accounts", self._compatibility_store)
        self.characters_card = StatCard("Characters", self._compatibility_store)
        self.release_card = LatestReleaseCard(self._compatibility_store)
        self.resources_card = ResourcesCard(self._compatibility_store)
        self.services_card = ServicesCard(
            self._compatibility_store,
            motion_controller=self._motion,
        )
        self.release_card.view_full_changelog_requested.connect(self._open_full_changelog)
        self.resources_card.changelog_requested.connect(self._open_full_changelog)
        self.resources_card.console_requested.connect(self.console_requested.emit)
        self.services_card.console_requested.connect(self.console_requested.emit)
        self.server_card = self.services_card.game_row

        canvas = QHBoxLayout(self._foreground)
        canvas.setContentsMargins(24, 16, 24, 16)
        canvas.setSpacing(0)

        self.command_column = QWidget(self._foreground)
        self.command_column.setObjectName("operationsCommandColumn")
        self.command_column.setProperty("deepSignal", True)
        self.command_column.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        command_layout = QVBoxLayout(self.command_column)
        command_layout.setContentsMargins(0, 0, 0, 0)
        command_layout.setSpacing(10)

        overview = QWidget(self.command_column)
        overview.setProperty("deepSignal", True)
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(4)
        self.page_header = PageHeader(
            "OPERATIONS",
            "Authoritative runtime telemetry and safe launcher-owned controls.",
            "DEEP SIGNAL // COMMAND NETWORK",
        )
        overview_layout.addWidget(self.page_header)
        overview_layout.addSpacing(2)
        self.overall_status_label = QLabel("SYSTEMS STANDBY")
        self.overall_status_label.setProperty("class", "overallSignal")
        self.overall_status_label.setProperty("state", "offline")
        overview_layout.addWidget(self.overall_status_label)
        self.overall_detail_label = QLabel(
            "Start the managed stack when you are ready."
        )
        self.overall_detail_label.setProperty("class", "pageSubtitle")
        overview_layout.addWidget(self.overall_detail_label)
        command_layout.addWidget(overview)

        self.instrument_rail = QFrame(self.command_column)
        self.instrument_rail.setObjectName("operationsSignalRail")
        self.instrument_rail.setProperty("deepSignal", True)
        signal_layout = QHBoxLayout(self.instrument_rail)
        signal_layout.setContentsMargins(0, 0, 0, 0)
        signal_layout.setSpacing(10)
        self.running_card = ClientSignalCard(
            self.instrument_rail,
            motion_controller=self._motion,
        )
        signal_layout.addWidget(self.services_card.game_row, 1)
        signal_layout.addWidget(self.services_card.market_row, 1)
        signal_layout.addWidget(self.running_card, 1)
        command_layout.addWidget(self.instrument_rail)

        # Two primary commands only: stack lifecycle and selected-group launch.
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)

        self.btn_start_servers = QPushButton("Start Stack")
        self.btn_start_servers.setProperty("class", "secondary")
        self.btn_start_servers.setProperty("deepRole", "launchStack")
        self.btn_start_servers.setFixedHeight(70)
        self.btn_start_servers.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_start_servers.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_servers.clicked.connect(self._emit_stack_action)
        actions.addWidget(self.btn_start_servers, 1)

        self.group_combo = QComboBox()
        self.group_combo.setFixedWidth(130)
        self.group_combo.setFixedHeight(34)
        self.group_combo.setAccessibleName("Launch group selector")
        self.group_combo.setToolTip("Choose the character group for Launch Group")
        self.group_combo.currentIndexChanged.connect(self._on_group_combo_changed)
        actions.addWidget(
            self.group_combo,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        self.btn_launch_all = QPushButton("Launch All")
        self.btn_launch_all.setProperty("class", "primary")
        self.btn_launch_all.setProperty("deepRole", "launchGroup")
        self.btn_launch_all.setFixedHeight(70)
        self.btn_launch_all.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_launch_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch_all.clicked.connect(self._emit_launch_action)
        actions.addWidget(self.btn_launch_all, 1)
        command_layout.addLayout(actions)

        self.btn_kill_all = QPushButton("Kill All Clients")
        self.btn_kill_all.setProperty("class", "dangerOutline")
        self.btn_kill_all.setFixedHeight(30)
        self.btn_kill_all.setMaximumWidth(148)
        self.btn_kill_all.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.btn_kill_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kill_all.clicked.connect(self.kill_all_clicked.emit)
        self.recent_activity = RecentActivityCard(self.command_column)
        self.recent_activity.add_header_action(self.btn_kill_all)
        command_layout.addWidget(self.recent_activity, 1)

        canvas.addWidget(self.command_column)
        canvas.addStretch(1)
        self._sync_command_width(self.width())

    def _sync_command_width(self, page_width: int) -> None:
        """Keep commands full-width at minimum size and cinematic when wide."""
        available = max(320, int(page_width) - 48)
        self.command_column.setFixedWidth(min(760, available))
        # Keep all live traffic on the exposed station side.  At the minimum
        # launcher width this intentionally leaves no animation area.
        self.traffic_overlay.set_reserved_left_px(
            24 + self.command_column.width() + 12
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "command_column"):
            self._sync_command_width(event.size().width())

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

    def set_animations_enabled(self, enabled: bool) -> None:
        """Apply one reduced-motion choice to every Operations decoration."""
        enabled = bool(enabled)
        self._motion.set_reduced_motion(not enabled)
        self.hero.set_animations_enabled(enabled)
        self.signal_background.set_motion_enabled(enabled)
        self.traffic_overlay.set_motion_enabled(enabled)

    def set_server_mode(self, label: str) -> None:
        """Show the configured server-mode policy without exposing its path."""
        self.services_card.set_mode(label)

    def set_group_state(self, state: TargetGroupState) -> None:
        """Synchronize the quick-launch selector with the Characters page."""
        self._group_state = state
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem(translate_ui_phrase("All Visible"), None)
        selected_index = 0
        for index, group in enumerate(state.groups, start=1):
            self.group_combo.addItem(
                format_ui_phrase(
                    "{group_name} ({member_count})",
                    group_name=group.name,
                    member_count=len(group.members),
                ),
                group.group_id,
            )
            if group.group_id == state.selected_group_id:
                selected_index = index
        self.group_combo.insertSeparator(self.group_combo.count())
        self.group_combo.addItem(
            translate_ui_phrase("Manage Groups…"),
            "__manage_groups__",
        )
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
        self._launch_progress = (attempted, total, succeeded, group_name)
        self.group_combo.setEnabled(False)
        prefix = (
            format_ui_phrase("Launching {group_name}", group_name=group_name)
            if group_name
            else translate_ui_phrase("Launching")
        )
        self.btn_launch_all.setText(
            format_ui_phrase(
                "{prefix} {attempted} of {total}…",
                prefix=prefix,
                attempted=attempted,
                total=total,
            )
        )
        self.btn_launch_all.setEnabled(True)
        set_translatable_tooltip(
            self.btn_launch_all,
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
        self._launch_progress = None
        self.group_combo.setEnabled(True)
        self._restore_launch_button()
        if cancelled:
            set_translatable_tooltip_template(
                self.btn_launch_all,
                f"Cancelled after launching {succeeded} of {attempted} account(s)"
            )

    def _restore_launch_button(self) -> None:
        if self._launch_in_progress:
            return
        group = self._group_state.selected_group
        if group is None:
            set_translatable_text(self.btn_launch_all, "Launch All")
        else:
            set_translatable_text_template(
                self.btn_launch_all,
                f"Launch {group.name} ({self._launch_ready_count})"
            )
        self.btn_launch_all.setEnabled(self._launch_available)
        if not self._launch_available:
            tooltip = self._launch_unavailable_reason
        elif group is None:
            tooltip = "Launch every eligible visible account"
        else:
            tooltip = "Launch every ready character in this group"
        set_translatable_tooltip(self.btn_launch_all, tooltip)

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
        self.recent_activity.record_snapshot(snapshot)
        self._update_overall_status(snapshot)
        self._update_stack_action(snapshot)
        has_running_clients = snapshot.running_clients > 0
        self.btn_kill_all.setEnabled(has_running_clients)
        set_translatable_tooltip(
            self.btn_kill_all,
            "Terminate every running EVE client"
            if has_running_clients
            else "No EVE clients are running"
        )

    def _update_overall_status(self, snapshot: RuntimeSnapshot) -> None:
        """Summarize the two independent service signals without new probes."""
        states = {snapshot.game, snapshot.market}
        if ServiceState.FAILED in states:
            label, detail, state = (
                "ATTENTION REQUIRED",
                "A service failed. Open its telemetry row for diagnostics.",
                "failed",
            )
        elif ServiceState.UNKNOWN in states:
            label, detail, state = (
                "TELEMETRY DEGRADED",
                "The selected runtime is not currently observable.",
                "degraded",
            )
        elif ServiceState.STARTING in states:
            label, detail, state = (
                "STACK INITIALIZING",
                "Services are starting; readiness checks remain authoritative.",
                "starting",
            )
        elif ServiceState.STOPPING in states:
            label, detail, state = (
                "STACK SHUTTING DOWN",
                "Launcher-owned services are stopping safely.",
                "starting",
            )
        elif states == {ServiceState.ONLINE}:
            label, detail, state = (
                "ALL SYSTEMS NOMINAL",
                "Game and market readiness checks are online.",
                "online",
            )
        elif ServiceState.ONLINE in states:
            label, detail, state = (
                "PARTIAL SIGNAL",
                "One service is online; review the remaining service telemetry.",
                "degraded",
            )
        else:
            label, detail, state = (
                "SYSTEMS STANDBY",
                "Start the managed stack when you are ready.",
                "offline",
            )

        set_translatable_text(self.overall_status_label, label)
        self.overall_status_label.setProperty("state", state)
        style = self.overall_status_label.style()
        style.unpolish(self.overall_status_label)
        style.polish(self.overall_status_label)
        set_translatable_text(self.overall_detail_label, detail)

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
            set_translatable_text(self.btn_start_servers, label)
            self.btn_start_servers.setEnabled(enabled)
            set_translatable_tooltip(self.btn_start_servers, tooltip)
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
        set_translatable_text(self.btn_start_servers, label)
        self.btn_start_servers.setEnabled(enabled)
        set_translatable_tooltip(self.btn_start_servers, tooltip)

    def retranslate_ui(self) -> None:
        """Refresh dynamic controls that contain retained group/user values."""
        self.set_group_state(self._group_state)
        self.recent_activity.retranslate_ui()
        if self._launch_in_progress and self._launch_progress is not None:
            self.set_launch_progress(*self._launch_progress)
        else:
            self._restore_launch_button()

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
