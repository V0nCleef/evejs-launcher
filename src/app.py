"""Main application window for EveJS Launcher V2.

Composes the frameless top-level window:
    TitleBar (top)
    NavPanel | QStackedWidget (5 pages; CharactersPage owns its DetailPanel)
    StatusBar (bottom)
    ConsolePanel (overlay child of central widget)

Wires together nav, server, character launching, and process tracking.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QObject,
    QThread,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QCursor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import config
from .audio.controller import AudioController
from .audio.events import (
    VoiceEvent,
    service_start_result_event,
    service_stop_result_event,
)
from .constants import APP_TITLE, Page, Ports
from .core.client_launch_queue import AsyncClientLaunchQueue
from .core.dashboard import visible_account_count, visible_character_rows
from .core.discovery import resolve_client_tq_path
from .core.db import (
    Account,
    Character,
    clear_solar_system_name_cache,
    get_character_detail,
    load_accounts,
)
from .core.client_autologin import AutoLoginLaunch
from .core.groups import (
    GroupValidationError,
    TargetGroupState,
    find_relink_candidates,
    load_target_groups,
    prune_deleted_characters,
    resolve_group,
    save_target_groups,
    select_group,
)
from .core.character_creation import (
    CharacterCreationRequest,
    CharacterCreationResult,
    normalize_character_name,
)
from .core.character_deletion import (
    CharacterDeletionRequest,
    CharacterDeletionResult,
    CharacterDeletionScope,
)
from .core.launcher import ClientLaunchContext, launch_client
from .core.overview_patch import (
    OverviewPatchState,
    inspect_overview_patch,
    is_eve_client_running,
)
from .core.overview_state import (
    OverviewSnapshotRequired,
    add_pending_overview_import,
    load_overview_state,
    pending_overview_source,
    prepare_overview_launch,
    process_overview_ack_files,
    remove_characters_from_overview_state,
)
from .core.platform import hard_exit, launch_tool_wrapper
from .core.process_tracker import ProcessTracker
from .core.profiles import (
    PROFILES_ROOT,
    configure_profile_game_endpoint,
    create_profile,
    prefill_username,
    profile_exists,
)
from .core.server_launcher import (
    get_server_console_log,
    get_market_console_log,
    get_server_log_path,
    is_server_running,
    start_game_server,
    start_market_server,
)
from .core.server_selection import (
    ASK_EVERY_TIME,
    choose_saved_script,
    discover_server_scripts,
    mode_for_script,
)
from .core.service_status import (
    DockerControlPolicy,
    RuntimeBackend,
    RuntimeSnapshot,
    ServiceState,
    derive_service_state,
)
from .core.runtime.docker_cli import DockerCommandRunner
from .core.runtime.docker_character_creation import (
    DockerCharacterCreationRequest,
    DockerCharacterCreationResult,
    ManagedDockerCharacterCreationController,
)
from .core.runtime.docker_compose import ComposeInspector, ComposeTarget
from .core.runtime.docker_controller import DockerLifecycleAction, ManagedComposeController
from .core.runtime.docker_setup import (
    DockerPreflightRequest,
    DockerSetupDraft,
    build_compose_target,
)
from .core.runtime.docker_tools import (
    DockerToolAction,
    DockerToolResult,
    ManagedDockerToolController,
)
from .core.runtime.docker_mods import (
    DockerModBridgeError,
    apply_docker_mod_override,
    attach_docker_mod_override,
)
from .core.runtime.data import (
    RuntimeDataSelection,
    docker_settings_identity,
    inspect_docker_data_source,
    native_data_selection,
)
from .core.runtime.portraits import PortraitTarget
from .core.tool_catalog import (
    ResolvedTool,
    ResolvedToolAction,
    ToolAction,
    ToolDispatchKind,
    resolve_tools,
)

from .pages.characters_page import CharactersPage
from .pages.home_page import HomePage
from .pages.mods_page import ModsPage
from .pages.settings_page import SettingsPage
from .pages.tools_page import ToolsPage
from .utils.cache import PortraitCache
from .utils.logger import setup_logger
from .widgets.console_panel import ConsolePanel
from .widgets.shipboard_caption import ShipboardCaption
from .widgets.character_groups_dialog import CharacterGroupsDialog
from .widgets.nav_panel import NavPanel
from .widgets.new_character_dialog import NewCharacterDialog, NewCharacterDraft
from .widgets.status_bar import StatusBar
from .widgets.title_bar import TitleBar
from .workers.docker_lifecycle_worker import DockerLifecycleWorker
from .workers.docker_character_creation_worker import (
    DockerCharacterCreationWorker,
)
from .workers.docker_log_worker import DockerLogWorker
from .workers.docker_monitor import DockerMonitor, DockerObservation
from .workers.docker_preflight_worker import DockerPreflightWorker
from .workers.docker_tool_worker import DockerToolWorker
from .workers.db_worker import (
    AccountLoadResult,
    AccountLoader,
    CharacterDetailLoader,
    CharacterDetailResult,
    DataLoadFailure,
)
from .workers.client_launch_worker import (
    ClientLaunchFailure,
    ClientLaunchRequest,
    ClientLaunchResult,
    ClientLaunchWorker,
    LaunchedProcess,
)
from .workers.character_creation_worker import (
    CharacterCreationFailure,
    CharacterCreationWorker,
)
from .workers.character_deletion_worker import (
    CharacterDeletionFailure,
    CharacterDeletionWorker,
)
from .workers.overview_patch_worker import (
    OverviewPatchAction,
    OverviewPatchFailure,
    OverviewPatchResult,
    OverviewPatchWorker,
)
from .workers.server_worker import (
    ServiceMonitor,
    ServiceProbe,
    ServiceStartResult,
    ServiceStartWorker,
    ServiceStopResult,
    ServiceStopWorker,
)
from .updater.checker import UpdateChecker
from .updater.dialog import UpdateDialog
from .updater.installer import UpdateInstallWorker
from .updater.progress_dialog import UpdateProgressDialog

log = setup_logger(__name__)


@dataclass(frozen=True)
class _DataRequestToken:
    """Private in-process attribution for one asynchronous data request."""

    sequence: int
    settings_generation: int
    settings_identity: tuple[object, ...]
    target_identity: str | None = None
    username: str | None = None
    character_id: int | None = None


def _restore_eve_window(window_title: str = "EVE", timeout: int = 30) -> None:
    """Wait for the EVE client window to appear, then restore and focus it.

    Runs in a daemon thread.  The EVE client takes 10-15 seconds to
    materialise its DirectX window on first launch; without this the
    window may appear minimised or behind the launcher.
    """
    from .core.platform import find_and_focus_eve_window

    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_and_focus_eve_window(window_title):
            return
        time.sleep(2)
    log.debug("EVE window '%s' not detected within %ss", window_title, timeout)


def _perform_client_launch(request: ClientLaunchRequest) -> LaunchedProcess:
    """Prepare one profile and create its EVE process outside the GUI thread."""
    if not profile_exists(request.username):
        create_profile(request.username, request.client_path)

    profile_path = request.profiles_root / request.username / "tq"
    if not profile_path.exists():
        raise FileNotFoundError("Profile junction not found.")

    # Refresh the account and endpoint settings immediately before every spawn.
    prefill_username(request.username)
    configure_profile_game_endpoint(
        request.username,
        profile_path,
        host=request.launch_context.game_host,
        port=request.launch_context.game_port,
    )
    auto_login = None
    if request.auto_login_enabled:
        if request.character_id is None:
            raise ValueError("A character ID is required for automatic login.")
        auto_login = AutoLoginLaunch(
            username=request.username,
            character_id=request.character_id,
        )
    return launch_client(
        evejs_root=request.evejs_root,
        profile_tq_path=profile_path,
        proxy_url=request.launch_context.proxy_url,
        client_path=request.client_path,
        launch_context=request.launch_context,
        auto_login=auto_login,
        overview_bridge=request.overview_bridge,
    )


# ═════════════════════════════════════════════════════════════════════════════
# MainWindow
# ═════════════════════════════════════════════════════════════════════════════


class MainWindow(QMainWindow):
    """Top-level frameless window hosting the entire launcher UI."""

    _service_probe_requested = pyqtSignal()
    _docker_observe_requested = pyqtSignal()
    _service_monitor_stop_requested = pyqtSignal()
    shipboard_caption_requested = pyqtSignal(str)

    _MARGIN = 8  # px resize-hit border around the frame
    _VOICE_PREPARE_MAX_ATTEMPTS = 3
    _VOICE_PREPARE_RETRY_DELAY_MS = 250

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1000, 640)
        self.resize(1366, 768)

        # Frameless window with custom title bar
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        # Window icon
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ── State ──────────────────────────────────────────────────────
        self._cfg = config.load()
        # Backends remain lazy until music or an announcement is requested.
        self._audio_controller = AudioController(self._cfg, self)
        self._voice_prepare_attempts = 0
        self._audio_controller.caption_requested.connect(
            self.shipboard_caption_requested.emit
        )
        resolved_client_path = resolve_client_tq_path(
            str(self._cfg.get("client_path", "")),
            str(self._cfg.get("evejs_root", "")),
        )
        if resolved_client_path is not None:
            self._cfg["client_path"] = str(resolved_client_path)
        self._tracker = ProcessTracker()
        self._server_proc: subprocess.Popen | None = None
        self._market_proc: subprocess.Popen | None = None
        self._server_intent: ServiceState | None = None
        self._market_intent: ServiceState | None = None
        self._server_error: str | None = None
        self._market_error: str | None = None
        self._runtime_snapshot = RuntimeSnapshot(
            game=ServiceState.OFFLINE,
            market=ServiceState.OFFLINE,
            running_clients=0,
        )
        self._service_reachability = (False, False)
        self._service_thread: QThread | None = None
        self._service_monitor: ServiceMonitor | DockerMonitor | None = None
        self._service_monitor_start_pending = False
        self._service_monitor_restart_pending = False
        self._docker_preflight_thread: QThread | None = None
        self._docker_preflight_worker: DockerPreflightWorker | None = None
        self._docker_preflight_result_received = False
        self._docker_preflight_thread_finished = False
        self._monitor_generation = 0
        self._log_generation = 0
        self._docker_log_thread: QThread | None = None
        self._docker_log_worker: DockerLogWorker | None = None
        self._docker_log_service: str | None = None
        self._docker_log_token: object | None = None
        self._pending_docker_log_service: str | None = None
        self._lifecycle_thread: QThread | None = None
        self._lifecycle_worker: QObject | None = None
        self._lifecycle_start_scope = (False, False)
        self._lifecycle_start_token: object | None = None
        self._lifecycle_start_voice_event: VoiceEvent | None = None
        self._lifecycle_stop_scope = (False, False)
        self._lifecycle_stop_voice_event: VoiceEvent | None = None
        self._lifecycle_ready_callback: Callable[[], None] | None = None
        self._lifecycle_stop_callback: Callable[[], None] | None = None
        self._lifecycle_after_thread_callback: Callable[[], None] | None = None
        self._lifecycle_result_received = False
        self._lifecycle_thread_finished = False
        self._close_after_lifecycle = False
        # Docker close coordination is separate from the proven Native flow.
        self._docker_close_pending = False
        self._docker_close_stop_started = False
        self._docker_close_stop_succeeded = False
        self._docker_lifecycle_snapshot: RuntimeSnapshot | None = None
        self._docker_lifecycle_generation: int | None = None
        self._docker_lifecycle_target: tuple[object, ...] | None = None
        self._docker_lifecycle_action: DockerLifecycleAction | None = None
        self._docker_lifecycle_observed_target: str | None = None
        self._docker_lifecycle_completion: Callable[[bool], None] | None = None
        self._docker_lifecycle_suppress_failure_dialog = False
        self._docker_tool_token: object | None = None
        self._docker_tool_generation: int | None = None
        self._docker_tool_target: tuple[object, ...] | None = None
        self._docker_tool_observed_target: str | None = None
        self._docker_tool_action: DockerToolAction | None = None
        self._docker_tool_request: tuple[str, str] | None = None
        self._docker_character_token: object | None = None
        self._docker_character_generation: int | None = None
        self._docker_character_target: tuple[object, ...] | None = None
        self._docker_character_observed_target: str | None = None
        self._docker_character_request: DockerCharacterCreationRequest | None = None
        self._docker_character_overview_source_id: int | None = None
        self._docker_character_result: DockerCharacterCreationResult | None = None
        self._docker_character_restore_game = False
        self._docker_character_restore_market = False
        self._close_in_progress = False
        self._launch_queue: AsyncClientLaunchQueue | None = None
        self._client_launch_thread: QThread | None = None
        self._client_launch_worker: ClientLaunchWorker | None = None
        self._client_launch_request: ClientLaunchRequest | None = None
        self._client_launch_show_errors = False
        self._client_launch_from_queue = False
        self._client_launch_result_received = False
        self._client_launch_thread_finished = False
        self._client_launch_succeeded = False
        self._pending_client_launches: set[str] = set()
        self._new_character_dialog: NewCharacterDialog | None = None
        self._character_creation_thread: QThread | None = None
        self._character_creation_worker: CharacterCreationWorker | None = None
        self._character_creation_request: CharacterCreationRequest | None = None
        self._character_creation_outcome: (
            CharacterCreationResult | CharacterCreationFailure | None
        ) = None
        self._character_creation_thread_finished = False
        self._character_creation_restart_game = False
        self._character_creation_restart_market = False
        self._character_creation_restart_mode: str | None = None
        self._character_deletion_thread: QThread | None = None
        self._character_deletion_worker: CharacterDeletionWorker | None = None
        self._character_deletion_request: CharacterDeletionRequest | None = None
        self._character_deletion_outcome: (
            CharacterDeletionResult | CharacterDeletionFailure | None
        ) = None
        self._character_deletion_thread_finished = False
        self._character_deletion_restart_game = False
        self._character_deletion_restart_market = False
        self._character_deletion_restart_mode: str | None = None
        self._character_deletion_progress: QProgressDialog | None = None
        self._overview_patch_thread: QThread | None = None
        self._overview_patch_worker: OverviewPatchWorker | None = None
        self._overview_patch_outcome: (
            OverviewPatchResult | OverviewPatchFailure | None
        ) = None
        self._overview_patch_thread_finished = False
        self._resizing = False
        self._cursor_override_active = False
        self._accounts: list[Account] = []
        self._data_selection: RuntimeDataSelection | None = None
        self._data_load_error: str = ""
        self._group_target_identity: str | None = None
        self._group_state = TargetGroupState()
        self._launch_queue_group_name: str | None = None
        self._launch_queue_skipped_running = 0
        self._launch_queue_skipped_unavailable = 0
        self._settings_generation = 0
        self._pending_settings_intent: tuple[str, int | None] | None = None
        self._data_request_sequence = 0
        self._account_thread: QThread | None = None
        self._account_worker: AccountLoader | None = None
        self._account_request_token: _DataRequestToken | None = None
        self._pending_account_request: tuple[
            _DataRequestToken,
            Callable[[], RuntimeDataSelection],
        ] | None = None
        self._account_start_scheduled = False
        self._detail_thread: QThread | None = None
        self._detail_worker: CharacterDetailLoader | None = None
        self._detail_request_token: _DataRequestToken | None = None
        self._pending_detail_request: tuple[
            _DataRequestToken,
            Callable[[], RuntimeDataSelection],
        ] | None = None
        self._detail_start_scheduled = False

        # ── Update state ───────────────────────────────────────────────
        self._latest_version: str = ""
        self._latest_changelog: str = ""
        self._latest_download_url: str = ""
        self._latest_published: str = ""
        self._update_install_worker: UpdateInstallWorker | None = None
        self._update_progress_dialog: UpdateProgressDialog | None = None
        self._update_install_result: tuple[bool, str] | None = None
        self._update_install_thread_finished = False

        # ── Build UI ───────────────────────────────────────────────────
        self._build_ui()
        self._title_bar.set_music_muted(self._audio_controller.music_muted)
        self._apply_runtime_settings()
        self._home_page.set_server_mode(self._effective_server_mode_label())

        # ── Wire signals ───────────────────────────────────────────────
        self._nav.page_changed.connect(self._switch_page)
        self._nav.server_toggled.connect(self._on_server_toggle)
        self._nav.market_toggled.connect(self._on_market_toggle)
        self._nav.kill_all_clicked.connect(self._kill_all_clients)

        self._home_page.launch_all_clicked.connect(self._launch_all)
        self._home_page.cancel_launches_clicked.connect(self._cancel_launch_queue)
        self._home_page.group_selection_changed.connect(
            self._on_group_selection_changed
        )
        self._home_page.manage_groups_requested.connect(self._show_group_manager)
        self._home_page.start_servers_clicked.connect(self._start_all_servers)
        self._home_page.stop_servers_clicked.connect(self._stop_all_servers)
        self._home_page.kill_all_clicked.connect(self._kill_all_clients)

        self._characters_page.launch_character.connect(self._on_character_launch)
        self._characters_page.group_selection_changed.connect(
            self._on_group_selection_changed
        )
        self._characters_page.launch_group_requested.connect(self._launch_all)
        self._characters_page.cancel_group_launches_requested.connect(
            self._cancel_launch_queue
        )
        self._characters_page.manage_groups_requested.connect(
            self._show_group_manager
        )
        self._characters_page.new_character_requested.connect(
            self._show_new_character_dialog
        )
        self._characters_page.delete_character_requested.connect(
            self._on_delete_character_requested
        )
        self._characters_page.delete_account_requested.connect(
            self._on_delete_account_requested
        )
        self._characters_page.character_selected.connect(self._on_character_selected)
        self._characters_page.hide_character.connect(self._on_hide_character)
        self._characters_page.portrait_loads_idle.connect(self._resume_close_after_data)
        self._mods_page.apply_restart_clicked.connect(self._on_mods_apply_restart)
        self._tools_page.open_settings_requested.connect(self._open_settings_page)
        self._tools_page.launch_requested.connect(self._on_tool_launch_requested)

        self._status_bar.console_toggled.connect(self._on_console_toggled)
        self._home_page.console_requested.connect(self._on_console_toggled)

        # ── Update system ──────────────────────────────────────────────
        self._title_bar.update_clicked.connect(self._on_update_clicked)
        self._title_bar.music_mute_changed.connect(
            self._audio_controller.set_music_muted
        )
        self._audio_controller.music_muted_changed.connect(
            self._title_bar.set_music_muted
        )
        self._audio_controller.music_muted_changed.connect(
            self._on_music_mute_changed
        )
        self._audio_controller.music_playback_changed.connect(
            self._title_bar.set_audio_status
        )

        self._active_update_checkers: list[UpdateChecker] = []
        self._update_checker = self._create_update_checker()

        self._settings_page.settings_update_check.connect(self._on_manual_update_check)
        self._settings_page.settings_saved.connect(self._on_settings_saved)
        self._settings_page.voice_preview_requested.connect(
            self._preview_shipboard_voice
        )
        self._settings_page.set_voice_preview_available(
            False,
            "Preparing bundled LYRA voice catalog…",
        )
        # Native QSoundEffect can report unavailable when constructed before
        # Qt enters its event loop. Probe once the window is event-loop ready,
        # just like the optional launcher ambience below.
        QTimer.singleShot(0, self._prepare_shipboard_voice)
        self._settings_page.save_finished.connect(
            self._on_settings_save_finished
        )
        self._settings_page.docker_preflight_requested.connect(
            self._begin_docker_preflight
        )

        if self._cfg.get("update_auto_check", True):
            QTimer.singleShot(2000, self._start_automatic_update_check)

        # Initialize local ambience only after the window has entered Qt's
        # event loop.  Playback is optional and never gates launcher startup.
        QTimer.singleShot(0, self._start_launcher_ambience)

        interval_hours = int(self._cfg.get("update_check_interval_hours", 6))
        if interval_hours > 0:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._start_automatic_update_check)
            self._update_timer.start(interval_hours * 3600 * 1000)

        # ── Periodic timers ────────────────────────────────────────────
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(5000)

        self._prune_timer = QTimer(self)
        self._prune_timer.timeout.connect(self._prune_and_update)
        self._prune_timer.start(3000)

        self._overview_ack_timer = QTimer(self)
        self._overview_ack_timer.timeout.connect(self._poll_overview_acks)
        self._overview_ack_timer.start(1500)

        # Initial paint
        self._update_status_bar()
        self._refresh_characters()

    # ── UI Construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        # Outside-console dismissal must observe clicks on every control in
        # this window, not just bare central-widget background clicks.
        QApplication.instance().installEventFilter(self)
        central.setMouseTracking(True)
        self.setMouseTracking(True)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        self._title_bar = TitleBar(self)
        root.addWidget(self._title_bar)

        # Content row: nav (left) | stacked pages (center)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        self._nav = NavPanel(self)
        content.addWidget(self._nav)

        self._stack = QStackedWidget()
        self._home_page = HomePage()
        self._characters_page = CharactersPage()
        self._mods_page = ModsPage()
        self._tools_page = ToolsPage(str(self._cfg.get("evejs_root", "")))
        self._settings_page = SettingsPage()
        self._stack.addWidget(self._home_page)        # Page.HOME = 0
        self._stack.addWidget(self._characters_page)  # Page.CHARACTERS = 1
        self._stack.addWidget(self._mods_page)        # Page.MODS = 2
        self._stack.addWidget(self._tools_page)       # Page.TOOLS = 3
        self._stack.addWidget(self._settings_page)    # Page.SETTINGS = 4
        content.addWidget(self._stack, 1)

        root.addLayout(content, 1)

        # Console overlay (child of central widget — floats above content)
        self._console_panel = ConsolePanel(central)
        self._console_panel.closed.connect(self._on_console_panel_closed)
        self._console_panel.hide()

        # Accessible text mirror for optional LYRA speech. It is deliberately
        # static while visible and never intercepts launcher input.
        self._shipboard_caption = ShipboardCaption(central)
        self.shipboard_caption_requested.connect(
            self._shipboard_caption.show_caption
        )

        # Status bar (bottom)
        self._status_bar = StatusBar(self)
        root.addWidget(self._status_bar)

    # ── Page switching with cross-fade ─────────────────────────────────

    def _switch_page(self, index: int) -> None:
        """Switch the center stack to a different page."""
        if self._stack.currentIndex() == index:
            return

        if getattr(self, "_pending_settings_intent", None) is not None:
            self._nav.set_active_page(self._stack.currentIndex())
            return
        if (
            self._stack.currentIndex() == int(Page.SETTINGS)
            and getattr(self, "_settings_page", None) is not None
            and self._settings_page.is_dirty()
        ):
            # QButtonGroup checks the clicked destination before emitting.
            # Restore Settings while the draft decision is unresolved.
            self._nav.set_active_page(int(Page.SETTINGS))
            answer = self._ask_unsaved_settings()
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Discard:
                self._settings_page.discard_changes()
            elif answer == QMessageBox.StandardButton.Save:
                self._pending_settings_intent = ("page", index)
                self._settings_page.save_settings()
                return
            else:
                return

        self._switch_page_now(index)

    def _switch_page_now(self, index: int) -> None:
        """Perform one page switch after any Settings draft is resolved."""
        if self._stack.currentIndex() == index:
            self._nav.set_active_page(index)
            return

        self._stack.setCurrentIndex(index)
        self._on_page_changed(index)

    def _ask_unsaved_settings(self) -> QMessageBox.StandardButton:
        """Ask how to resolve the visible Settings draft."""
        return QMessageBox.question(
            self,
            "Unsaved Settings",
            "You have unsaved Settings changes. Save them before leaving?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

    @pyqtSlot(bool)
    def _on_settings_save_finished(self, success: bool) -> None:
        """Complete a deferred page change or close only after a real save."""
        intent = getattr(self, "_pending_settings_intent", None)
        if intent is None:
            return
        self._pending_settings_intent = None
        if not success:
            if hasattr(self, "_stack"):
                self._nav.set_active_page(self._stack.currentIndex())
            return
        kind, target = intent
        if kind == "page" and target is not None:
            self._switch_page_now(target)
        elif kind == "close":
            QTimer.singleShot(0, self.close)

    def _on_page_changed(self, index: int) -> None:
        """Side effects when the active page changes."""
        # Sync the nav button checked state
        btn = self._nav.nav_group.button(index)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)

        # Refresh settings from config when the user visits the Settings tab,
        # so auto-hidden accounts (persisted by _refresh_characters after
        # startup) show up in the Hidden Accounts list.
        if index == int(Page.SETTINGS):
            self._settings_page.refresh_if_clean()
        elif index == int(Page.TOOLS):
            self._tools_page.refresh_tools(str(self._cfg.get("evejs_root", "")))

    def _apply_runtime_settings(self) -> None:
        """Apply persisted Home animation preferences without restarting the app."""
        try:
            interval_sec = int(self._cfg.get("hero_rotation_interval_sec", 6))
        except (TypeError, ValueError):
            interval_sec = 6
        hero = self._home_page.hero
        hero.set_rotation_interval(interval_sec)
        animations_enabled = bool(self._cfg.get("animations_enabled", True))
        apply_page_motion = getattr(
            self._home_page,
            "set_animations_enabled",
            None,
        )
        if callable(apply_page_motion):
            apply_page_motion(animations_enabled)
        else:
            # Lightweight controller test doubles may expose only the legacy
            # hero seam; production HomePage owns the full motion policy.
            hero.set_animations_enabled(animations_enabled)

        # Keep the preference application tolerant of lightweight controller
        # fixtures while making the production setting truly launcher-wide.
        for surface in (
            getattr(self, "_characters_page", None),
            getattr(self, "_status_bar", None),
        ):
            apply_surface_motion = getattr(
                surface,
                "set_animations_enabled",
                None,
            )
            if callable(apply_surface_motion):
                apply_surface_motion(animations_enabled)

    def _open_settings_page(self) -> None:
        """Route page-owned setup actions through the central navigation state."""
        self._switch_page(int(Page.SETTINGS))

    def _on_tool_launch_requested(
        self,
        tool: ResolvedTool,
        action: ResolvedToolAction | ToolAction,
    ) -> None:
        """Re-resolve one reviewed action, then dispatch its semantic backend."""
        if not isinstance(tool, ResolvedTool) or not isinstance(
            action,
            (ResolvedToolAction, ToolAction),
        ):
            log.warning("Rejected malformed tool launch request")
            return
        tool_id = tool.definition.id
        action_id = action.id
        try:
            backend = RuntimeBackend(
                str(self._cfg.get("runtime_backend", RuntimeBackend.NATIVE.value))
            )
        except ValueError:
            message = "Tool Deck runtime configuration is invalid"
            log.warning("Rejected tool launch with invalid runtime backend")
            self._tools_page.set_launch_result(
                tool_id,
                action_id,
                success=False,
                message=message,
            )
            QMessageBox.warning(self, "Tool Unavailable", message)
            return
        policy = DockerControlPolicy.CONNECT_ONLY
        if backend is RuntimeBackend.DOCKER_COMPOSE:
            try:
                policy = DockerControlPolicy(
                    str(
                        self._cfg.get(
                            "docker_control_policy",
                            DockerControlPolicy.CONNECT_ONLY.value,
                        )
                    )
                )
            except ValueError:
                message = "Docker Tool Deck runtime configuration is invalid"
                log.warning("Rejected Docker tool launch with invalid control policy")
                self._tools_page.set_launch_result(
                    tool_id,
                    action_id,
                    success=False,
                    message=message,
                )
                QMessageBox.warning(self, "Tool Unavailable", message)
                return
        current_tools = resolve_tools(
            str(self._cfg.get("evejs_root", "")),
            backend=backend,
            docker_policy=policy,
            compose_file=self._cfg.get("docker_compose_file") or None,
        )
        canonical_tool = next(
            (
                resolved
                for resolved in current_tools
                if resolved.definition.id == tool_id
            ),
            None,
        )
        canonical_action = (
            next(
                (
                    candidate for candidate in canonical_tool.actions
                    if candidate.id == action_id
                ),
                None,
            )
            if canonical_tool is not None
            else None
        )
        if canonical_tool is None or canonical_action is None:
            message = "This tool action is not in the reviewed launcher catalog"
            log.warning("Unsupported tool launch request: %s/%s", tool_id, action_id)
            self._tools_page.set_launch_result(
                tool_id,
                action_id,
                success=False,
                message=message,
            )
            QMessageBox.warning(self, "Unsupported Tool Action", message)
            return

        if backend is RuntimeBackend.DOCKER_COMPOSE and (
            not isinstance(action, ResolvedToolAction)
            or action != canonical_action
            or tool != canonical_tool
        ):
            message = "This Docker tool request is stale or not reviewed"
            log.warning("Rejected stale Docker tool request: %s/%s", tool_id, action_id)
            self._tools_page.set_launch_result(
                tool_id,
                action_id,
                success=False,
                message=message,
            )
            QMessageBox.warning(self, "Unsupported Tool Action", message)
            return

        if (
            not canonical_tool.available
            or not canonical_action.available
            or canonical_action.dispatch_kind is ToolDispatchKind.UNAVAILABLE
        ):
            message = (
                canonical_action.unavailable_reason
                or canonical_tool.unavailable_reason
                or "Tool action is unavailable"
            )
            log.warning("Tool launch rejected for %s: %s", tool_id, message)
            self._tools_page.set_launch_result(
                tool_id,
                action_id,
                success=False,
                message=message,
            )
            QMessageBox.warning(self, "Tool Unavailable", message)
            return

        if (
            canonical_action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE
            and self._lifecycle_active()
        ):
            self._docker_unavailable(
                "Another service or Docker tool operation is already running."
            )
            return

        reviewed_action = canonical_action.action
        if reviewed_action.confirmation_title:
            result = QMessageBox.warning(
                self,
                reviewed_action.confirmation_title,
                reviewed_action.confirmation_body,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if result != QMessageBox.StandardButton.Yes:
                log.info(
                    "Tool launch cancelled for %s action %s",
                    canonical_tool.definition.id,
                    reviewed_action.id,
                )
                return

        if canonical_action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE:
            self._begin_docker_tool(canonical_tool, canonical_action)
            return
        self._launch_native_tool(canonical_tool, reviewed_action)

    def _launch_native_tool(
        self,
        tool: ResolvedTool,
        action: ToolAction,
    ) -> None:
        """Preserve the reviewed Native wrapper launch boundary unchanged."""
        entrypoint = tool.absolute_entrypoint
        if entrypoint is None:
            message = tool.unavailable_reason or "Tool wrapper is unavailable"
            self._tools_page.set_launch_result(
                tool.definition.id,
                action.id,
                success=False,
                message=message,
            )
            QMessageBox.warning(self, "Tool Unavailable", message)
            return

        try:
            launch_tool_wrapper(entrypoint, action.arguments)
        except (OSError, RuntimeError, ValueError) as exc:
            message = str(exc)
            log.error("Tool launch failed for %s: %s", tool.definition.id, message)
            self._tools_page.set_launch_result(
                tool.definition.id,
                action.id,
                success=False,
                message=message,
            )
            QMessageBox.critical(
                self,
                "Tool Launch Failed",
                f"{tool.definition.name} could not be launched.\n\n{message}",
            )
            return

        log.info(
            "Launched tool %s via %s",
            tool.definition.id,
            tool.definition.relative_entrypoint,
        )
        self._tools_page.set_launch_result(
            tool.definition.id,
            action.id,
            success=True,
            message="Tool wrapper launched",
        )

    def _begin_docker_tool(
        self,
        tool: ResolvedTool,
        action: ResolvedToolAction,
    ) -> bool:
        """Own one managed semantic Tool Deck operation in the lifecycle slot."""
        docker_action = action.docker_action
        if not self._docker_managed() or docker_action is None:
            self._docker_unavailable(self._docker_control_reason())
            return False
        if self._lifecycle_active():
            self._docker_unavailable(
                "Another service or Docker tool operation is already running."
            )
            return False
        observed_target = self._current_observed_docker_target_identity()
        if observed_target is None:
            self._docker_unavailable(
                "Docker target context is not current. Wait for Docker status to "
                "refresh, then try again."
            )
            return False

        def controller_factory(target: ComposeTarget) -> ManagedDockerToolController:
            # Factory execution occurs only after the worker owns its QThread.
            runner = DockerCommandRunner()
            return ManagedDockerToolController(
                target,
                ComposeInspector(runner),
                runner,
                policy=DockerControlPolicy.MANAGED,
                expected_target_identity=observed_target,
            )

        token = object()
        worker = DockerToolWorker(
            self._docker_lifecycle_target_factory(),
            controller_factory,
            docker_action,
            policy=DockerControlPolicy.MANAGED,
            request_token=token,
        )
        self._docker_tool_token = token
        self._docker_tool_generation = getattr(self, "_monitor_generation", 0)
        self._docker_tool_target = self._docker_target_identity()
        self._docker_tool_observed_target = observed_target
        self._docker_tool_action = docker_action
        self._docker_tool_request = (tool.definition.id, action.id)
        self._begin_lifecycle_worker(worker, self._on_docker_tool_completed)
        return True

    @pyqtSlot(object)
    def _on_docker_tool_completed(self, result: object) -> None:
        """Apply only the exact current operation's private-safe semantic result."""
        current = (
            isinstance(result, DockerToolResult)
            and result.request_token is getattr(self, "_docker_tool_token", None)
            and result.action is getattr(self, "_docker_tool_action", None)
            and self._docker_tool_generation == getattr(self, "_monitor_generation", 0)
            and self._docker_tool_target == self._docker_target_identity()
            and self._docker_tool_observed_target is not None
            and self._docker_tool_observed_target
            == self._current_observed_docker_target_identity()
            and result.target_identity == self._docker_tool_observed_target
            and self._docker_managed()
            and not getattr(self, "_close_in_progress", False)
        )
        request = getattr(self, "_docker_tool_request", None)
        if current and request is not None:
            tool_id, action_id = request
            self._tools_page.set_launch_result(
                tool_id,
                action_id,
                success=result.succeeded,
                message=result.message if result.succeeded else result.error,
                completed=True,
            )
            if not result.succeeded:
                QMessageBox.critical(
                    self,
                    "Docker Tool Operation Failed",
                    result.error or "Docker tool operation failed.",
                )
            self._docker_observe_requested.emit()

        self._docker_tool_token = None
        self._docker_tool_generation = None
        self._docker_tool_target = None
        self._docker_tool_observed_target = None
        self._docker_tool_action = None
        self._docker_tool_request = None
        self._lifecycle_result_received = True
        self._finish_lifecycle_if_complete()

    # ── Server control ─────────────────────────────────────────────────

    def _docker_mode(self) -> bool:
        return self._cfg.get("runtime_backend") == "docker_compose"

    def _docker_monitor_settings_identity(self) -> str:
        """Hash selected Docker target settings without exposing private paths."""
        return docker_settings_identity(
            str(self._cfg.get("evejs_root", "")),
            str(self._cfg.get("docker_compose_file", "")),
            str(self._cfg.get("docker_project_name", "")),
        )

    def _docker_control_reason(self) -> str:
        if self._cfg.get("docker_control_policy") == "connect_only":
            return "Connect-only Docker mode cannot change containers."
        return "Docker controls require Managed Docker mode."

    def _docker_managed(self) -> bool:
        return self._docker_mode() and self._cfg.get("docker_control_policy") == "managed"

    def _docker_unavailable(self, message: str) -> None:
        QMessageBox.information(self, "Docker Compose", message)

    def _docker_lifecycle_target_factory(self) -> Callable[[], ComposeTarget]:
        return self._docker_log_target_factory()

    def _docker_setup_draft(self) -> DockerSetupDraft:
        """Capture the selected target once before worker-thread validation."""
        return DockerSetupDraft(
            evejs_root=str(self._cfg.get("evejs_root", "")),
            compose_file=str(self._cfg.get("docker_compose_file", "")),
            project_name=str(self._cfg.get("docker_project_name", "")),
            control_policy=str(
                self._cfg.get("docker_control_policy", "connect_only")
            ),
            keep_running_on_exit=bool(
                self._cfg.get("docker_keep_running_on_exit", True)
            ),
            client_path=str(self._cfg.get("client_path", "")),
        )

    def _docker_target_identity(self) -> tuple[object, ...]:
        """Return the exact settings identity that produced a lifecycle worker."""
        return (
            self._cfg.get("runtime_backend"),
            self._cfg.get("docker_control_policy"),
            self._cfg.get("evejs_root"),
            self._cfg.get("docker_compose_file"),
            self._cfg.get("docker_project_name"),
        )

    def _begin_docker_lifecycle(
        self,
        action: DockerLifecycleAction,
        *,
        expected_target_identity: str | None = None,
        on_complete: Callable[[bool], None] | None = None,
        suppress_failure_dialog: bool = False,
    ) -> bool:
        if not self._docker_managed():
            self._docker_unavailable(self._docker_control_reason())
            return False
        if self._lifecycle_active():
            return False
        def controller_factory(target: ComposeTarget) -> ManagedComposeController:
            # This factory runs only after DockerLifecycleWorker has moved to
            # its worker thread. Inspector and controller intentionally share
            # one runner, keeping discovery and CLI use off the GUI thread.
            runner = DockerCommandRunner()
            return ManagedComposeController(
                target, ComposeInspector(runner), runner,
                policy=DockerControlPolicy.MANAGED,
                expected_target_identity=expected_target_identity,
            )

        worker = DockerLifecycleWorker(
            self._docker_lifecycle_target_factory(),
            controller_factory,
            action,
            policy=DockerControlPolicy.MANAGED,
        )
        # A Docker lifecycle snapshot must never retain Native process
        # identity, including the rollback copy used for no-record failures.
        snapshot = replace(
            self._docker_cached_snapshot(),
            game_pid=None,
            market_pid=None,
            game_owned=False,
            market_owned=False,
        )
        self._docker_lifecycle_snapshot = snapshot
        self._docker_lifecycle_generation = getattr(self, "_monitor_generation", 0)
        self._docker_lifecycle_target = self._docker_target_identity()
        self._docker_lifecycle_action = action
        self._docker_lifecycle_observed_target = expected_target_identity
        self._docker_lifecycle_completion = on_complete
        self._docker_lifecycle_suppress_failure_dialog = bool(
            suppress_failure_dialog
        )
        game = ServiceState.STARTING if action in {
            DockerLifecycleAction.START_GAME,
            DockerLifecycleAction.START_STACK,
            DockerLifecycleAction.RESTART_GAME,
            DockerLifecycleAction.RECREATE_GAME,
        } else snapshot.game
        market = (ServiceState.STARTING
                  if action in {DockerLifecycleAction.START_MARKET, DockerLifecycleAction.START_GAME, DockerLifecycleAction.START_STACK}
                  and snapshot.market is not ServiceState.ONLINE else snapshot.market)
        game = ServiceState.STOPPING if action in {DockerLifecycleAction.STOP_GAME, DockerLifecycleAction.STOP_ALL} else game
        market = ServiceState.STOPPING if action in {DockerLifecycleAction.STOP_MARKET, DockerLifecycleAction.STOP_ALL} else market
        self._runtime_snapshot = replace(snapshot, game=game, market=market)
        self._apply_runtime_snapshot(self._runtime_snapshot)
        launching_event = self._docker_start_voice_event(action)
        self._begin_lifecycle_worker(worker, self._on_docker_lifecycle_completed)
        if launching_event is not None:
            self._announce_shipboard(launching_event)
        stopping_event = self._docker_stop_voice_event(action)
        if stopping_event is not None:
            self._announce_shipboard(stopping_event)
        return True

    @pyqtSlot(object)
    def _on_docker_lifecycle_completed(self, result: object) -> None:
        from .core.runtime.docker_controller import DockerLifecycleResult
        expected_action = getattr(self, "_docker_lifecycle_action", None)
        expected_observed_target = getattr(
            self,
            "_docker_lifecycle_observed_target",
            None,
        )
        current = (
            isinstance(result, DockerLifecycleResult)
            and result.action is expected_action
            and self._docker_lifecycle_generation == getattr(self, "_monitor_generation", 0)
            and self._docker_lifecycle_target == self._docker_target_identity()
            and (
                expected_observed_target is None
                or (
                    result.target_identity == expected_observed_target
                    and self._current_observed_docker_target_identity()
                    == expected_observed_target
                )
            )
        )
        close_stop_result = (
            self._docker_close_pending and self._docker_close_stop_started
        )
        if isinstance(result, DockerLifecycleResult) and self._docker_managed() and current:
            launching_event = self._docker_start_voice_event(expected_action)
            if launching_event is not None:
                self._announce_shipboard(
                    service_start_result_event(
                        launching_event,
                        succeeded=result.succeeded,
                    )
                )
            stopping_event = self._docker_stop_voice_event(expected_action)
            if stopping_event is not None:
                self._announce_shipboard(
                    service_stop_result_event(
                        stopping_event,
                        succeeded=result.succeeded,
                    )
                )
            snapshot = self._docker_cached_snapshot()
            records = result.records or {}
            game_record, market_record = records.get("server"), records.get("market")
            affected_game, affected_market = self._docker_lifecycle_scope(result.action)
            if game_record is not None:
                snapshot = replace(
                    snapshot, game=self._docker_lifecycle_record_state(game_record),
                    game_container=game_record.short_id, game_health=game_record.health,
                    game_error=None if result.succeeded else (
                        result.error if affected_game else snapshot.game_error
                    ), game_pid=None, game_owned=False,
                )
            if market_record is not None:
                snapshot = replace(
                    snapshot, market=self._docker_lifecycle_record_state(market_record),
                    market_container=market_record.short_id, market_health=market_record.health,
                    market_error=None if result.succeeded else (
                        result.error if affected_market else snapshot.market_error
                    ), market_pid=None, market_owned=False,
                )
            if not records and not result.succeeded and self._docker_lifecycle_snapshot is not None:
                prior = self._docker_lifecycle_snapshot
                game_error = result.error if affected_game else prior.game_error
                market_error = result.error if affected_market else prior.market_error
                snapshot = replace(prior, game_error=game_error, market_error=market_error)
            self._runtime_snapshot = snapshot
            self._apply_runtime_snapshot(snapshot)
            if (
                not result.succeeded
                and not close_stop_result
                and not getattr(
                    self,
                    "_docker_lifecycle_suppress_failure_dialog",
                    False,
                )
            ):
                QMessageBox.critical(self, "Docker Lifecycle Failed", result.error or "Docker lifecycle operation failed.")
            self._docker_observe_requested.emit()
        if close_stop_result:
            self._docker_close_stop_succeeded = bool(
                isinstance(result, DockerLifecycleResult) and current and result.succeeded
            )
            if not self._docker_close_stop_succeeded:
                self._docker_close_pending = False
                self._docker_close_stop_started = False
                self._close_in_progress = False
                QMessageBox.critical(
                    self,
                    "Docker Shutdown Failed",
                    "Docker shutdown could not be confirmed. The launcher remains open; check Docker status and retry.",
                )
        completion = getattr(self, "_docker_lifecycle_completion", None)
        completion_succeeded = bool(
            isinstance(result, DockerLifecycleResult)
            and current
            and result.succeeded
        )
        if callable(completion):
            self._lifecycle_after_thread_callback = (
                lambda callback=completion, succeeded=completion_succeeded: callback(
                    succeeded
                )
            )
        self._docker_lifecycle_snapshot = None
        self._docker_lifecycle_generation = None
        self._docker_lifecycle_target = None
        self._docker_lifecycle_action = None
        self._docker_lifecycle_observed_target = None
        self._docker_lifecycle_completion = None
        self._docker_lifecycle_suppress_failure_dialog = False
        self._lifecycle_result_received = True
        self._finish_lifecycle_if_complete()

    @staticmethod
    def _docker_lifecycle_record_state(record: object) -> ServiceState:
        """Never render a running container Online without health evidence."""
        if (
            getattr(record, "raw_state", None) == "running"
            and getattr(record, "health", None) is None
        ):
            return ServiceState.STARTING
        return getattr(record, "state", ServiceState.UNKNOWN)

    @staticmethod
    def _docker_lifecycle_scope(action: DockerLifecycleAction) -> tuple[bool, bool]:
        """Return the services an action may change or report as failed."""
        return {
            DockerLifecycleAction.START_MARKET: (False, True),
            DockerLifecycleAction.START_GAME: (True, True),
            DockerLifecycleAction.START_STACK: (True, True),
            DockerLifecycleAction.STOP_GAME: (True, False),
            DockerLifecycleAction.STOP_MARKET: (False, True),
            DockerLifecycleAction.STOP_ALL: (True, True),
            DockerLifecycleAction.RESTART_GAME: (True, False),
            DockerLifecycleAction.RECREATE_GAME: (True, False),
        }[action]

    @staticmethod
    def _docker_stop_voice_event(
        action: DockerLifecycleAction | None,
    ) -> VoiceEvent | None:
        """Map only explicit Docker stop actions to bounded LYRA events."""
        return {
            DockerLifecycleAction.STOP_GAME: VoiceEvent.GAME_SERVER_STOPPING,
            DockerLifecycleAction.STOP_MARKET: VoiceEvent.MARKET_SERVER_STOPPING,
            DockerLifecycleAction.STOP_ALL: VoiceEvent.SERVER_STACK_STOPPING,
        }.get(action)

    @staticmethod
    def _docker_start_voice_event(
        action: DockerLifecycleAction | None,
    ) -> VoiceEvent | None:
        """Map only explicit Docker start actions to bounded LYRA events."""
        return {
            DockerLifecycleAction.START_GAME: VoiceEvent.GAME_SERVER_LAUNCHING,
            DockerLifecycleAction.START_MARKET: VoiceEvent.MARKET_SERVER_LAUNCHING,
            DockerLifecycleAction.START_STACK: VoiceEvent.SERVER_STACK_LAUNCHING,
        }.get(action)

    # ── Server control ─────────────────────────────────────────────────

    @staticmethod
    def _server_mode_label(mode: str) -> str:
        """Return a private-safe presentation label for an explicit mode."""
        labels = {"vanilla": "Vanilla", "modded": "Modded"}
        return labels.get(mode.casefold(), "Unsupported")

    def _effective_server_mode_label(self) -> str:
        """Return the private-safe mode that the next start would resolve to."""
        if self._docker_mode():
            policy = str(self._cfg.get("docker_control_policy", "connect_only"))
            return f"DOCKER • {policy.replace('_', ' ').upper()}"
        evejs_root = str(self._cfg.get("evejs_root", ""))
        scripts = discover_server_scripts(evejs_root)
        if len(scripts) == 1:
            try:
                return self._server_mode_label(mode_for_script(scripts[0]))
            except ValueError:
                return "Unsupported"

        preference = str(
            self._cfg.get("server_start_preference", ASK_EVERY_TIME)
        ).casefold()
        legacy_entry = Path(evejs_root) / "server" / "index.js"
        if not scripts and evejs_root and legacy_entry.is_file():
            return self._server_mode_label(str(self._cfg.get("server_mode", "modded")))
        if preference == ASK_EVERY_TIME:
            return "Ask on start"
        selected = choose_saved_script(scripts, preference)
        if selected is None:
            return "Ask on start"
        try:
            return self._server_mode_label(mode_for_script(selected))
        except ValueError:
            return "Unsupported"

    def _resolve_server_start(self) -> tuple[str, Path | None] | None:
        """Resolve the explicit Node launch mode and its indicator script."""
        evejs_root = str(self._cfg.get("evejs_root", ""))
        scripts = discover_server_scripts(evejs_root)
        if not scripts:
            index_js = Path(evejs_root) / "server" / "index.js"
            fallback_mode = str(self._cfg.get("server_mode", "modded"))
            if not index_js.is_file():
                QMessageBox.critical(
                    self,
                    "Invalid EveJS Installation",
                    "No StartServer*.bat indicator was found, and the legacy "
                    "server/index.js entry point is missing.",
                )
                return None
            if fallback_mode in {"vanilla", "modded"}:
                log.info(
                    "Resolved server start: no indicator script -> legacy %s mode",
                    fallback_mode,
                )
                return fallback_mode, None
            QMessageBox.critical(
                self,
                "Invalid Server Mode",
                f"Unsupported legacy server mode: {fallback_mode}",
            )
            return None
        preference = str(
            self._cfg.get("server_start_preference", ASK_EVERY_TIME)
        )
        selected = choose_saved_script(
            scripts,
            preference,
        )
        if selected is None and len(scripts) > 1:
            if preference and preference.casefold() != ASK_EVERY_TIME:
                self._cfg["server_start_preference"] = ASK_EVERY_TIME
                config.save(self._cfg)
            chosen_name, accepted = QInputDialog.getItem(
                self,
                "Choose Server Start Script",
                "Select the server mode indicator for this start:",
                [script.name for script in scripts],
                0,
                False,
            )
            if not accepted:
                return None
            selected = next(
                (script for script in scripts if script.name == chosen_name),
                None,
            )
        if selected is None:
            return None
        try:
            mode = mode_for_script(selected)
        except ValueError as exc:
            QMessageBox.critical(self, "Unsupported Server Script", str(exc))
            return None
        log.info("Resolved server start: %s -> %s", selected.name, mode)
        return mode, selected

    def _on_server_toggle(self) -> None:
        if self._docker_mode():
            if not self._docker_managed():
                self._docker_unavailable(self._docker_control_reason())
            elif self._docker_cached_snapshot().game is ServiceState.ONLINE:
                self._stop_server()
            else:
                self._start_server()
            return
        if is_server_running(port=int(Ports.GAME_TCP)):
            self._stop_server()
        else:
            self._start_server()

    def _on_market_toggle(self) -> None:
        if self._docker_mode():
            if not self._docker_managed():
                self._docker_unavailable(self._docker_control_reason())
            elif self._docker_cached_snapshot().market is ServiceState.ONLINE:
                self._stop_market()
            else:
                self._start_market()
            return
        if self._is_market_running():
            self._stop_market()
        else:
            self._start_market()

    def _server_process_alive(self) -> bool:
        """Return whether this launcher owns a live game-server process."""
        return self._server_proc is not None and self._server_proc.poll() is None

    def _lifecycle_active(self) -> bool:
        """Return whether a lifecycle operation still owns the continuation slot."""
        return getattr(self, "_lifecycle_thread", None) is not None

    def _publish_cached_runtime(self) -> None:
        """Render already-known service state without doing GUI-thread socket I/O."""
        if self._docker_mode():
            snapshot = self._docker_cached_snapshot()
            self._runtime_snapshot = snapshot
            self._apply_runtime_snapshot(snapshot)
            return
        snapshot = self._build_runtime_snapshot()
        self._runtime_snapshot = snapshot
        self._apply_runtime_snapshot(snapshot)
        if getattr(self, "_service_monitor", None) is not None:
            self._service_probe_requested.emit()

    def _docker_unknown_snapshot(self) -> RuntimeSnapshot:
        """Return a clean Docker bootstrap without prior target identity."""
        return RuntimeSnapshot(
            ServiceState.UNKNOWN,
            ServiceState.UNKNOWN,
            self._tracker.running_count,
            backend=RuntimeBackend.DOCKER_COMPOSE,
            docker_control_policy=DockerControlPolicy(
                self._cfg.get("docker_control_policy", "connect_only")
            ),
            settings_identity=self._docker_monitor_settings_identity(),
            monitor_generation=getattr(self, "_monitor_generation", 0),
        )

    def _docker_cached_snapshot(self) -> RuntimeSnapshot:
        """Return Docker state cached for the current observer target."""
        snapshot = self._runtime_snapshot
        if snapshot.backend is not RuntimeBackend.DOCKER_COMPOSE:
            return self._docker_unknown_snapshot()
        return replace(
            snapshot,
            running_clients=self._tracker.running_count,
            docker_control_policy=DockerControlPolicy(
                self._cfg.get("docker_control_policy", "connect_only")
            ),
        )

    @pyqtSlot(object)
    def _begin_docker_preflight(self, request: object) -> None:
        """Own one read-only setup worker until result delivery and teardown."""
        if not isinstance(request, DockerPreflightRequest):
            return
        if self._docker_preflight_thread is not None:
            self._settings_page.reject_docker_preflight_request(
                request,
                "Another Docker setup check is still finishing. Try again in a moment.",
            )
            return

        factory = getattr(self, "_docker_preflight_worker_factory", None)
        worker = (
            factory(request)
            if callable(factory)
            else DockerPreflightWorker(request)
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_docker_preflight_completed)
        worker.cleanup.connect(
            worker.deleteLater,
            Qt.ConnectionType.DirectConnection,
        )
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_docker_preflight_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._docker_preflight_thread = thread
        self._docker_preflight_worker = worker
        self._docker_preflight_result_received = False
        self._docker_preflight_thread_finished = False
        thread.start()

    @pyqtSlot(object)
    def _on_docker_preflight_completed(self, result: object) -> None:
        self._settings_page.apply_docker_preflight_result(result)
        self._docker_preflight_result_received = True
        self._finish_docker_preflight_if_complete()

    @pyqtSlot()
    def _on_docker_preflight_thread_finished(self) -> None:
        self._docker_preflight_thread_finished = True
        self._finish_docker_preflight_if_complete()

    def _finish_docker_preflight_if_complete(self) -> None:
        if not (
            self._docker_preflight_result_received
            and self._docker_preflight_thread_finished
        ):
            return
        thread = self._docker_preflight_thread
        self._docker_preflight_thread = None
        self._docker_preflight_worker = None
        self._docker_preflight_result_received = False
        self._docker_preflight_thread_finished = False
        if thread is not None:
            thread.deleteLater()
        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _begin_lifecycle_worker(
        self,
        worker: QObject,
        completed_handler: Callable[[object], None],
    ) -> None:
        """Move a fresh one-shot worker to its own thread and retain it safely."""
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        worker.completed.connect(completed_handler)  # type: ignore[attr-defined]
        if isinstance(
            worker,
            (DockerLifecycleWorker, DockerToolWorker, DockerCharacterCreationWorker),
        ):
            # QObject deletion must be delivered in its owning worker thread.
            # Deletion then tears down the dedicated event loop; connecting
            # thread.finished -> worker.deleteLater is too late and leaks.
            worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
            worker.destroyed.connect(thread.quit)
        else:
            worker.completed.connect(thread.quit)  # type: ignore[attr-defined]
            thread.finished.connect(worker.deleteLater)
        thread.finished.connect(
            self._on_lifecycle_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._lifecycle_thread = thread
        self._lifecycle_worker = worker
        self._lifecycle_result_received = False
        self._lifecycle_thread_finished = False
        thread.start()

    @pyqtSlot()
    def _on_lifecycle_thread_finished(self) -> None:
        """Record worker shutdown in the GUI thread before running continuations."""
        if getattr(self, "_lifecycle_thread", None) is None:
            return
        self._lifecycle_thread_finished = True
        self._finish_lifecycle_if_complete()

    def _finish_lifecycle_if_complete(self) -> None:
        """Run the continuation only after result handling and thread teardown."""
        if not getattr(self, "_lifecycle_result_received", False):
            return
        if not getattr(self, "_lifecycle_thread_finished", False):
            return
        thread = self._lifecycle_thread
        self._lifecycle_thread = None
        self._lifecycle_worker = None
        self._lifecycle_result_received = False
        self._lifecycle_thread_finished = False
        callback = getattr(self, "_lifecycle_after_thread_callback", None)
        self._lifecycle_after_thread_callback = None
        if thread is not None:
            thread.deleteLater()
        if callback is not None:
            callback()
        if (
            getattr(self, "_docker_close_pending", False)
            and getattr(self, "_docker_close_stop_started", False)
        ):
            if getattr(self, "_docker_close_stop_succeeded", False):
                QTimer.singleShot(0, self.close)
        elif getattr(self, "_close_in_progress", False) and self._docker_mode():
            QTimer.singleShot(0, self.close)

    def _start_service_sequence(
        self,
        *,
        start_market: bool,
        start_game: bool,
        mode: str | None,
        on_ready: Callable[[], None] | None,
        error_title: str,
        voice_event: VoiceEvent | None = None,
    ) -> bool:
        """Start requested services in a worker, waiting Market → Game readiness."""
        if self._docker_mode():
            self._docker_unavailable(self._docker_control_reason())
            return False
        if self._lifecycle_active():
            log.info("Ignored service start while another lifecycle operation is active")
            return False
        if not start_market and not start_game:
            log.info("Ignored empty service-start request")
            return False
        evejs_root = str(self._cfg.get("evejs_root", ""))
        if not evejs_root:
            return False

        if start_market:
            self._market_intent = ServiceState.STARTING
            self._market_error = None
        if start_game:
            self._server_intent = ServiceState.STARTING
            self._server_error = None
        self._lifecycle_start_scope = (start_market, start_game)
        start_token = object()
        self._lifecycle_start_token = start_token
        self._lifecycle_start_voice_event = voice_event
        self._lifecycle_ready_callback = on_ready
        self._lifecycle_error_title = error_title
        worker = ServiceStartWorker(
            evejs_root,
            mode=mode,
            start_market=start_market,
            start_game=start_game,
            start_market_fn=start_market_server,
            start_game_fn=start_game_server,
        )
        self._begin_lifecycle_worker(
            worker,
            lambda result, token=start_token: self._on_service_start_completed_for_token(
                result,
                token,
            ),
        )
        self._publish_cached_runtime()
        if voice_event is not None:
            self._announce_shipboard(voice_event)
        return True

    def _on_service_start_completed_for_token(
        self,
        result: ServiceStartResult,
        token: object,
    ) -> None:
        """Accept a Native start result only from its retained worker binding."""
        if token is not getattr(self, "_lifecycle_start_token", None):
            log.info("Ignored stale or duplicate service-start completion")
            return
        self._on_service_start_completed(result)

    @pyqtSlot(object)
    def _on_service_start_completed(self, result: ServiceStartResult) -> None:
        """Apply a worker result and continue only after all requested readiness checks."""
        start_market, start_game = getattr(
            self,
            "_lifecycle_start_scope",
            (False, False),
        )
        game_reachable, market_reachable = getattr(
            self,
            "_service_reachability",
            (False, False),
        )

        if start_market:
            if result.market_process is not None:
                self._market_proc = result.market_process
            if result.market_ready:
                market_reachable = True
                self._market_intent = None
                self._market_error = None
            elif result.market_error:
                market_reachable = False
                self._market_intent = ServiceState.STARTING
                self._market_error = result.market_error

        if start_game:
            if result.game_process is not None:
                self._server_proc = result.game_process
            if result.game_ready:
                game_reachable = True
                self._server_intent = None
                self._server_error = None
            elif result.game_error:
                game_reachable = False
                self._server_intent = ServiceState.STARTING
                self._server_error = result.game_error

        self._service_reachability = (game_reachable, market_reachable)
        self._lifecycle_start_scope = (False, False)
        self._lifecycle_start_token = None
        callback = getattr(self, "_lifecycle_ready_callback", None)
        self._lifecycle_ready_callback = None
        launching_event = getattr(self, "_lifecycle_start_voice_event", None)
        self._lifecycle_start_voice_event = None
        if launching_event is not None:
            self._announce_shipboard(
                service_start_result_event(
                    launching_event,
                    succeeded=result.succeeded,
                )
            )
        if result.succeeded:
            self._lifecycle_after_thread_callback = callback
        else:
            self._lifecycle_after_thread_callback = None
            diagnostics = [
                message
                for message in (result.market_error, result.game_error)
                if message
            ]
            QMessageBox.critical(
                self,
                getattr(self, "_lifecycle_error_title", "Service Startup Failed"),
                "\n\n".join(diagnostics)
                + "\n\nUse the Game Console or Market Console button on Home for details.",
            )
        self._publish_cached_runtime()
        self._lifecycle_result_received = True
        self._finish_lifecycle_if_complete()

    def _run_stop_sequence(
        self,
        *,
        stop_game: bool,
        stop_market: bool,
        on_complete: Callable[[], None] | None,
        voice_event: VoiceEvent | None = None,
    ) -> bool:
        """Stop launcher-owned Game then Market processes in a worker."""
        if self._docker_mode():
            self._docker_unavailable(self._docker_control_reason())
            return False
        if self._lifecycle_active():
            log.info("Ignored service stop while another lifecycle operation is active")
            return False
        game_process = (
            self._server_proc
            if stop_game and self._server_process_alive()
            else None
        )
        market_process = (
            self._market_proc
            if stop_market
            and self._market_proc is not None
            and self._market_proc.poll() is None
            else None
        )
        if game_process is None and market_process is None:
            if on_complete is not None:
                on_complete()
            return True

        if game_process is not None:
            self._server_intent = ServiceState.STOPPING
            self._server_error = None
        if market_process is not None:
            self._market_intent = ServiceState.STOPPING
            self._market_error = None
        self._lifecycle_stop_scope = (
            game_process is not None,
            market_process is not None,
        )
        self._lifecycle_stop_callback = on_complete
        if voice_event is VoiceEvent.SERVER_STACK_STOPPING:
            voice_event = {
                (True, True): VoiceEvent.SERVER_STACK_STOPPING,
                (True, False): VoiceEvent.GAME_SERVER_STOPPING,
                (False, True): VoiceEvent.MARKET_SERVER_STOPPING,
            }[self._lifecycle_stop_scope]
        self._lifecycle_stop_voice_event = voice_event
        worker = ServiceStopWorker(game_process, market_process)
        self._begin_lifecycle_worker(worker, self._on_service_stop_completed)
        self._publish_cached_runtime()
        if voice_event is not None:
            self._announce_shipboard(voice_event)
        return True

    @pyqtSlot(object)
    def _on_service_stop_completed(self, result: ServiceStopResult) -> None:
        """Apply background shutdown results without touching external services."""
        stop_game, stop_market = getattr(
            self,
            "_lifecycle_stop_scope",
            (False, False),
        )
        game_reachable, market_reachable = getattr(
            self,
            "_service_reachability",
            (False, False),
        )

        if stop_game:
            if result.game_stopped:
                self._server_proc = None
                self._server_intent = None
                self._server_error = None
                game_reachable = False
            else:
                self._server_intent = None
                self._server_error = result.game_error
        if stop_market:
            if result.market_stopped:
                self._market_proc = None
                self._market_intent = None
                self._market_error = None
                market_reachable = False
            else:
                self._market_intent = None
                self._market_error = result.market_error

        self._service_reachability = (game_reachable, market_reachable)
        self._lifecycle_stop_scope = (False, False)
        stopping_event = getattr(self, "_lifecycle_stop_voice_event", None)
        self._lifecycle_stop_voice_event = None
        callback = getattr(self, "_lifecycle_stop_callback", None)
        self._lifecycle_stop_callback = None
        if stopping_event is not None:
            self._announce_shipboard(
                service_stop_result_event(
                    stopping_event,
                    succeeded=result.succeeded,
                )
            )
        if result.succeeded:
            self._lifecycle_after_thread_callback = callback
        else:
            self._lifecycle_after_thread_callback = None
            self._close_after_lifecycle = False
            self._close_in_progress = False
            diagnostics = [
                message
                for message in (result.game_error, result.market_error)
                if message
            ]
            QMessageBox.critical(
                self,
                "Service Shutdown Failed",
                "\n\n".join(diagnostics)
                + "\n\nUse the Game Console or Market Console button on Home for details.",
            )
            if getattr(self, "_character_creation_request", None) is not None:
                self._character_creation_request = None
                self._character_creation_restart_game = False
                self._character_creation_restart_market = False
                self._character_creation_restart_mode = None
                dialog = self._new_character_dialog
                if dialog is not None:
                    dialog.set_busy(False)
                    dialog.show_error(
                        "Character creation was cancelled because an EveJS service "
                        "could not be stopped safely. No database changes were made."
                    )
            if getattr(self, "_character_deletion_request", None) is not None:
                self._character_deletion_request = None
                self._character_deletion_restart_game = False
                self._character_deletion_restart_market = False
                self._character_deletion_restart_mode = None
                progress = self._character_deletion_progress
                self._character_deletion_progress = None
                if progress is not None:
                    progress.close()
                    progress.deleteLater()
        self._publish_cached_runtime()
        self._lifecycle_result_received = True
        self._finish_lifecycle_if_complete()

    def _start_server(self) -> None:
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.START_GAME)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS root in Settings first.")
            return
        if self._server_process_alive() or is_server_running(port=int(Ports.GAME_TCP)):
            log.info("Ignored duplicate game-server start while already active")
            self._update_status_bar()
            return
        resolved = self._resolve_server_start()
        if resolved is None:
            return
        mode, indicator_script = resolved
        indicator = indicator_script.name if indicator_script else "legacy fallback"
        log.info("Queueing game-server start (mode=%s, indicator=%s)", mode, indicator)
        self._start_service_sequence(
            start_market=False,
            start_game=True,
            mode=mode,
            on_ready=None,
            error_title="Game Server Error",
            voice_event=VoiceEvent.GAME_SERVER_LAUNCHING,
        )

    def _stop_server(self) -> None:
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.STOP_GAME)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        proc = self._server_proc
        if proc is None or proc.poll() is not None:
            if is_server_running(port=int(Ports.GAME_TCP)):
                QMessageBox.information(
                    self,
                    "Game Server",
                    "The game server was started outside this launcher.\n\n"
                    "Stop it from its original console, then restart it through "
                    "this launcher if you want the launcher to manage it.",
                )
                return
            self._server_intent = None
            self._service_reachability = (False, self._service_reachability[1])
            self._update_status_bar()
            return

        self._run_stop_sequence(
            stop_game=True,
            stop_market=False,
            on_complete=None,
            voice_event=VoiceEvent.GAME_SERVER_STOPPING,
        )

    def _on_mods_apply_restart(self) -> None:
        """Apply the selected backend's truthful mod activation contract."""
        if not self._docker_mode():
            self._restart_server()
            return
        if not self._docker_managed():
            self._docker_unavailable(
                "Connect-only Docker mode cannot change mod or Compose state."
            )
            return
        if self._lifecycle_active():
            self._docker_unavailable(
                "Another service or Docker tool operation is already running."
            )
            return

        reply = QMessageBox.question(
            self,
            "Apply Docker Mods",
            "Apply the selected mod preload chain and recreate the server "
            "container?\n\nConnected clients will be disconnected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = apply_docker_mod_override(
                str(self._cfg.get("evejs_root", "")),
                self._mods_page.selected_mod_names(),
                policy=DockerControlPolicy.MANAGED,
            )
        except (DockerModBridgeError, OSError):
            QMessageBox.critical(
                self,
                "Docker Mods Failed",
                "The Docker mod preload configuration could not be updated safely.",
            )
            return

        if not result.requires_recreation:
            QMessageBox.information(
                self,
                "Docker Mods",
                "Docker mod preload configuration is already current.",
            )
            return
        self._restart_docker_monitor_for_compose_change()
        self._begin_docker_lifecycle(DockerLifecycleAction.RECREATE_GAME)

    def _restart_server(self) -> None:
        """Resolve the launch mode before stopping, then restart the server."""
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.RESTART_GAME)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS root in Settings first.")
            return

        resolved = self._resolve_server_start()
        if resolved is None:
            return
        mode, _indicator_script = resolved

        def start_after_stop() -> None:
            self._start_service_sequence(
                start_market=False,
                start_game=True,
                mode=mode,
                on_ready=None,
                error_title="Restart Server Error",
            )

        if self._server_process_alive():
            self._run_stop_sequence(
                stop_game=True,
                stop_market=False,
                on_complete=start_after_stop,
            )
            return
        if is_server_running(port=int(Ports.GAME_TCP)):
            QMessageBox.information(
                self,
                "Game Server",
                "The game server was started outside this launcher.\n\n"
                "Stop it from its original console before starting a replacement "+
                "through this launcher.",
            )
            return
        start_after_stop()

    def _start_market(self) -> None:
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.START_MARKET)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS root in Settings first.")
            return
        if self._is_market_running():
            log.info("Ignored duplicate Market start while already active")
            self._update_status_bar()
            return
        self._start_service_sequence(
            start_market=True,
            start_game=False,
            mode=None,
            on_ready=None,
            error_title="Market Server Error",
            voice_event=VoiceEvent.MARKET_SERVER_LAUNCHING,
        )

    def _stop_market(self) -> None:
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.STOP_MARKET)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        if self._market_proc is not None and self._market_proc.poll() is None:
            self._run_stop_sequence(
                stop_game=False,
                stop_market=True,
                on_complete=None,
                voice_event=VoiceEvent.MARKET_SERVER_STOPPING,
            )
            return
        if is_server_running(port=int(Ports.MARKET_RPC)):
            # Market was started outside this launcher — can't kill safely
            QMessageBox.information(
                self, "Market Server",
                "The market server was started outside this launcher.\n\n"
                "Close it manually via Task Manager, or stop the game server\n"
                "and restart both through the launcher."
            )
            return
        self._market_intent = None
        self._service_reachability = (self._service_reachability[0], False)
        self._update_status_bar()

    def _is_market_running(self) -> bool:
        # The actual market server listens on 40110/40111, NOT 26001.
        # Port 26001 is the game server's own market proxy endpoint.
        if self._market_proc is not None and self._market_proc.poll() is None:
            return True
        # Fallback: check the real market RPC port in case market was
        # started outside the launcher.
        return is_server_running(port=int(Ports.MARKET_RPC))

    def _start_all_servers(self) -> None:
        """Start Market then Game server (both auto-discover each other)."""
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.START_STACK)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS first.")
            return

        game_active = self._server_process_alive() or is_server_running(
            port=int(Ports.GAME_TCP)
        )
        resolved: tuple[str, Path | None] | None = None
        if not game_active:
            # Resolve before Market so cancelling cannot leave a partial stack.
            resolved = self._resolve_server_start()
            if resolved is None:
                return

        start_market = not self._is_market_running()
        start_game = not game_active
        if not start_market and not start_game:
            QMessageBox.information(self, "Already Running", "Both servers are already online.")
            return

        if self._start_service_sequence(
            start_market=start_market,
            start_game=start_game,
            mode=resolved[0] if resolved is not None else None,
            on_ready=None,
            error_title="Service Startup Failed",
            voice_event=VoiceEvent.SERVER_STACK_LAUNCHING,
        ):
            return

    def _stop_all_servers(self) -> None:
        """Stop launcher-owned Game then Market in one ordered worker sequence."""
        if self._docker_mode():
            if self._docker_managed():
                self._begin_docker_lifecycle(DockerLifecycleAction.STOP_ALL)
            else:
                self._docker_unavailable(self._docker_control_reason())
            return
        self._run_stop_sequence(
            stop_game=True,
            stop_market=True,
            on_complete=None,
            voice_event=VoiceEvent.SERVER_STACK_STOPPING,
        )

    # ── Auto-start hook used before client launches ───────────────────

    def _resolve_client_launch_context(
        self,
        snapshot: RuntimeSnapshot | None = None,
    ) -> tuple[ClientLaunchContext | None, str]:
        """Resolve one authoritative client context without mutating profiles."""
        if not self._docker_mode():
            try:
                return (
                    ClientLaunchContext.native(
                        game_port=int(self._cfg.get("game_port", 26000)),
                        proxy_url=str(
                            self._cfg.get(
                                "proxy_url",
                                "http://127.0.0.1:26002",
                            )
                        ),
                    ),
                    "",
                )
            except (TypeError, ValueError):
                return None, "The configured Native client endpoints are invalid."

        observed = snapshot or getattr(self, "_runtime_snapshot", None)
        if (
            observed is None
            or observed.backend is not RuntimeBackend.DOCKER_COMPOSE
        ):
            return None, "Docker client endpoints have not been observed yet."
        if (
            observed.target_identity is None
            or observed.settings_identity != self._docker_monitor_settings_identity()
            or observed.monitor_generation
            != getattr(self, "_monitor_generation", 0)
        ):
            return (
                None,
                "Docker endpoint context is not current for the selected target. "
                "Wait for Docker status to refresh, then try again.",
            )
        if observed.game is not ServiceState.ONLINE:
            if self._docker_managed():
                return (
                    None,
                    "The Docker server is not ready. Start Server, wait for it to be "
                    "online, then try again.",
                )
            return (
                None,
                "The Docker server is not ready. Start it externally, wait for it "
                "to be online, then try again.",
            )
        try:
            return (
                ClientLaunchContext.from_docker(
                    observed.endpoints,
                    target_identity=observed.target_identity,
                    settings_identity=observed.settings_identity,
                    monitor_generation=observed.monitor_generation,
                ),
                "",
            )
        except (AttributeError, TypeError, ValueError):
            return (
                None,
                "Docker client endpoints are incomplete or unavailable. Wait for "
                "Docker status to refresh, then try again.",
            )

    def _docker_launch_capability(
        self,
        snapshot: RuntimeSnapshot | None = None,
    ) -> tuple[bool, str]:
        """Return whether the selected Docker target can launch host clients now."""
        context, reason = self._resolve_client_launch_context(snapshot)
        if context is None:
            return False, reason
        if not str(self._cfg.get("evejs_root", "")) or not str(
            self._cfg.get("client_path", "")
        ):
            return False, "Configure the Docker project root and EVE client path first."
        return True, ""

    def _ensure_server_if_needed(self, on_ready: Callable[[], None]) -> bool:
        """Invoke ``on_ready`` only after configured service auto-starts are ready."""
        if self._docker_mode():
            context, reason = self._resolve_client_launch_context()
            if context is None:
                self._docker_unavailable(reason)
                return False
            on_ready()
            return True
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            on_ready()
            return True

        auto_start_server = bool(self._cfg.get("auto_start_server", False))
        game_active = self._server_process_alive() or is_server_running(
            port=int(Ports.GAME_TCP)
        )
        resolved: tuple[str, Path | None] | None = None
        start_game = auto_start_server and not game_active
        if start_game:
            # Resolve before Market so cancelling also aborts the client action.
            resolved = self._resolve_server_start()
            if resolved is None:
                return False

        start_market = bool(self._cfg.get("auto_start_market", False)) and not (
            self._is_market_running()
        )
        if not start_market and not start_game:
            on_ready()
            return True

        return self._start_service_sequence(
            start_market=start_market,
            start_game=start_game,
            mode=resolved[0] if resolved is not None else None,
            on_ready=on_ready,
            error_title="Auto-start Services Failed",
        )

    # ── Character launching ───────────────────────────────────────────

    def _snapshot_ready_ids(self) -> set[int]:
        snapshots = load_overview_state().get("snapshots", {})
        result: set[int] = set()
        for key, value in snapshots.items():
            try:
                character_id = int(key)
            except (TypeError, ValueError):
                continue
            if character_id > 0 and isinstance(value, dict):
                result.add(character_id)
        return result

    def _poll_overview_acks(self) -> None:
        try:
            events = process_overview_ack_files()
        except Exception:
            log.exception("Unable to process overview bridge acknowledgements")
            return
        if not events:
            return
        dialog = self._new_character_dialog
        if dialog is not None:
            dialog.set_snapshot_ready_ids(self._snapshot_ready_ids())
        for event in events:
            if event.kind == "capture":
                log.info("Captured overview snapshot for character ID %s", event.character_id)
            elif event.kind == "apply":
                log.info("Applied pending overview for character ID %s", event.character_id)
            elif event.kind == "error":
                log.warning(
                    "Overview bridge failed for character ID %s: %s",
                    event.character_id,
                    event.message,
                )
            elif event.kind == "invalid":
                log.warning("Discarded invalid overview acknowledgement %s", event.message)

    def _show_new_character_dialog(self) -> None:
        if self._docker_mode():
            if not self._docker_managed():
                QMessageBox.information(
                    self,
                    "New Character",
                    "Launcher-managed Docker character creation requires Managed "
                    "Docker mode. Connect-only mode remains read-only.",
                )
                return
            if self._current_observed_docker_target_identity() is None:
                QMessageBox.information(
                    self,
                    "New Character",
                    "Wait for the launcher to verify the selected Docker Compose "
                    "project, then try again.",
                )
                return
        root = str(self._cfg.get("evejs_root", ""))
        client_path = str(self._cfg.get("client_path", ""))
        if not root or not client_path:
            QMessageBox.warning(
                self,
                "Not Configured",
                "Configure the EveJS root and copied EVE client path first.",
            )
            return
        if self._new_character_dialog is not None:
            self._new_character_dialog.raise_()
            self._new_character_dialog.activateWindow()
            return
        dialog = NewCharacterDialog(
            self._accounts,
            inspect_overview_patch(client_path),
            self._snapshot_ready_ids(),
            parent=self,
            runtime_label=(
                "MANAGED DOCKER COMPOSE" if self._docker_mode() else "NATIVE RUNTIME"
            ),
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.create_requested.connect(self._on_new_character_create)
        dialog.patch_requested.connect(
            lambda: self._begin_overview_patch(OverviewPatchAction.PATCH)
        )
        dialog.restore_requested.connect(
            lambda: self._begin_overview_patch(OverviewPatchAction.RESTORE)
        )
        dialog.finished.connect(self._on_new_character_dialog_finished)
        self._new_character_dialog = dialog
        dialog.show()

    @pyqtSlot(int)
    def _on_new_character_dialog_finished(self, _result: int) -> None:
        if self.sender() is self._new_character_dialog:
            self._new_character_dialog = None

    def _begin_overview_patch(self, action: OverviewPatchAction) -> None:
        dialog = self._new_character_dialog
        if dialog is None or self._overview_patch_thread is not None:
            return
        if (
            self._character_creation_thread is not None
            or self._character_deletion_thread is not None
            or self._client_launch_thread is not None
            or self._lifecycle_active()
        ):
            dialog.show_error("Wait for the current launcher operation to finish.")
            return
        if self._tracker.running_count or is_eve_client_running():
            dialog.show_error("Close every EVE client before changing code.ccp.")
            return
        client_path = str(self._cfg.get("client_path", ""))
        status = inspect_overview_patch(client_path)
        if action is OverviewPatchAction.PATCH and not status.can_patch:
            dialog.set_patch_status(status)
            return
        if action is OverviewPatchAction.RESTORE and not status.can_restore:
            dialog.set_patch_status(status)
            return
        verb = (
            "install the optional overview bridge"
            if action is OverviewPatchAction.PATCH
            else "restore the original client archive"
        )
        reply = QMessageBox.question(
            self,
            "EVE Client Overview Patch",
            f"{verb.capitalize()}?\n\n"
            "The launcher verifies build 3396210, keeps the original beside "
            "code.ccp, stages the replacement, and validates the completed archive.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        worker_factory = getattr(self, "_overview_patch_worker_factory", None)
        worker = (
            worker_factory(action, client_path)
            if callable(worker_factory)
            else OverviewPatchWorker(action, client_path)
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_overview_patch_completed)
        worker.failed.connect(self._on_overview_patch_failed)
        worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_overview_patch_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._overview_patch_thread = thread
        self._overview_patch_worker = worker
        self._overview_patch_outcome = None
        self._overview_patch_thread_finished = False
        dialog.show_error("")
        dialog.set_busy(
            True,
            "PATCHING CLIENT…"
            if action is OverviewPatchAction.PATCH
            else "RESTORING CLIENT…",
        )
        thread.start()

    @pyqtSlot(object)
    def _on_overview_patch_completed(self, result: OverviewPatchResult) -> None:
        self._overview_patch_outcome = result
        self._finish_overview_patch_if_complete()

    @pyqtSlot(object)
    def _on_overview_patch_failed(self, failure: OverviewPatchFailure) -> None:
        self._overview_patch_outcome = failure
        self._finish_overview_patch_if_complete()

    @pyqtSlot()
    def _on_overview_patch_thread_finished(self) -> None:
        if self.sender() is not self._overview_patch_thread:
            return
        self._overview_patch_thread_finished = True
        self._finish_overview_patch_if_complete()

    def _finish_overview_patch_if_complete(self) -> None:
        if self._overview_patch_outcome is None or not self._overview_patch_thread_finished:
            return
        outcome = self._overview_patch_outcome
        thread = self._overview_patch_thread
        self._overview_patch_thread = None
        self._overview_patch_worker = None
        self._overview_patch_outcome = None
        self._overview_patch_thread_finished = False
        if thread is not None:
            thread.deleteLater()
        dialog = self._new_character_dialog
        if dialog is not None:
            dialog.set_busy(False)
            if isinstance(outcome, OverviewPatchResult):
                dialog.set_patch_status(outcome.status)
                dialog.set_snapshot_ready_ids(self._snapshot_ready_ids())
                dialog.show_error("")
            else:
                dialog.set_patch_status(
                    inspect_overview_patch(str(self._cfg.get("client_path", "")))
                )
                dialog.show_error(outcome.message)
        if isinstance(outcome, OverviewPatchResult) and not self._close_in_progress:
            message = (
                "The overview bridge is installed.\n\nYou can create the character "
                "now. If its source overview has not been captured yet, launch the "
                "source character once afterwards, then launch the new character to "
                "apply the queued copy."
                if outcome.action is OverviewPatchAction.PATCH
                else "The original EVE client archive has been restored."
            )
            QMessageBox.information(self, "EVE Client", message)
        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _on_new_character_create(self, draft: NewCharacterDraft) -> None:
        dialog = self._new_character_dialog
        if dialog is None or not isinstance(draft, NewCharacterDraft):
            return
        if any(
            (
                self._character_creation_thread is not None,
                self._character_deletion_thread is not None,
                self._overview_patch_thread is not None,
                self._client_launch_thread is not None,
                self._launch_queue is not None,
                self._lifecycle_active(),
            )
        ):
            dialog.show_error("Wait for the current launcher operation to finish.")
            return
        if self._tracker.running_count or is_eve_client_running():
            dialog.show_error("Close every EVE client before creating a character.")
            return

        client_path = str(self._cfg.get("client_path", ""))
        if draft.overview_source_character_id is not None:
            patch_status = inspect_overview_patch(client_path)
            if patch_status.state is not OverviewPatchState.PATCHED:
                dialog.set_patch_status(patch_status)
                dialog.show_error("Install the overview bridge before copying an overview.")
                return

        if self._docker_mode():
            self._begin_docker_character_creation(draft)
            return

        game_active = is_server_running(port=int(Ports.GAME_TCP))
        market_active = self._is_market_running()
        game_owned = self._server_process_alive()
        market_owned = (
            self._market_proc is not None and self._market_proc.poll() is None
        )
        if game_active and not game_owned:
            dialog.show_error(
                "The game server was started outside this launcher. Stop it from "
                "its original console before creating a character."
            )
            return
        if market_active and not market_owned:
            dialog.show_error(
                "The market server was started outside this launcher. Stop it "
                "before creating a character."
            )
            return

        restart_mode: str | None = None
        if game_owned:
            resolved = self._resolve_server_start()
            if resolved is None:
                return
            restart_mode = resolved[0]
        if game_owned or market_owned:
            reply = QMessageBox.question(
                self,
                "Create Character Safely",
                "The launcher will temporarily stop its EveJS services, back up "
                "the affected game-store tables, create and verify the character, "
                "then restore the previous service state. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        request = CharacterCreationRequest(
            evejs_root=str(self._cfg.get("evejs_root", "")),
            username=draft.username,
            character_name=draft.character_name,
            is_gm=draft.is_gm,
            overview_source_character_id=draft.overview_source_character_id,
        )
        self._character_creation_request = request
        self._character_creation_restart_game = game_owned
        self._character_creation_restart_market = market_owned
        self._character_creation_restart_mode = restart_mode
        dialog.show_error("")
        dialog.set_busy(True, "CREATING & VERIFYING…")
        if not self._run_stop_sequence(
            stop_game=game_owned,
            stop_market=market_owned,
            on_complete=self._begin_character_creation_worker,
        ):
            self._character_creation_request = None
            dialog.set_busy(False)
            dialog.show_error("The EveJS services could not be prepared for creation.")

    def _docker_character_context_is_current(
        self,
        token: object | None = None,
        *,
        allow_closing: bool = False,
    ) -> bool:
        """Recheck every authority component captured for Docker mutation."""
        expected_token = getattr(self, "_docker_character_token", None)
        expected_target = getattr(self, "_docker_character_observed_target", None)
        return (
            expected_token is not None
            and (token is None or token is expected_token)
            and self._docker_managed()
            and (
                allow_closing
                or not getattr(self, "_close_in_progress", False)
            )
            and getattr(self, "_docker_character_generation", None)
            == getattr(self, "_monitor_generation", 0)
            and getattr(self, "_docker_character_target", None)
            == self._docker_target_identity()
            and expected_target is not None
            and self._current_observed_docker_target_identity() == expected_target
        )

    def _begin_docker_character_creation(self, draft: NewCharacterDraft) -> None:
        """Sequence stop -> one transactional helper -> prior-state restore."""
        dialog = self._new_character_dialog
        if dialog is None:
            return
        character_name = normalize_character_name(draft.character_name)
        if character_name is None:
            dialog.show_error(
                "Character name must be 3-37 characters and cannot contain "
                "control characters."
            )
            return
        if not self._docker_managed():
            dialog.show_error(
                "Docker character creation requires Managed mode; Connect-only "
                "mode remains read-only."
            )
            return
        observed_target = self._current_observed_docker_target_identity()
        if observed_target is None:
            dialog.show_error(
                "Docker target context is not current. Wait for Docker status to "
                "refresh, then try again."
            )
            return
        snapshot = self._docker_cached_snapshot()
        transitional = {
            ServiceState.STARTING,
            ServiceState.STOPPING,
            ServiceState.UNKNOWN,
        }
        if snapshot.game in transitional or snapshot.market in transitional:
            dialog.show_error(
                "Wait for Docker Game and Market status to become stable first."
            )
            return
        if (
            snapshot.game is ServiceState.ONLINE
            and snapshot.market is ServiceState.OFFLINE
        ):
            dialog.show_error(
                "Docker Game is online while Market is offline. Start Market or "
                "stop Game, wait for status to settle, then try again."
            )
            return

        restore_game = snapshot.game is ServiceState.ONLINE
        restore_market = snapshot.market is ServiceState.ONLINE
        needs_stop = snapshot.game is not ServiceState.OFFLINE or (
            snapshot.market is not ServiceState.OFFLINE
        )
        if needs_stop:
            reply = QMessageBox.question(
                self,
                "Create Character Safely",
                "The launcher will temporarily stop the selected Compose services, "
                "create a scoped game-store backup, create and verify the character, "
                "then restore only the services that were online. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        token = object()
        self._docker_character_token = token
        self._docker_character_generation = getattr(self, "_monitor_generation", 0)
        self._docker_character_target = self._docker_target_identity()
        self._docker_character_observed_target = observed_target
        self._docker_character_request = DockerCharacterCreationRequest(
            draft.username,
            character_name,
            draft.is_gm,
        )
        self._docker_character_overview_source_id = draft.overview_source_character_id
        self._docker_character_result = None
        self._docker_character_restore_game = restore_game
        self._docker_character_restore_market = restore_market
        dialog.show_error("")
        dialog.set_busy(True, "PREPARING DOCKER DATA…" if needs_stop else "CREATING & VERIFYING…")

        if needs_stop:
            began = self._begin_docker_lifecycle(
                DockerLifecycleAction.STOP_ALL,
                expected_target_identity=observed_target,
                on_complete=lambda succeeded, operation_token=token: (
                    self._after_docker_character_stop(operation_token, succeeded)
                ),
                suppress_failure_dialog=True,
            )
            if not began:
                self._abort_docker_character_creation(
                    "The selected Compose services could not be prepared safely."
                )
            return
        self._begin_docker_character_creation_worker(token)

    def _after_docker_character_stop(
        self,
        token: object,
        succeeded: bool,
    ) -> None:
        """Continue only after the retained lifecycle worker has been released."""
        if not self._docker_character_context_is_current(
            token,
            allow_closing=True,
        ):
            self._clear_docker_character_creation()
            return
        if not succeeded:
            self._finish_docker_character_without_mutation(
                token,
                "The selected Compose services could not be stopped; no character "
                "data was changed.",
            )
            return
        if getattr(self, "_close_in_progress", False):
            self._finish_docker_character_without_mutation(
                token,
                "Character creation was cancelled while the launcher was closing; "
                "no character data was changed.",
            )
            return
        if self._tracker.running_count or is_eve_client_running():
            result = DockerCharacterCreationResult(
                False,
                rollback_confirmed=True,
                restart_safe=True,
                error=(
                    "An EVE client started while services were stopping; no "
                    "character data was changed."
                ),
                request_token=token,
                target_identity=getattr(
                    self,
                    "_docker_character_observed_target",
                    None,
                ),
            )
            self._docker_character_result = result
            self._after_docker_character_creation_worker()
            return
        self._begin_docker_character_creation_worker(token)

    def _finish_docker_character_without_mutation(
        self,
        token: object,
        error: str,
    ) -> None:
        """Restore prior service state after a confirmed no-mutation outcome."""
        result = DockerCharacterCreationResult(
            False,
            backup_created=False,
            rollback_confirmed=True,
            restart_safe=True,
            error=error,
            request_token=token,
            target_identity=getattr(
                self,
                "_docker_character_observed_target",
                None,
            ),
        )
        self._docker_character_result = result
        self._after_docker_character_creation_worker()

    def _begin_docker_character_creation_worker(self, token: object) -> None:
        """Create the Docker controller only after entering its worker thread."""
        request = getattr(self, "_docker_character_request", None)
        observed_target = getattr(self, "_docker_character_observed_target", None)
        if (
            request is None
            or observed_target is None
            or not self._docker_character_context_is_current(token)
            or self._lifecycle_active()
        ):
            self._abort_docker_character_creation(
                "Docker target context changed before character creation could run."
            )
            return

        helper_directory = Path(__file__).resolve().parent / "core" / "helpers"
        backup_directory = (
            config.CONFIG_DIR / "backups" / "docker_character_creation"
        )

        def controller_factory(
            target: ComposeTarget,
        ) -> ManagedDockerCharacterCreationController:
            runner = DockerCommandRunner()
            return ManagedDockerCharacterCreationController(
                target,
                ComposeInspector(runner),
                runner,
                policy=DockerControlPolicy.MANAGED,
                expected_target_identity=observed_target,
                helper_directory=helper_directory,
                backup_directory=backup_directory,
            )

        worker_factory = getattr(
            self,
            "_docker_character_creation_worker_factory",
            None,
        )
        worker = (
            worker_factory(request, observed_target, token)
            if callable(worker_factory)
            else DockerCharacterCreationWorker(
                self._docker_lifecycle_target_factory(),
                controller_factory,
                request,
                policy=DockerControlPolicy.MANAGED,
                expected_target_identity=observed_target,
                request_token=token,
            )
        )
        dialog = self._new_character_dialog
        if dialog is not None:
            dialog.set_busy(True, "CREATING & VERIFYING…")
        self._begin_lifecycle_worker(
            worker,
            self._on_docker_character_creation_completed,
        )

    @pyqtSlot(object)
    def _on_docker_character_creation_completed(self, result: object) -> None:
        """Accept only a result attributed to the still-current target context."""
        current = (
            isinstance(result, DockerCharacterCreationResult)
            and result.request_token is getattr(self, "_docker_character_token", None)
            and self._docker_character_context_is_current(
                result.request_token,
                allow_closing=True,
            )
            and result.target_identity
            == getattr(self, "_docker_character_observed_target", None)
        )
        if current:
            self._docker_character_result = result
            self._lifecycle_after_thread_callback = (
                self._after_docker_character_creation_worker
            )
        else:
            dialog = getattr(self, "_new_character_dialog", None)
            closing = bool(getattr(self, "_close_in_progress", False))
            self._clear_docker_character_creation()
            if dialog is not None and not closing:
                dialog.set_busy(False)
                dialog.show_error(
                    "Docker target context changed while character creation was "
                    "finishing. Refresh Docker status before trying again."
                )
        self._lifecycle_result_received = True
        self._finish_lifecycle_if_complete()

    def _after_docker_character_creation_worker(self) -> None:
        """Restore prior services only after a verified success or rollback."""
        result = getattr(self, "_docker_character_result", None)
        token = getattr(self, "_docker_character_token", None)
        if (
            not isinstance(result, DockerCharacterCreationResult)
            or not self._docker_character_context_is_current(
                token,
                allow_closing=True,
            )
        ):
            self._clear_docker_character_creation()
            return

        restart_safe = (
            result.restart_safe is True
            and (
                result.cleanup_confirmed is True
                if result.succeeded
                else result.rollback_confirmed is True
            )
        )
        restore_game = bool(self._docker_character_restore_game)
        restore_market = bool(self._docker_character_restore_market)
        closing = bool(getattr(self, "_close_in_progress", False))
        keep_running = bool(
            self._cfg.get("docker_keep_running_on_exit", True)
        )
        should_restore = not closing or keep_running
        if restart_safe and should_restore and (restore_game or restore_market):
            action = (
                DockerLifecycleAction.START_STACK
                if restore_game
                else DockerLifecycleAction.START_MARKET
            )
            began = self._begin_docker_lifecycle(
                action,
                expected_target_identity=result.target_identity,
                on_complete=lambda succeeded, outcome=result: (
                    self._finalize_docker_character_creation(
                        outcome,
                        restore_succeeded=succeeded,
                    )
                ),
                suppress_failure_dialog=True,
            )
            if began:
                return
            self._finalize_docker_character_creation(
                result,
                restore_succeeded=False,
            )
            return
        self._finalize_docker_character_creation(
            result,
            restore_succeeded=None,
        )

    def _finalize_docker_character_creation(
        self,
        result: DockerCharacterCreationResult,
        *,
        restore_succeeded: bool | None,
    ) -> None:
        """Publish one current result after all authorized restoration is done."""
        token = getattr(self, "_docker_character_token", None)
        current = (
            self._docker_character_context_is_current(
                token,
                allow_closing=True,
            )
            and result is getattr(self, "_docker_character_result", None)
        )
        request = getattr(self, "_docker_character_request", None)
        source_id = getattr(self, "_docker_character_overview_source_id", None)
        closing = bool(getattr(self, "_close_in_progress", False))
        self._clear_docker_character_creation()
        if not current or request is None or closing:
            if closing:
                QTimer.singleShot(0, self.close)
            return

        dialog = self._new_character_dialog
        if result.succeeded:
            overview_error = ""
            if source_id is not None and result.character_id is not None:
                try:
                    add_pending_overview_import(result.character_id, source_id)
                except Exception as exc:
                    log.exception("Unable to persist pending overview import")
                    overview_error = str(exc) or type(exc).__name__
            self._keep_created_character_visible(request.character_name)
            self._refresh_characters()
            if dialog is not None:
                dialog.set_busy(False)
                dialog.accept()
            created = "The Docker account and character were created and verified."
            if result.cleanup_confirmed is False:
                QMessageBox.warning(
                    self,
                    "Character Created — Cleanup Unconfirmed",
                    f"{created}\n\nDo not retry creation. EveJS did not confirm "
                    "final maintenance cleanup, so the Compose services were kept "
                    "stopped. Retain the scoped backup and verify the game store "
                    "before starting services.",
                )
            elif restore_succeeded is False:
                QMessageBox.warning(
                    self,
                    "Character Created — Services Not Restored",
                    f"{created}\n\nThe prior Compose service state could not be "
                    "restored automatically.",
                )
            elif overview_error:
                QMessageBox.warning(
                    self,
                    "Character Created — Overview Not Queued",
                    f"{created}\n\nThe pending overview import could not be saved: "
                    f"{overview_error}",
                )
            else:
                QMessageBox.information(self, "Character Created", created)
        else:
            if dialog is not None:
                dialog.set_busy(False)
                message = result.error or "Docker character creation failed."
                if restore_succeeded is False:
                    message += " The prior Compose service state was not restored."
                dialog.show_error(message)
            self._docker_observe_requested.emit()

    def _abort_docker_character_creation(self, message: str) -> None:
        """Release a not-yet-mutating request and keep the dialog actionable."""
        dialog = self._new_character_dialog
        self._clear_docker_character_creation()
        if dialog is not None and not getattr(self, "_close_in_progress", False):
            dialog.set_busy(False)
            dialog.show_error(message)

    def _clear_docker_character_creation(self) -> None:
        self._docker_character_token = None
        self._docker_character_generation = None
        self._docker_character_target = None
        self._docker_character_observed_target = None
        self._docker_character_request = None
        self._docker_character_overview_source_id = None
        self._docker_character_result = None
        self._docker_character_restore_game = False
        self._docker_character_restore_market = False

    def _begin_character_creation_worker(self) -> None:
        request = self._character_creation_request
        dialog = self._new_character_dialog
        if request is None:
            return
        if is_server_running(port=int(Ports.GAME_TCP)) or is_server_running(
            port=int(Ports.MARKET_RPC)
        ):
            if dialog is not None:
                dialog.set_busy(False)
                dialog.show_error(
                    "An EveJS service is still running; no database changes were made."
                )
            self._character_creation_request = None
            self._character_creation_restart_game = False
            self._character_creation_restart_market = False
            self._character_creation_restart_mode = None
            return

        worker_factory = getattr(self, "_character_creation_worker_factory", None)
        worker = (
            worker_factory(request)
            if callable(worker_factory)
            else CharacterCreationWorker(request)
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_character_creation_completed)
        worker.failed.connect(self._on_character_creation_failed)
        worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_character_creation_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._character_creation_thread = thread
        self._character_creation_worker = worker
        self._character_creation_outcome = None
        self._character_creation_thread_finished = False
        thread.start()

    @pyqtSlot(object)
    def _on_character_creation_completed(self, result: CharacterCreationResult) -> None:
        self._character_creation_outcome = result
        self._finish_character_creation_if_complete()

    @pyqtSlot(object)
    def _on_character_creation_failed(self, failure: CharacterCreationFailure) -> None:
        self._character_creation_outcome = failure
        self._finish_character_creation_if_complete()

    @pyqtSlot()
    def _on_character_creation_thread_finished(self) -> None:
        if self.sender() is not self._character_creation_thread:
            return
        self._character_creation_thread_finished = True
        self._finish_character_creation_if_complete()

    def _finish_character_creation_if_complete(self) -> None:
        if (
            self._character_creation_outcome is None
            or not self._character_creation_thread_finished
        ):
            return
        outcome = self._character_creation_outcome
        thread = self._character_creation_thread
        self._character_creation_thread = None
        self._character_creation_worker = None
        self._character_creation_request = None
        self._character_creation_outcome = None
        self._character_creation_thread_finished = False
        if thread is not None:
            thread.deleteLater()

        if self._close_in_progress:
            self._character_creation_restart_game = False
            self._character_creation_restart_market = False
            self._character_creation_restart_mode = None
        else:
            # Begin restoring the previous runtime state before showing a modal
            # result dialog. QMessageBox runs a nested event loop, so readiness
            # signals can continue to arrive while the user reads the result.
            self._restart_services_after_character_creation()

        dialog = self._new_character_dialog
        if isinstance(outcome, CharacterCreationResult):
            source_id = outcome.request.overview_source_character_id
            overview_state_error = ""
            if source_id is not None:
                try:
                    add_pending_overview_import(outcome.character_id, source_id)
                except Exception as exc:
                    log.exception("Unable to persist pending overview import")
                    overview_state_error = str(exc) or type(exc).__name__
            self._keep_created_character_visible(outcome.request.character_name)
            self._refresh_characters()
            if dialog is not None:
                dialog.set_busy(False)
                dialog.accept()
            if not self._close_in_progress:
                overview_note = ""
                if source_id is not None:
                    if source_id in self._snapshot_ready_ids():
                        overview_note = (
                            " Its copied overview will be applied on first launcher login."
                        )
                    else:
                        source_name = next(
                            (
                                character.name
                                for account in self._accounts
                                for character in account.characters
                                if character.char_id == source_id
                            ),
                            "the selected source character",
                        )
                        overview_note = (
                            f" Next, launch '{source_name}' once through the launcher "
                            "to capture its overview. Then launch the new character to "
                            "apply the queued copy."
                        )
                created_message = (
                    f"Created account '{outcome.request.username}' and character "
                    f"'{outcome.request.character_name}'.{overview_note}"
                )
                if overview_state_error:
                    QMessageBox.warning(
                        self,
                        "Character Created — Overview Not Queued",
                        f"{created_message}\n\nThe pending overview import could not "
                        f"be saved: {overview_state_error}",
                    )
                else:
                    QMessageBox.information(
                        self,
                        "Character Created",
                        created_message,
                    )
        else:
            if dialog is not None:
                dialog.set_busy(False)
                dialog.show_error(outcome.message)

        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _restart_services_after_character_creation(self) -> None:
        restart_game = self._character_creation_restart_game
        restart_market = self._character_creation_restart_market
        restart_mode = self._character_creation_restart_mode
        self._character_creation_restart_game = False
        self._character_creation_restart_market = False
        self._character_creation_restart_mode = None
        if not restart_game and not restart_market:
            return
        if not self._start_service_sequence(
            start_market=restart_market,
            start_game=restart_game,
            mode=restart_mode,
            on_ready=None,
            error_title="Service Restore Failed",
        ):
            QMessageBox.warning(
                self,
                "Service Restore",
                "Character creation finished, but the prior EveJS services could "
                "not be restarted automatically.",
            )

    def _on_delete_character_requested(
        self,
        username: str,
        character_name: str,
        character_id: int,
    ) -> None:
        self._request_character_deletion(
            username,
            character_name,
            character_id,
            CharacterDeletionScope.CHARACTER,
        )

    def _on_delete_account_requested(
        self,
        username: str,
        character_name: str,
        character_id: int,
    ) -> None:
        self._request_character_deletion(
            username,
            character_name,
            character_id,
            CharacterDeletionScope.ACCOUNT,
        )

    def _request_character_deletion(
        self,
        username: str,
        character_name: str,
        character_id: int,
        scope: CharacterDeletionScope,
    ) -> None:
        """Confirm and coordinate one exact offline Native deletion."""
        if self._docker_mode():
            QMessageBox.information(
                self,
                "Delete Character or Account",
                "Launcher-managed deletion is currently available for Native "
                "EveJS installations only.",
            )
            return
        if any(
            (
                self._character_creation_thread is not None,
                self._character_deletion_thread is not None,
                self._overview_patch_thread is not None,
                self._client_launch_thread is not None,
                self._launch_queue is not None,
                self._lifecycle_active(),
            )
        ):
            QMessageBox.information(
                self,
                "Launcher Busy",
                "Wait for the current launcher operation to finish.",
            )
            return
        if self._tracker.running_count or is_eve_client_running():
            QMessageBox.warning(
                self,
                "EVE Clients Running",
                "Close every EVE client before deleting a character or account.",
            )
            return

        account = next(
            (candidate for candidate in self._accounts if candidate.username == username),
            None,
        )
        character = next(
            (
                candidate
                for candidate in (account.characters if account is not None else [])
                if candidate.char_id == character_id
                and candidate.name == character_name
            ),
            None,
        )
        if account is None or character is None or account.account_id <= 0:
            QMessageBox.warning(
                self,
                "Selection Changed",
                "The selected account or character changed. Refresh and try again.",
            )
            self._refresh_characters()
            return
        if (
            scope is CharacterDeletionScope.CHARACTER
            and len(account.characters) <= 1
        ):
            QMessageBox.information(
                self,
                "Delete Account Instead",
                f"'{character_name}' is the only character on '{username}'.\n\n"
                "Use Delete Account so the empty account does not become inaccessible.",
            )
            return

        game_active = is_server_running(port=int(Ports.GAME_TCP))
        market_active = self._is_market_running()
        game_owned = self._server_process_alive()
        market_owned = (
            self._market_proc is not None and self._market_proc.poll() is None
        )
        if game_active and not game_owned:
            QMessageBox.warning(
                self,
                "External Game Server",
                "The game server was started outside this launcher. Stop it from "
                "its original console before deleting data.",
            )
            return
        if market_active and not market_owned:
            QMessageBox.warning(
                self,
                "External Market Server",
                "The market server was started outside this launcher. Stop it "
                "before deleting data.",
            )
            return

        restart_mode: str | None = None
        if game_owned:
            resolved = self._resolve_server_start()
            if resolved is None:
                return
            restart_mode = resolved[0]

        if scope is CharacterDeletionScope.ACCOUNT:
            names = ", ".join(candidate.name for candidate in account.characters)
            subject = f"account '{username}' and {len(account.characters)} character(s)"
            detail = f"Characters: {names}"
            confirmation_target = username
        else:
            subject = f"character '{character_name}'"
            detail = f"Account '{username}' will be retained."
            confirmation_target = character_name
        service_note = (
            "\n\nLauncher-owned EveJS services will be stopped and restored."
            if game_owned or market_owned
            else ""
        )
        reply = QMessageBox.question(
            self,
            "Confirm EveJS Deletion",
            f"Delete {subject}?\n\n{detail}\n\n"
            "EveJS will run its native character cleanup. The launcher will keep "
            "a recoverable backup of every affected table and portrait. Account "
            f"profile/settings folders are preserved.{service_note}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        expected = f"DELETE {confirmation_target}"
        typed, accepted = QInputDialog.getText(
            self,
            "Type to Confirm",
            f"Type exactly:\n{expected}",
        )
        if not accepted:
            return
        if typed != expected:
            QMessageBox.warning(
                self,
                "Deletion Cancelled",
                "The confirmation text did not match. No data was changed.",
            )
            return

        request = CharacterDeletionRequest(
            evejs_root=str(self._cfg.get("evejs_root", "")),
            username=username,
            account_id=account.account_id,
            character_id=character_id,
            character_name=character_name,
            scope=scope,
        )
        self._character_deletion_request = request
        self._character_deletion_restart_game = game_owned
        self._character_deletion_restart_market = market_owned
        self._character_deletion_restart_mode = restart_mode
        progress = QProgressDialog(self)
        progress.setWindowTitle("EveJS Deletion")
        progress.setLabelText("Preparing a recoverable backup...")
        progress.setRange(0, 0)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.show()
        self._character_deletion_progress = progress
        if not self._run_stop_sequence(
            stop_game=game_owned,
            stop_market=market_owned,
            on_complete=self._begin_character_deletion_worker,
        ):
            self._character_deletion_request = None
            self._character_deletion_restart_game = False
            self._character_deletion_restart_market = False
            self._character_deletion_restart_mode = None
            self._character_deletion_progress = None
            progress.close()
            progress.deleteLater()
            QMessageBox.warning(
                self,
                "Deletion Cancelled",
                "The EveJS services could not be prepared safely. No database "
                "changes were made.",
            )

    def _begin_character_deletion_worker(self) -> None:
        request = self._character_deletion_request
        if request is None:
            return
        if is_server_running(port=int(Ports.GAME_TCP)) or is_server_running(
            port=int(Ports.MARKET_RPC)
        ):
            progress = self._character_deletion_progress
            self._character_deletion_progress = None
            if progress is not None:
                progress.close()
                progress.deleteLater()
            self._character_deletion_request = None
            self._character_deletion_restart_game = False
            self._character_deletion_restart_market = False
            self._character_deletion_restart_mode = None
            QMessageBox.warning(
                self,
                "Deletion Cancelled",
                "An EveJS service is still running; no database changes were made.",
            )
            return
        progress = self._character_deletion_progress
        if progress is not None:
            progress.setLabelText("Backing up, deleting, and verifying...")

        worker_factory = getattr(self, "_character_deletion_worker_factory", None)
        worker = (
            worker_factory(request)
            if callable(worker_factory)
            else CharacterDeletionWorker(request)
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_character_deletion_completed)
        worker.failed.connect(self._on_character_deletion_failed)
        worker.cleanup.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_character_deletion_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._character_deletion_thread = thread
        self._character_deletion_worker = worker
        self._character_deletion_outcome = None
        self._character_deletion_thread_finished = False
        thread.start()

    @pyqtSlot(object)
    def _on_character_deletion_completed(self, result: CharacterDeletionResult) -> None:
        self._character_deletion_outcome = result
        self._finish_character_deletion_if_complete()

    @pyqtSlot(object)
    def _on_character_deletion_failed(self, failure: CharacterDeletionFailure) -> None:
        self._character_deletion_outcome = failure
        self._finish_character_deletion_if_complete()

    @pyqtSlot()
    def _on_character_deletion_thread_finished(self) -> None:
        if self.sender() is not self._character_deletion_thread:
            return
        self._character_deletion_thread_finished = True
        self._finish_character_deletion_if_complete()

    def _finish_character_deletion_if_complete(self) -> None:
        if (
            self._character_deletion_outcome is None
            or not self._character_deletion_thread_finished
        ):
            return
        outcome = self._character_deletion_outcome
        thread = self._character_deletion_thread
        self._character_deletion_thread = None
        self._character_deletion_worker = None
        self._character_deletion_request = None
        self._character_deletion_outcome = None
        self._character_deletion_thread_finished = False
        if thread is not None:
            thread.deleteLater()
        progress = self._character_deletion_progress
        self._character_deletion_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

        if self._close_in_progress:
            self._character_deletion_restart_game = False
            self._character_deletion_restart_market = False
            self._character_deletion_restart_mode = None
        else:
            self._restart_services_after_character_deletion()

        if isinstance(outcome, CharacterDeletionResult):
            try:
                remove_characters_from_overview_state(
                    list(outcome.deleted_character_ids)
                )
            except Exception:
                log.exception("Unable to prune overview state after deletion")
            group_cleanup_error = False
            target_identity = getattr(self, "_group_target_identity", None)
            if target_identity:
                current_groups = getattr(self, "_group_state", TargetGroupState())
                updated_groups = prune_deleted_characters(
                    current_groups,
                    outcome.deleted_character_ids,
                )
                if updated_groups != current_groups:
                    try:
                        save_target_groups(target_identity, updated_groups)
                        self._group_state = updated_groups
                    except (OSError, GroupValidationError):
                        group_cleanup_error = True
                        log.exception(
                            "Unable to prune character groups after deletion"
                        )
            deleted_names = set(outcome.deleted_character_names)
            hidden = [
                name
                for name in self._cfg.get("hidden_characters", [])
                if name not in deleted_names
            ]
            never = [
                name
                for name in self._cfg.get("never_hide_characters", [])
                if name not in deleted_names
            ]
            if (
                hidden != self._cfg.get("hidden_characters", [])
                or never != self._cfg.get("never_hide_characters", [])
            ):
                self._cfg["hidden_characters"] = hidden
                self._cfg["never_hide_characters"] = never
                config.save(self._cfg)
            PortraitCache.clear()
            self._refresh_characters()
            if not self._close_in_progress:
                if outcome.account_deleted:
                    summary = (
                        f"Deleted account '{outcome.request.username}' and "
                        f"{len(outcome.deleted_character_ids)} character(s)."
                    )
                else:
                    summary = (
                        f"Deleted character '{outcome.request.character_name}'. "
                        f"Account '{outcome.request.username}' was retained."
                    )
                QMessageBox.information(
                    self,
                    "Deletion Complete",
                    f"{summary}\n\nAccount profile/settings folders were preserved. "
                    f"A recovery backup is retained at:\n{outcome.backup_path}"
                    + (
                        "\n\nOne or more group memberships could not be saved; "
                        "open Manage Groups to remove missing entries."
                        if group_cleanup_error
                        else ""
                    ),
                )
        elif not self._close_in_progress:
            QMessageBox.critical(
                self,
                "Deletion Failed",
                f"{outcome.message}\n\nThe launcher attempted automatic rollback; "
                "no unverified deletion was accepted.",
            )

        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _restart_services_after_character_deletion(self) -> None:
        restart_game = self._character_deletion_restart_game
        restart_market = self._character_deletion_restart_market
        restart_mode = self._character_deletion_restart_mode
        self._character_deletion_restart_game = False
        self._character_deletion_restart_market = False
        self._character_deletion_restart_mode = None
        if not restart_game and not restart_market:
            return
        if not self._start_service_sequence(
            start_market=restart_market,
            start_game=restart_game,
            mode=restart_mode,
            on_ready=None,
            error_title="Service Restore Failed",
        ):
            QMessageBox.warning(
                self,
                "Service Restore",
                "Deletion finished, but the prior EveJS services could not be "
                "restarted automatically.",
            )

    def _make_client_launch_request(
        self,
        username: str,
        character_name: str,
        character_id: int | None = None,
        *,
        show_errors: bool = False,
        launch_context: ClientLaunchContext | None = None,
    ) -> ClientLaunchRequest | None:
        """Validate current GUI state and capture immutable launch inputs."""
        current_context, context_error = self._resolve_client_launch_context()
        if current_context is None or (
            launch_context is not None and launch_context != current_context
        ):
            if current_context is not None:
                context_error = (
                    "Docker endpoint context is not current for the selected target. "
                    "Wait for Docker status to refresh, then try again."
                )
            if show_errors:
                if self._docker_mode():
                    self._docker_unavailable(context_error)
                else:
                    QMessageBox.warning(self, "Invalid Configuration", context_error)
            return None
        launch_context = current_context
        evejs_root = str(self._cfg.get("evejs_root", ""))
        client_path = str(self._cfg.get("client_path", ""))
        if not evejs_root or not client_path:
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Not Configured",
                    "EveJS root or client path not set.",
                )
            return None
        resolved_client_path = self._resolve_configured_client_path(
            client_path,
            evejs_root,
        )
        if resolved_client_path is None:
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Invalid EVE Client Path",
                    "Select the copied EVE client tq folder in Settings. It must "
                    "contain start.ini and bin64\\exefile.exe.",
                )
            return None
        client_path = str(resolved_client_path)
        if self._tracker.is_account_running(username):
            return None
        if username in getattr(self, "_pending_client_launches", set()):
            return None

        overview_bridge = None
        if character_id is not None and not self._docker_mode():
            source_id = pending_overview_source(character_id)
            patch_status = inspect_overview_patch(client_path)
            if patch_status.state is OverviewPatchState.PATCHED:
                try:
                    overview_bridge = prepare_overview_launch(character_id)
                except OverviewSnapshotRequired as exc:
                    source_id = exc.source_character_id or source_id
                    source_name = next(
                        (
                            character.name
                            for account in self._accounts
                            for character in account.characters
                            if character.char_id == source_id
                        ),
                        "the selected source character",
                    )
                    message = (
                        f"Launch {source_name} once through the launcher first. "
                        "Its overview snapshot has not been captured yet."
                    )
                    log.warning("Blocked pending overview import: %s", message)
                    if show_errors:
                        QMessageBox.information(self, "Overview Copy", message)
                    return None
            elif source_id is not None:
                message = (
                    "This character has a pending overview import. Install the "
                    "overview client patch before its first login."
                )
                if show_errors:
                    QMessageBox.information(self, "Overview Copy", message)
                return None

        return ClientLaunchRequest(
            username=username,
            character_name=character_name,
            evejs_root=evejs_root,
            client_path=client_path,
            profiles_root=Path(PROFILES_ROOT),
            launch_context=launch_context,
            character_id=character_id,
            auto_login_enabled=(
                not self._docker_mode()
                and bool(self._cfg.get("auto_login_enabled", False))
            ),
            overview_bridge=overview_bridge,
        )

    @staticmethod
    def _resolve_configured_client_path(
        client_path: str,
        evejs_root: str,
    ) -> Path | None:
        """Testable seam for the canonical copied-client path contract."""
        return resolve_client_tq_path(client_path, evejs_root)

    def _finalize_client_launch(
        self,
        result: ClientLaunchResult,
    ) -> None:
        """Track a successfully created process and start its window restorer."""
        request = result.request
        self._tracker.add(
            request.username,
            request.character_name,
            result.process,
        )
        log.info(
            "Launched client for %s as %s (pid=%s auto_login=%s character_id=%s)",
            request.username,
            request.character_name,
            result.process.pid,
            request.auto_login_enabled,
            request.character_id,
        )
        threading.Thread(
            target=_restore_eve_window,
            args=("EVE",),
            daemon=True,
        ).start()

    def _launch_account(
        self,
        username: str,
        character_name: str,
        character_id: int | None = None,
        *,
        show_errors: bool = False,
        launch_context: ClientLaunchContext | None = None,
    ) -> bool:
        """Synchronous compatibility seam; production UI uses the worker path."""
        request = self._make_client_launch_request(
            username,
            character_name,
            character_id,
            show_errors=show_errors,
            launch_context=launch_context,
        )
        if request is None:
            return False
        try:
            process = _perform_client_launch(request)
        except Exception as exc:  # noqa: BLE001 - subprocess errors vary by OS
            log.exception("Launch failed for %s", username)
            if show_errors:
                QMessageBox.critical(self, "Launch Error", str(exc))
            return False
        self._finalize_client_launch(ClientLaunchResult(request, process))
        return True

    def _set_client_launch_pending(
        self,
        request: ClientLaunchRequest,
        pending: bool,
    ) -> None:
        pending_accounts = getattr(self, "_pending_client_launches", None)
        if pending_accounts is None:
            pending_accounts = set()
            self._pending_client_launches = pending_accounts
        if pending:
            pending_accounts.add(request.username)
        else:
            pending_accounts.discard(request.username)
        page = getattr(self, "_characters_page", None)
        setter = getattr(page, "set_account_launching", None)
        if callable(setter):
            setter(request.username, request.character_name, pending)

    def _start_client_launch(
        self,
        username: str,
        character_name: str,
        character_id: int | None = None,
        *,
        show_errors: bool = False,
        launch_context: ClientLaunchContext | None = None,
        from_queue: bool = False,
    ) -> bool:
        """Start one non-blocking profile preparation and client spawn."""
        if getattr(self, "_client_launch_thread", None) is not None:
            log.info(
                "Ignored duplicate client launch while another launch is active (%s)",
                username,
            )
            return False
        request = self._make_client_launch_request(
            username,
            character_name,
            character_id,
            show_errors=show_errors,
            launch_context=launch_context,
        )
        if request is None:
            return False

        worker_factory = getattr(self, "_client_launch_worker_factory", None)
        worker = (
            worker_factory(request)
            if callable(worker_factory)
            else ClientLaunchWorker(request, _perform_client_launch)
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_client_launch_completed)
        worker.failed.connect(self._on_client_launch_failed)
        worker.cleanup.connect(
            worker.deleteLater,
            Qt.ConnectionType.DirectConnection,
        )
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_client_launch_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )

        self._client_launch_thread = thread
        self._client_launch_worker = worker
        self._client_launch_request = request
        self._client_launch_show_errors = show_errors
        self._client_launch_from_queue = from_queue
        self._client_launch_result_received = False
        self._client_launch_thread_finished = False
        self._client_launch_succeeded = False
        self._set_client_launch_pending(request, True)
        log.info(
            "Queued client launch for %s as %s",
            request.username,
            request.character_name,
        )
        if not from_queue:
            self._announce_shipboard(
                VoiceEvent.CHARACTER_LAUNCHING,
                character_name=request.character_name,
            )
        thread.start()
        return True

    @pyqtSlot(object)
    def _on_client_launch_completed(self, result: ClientLaunchResult) -> None:
        request = self._client_launch_request
        if (
            request is None
            or result.request != request
            or self._client_launch_result_received
        ):
            return
        self._client_launch_result_received = True
        self._client_launch_succeeded = True
        self._set_client_launch_pending(request, False)
        self._finalize_client_launch(result)
        self._refresh_character_views()
        self._update_status_bar()
        self._finish_client_launch_if_complete()

    @pyqtSlot(object)
    def _on_client_launch_failed(self, failure: ClientLaunchFailure) -> None:
        request = self._client_launch_request
        if (
            request is None
            or failure.request != request
            or self._client_launch_result_received
        ):
            return
        self._client_launch_result_received = True
        self._client_launch_succeeded = False
        self._set_client_launch_pending(request, False)
        log.error(
            "Client launch failed for %s (%s): %s",
            request.username,
            failure.error_type,
            failure.message,
        )
        if not self._client_launch_from_queue:
            self._announce_shipboard(
                VoiceEvent.CHARACTER_LAUNCH_FAILED,
                character_name=request.character_name,
            )
        if self._client_launch_show_errors and not self._close_in_progress:
            QMessageBox.critical(self, "Launch Error", failure.message)
        self._refresh_character_views()
        self._update_status_bar()
        self._finish_client_launch_if_complete()

    @pyqtSlot()
    def _on_client_launch_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self._client_launch_thread:
            return
        self._client_launch_thread_finished = True
        self._finish_client_launch_if_complete()

    def _finish_client_launch_if_complete(self) -> None:
        """Release worker ownership after both result delivery and teardown."""
        if not (
            self._client_launch_result_received
            and self._client_launch_thread_finished
        ):
            return
        thread = self._client_launch_thread
        from_queue = self._client_launch_from_queue
        succeeded = self._client_launch_succeeded

        self._client_launch_thread = None
        self._client_launch_worker = None
        self._client_launch_request = None
        self._client_launch_show_errors = False
        self._client_launch_from_queue = False
        self._client_launch_result_received = False
        self._client_launch_thread_finished = False
        self._client_launch_succeeded = False
        if isinstance(thread, QThread):
            thread.deleteLater()

        queue = getattr(self, "_launch_queue", None)
        if from_queue and queue is not None:
            queue.item_finished(succeeded)
        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _on_character_launch(
        self,
        username: str,
        character_name: str,
        character_id: int | None = None,
    ) -> None:
        if username in getattr(self, "_pending_client_launches", set()):
            log.info("Ignored duplicate client launch click for %s", username)
            return
        if (
            getattr(self, "_launch_queue", None) is not None
            or getattr(self, "_client_launch_thread", None) is not None
        ):
            QMessageBox.information(
                self,
                "Launch In Progress",
                "Another client is currently being prepared. Please wait.",
            )
            return
        if self._tracker.is_account_running(username):
            running_character = self._tracker.get_running_character(username)
            QMessageBox.warning(
                self,
                "Account Already Running",
                f"Account '{username}' is already running character '{running_character}'.",
            )
            return
        if not self._ensure_server_if_needed(
            lambda: self._launch_character_after_services(
                username,
                character_name,
                character_id,
            )
        ):
            return

    def _launch_character_after_services(
        self,
        username: str,
        character_name: str,
        character_id: int | None = None,
    ) -> None:
        """Launch one client only after its configured service gate is ready."""
        if character_id is None:
            self._start_client_launch(username, character_name, show_errors=True)
            return
        self._start_client_launch(
            username,
            character_name,
            character_id,
            show_errors=True,
        )

    def _sync_character_groups(self, target_identity: str) -> None:
        """Load groups once when the authoritative runtime target changes."""
        # Some attribution tests intentionally construct an uninitialized Qt
        # wrapper; consult the instance dictionary so SIP does not route a
        # missing attribute through QObject's uninitialized fallback.
        if target_identity == self.__dict__.get("_group_target_identity"):
            return
        try:
            state = load_target_groups(target_identity)
        except (OSError, GroupValidationError):
            log.exception("Unable to load character groups")
            state = TargetGroupState()
        self._group_target_identity = target_identity
        self._group_state = state

    def _on_group_selection_changed(self, group_id: object = None) -> None:
        """Persist one shared Home/Characters launch-target selection."""
        target_identity = getattr(self, "_group_target_identity", None)
        if not target_identity:
            return
        normalized_id = group_id if isinstance(group_id, str) else None
        current = getattr(self, "_group_state", TargetGroupState())
        updated = select_group(current, normalized_id)
        if updated == current:
            return
        try:
            save_target_groups(target_identity, updated)
        except (OSError, GroupValidationError) as exc:
            log.exception("Unable to save selected character group")
            QMessageBox.warning(
                self,
                "Groups Not Saved",
                f"The selected group could not be saved: {exc}",
            )
            self._refresh_character_views()
            return
        self._group_state = updated
        self._refresh_character_views()

    def _show_group_manager(self, focus_character_id: object = None) -> None:
        """Open one atomic editor for the current runtime target's groups."""
        target_identity = getattr(self, "_group_target_identity", None)
        if not target_identity or getattr(self, "_data_selection", None) is None:
            QMessageBox.information(
                self,
                "Character Groups",
                "Wait for the current EveJS character data to finish loading.",
            )
            return
        if (
            getattr(self, "_launch_queue", None) is not None
            or getattr(self, "_client_launch_thread", None) is not None
        ):
            QMessageBox.information(
                self,
                "Launch In Progress",
                "Wait for the current character launch queue to finish before "
                "editing groups.",
            )
            return
        character_id = (
            focus_character_id
            if isinstance(focus_character_id, int)
            and not isinstance(focus_character_id, bool)
            and focus_character_id > 0
            else None
        )
        try:
            relink_candidates = find_relink_candidates(
                target_identity,
                self._accounts,
            )
        except (OSError, GroupValidationError):
            log.exception("Unable to inspect previous character groups")
            relink_candidates = ()
        dialog = CharacterGroupsDialog(
            self._accounts,
            self._effective_hidden_characters(),
            getattr(self, "_group_state", TargetGroupState()),
            focus_character_id=character_id,
            relink_candidates=relink_candidates,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        current_selection = getattr(self, "_data_selection", None)
        if (
            current_selection is None
            or current_selection.target_identity != target_identity
        ):
            QMessageBox.warning(
                self,
                "Data Source Changed",
                "The active EveJS data source changed while groups were open. "
                "Reopen Manage Groups and try again.",
            )
            return
        updated = dialog.group_state
        try:
            save_target_groups(target_identity, updated)
        except (OSError, GroupValidationError) as exc:
            log.exception("Unable to save character groups")
            QMessageBox.critical(
                self,
                "Groups Not Saved",
                f"Character groups could not be saved: {exc}",
            )
            return
        self._group_state = updated
        self._refresh_character_views()

    def _batch_launch_rows(
        self,
        hidden: set[str] | None = None,
    ) -> tuple[list[tuple[Account, Character]], str | None]:
        """Resolve All Visible or one exact group through shared visibility."""
        if hidden is None:
            hidden = self._effective_hidden_characters()
        visible_rows = visible_character_rows(self._accounts, hidden)
        group = getattr(self, "_group_state", TargetGroupState()).selected_group
        if group is None:
            rows: list[tuple[Account, Character]] = []
            seen_accounts: set[str] = set()
            for account, character in visible_rows:
                if account.username not in seen_accounts:
                    rows.append((account, character))
                    seen_accounts.add(account.username)
            return rows, None

        resolution = resolve_group(group, self._accounts)
        if resolution.conflicting_account_ids:
            return [], (
                f"'{group.name}' contains more than one character from the "
                "same account. Open Manage Groups and keep one per account."
            )
        visible_keys = {
            (account.account_id, character.char_id)
            for account, character in visible_rows
        }
        return (
            [
                (account, character)
                for account, character in resolution.rows
                if (account.account_id, character.char_id) in visible_keys
            ],
            None,
        )

    def _launch_all(self) -> None:
        """Queue the selected group (or All Visible) serially."""
        if (
            getattr(self, "_launch_queue", None) is not None
            or getattr(self, "_client_launch_thread", None) is not None
        ):
            return
        evejs_root = self._cfg.get("evejs_root", "")
        client_path = self._cfg.get("client_path", "")
        if not evejs_root or not client_path:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS first.")
            return

        hidden = self._effective_hidden_characters()
        launch_rows, group_error = self._batch_launch_rows(hidden)
        if group_error:
            QMessageBox.warning(self, "Group Needs Attention", group_error)
            self._refresh_character_views()
            return
        candidates: list[tuple[Account, Character]] = []
        for account, character in launch_rows:
            if (
                not self._tracker.is_account_running(account.username)
                and account.username
                not in getattr(self, "_pending_client_launches", set())
            ):
                candidates.append((account, character))
        if not candidates:
            self._refresh_character_views()
            return

        selected_group = getattr(
            self,
            "_group_state",
            TargetGroupState(),
        ).selected_group
        group_name = selected_group.name if selected_group is not None else None
        skipped_running = max(0, len(launch_rows) - len(candidates))
        skipped_unavailable = (
            max(0, len(selected_group.members) - len(launch_rows))
            if selected_group is not None
            else 0
        )
        self._ensure_server_if_needed(
            lambda: self._begin_client_launch_queue(
                candidates,
                group_name=group_name,
                skipped_running=skipped_running,
                skipped_unavailable=skipped_unavailable,
            )
        )

    def _begin_client_launch_queue(
        self,
        candidates: list[tuple[Account, Character]],
        *,
        group_name: str | None = None,
        skipped_running: int = 0,
        skipped_unavailable: int = 0,
    ) -> None:
        """Create the serial Qt launch queue after required services are ready."""
        if self._launch_queue is not None:
            return
        launch_context, context_error = self._resolve_client_launch_context()
        if launch_context is None:
            if self._docker_mode():
                self._docker_unavailable(context_error)
            else:
                QMessageBox.warning(self, "Invalid Configuration", context_error)
            return
        stagger_seconds = max(0, int(self._cfg.get("stagger_delay_sec", 3)))
        queue = AsyncClientLaunchQueue(
            candidates,
            lambda candidate: self._start_client_launch(
                candidate[0].username,
                candidate[1].name,
                character_id=candidate[1].char_id,
                launch_context=launch_context,
                from_queue=True,
            ),
            stagger_ms=stagger_seconds * 1_000,
            parent=self,
        )
        self._launch_queue = queue
        self._launch_queue_group_name = group_name
        self._launch_queue_skipped_running = max(0, int(skipped_running))
        self._launch_queue_skipped_unavailable = max(
            0,
            int(skipped_unavailable),
        )
        queue.progress.connect(self._on_launch_queue_progress)
        queue.finished.connect(self._on_launch_queue_finished)
        self._home_page.set_launch_progress(
            0,
            len(candidates),
            0,
            group_name,
        )
        self._characters_page.set_group_launch_progress(
            0,
            len(candidates),
            0,
            group_name,
        )
        self._announce_shipboard(
            VoiceEvent.GROUP_LAUNCHING,
            group_name=group_name or "",
        )
        queue.start()

    def _cancel_launch_queue(self) -> None:
        """Cancel future queued launches without terminating started clients."""
        queue = getattr(self, "_launch_queue", None)
        if queue is not None:
            queue.cancel()

    def _on_launch_queue_progress(
        self,
        attempted: int,
        total: int,
        succeeded: int,
    ) -> None:
        group_name = getattr(self, "_launch_queue_group_name", None)
        self._home_page.set_launch_progress(
            attempted,
            total,
            succeeded,
            group_name,
        )
        self._characters_page.set_group_launch_progress(
            attempted,
            total,
            succeeded,
            group_name,
        )
        self._refresh_character_views()
        self._update_status_bar()

    def _on_launch_queue_finished(
        self,
        attempted: int,
        succeeded: int,
        cancelled: bool,
    ) -> None:
        if getattr(self, "_launch_queue", None) is None:
            return
        skipped_running = getattr(self, "_launch_queue_skipped_running", 0)
        skipped_unavailable = getattr(
            self,
            "_launch_queue_skipped_unavailable",
            0,
        )
        group_name = getattr(self, "_launch_queue_group_name", None)
        self._launch_queue = None
        self._home_page.finish_launch_progress(attempted, succeeded, cancelled)
        self._characters_page.finish_group_launch_progress()
        self._launch_queue_group_name = None
        self._launch_queue_skipped_running = 0
        self._launch_queue_skipped_unavailable = 0
        self._refresh_character_views()
        self._update_status_bar()
        if self._close_in_progress:
            return
        self._announce_shipboard(
            VoiceEvent.LAUNCH_SEQUENCE_COMPLETE,
            group_name=group_name or "",
            launched_count=succeeded,
            failed_count=max(0, attempted - succeeded),
            cancelled=cancelled,
        )
        if cancelled:
            message = (
                f"Launched {succeeded} client(s); remaining queued launches were cancelled."
            )
            if skipped_running:
                message += f" Skipped {skipped_running} already-running account(s)."
            if skipped_unavailable:
                message += (
                    f" Skipped {skipped_unavailable} hidden, banned, missing, "
                    "or otherwise unavailable character(s)."
                )
            QMessageBox.information(self, "Launch Cancelled", message)
        else:
            failures = max(0, attempted - succeeded)
            details: list[str] = []
            if skipped_running:
                details.append(f"{skipped_running} already running")
            if skipped_unavailable:
                details.append(
                    f"{skipped_unavailable} hidden, banned, missing, or unavailable"
                )
            if failures:
                details.append(f"{failures} failed")
            suffix = f" Skipped: {', '.join(details)}." if details else ""
            QMessageBox.information(
                self,
                "Launch Complete",
                f"Launched {succeeded} client(s).{suffix}",
            )

    def _kill_all_clients(self) -> None:
        if self._tracker.running_count > 0:
            self._announce_shipboard(VoiceEvent.CLIENTS_TERMINATING)
        count = self._tracker.kill_all()
        self._refresh_characters()
        self._update_status_bar()
        if count > 0:
            self._announce_shipboard(VoiceEvent.CLIENTS_TERMINATED)
            QMessageBox.information(self, "Killed", f"Terminated {count} client(s).")

    def _on_hide_character(self, character_name: str) -> None:
        """Add a character name to hidden_characters and refresh the grid.

        Also removes it from ``never_hide_characters`` (if present) — the
        user actively wants this character hidden, so the auto-hide exemption
        no longer applies.
        """
        hidden: list[str] = list(self._cfg.get("hidden_characters", []))
        if character_name not in hidden:
            hidden.append(character_name)
            self._cfg["hidden_characters"] = hidden
            config.save(self._cfg)
            log.info("Hid character '%s'", character_name)
        # Remove from never-hide list so auto-hide can re-claim it on restart
        never: list[str] = list(self._cfg.get("never_hide_characters", []))
        if character_name in never:
            never.remove(character_name)
            self._cfg["never_hide_characters"] = never
            config.save(self._cfg)
        self._refresh_characters()

    # ── Refresh + status ──────────────────────────────────────────────

    def _keep_created_character_visible(self, character_name: str) -> None:
        """Exempt a launcher-created character from automatic test/GM hiding."""
        hidden = list(self._cfg.get("hidden_characters", []))
        never_hide = list(self._cfg.get("never_hide_characters", []))
        updated_hidden = [name for name in hidden if name != character_name]
        changed = updated_hidden != hidden
        if character_name not in never_hide:
            never_hide.append(character_name)
            changed = True
        if not changed:
            return
        self._cfg["hidden_characters"] = updated_hidden
        self._cfg["never_hide_characters"] = never_hide
        config.save(self._cfg)

    def _effective_hidden_characters(self, *, persist: bool = False) -> set[str]:
        """Return the shared explicit + automatic hidden-character set."""
        configured = list(self._cfg.get("hidden_characters", []))
        hidden = set(configured)
        if self._cfg.get("hide_test_characters", True):
            never_hide = set(self._cfg.get("never_hide_characters", []))
            for account in self._accounts:
                username_lower = account.username.lower()
                if username_lower.startswith("test") or "gm" in username_lower:
                    hidden.update(
                        character.name
                        for character in account.characters
                        if character.name not in never_hide
                    )

        if persist:
            newly_hidden = [name for name in hidden if name not in configured]
            if newly_hidden:
                self._cfg["hidden_characters"] = configured + sorted(newly_hidden)
                config.save(self._cfg)
        return hidden

    def _refresh_characters(self) -> None:
        """Schedule one serialized account refresh outside the GUI thread."""
        if self._close_in_progress:
            return

        evejs_root = str(self._cfg.get("evejs_root", ""))
        if not evejs_root:
            self._account_request_token = None
            self._pending_account_request = None
            self._account_start_scheduled = False
            if self._account_worker is not None:
                self._account_worker.request_cancel()
            self._cancel_detail_load()
            self._data_selection = None
            self._clear_data_load_error()
            self._accounts = []
            self._group_target_identity = None
            self._group_state = TargetGroupState()
            self._refresh_character_views()
            return

        self._cancel_detail_load()
        token = self._new_data_token(
            target_identity=self._current_observed_docker_target_identity()
        )
        self._account_request_token = token
        self._pending_account_request = (
            token,
            self._make_data_selection_factory(),
        )
        if self._account_worker is not None:
            self._account_worker.request_cancel()
            return
        self._schedule_account_load()

    def _data_settings_identity(self) -> tuple[object, ...]:
        """Return the exact settings tuple that authorizes one data result."""
        return (
            self._cfg.get("runtime_backend"),
            self._cfg.get("evejs_root"),
            self._cfg.get("docker_compose_file"),
            self._cfg.get("docker_project_name"),
            self._cfg.get("docker_control_policy"),
        )

    def _new_data_token(
        self,
        *,
        target_identity: str | None = None,
        username: str | None = None,
        character_id: int | None = None,
    ) -> _DataRequestToken:
        self._data_request_sequence += 1
        return _DataRequestToken(
            self._data_request_sequence,
            self._settings_generation,
            self._data_settings_identity(),
            target_identity,
            username,
            character_id,
        )

    def _make_data_selection_factory(
        self,
    ) -> Callable[[], RuntimeDataSelection]:
        """Capture raw settings while deferring path/CLI work to the worker."""
        root = str(self._cfg.get("evejs_root", ""))
        if not self._docker_mode():
            accounts_loader = load_accounts
            detail_loader = get_character_detail
            return lambda: native_data_selection(
                root,
                accounts_loader=accounts_loader,
                detail_loader=detail_loader,
            )

        target_factory = self._docker_log_target_factory()
        policy = DockerControlPolicy(
            self._cfg.get("docker_control_policy", "connect_only")
        )
        settings_identity = self._docker_monitor_settings_identity()
        monitor_generation = getattr(self, "_monitor_generation", 0)
        return lambda: inspect_docker_data_source(
            target_factory(),
            control_policy=policy,
            settings_identity=settings_identity,
            monitor_generation=monitor_generation,
        )

    def _docker_data_selection_is_current(
        self,
        selection: RuntimeDataSelection,
    ) -> bool:
        """Reject Docker data attributed to another target selection generation."""
        if not self._docker_mode():
            return True
        observed_target = self._current_observed_docker_target_identity()
        return (
            observed_target is not None
            and selection.target_identity == observed_target
            and selection.settings_identity
            == self._docker_monitor_settings_identity()
            and selection.monitor_generation
            == getattr(self, "_monitor_generation", 0)
        )

    def _current_observed_docker_target_identity(self) -> str | None:
        """Return target authority only from the current attributed observation."""
        if not self._docker_mode():
            return None
        snapshot = getattr(self, "_runtime_snapshot", None)
        if (
            snapshot is None
            or snapshot.backend is not RuntimeBackend.DOCKER_COMPOSE
            or snapshot.settings_identity != self._docker_monitor_settings_identity()
            or snapshot.monitor_generation
            != getattr(self, "_monitor_generation", 0)
        ):
            return None
        return snapshot.target_identity

    def _schedule_account_load(self) -> None:
        if (
            self._account_start_scheduled
            or self._account_thread is not None
            or self._pending_account_request is None
            or self._close_in_progress
        ):
            return
        self._account_start_scheduled = True
        QTimer.singleShot(0, self._start_pending_account_load)

    @pyqtSlot()
    def _start_pending_account_load(self) -> None:
        if not self._account_start_scheduled:
            return
        self._account_start_scheduled = False
        if self._close_in_progress or self._account_thread is not None:
            return
        request = self._pending_account_request
        self._pending_account_request = None
        if request is None:
            return
        token, selection_factory = request
        if token is not self._account_request_token:
            return

        thread = QThread(self)
        worker = AccountLoader(selection_factory, token=token)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_account_load_completed)
        worker.failed.connect(self._on_account_load_failed)
        worker.cleanup.connect(
            worker.deleteLater,
            Qt.ConnectionType.DirectConnection,
        )
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_account_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._account_thread = thread
        self._account_worker = worker
        thread.start()

    def _data_token_is_current(self, token: object | None) -> bool:
        return (
            isinstance(token, _DataRequestToken)
            and token.settings_generation == self._settings_generation
            and token.settings_identity == self._data_settings_identity()
        )

    @pyqtSlot(object)
    def _on_account_load_completed(self, result: AccountLoadResult) -> None:
        token = result.token
        if (
            self._close_in_progress
            or token is not self._account_request_token
            or not self._data_token_is_current(token)
            or not self._docker_data_selection_is_current(result.selection)
        ):
            return
        if self._docker_mode() and (
            token.target_identity is None
            or result.selection.target_identity != token.target_identity
        ):
            return
        self._data_selection = result.selection
        self._clear_data_load_error()
        self._accounts = list(result.accounts)
        self._sync_character_groups(result.selection.target_identity)
        self._refresh_character_views()

    @pyqtSlot(object)
    def _on_account_load_failed(self, failure: DataLoadFailure) -> None:
        if (
            self._close_in_progress
            or failure.token is not self._account_request_token
            or not self._data_token_is_current(failure.token)
        ):
            return
        log.warning("Character account load failed (%s)", failure.code)
        self._data_selection = None
        # An empty roster caused by a read failure must not look like an empty
        # game store; keep the already-redacted reason for the Characters page.
        self._data_load_error = failure.message or "Character data could not be loaded."
        self._accounts = []
        self._refresh_character_views()

    @pyqtSlot()
    def _on_account_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self._account_thread:
            return
        self._account_worker = None
        self._account_thread = None
        if isinstance(thread, QThread):
            thread.deleteLater()
        if self._close_in_progress:
            self._resume_close_after_data()
        elif self._pending_account_request is not None:
            self._schedule_account_load()

    def _on_character_selected(
        self,
        username: str,
        _character_name: str,
        character_id: int,
    ) -> None:
        """Load selected-character detail through the current runtime source."""
        selection = self._data_selection
        if self._close_in_progress or selection is None:
            return
        token = self._new_data_token(
            target_identity=selection.target_identity,
            username=username,
            character_id=character_id,
        )
        self._detail_request_token = token
        self._pending_detail_request = (
            token,
            self._make_data_selection_factory(),
        )
        if self._detail_worker is not None:
            self._detail_worker.request_cancel()
            return
        self._schedule_detail_load()

    def _schedule_detail_load(self) -> None:
        if (
            self._detail_start_scheduled
            or self._detail_thread is not None
            or self._pending_detail_request is None
            or self._close_in_progress
        ):
            return
        self._detail_start_scheduled = True
        QTimer.singleShot(0, self._start_pending_detail_load)

    @pyqtSlot()
    def _start_pending_detail_load(self) -> None:
        if not self._detail_start_scheduled:
            return
        self._detail_start_scheduled = False
        if self._close_in_progress or self._detail_thread is not None:
            return
        request = self._pending_detail_request
        self._pending_detail_request = None
        if request is None:
            return
        token, selection_factory = request
        if token is not self._detail_request_token or token.character_id is None:
            return

        thread = QThread(self)
        worker = CharacterDetailLoader(
            selection_factory,
            token.character_id,
            token=token,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._on_character_detail_completed)
        worker.failed.connect(self._on_character_detail_failed)
        worker.cleanup.connect(
            worker.deleteLater,
            Qt.ConnectionType.DirectConnection,
        )
        worker.destroyed.connect(thread.quit)
        thread.finished.connect(
            self._on_detail_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._detail_thread = thread
        self._detail_worker = worker
        thread.start()

    @pyqtSlot(object)
    def _on_character_detail_completed(
        self,
        result: CharacterDetailResult,
    ) -> None:
        token = result.token
        selection = self._data_selection
        if (
            self._close_in_progress
            or token is not self._detail_request_token
            or not self._data_token_is_current(token)
            or selection is None
            or token.target_identity != selection.target_identity
            or result.selection.target_identity != selection.target_identity
            or not self._docker_data_selection_is_current(selection)
            or not self._docker_data_selection_is_current(result.selection)
            or result.selection.settings_identity != selection.settings_identity
            or result.selection.monitor_generation != selection.monitor_generation
            or token.character_id != result.character_id
            or token.username is None
            or result.detail is None
        ):
            return
        self._characters_page.apply_character_detail(
            token.username,
            result.character_id,
            result.detail,
        )

    @pyqtSlot(object)
    def _on_character_detail_failed(self, failure: DataLoadFailure) -> None:
        if (
            failure.token is self._detail_request_token
            and self._data_token_is_current(failure.token)
            and not self._close_in_progress
        ):
            log.warning("Character detail load failed (%s)", failure.code)

    @pyqtSlot()
    def _on_detail_thread_finished(self) -> None:
        thread = self.sender()
        if thread is not self._detail_thread:
            return
        self._detail_worker = None
        self._detail_thread = None
        if isinstance(thread, QThread):
            thread.deleteLater()
        if self._close_in_progress:
            self._resume_close_after_data()
        elif self._pending_detail_request is not None:
            self._schedule_detail_load()

    def _cancel_detail_load(self) -> None:
        self._detail_request_token = None
        self._pending_detail_request = None
        self._detail_start_scheduled = False
        if self._detail_worker is not None:
            self._detail_worker.request_cancel()

    def _cancel_data_loads(self) -> None:
        """Invalidate queued delivery and request non-blocking worker cancellation."""
        self._account_request_token = None
        self._pending_account_request = None
        self._account_start_scheduled = False
        if self._account_worker is not None:
            self._account_worker.request_cancel()
        self._cancel_detail_load()

    def _data_load_active(self) -> bool:
        return any(
            (
                self._account_thread is not None,
                self._detail_thread is not None,
                self._account_start_scheduled,
                self._detail_start_scheduled,
            )
        )

    def _resume_close_after_data(self) -> None:
        if self._close_in_progress and not self._background_data_active():
            QTimer.singleShot(0, self.close)

    def _refresh_character_creation_availability(
        self,
        snapshot: RuntimeSnapshot | None = None,
    ) -> None:
        """Expose creation only for Native or a current stable Managed target."""
        creation_setter = getattr(
            getattr(self, "_characters_page", None),
            "set_character_creation_available",
            None,
        )
        if not callable(creation_setter):
            return
        if not self._cfg.get("evejs_root", "") or not self._cfg.get(
            "client_path",
            "",
        ):
            creation_setter(
                False,
                "Configure the EveJS root and copied EVE client path first.",
            )
            return
        if not self._docker_mode():
            creation_setter(True)
            return
        if not self._docker_managed():
            creation_setter(
                False,
                "Managed Docker mode is required; Connect-only mode is read-only.",
            )
            return
        current = snapshot or getattr(self, "_runtime_snapshot", None)
        if (
            not isinstance(current, RuntimeSnapshot)
            or self._current_observed_docker_target_identity() is None
        ):
            creation_setter(
                False,
                "Wait for the selected Docker Compose target to be verified.",
            )
            return
        transitional = {
            ServiceState.STARTING,
            ServiceState.STOPPING,
            ServiceState.UNKNOWN,
        }
        if current.game in transitional or current.market in transitional:
            creation_setter(
                False,
                "Wait for Docker Game and Market status to become stable.",
            )
            return
        creation_setter(True)

    def _refresh_character_views(self) -> None:
        """Refresh cards and dashboard metrics without another database read."""
        evejs_root = self._cfg.get("evejs_root", "")

        hidden = self._effective_hidden_characters(persist=True)
        rows = visible_character_rows(self._accounts, hidden)
        group_state = getattr(self, "_group_state", TargetGroupState())

        for page in (
            getattr(self, "_home_page", None),
            getattr(self, "_characters_page", None),
        ):
            setter = getattr(page, "set_group_state", None)
            if callable(setter):
                setter(group_state)

        error_setter = getattr(self._characters_page, "set_data_error", None)
        if callable(error_setter):
            error_setter(self._data_load_error)

        try:
            self._characters_page.refresh(
                self._accounts,
                sorted(hidden),
                self._tracker,
                "" if self._docker_mode() else evejs_root,
                portrait_target=self._current_portrait_target(),
            )
        except Exception:
            log.exception("Characters page refresh failed")

        self._refresh_character_creation_availability()

        management_setter = getattr(
            self._characters_page,
            "set_group_management_available",
            None,
        )
        if callable(management_setter):
            group_data_ready = (
                self._data_selection is not None
                and self._group_target_identity
                == self._data_selection.target_identity
            )
            management_setter(
                group_data_ready,
                "Wait for current EveJS character data to finish loading.",
            )

        self._home_page.set_character_stats(
            visible_account_count(rows),
            len(rows),
        )
        launch_rows, group_error = self._batch_launch_rows(hidden)
        launch_usernames = {account.username for account, _character in launch_rows}
        eligible_usernames = {
            username
            for username in launch_usernames
            if (
                not self._tracker.is_account_running(username)
                and username
                not in getattr(self, "_pending_client_launches", set())
            )
        }
        selected_group = group_state.selected_group
        selected_name = selected_group.name if selected_group is not None else ""

        def apply_launch_availability(available: bool, reason: str = "") -> None:
            self._home_page.set_launch_available(
                available,
                reason,
                len(eligible_usernames),
            )
            group_setter = getattr(
                self._characters_page,
                "set_group_launch_available",
                None,
            )
            if callable(group_setter):
                group_setter(
                    available,
                    len(eligible_usernames),
                    reason,
                )

        if group_error:
            apply_launch_availability(False, group_error)
            return
        if self._docker_mode():
            launch_available, launch_reason = self._docker_launch_capability()
            if not launch_available:
                apply_launch_availability(False, launch_reason)
            elif not launch_usernames:
                apply_launch_availability(
                    False,
                    (
                        f"No visible, non-banned characters in {selected_name}"
                        if selected_group is not None and selected_group.members
                        else (
                            f"{selected_name} has no characters"
                            if selected_group is not None
                            else "No visible accounts available"
                        )
                    ),
                )
            elif not eligible_usernames:
                apply_launch_availability(
                    False,
                    (
                        f"All ready characters in {selected_name} are already running"
                        if selected_group is not None
                        else "All visible accounts are already running"
                    ),
                )
            else:
                apply_launch_availability(True)
        elif not evejs_root or not self._cfg.get("client_path", ""):
            apply_launch_availability(
                False,
                "Configure the EveJS root and EVE client path first",
            )
        elif not launch_usernames:
            apply_launch_availability(
                False,
                (
                    f"No visible, non-banned characters in {selected_name}"
                    if selected_group is not None and selected_group.members
                    else (
                        f"{selected_name} has no characters"
                        if selected_group is not None
                        else "No visible accounts available"
                    )
                ),
            )
        elif not eligible_usernames:
            apply_launch_availability(
                False,
                (
                    f"All ready characters in {selected_name} are already running"
                    if selected_group is not None
                    else "All visible accounts are already running"
                ),
            )
        else:
            apply_launch_availability(True)

    def _clear_data_load_error(self) -> None:
        """Retire a roster failure when its data authority is no longer current."""
        self._data_load_error = ""
        setter = getattr(
            self.__dict__.get("_characters_page"),
            "set_data_error",
            None,
        )
        if callable(setter):
            setter("")

    def _current_portrait_target(self) -> PortraitTarget | None:
        selection = self._data_selection
        if selection is None:
            return None
        if self._docker_mode():
            snapshot = getattr(self, "_runtime_snapshot", None)
            if (
                not self._docker_data_selection_is_current(selection)
                or snapshot is None
                or snapshot.backend is not RuntimeBackend.DOCKER_COMPOSE
                or snapshot.target_identity != selection.target_identity
                or snapshot.settings_identity != selection.settings_identity
                or snapshot.monitor_generation != selection.monitor_generation
                or snapshot.endpoints is None
                or snapshot.endpoints.image is None
            ):
                return None
            return PortraitTarget(
                target_identity=selection.target_identity,
                image_endpoint=snapshot.endpoints.image,
                settings_identity=selection.settings_identity,
                monitor_generation=selection.monitor_generation,
            )
        root = str(self._cfg.get("evejs_root", ""))
        if not root:
            return None
        return PortraitTarget(
            target_identity=selection.target_identity,
            native_root=Path(root),
        )

    def _background_data_active(self) -> bool:
        portrait_checker = getattr(
            getattr(self, "_characters_page", None),
            "portrait_loads_active",
            None,
        )
        portrait_active = bool(
            callable(portrait_checker) and portrait_checker()
        )
        return self._data_load_active() or portrait_active

    def _prune_and_update(self) -> None:
        if self._tracker.prune_dead() > 0:
            self._refresh_characters()
            self._update_status_bar()

    def _build_runtime_snapshot(
        self,
        game_reachable: bool | None = None,
        market_reachable: bool | None = None,
    ) -> RuntimeSnapshot:
        """Derive one authoritative snapshot from one reachability observation."""
        cached_game, cached_market = getattr(
            self,
            "_service_reachability",
            (False, False),
        )
        if game_reachable is None:
            game_reachable = cached_game
        if market_reachable is None:
            market_reachable = cached_market
        running_clients = self._tracker.running_count

        game_state, game_pid, game_error = derive_service_state(
            reachable=game_reachable,
            process=self._server_proc,
            intent=getattr(self, "_server_intent", None),
            last_error=getattr(self, "_server_error", None),
        )
        market_state, market_pid, market_error = derive_service_state(
            reachable=market_reachable,
            process=self._market_proc,
            intent=getattr(self, "_market_intent", None),
            last_error=getattr(self, "_market_error", None),
        )
        return RuntimeSnapshot(
            game=game_state,
            market=market_state,
            running_clients=running_clients,
            game_pid=game_pid,
            market_pid=market_pid,
            game_owned=game_pid is not None,
            market_owned=market_pid is not None,
            game_error=game_error,
            market_error=market_error,
        )

    @pyqtSlot(object)
    def _on_docker_observation(
        self, observation: DockerObservation, generation: int | None = None
    ) -> None:
        """Adapt read-only container state into the existing snapshot fan-out."""
        current_generation = getattr(self, "_monitor_generation", 0)
        if (
            self._close_in_progress
            or not self._docker_mode()
            or (generation is not None and generation != current_generation)
            or observation.monitor_generation != current_generation
            or observation.settings_identity
            != self._docker_monitor_settings_identity()
        ):
            return
        previous_snapshot = getattr(self, "_runtime_snapshot", None)
        target_changed = (
            previous_snapshot is not None
            and previous_snapshot.backend is RuntimeBackend.DOCKER_COMPOSE
            and previous_snapshot.target_identity != observation.target_identity
        )
        previous_image = (
            previous_snapshot.endpoints.image
            if previous_snapshot is not None
            and previous_snapshot.endpoints is not None
            else None
        )
        current_image = (
            observation.endpoints.image
            if observation.endpoints is not None
            else None
        )
        portrait_context_changed = (
            previous_snapshot is not None
            and previous_snapshot.backend is RuntimeBackend.DOCKER_COMPOSE
            and (
                previous_snapshot.target_identity,
                previous_snapshot.settings_identity,
                previous_snapshot.monitor_generation,
                previous_image,
            )
            != (
                observation.target_identity,
                observation.settings_identity,
                observation.monitor_generation,
                current_image,
            )
        )
        if target_changed:
            self._docker_tool_token = None
            if getattr(self, "_docker_character_token", None) is not None:
                self._abort_docker_character_creation(
                    "The observed Docker Compose target changed while character "
                    "creation was finishing. Verify the selected target before "
                    "trying again."
                )
            self._cancel_launch_queue()
            if hasattr(self, "_account_thread"):
                self._cancel_data_loads()
            self._data_selection = None
            self._clear_data_load_error()
            self._accounts = []
            portrait_invalidate = getattr(
                getattr(self, "_characters_page", None),
                "invalidate_portrait_target",
                None,
            )
            if callable(portrait_invalidate):
                portrait_invalidate()
            self._stop_docker_log_stream()
        policy = DockerControlPolicy(self._cfg.get("docker_control_policy", "connect_only"))
        snapshot = RuntimeSnapshot(
            game=observation.game, market=observation.market,
            running_clients=self._tracker.running_count,
            game_error=observation.game_error, market_error=observation.market_error,
            backend=RuntimeBackend.DOCKER_COMPOSE, docker_control_policy=policy,
            game_container=observation.game_identity, market_container=observation.market_identity,
            game_health=observation.game_health, market_health=observation.market_health,
            endpoints=observation.endpoints,
            target_identity=observation.target_identity,
            settings_identity=observation.settings_identity,
            monitor_generation=observation.monitor_generation,
        )
        self._runtime_snapshot = snapshot
        if portrait_context_changed:
            self._refresh_character_views()
        self._apply_runtime_snapshot(snapshot)
        if target_changed:
            self._refresh_characters()

    @staticmethod
    def _service_action_text(
        service: str,
        state: ServiceState,
        owned: bool,
        backend: RuntimeBackend = RuntimeBackend.NATIVE,
        policy: DockerControlPolicy | None = None,
    ) -> str:
        if backend is RuntimeBackend.DOCKER_COMPOSE:
            if policy is DockerControlPolicy.MANAGED:
                return {
                    ServiceState.OFFLINE: f"▶ Start {service}",
                    ServiceState.STARTING: f"⏳ Starting {service}…",
                    ServiceState.ONLINE: f"■ Stop {service}",
                    ServiceState.STOPPING: f"⏳ Stopping {service}…",
                    ServiceState.FAILED: f"↻ Retry {service}",
                    ServiceState.UNKNOWN: f"{service}: Docker unavailable",
                }[state]
            labels = {
                ServiceState.OFFLINE: f"{service}: Offline",
                ServiceState.STARTING: f"{service}: Starting…",
                ServiceState.ONLINE: f"{service}: Online",
                ServiceState.STOPPING: f"{service}: Stopping…",
                ServiceState.FAILED: f"{service}: Failed",
                ServiceState.UNKNOWN: f"{service}: Docker unavailable",
            }
            return labels[state]
        if state is ServiceState.ONLINE and not owned:
            return f"{service}: External"
        labels = {
            ServiceState.OFFLINE: f"▶ Start {service}",
            ServiceState.STARTING: f"⏳ Starting {service}…",
            ServiceState.ONLINE: f"■ Stop {service}",
            ServiceState.STOPPING: f"⏳ Stopping {service}…",
            ServiceState.FAILED: f"↻ Retry {service}",
            ServiceState.UNKNOWN: f"{service}: Docker unavailable",
        }
        return labels[state]

    @staticmethod
    def _service_action_enabled(
        state: ServiceState,
        owned: bool,
        backend: RuntimeBackend = RuntimeBackend.NATIVE,
        policy: DockerControlPolicy | None = None,
    ) -> bool:
        """Keep launcher controls inactive for external or transitional services."""
        if backend is RuntimeBackend.DOCKER_COMPOSE:
            return policy is DockerControlPolicy.MANAGED and state not in {
                ServiceState.STARTING, ServiceState.STOPPING, ServiceState.UNKNOWN,
            }
        if state in {ServiceState.STARTING, ServiceState.STOPPING}:
            return False
        return state is not ServiceState.ONLINE or owned

    @staticmethod
    def _service_action_tooltip(
        service: str,
        state: ServiceState,
        owned: bool,
        backend: RuntimeBackend = RuntimeBackend.NATIVE,
        policy: DockerControlPolicy | None = None,
    ) -> str:
        """Explain why a service action is unavailable without exposing paths."""
        if backend is RuntimeBackend.DOCKER_COMPOSE:
            if policy is DockerControlPolicy.CONNECT_ONLY:
                return "Connect-only Docker mode cannot change containers."
            if state in {ServiceState.STARTING, ServiceState.STOPPING}:
                return f"{service} is changing state"
            if state is ServiceState.UNKNOWN:
                return "Docker state is unavailable"
            return ""
        if state is ServiceState.ONLINE and not owned:
            return (
                f"{service} was started outside this launcher and must be stopped "
                "from its original console"
            )
        if state is ServiceState.STARTING:
            return f"{service} is starting"
        if state is ServiceState.STOPPING:
            return f"{service} is stopping"
        return ""

    def _set_runtime_page_roots(self, root: str) -> None:
        """Update both root-dependent pages, tolerating isolated test doubles."""
        mods_page = getattr(self, "_mods_page", None)
        set_mods_root = getattr(mods_page, "set_evejs_root", None)
        if callable(set_mods_root):
            set_mods_root(root)
        else:
            refresh_mods = getattr(mods_page, "refresh_mods", None)
            if callable(refresh_mods):
                refresh_mods()
        set_tools_root = getattr(
            getattr(self, "_tools_page", None),
            "set_evejs_root",
            None,
        )
        if callable(set_tools_root):
            set_tools_root(root)

    def _sync_runtime_pages(self, snapshot: RuntimeSnapshot) -> None:
        """Push one cached root/backend capability context into Mods and Tools."""
        cfg = getattr(self, "_cfg", None)
        if not isinstance(cfg, dict):
            return
        root = str(cfg.get("evejs_root", ""))
        compose_file = str(cfg.get("docker_compose_file", ""))
        current_context = (
            root,
            snapshot.backend,
            snapshot.docker_control_policy,
            compose_file,
        )
        previous_context = getattr(self, "_runtime_page_context", None)
        if previous_context == current_context:
            return

        root_changed = previous_context is None or previous_context[0] != root
        mods_context_changed = (
            previous_context is None
            or previous_context[1:3] != current_context[1:3]
        )
        tools_context_changed = (
            previous_context is None
            or previous_context[1:4] != current_context[1:4]
        )
        mods_page = getattr(self, "_mods_page", None)
        tools_page = getattr(self, "_tools_page", None)

        if root_changed:
            self._set_runtime_page_roots(root)
        if mods_context_changed:
            set_mods_context = getattr(mods_page, "set_runtime_context", None)
            if callable(set_mods_context):
                set_mods_context(
                    snapshot.backend,
                    snapshot.docker_control_policy,
                )
        if tools_context_changed:
            set_tools_context = getattr(tools_page, "set_runtime_context", None)
            if callable(set_tools_context):
                set_tools_context(
                    snapshot.backend,
                    snapshot.docker_control_policy,
                    compose_file=compose_file,
                )
        self._runtime_page_context = current_context

    def _apply_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        """Fan one snapshot out to footer, navigation, and Home."""
        self._sync_runtime_pages(snapshot)
        self._status_bar.set_server_state(
            snapshot.game, pid=snapshot.game_pid, container=snapshot.game_container,
            error=snapshot.game_error
        )
        self._status_bar.set_market_state(
            snapshot.market, pid=snapshot.market_pid, container=snapshot.market_container,
            error=snapshot.market_error
        )
        self._status_bar.set_client_count(snapshot.running_clients)

        self._nav.btn_server.setText(
            self._service_action_text(
                "Server",
                snapshot.game,
                snapshot.game_owned,
                snapshot.backend,
                snapshot.docker_control_policy,
            )
        )
        self._nav.btn_market.setText(
            self._service_action_text(
                "Market",
                snapshot.market,
                snapshot.market_owned,
                snapshot.backend,
                snapshot.docker_control_policy,
            )
        )
        self._nav.btn_server.setEnabled(
            self._service_action_enabled(
                snapshot.game, snapshot.game_owned, snapshot.backend,
                snapshot.docker_control_policy,
            )
        )
        self._nav.btn_market.setEnabled(
            self._service_action_enabled(
                snapshot.market, snapshot.market_owned, snapshot.backend,
                snapshot.docker_control_policy,
            )
        )
        self._nav.btn_server.setToolTip(
            self._service_action_tooltip(
                "Server",
                snapshot.game,
                snapshot.game_owned,
                snapshot.backend,
                snapshot.docker_control_policy,
            )
        )
        self._nav.btn_market.setToolTip(
            self._service_action_tooltip(
                "Market",
                snapshot.market,
                snapshot.market_owned,
                snapshot.backend,
                snapshot.docker_control_policy,
            )
        )
        self._nav.set_badge_count(
            int(Page.CHARACTERS),
            snapshot.running_clients,
        )
        self._nav.btn_kill_all.setEnabled(snapshot.running_clients > 0)
        self._nav.btn_kill_all.setToolTip(
            "Terminate every running EVE client"
            if snapshot.running_clients > 0
            else "No EVE clients are running"
        )
        self._home_page.apply_runtime_snapshot(snapshot)
        self._refresh_character_creation_availability(snapshot)
        docker = snapshot.backend is RuntimeBackend.DOCKER_COMPOSE
        if docker:
            managed = snapshot.docker_control_policy is DockerControlPolicy.MANAGED
            transitional = {ServiceState.STARTING, ServiceState.STOPPING, ServiceState.UNKNOWN}
            game_busy = snapshot.game in transitional
            market_busy = snapshot.market in transitional
            game_enabled = managed and not game_busy
            market_blocked = snapshot.game in {
                ServiceState.ONLINE, ServiceState.STARTING, ServiceState.STOPPING,
                ServiceState.FAILED, ServiceState.UNKNOWN,
            }
            market_enabled = managed and not market_busy and not market_blocked
            self._nav.btn_server.setEnabled(game_enabled)
            self._nav.btn_market.setEnabled(market_enabled)
            if not managed:
                reason = "Connect-only Docker mode cannot change containers."
                self._nav.btn_server.setToolTip(reason)
                self._nav.btn_market.setToolTip(reason)
                self._home_page.btn_start_servers.setEnabled(False)
                self._home_page.btn_start_servers.setToolTip(reason)
            elif market_blocked:
                self._nav.btn_market.setToolTip("Stop Server first")
            self._nav.btn_characters.setEnabled(True)
            self._nav.btn_characters.setToolTip("")
            set_character_launch = getattr(
                getattr(self, "_characters_page", None),
                "set_launch_available",
                None,
            )
            if callable(set_character_launch):
                launch_available, launch_reason = self._docker_launch_capability(
                    snapshot
                )
                set_character_launch(launch_available, launch_reason)
            self._nav.btn_mods.setEnabled(True)
            self._nav.btn_mods.setToolTip("")
            self._nav.btn_tools.setEnabled(True)
            self._nav.btn_tools.setToolTip("")
        else:
            self._nav.btn_characters.setEnabled(True)
            self._nav.btn_characters.setToolTip("")
            set_character_launch = getattr(
                getattr(self, "_characters_page", None),
                "set_launch_available",
                None,
            )
            if callable(set_character_launch):
                set_character_launch(True)
            self._nav.btn_mods.setEnabled(True)
            self._nav.btn_mods.setToolTip("")
            self._nav.btn_tools.setEnabled(True)
            self._nav.btn_tools.setToolTip("")

    def _on_service_probe(
        self, probe: ServiceProbe, generation: int | None = None
    ) -> None:
        """Receive one worker observation and fan it out without re-probing."""
        if (
            self._close_in_progress
            or self._docker_mode()
            or (generation is not None and generation != getattr(self, "_monitor_generation", 0))
        ):
            return
        self._service_reachability = (
            probe.game_reachable,
            probe.market_reachable,
        )
        if probe.game_reachable:
            self._server_intent = None
            self._server_error = None
        if probe.market_reachable:
            self._market_intent = None
            self._market_error = None
        snapshot = self._build_runtime_snapshot(
            game_reachable=probe.game_reachable,
            market_reachable=probe.market_reachable,
        )
        self._runtime_snapshot = snapshot
        self._apply_runtime_snapshot(snapshot)

    def _schedule_service_monitor_start(self) -> None:
        """Yield one GUI event turn before creating the monitor worker."""
        if (
            self._service_thread is not None
            or self._service_monitor_start_pending
            or self._close_in_progress
        ):
            return
        self._service_monitor_start_pending = True
        QTimer.singleShot(0, self._start_service_monitor_after_show)

    @pyqtSlot()
    def _start_service_monitor_after_show(self) -> None:
        """Create the monitor only when the window remains open after showEvent."""
        if not self._service_monitor_start_pending:
            return
        self._service_monitor_start_pending = False
        if self._close_in_progress:
            return
        self._start_service_monitor()

    def _start_service_monitor(self) -> None:
        """Start the long-lived endpoint monitor after the window is shown."""
        if self._service_thread is not None:
            return
        thread = QThread(self)
        generation = getattr(self, "_monitor_generation", 0)
        if self._docker_mode():
            draft = self._docker_setup_draft()

            def target_factory() -> ComposeTarget:
                # Monitoring, data, lifecycle, and mutation workers must all
                # observe the same ordered launcher-owned override chain.
                return attach_docker_mod_override(build_compose_target(draft))

            monitor = DockerMonitor(
                target_factory,
                inspector_factory=lambda: ComposeInspector(DockerCommandRunner()),
                interval_ms=5_000,
                monitor_generation=generation,
                settings_identity=self._docker_monitor_settings_identity(),
            )
        else:
            monitor = ServiceMonitor(interval_ms=5_000)
        monitor.moveToThread(thread)
        thread.started.connect(monitor.start)
        if isinstance(monitor, DockerMonitor):
            monitor.observation_changed.connect(
                lambda observation, generation=generation: self._on_docker_observation(
                    observation, generation
                )
            )
            self._docker_observe_requested.connect(monitor.observe_now)
        else:
            monitor.probe_changed.connect(
                lambda probe, generation=generation: self._on_service_probe(probe, generation)
            )
            self._service_probe_requested.connect(monitor.probe_now)
        self._service_monitor_stop_requested.connect(monitor.stop)
        thread.finished.connect(monitor.deleteLater)
        thread.finished.connect(
            lambda monitored_thread=thread: self._on_service_monitor_thread_finished(monitored_thread),
            Qt.ConnectionType.QueuedConnection,
        )
        self._service_thread = thread
        self._service_monitor = monitor
        if self._docker_mode():
            snapshot = self._docker_unknown_snapshot()
            self._runtime_snapshot = snapshot
            self._apply_runtime_snapshot(snapshot)
        thread.start()

    def _on_service_monitor_thread_finished(self, finished_thread: QThread | None = None) -> None:
        """Retry a deferred window close once the retained worker has stopped."""
        thread = self._service_thread
        if finished_thread is not None and finished_thread is not thread:
            return
        self._service_monitor = None
        self._service_thread = None
        if thread is not None:
            thread.deleteLater()
        if self._service_monitor_restart_pending:
            self._service_monitor_restart_pending = False
            if not self._close_in_progress:
                self._schedule_service_monitor_start()
        elif self._service_monitor_start_pending and not self._close_in_progress:
            self._schedule_service_monitor_start()
        if self._close_in_progress and self._service_thread is not None:
            QTimer.singleShot(0, self.close)
        elif self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _stop_service_monitor(self) -> bool:
        """Stop the worker timer and report whether its thread has terminated."""
        self._service_monitor_start_pending = False
        thread = self._service_thread
        if thread is None:
            return True
        monitor = self._service_monitor
        if monitor is not None:
            monitor.request_shutdown()
        thread.requestInterruption()
        self._service_monitor_stop_requested.emit()
        thread.quit()
        if isinstance(monitor, DockerMonitor):
            # Docker CLI may still be returning.  Its shutdown is intentionally
            # asynchronous; finished callback owns reference release/restart.
            return False
        if not thread.wait(2_000):
            log.warning("Service monitor thread did not stop within 2 seconds")
            thread.requestInterruption()
            thread.quit()
            if not thread.wait(1_000):
                log.error("Service monitor thread remained alive after shutdown")
                return False
        self._service_monitor = None
        self._service_thread = None
        return True

    def _update_status_bar(self) -> None:
        # Before show() there is no worker yet; perform one deterministic
        # bootstrap/test probe.  Once shown, all socket I/O stays in the worker.
        if self._docker_mode():
            snapshot = self._docker_cached_snapshot()
            self._runtime_snapshot = snapshot
            self._apply_runtime_snapshot(snapshot)
            return
        if self._service_monitor is None:
            self._service_reachability = (
                is_server_running(port=int(Ports.GAME_TCP)),
                is_server_running(port=int(Ports.MARKET_RPC)),
            )
        snapshot = self._build_runtime_snapshot()
        self._runtime_snapshot = snapshot
        self._apply_runtime_snapshot(snapshot)
        if self._service_monitor is not None:
            self._service_probe_requested.emit()

    # ── Console panel toggle ──────────────────────────────────────────

    def _docker_log_target_factory(self) -> Callable[[], ComposeTarget]:
        """Capture raw settings; worker-thread factory validates and resolves them."""
        draft = self._docker_setup_draft()

        def target_factory() -> ComposeTarget:
            return attach_docker_mod_override(
                build_compose_target(draft)
            )

        return target_factory

    def _restart_docker_monitor_for_compose_change(self) -> None:
        """Replace a monitor whose captured Compose file chain is now stale."""
        self._service_monitor_restart_pending = True
        if self._stop_service_monitor():
            self._service_monitor_restart_pending = False
            if not self._close_in_progress:
                self._schedule_service_monitor_start()

    def _start_docker_log_stream(self, service: str) -> None:
        """Start exactly one read-only local CLI follower for the selected service."""
        active = getattr(self, "_docker_log_thread", None)
        if active is not None:
            self._pending_docker_log_service = service
            self._stop_docker_log_stream(clear_pending=False)
            return
        self._log_generation += 1
        self._console_panel.begin_stream(f"Docker Compose — {service.title()} logs")
        thread = QThread(self)
        token = object()
        worker = DockerLogWorker(self._docker_log_target_factory(), service=service, token=token)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.line.connect(self._on_docker_log_line)
        worker.diagnostic.connect(self._on_docker_log_diagnostic)
        worker.terminal.connect(worker.deleteLater, Qt.ConnectionType.DirectConnection)
        worker.terminal.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(self._on_docker_log_thread_finished)
        self._docker_log_thread = thread
        self._docker_log_worker = worker
        self._docker_log_service = service
        self._docker_log_token = token
        thread.start()

    def _stop_docker_log_stream(self, *, clear_pending: bool = True) -> bool:
        """Request follower cancellation without waiting on the GUI thread."""
        self._log_generation = getattr(self, "_log_generation", 0) + 1
        if clear_pending:
            self._pending_docker_log_service = None
        # Queued cross-thread line/diagnostic delivery must be invalid before
        # cancellation returns; worker/thread ownership remains retained until
        # their exact QThread finalizer runs.
        self._docker_log_token = None
        worker = getattr(self, "_docker_log_worker", None)
        thread = getattr(self, "_docker_log_thread", None)
        if worker is None or thread is None:
            return True
        worker.request_cancel()
        return False

    def _on_console_panel_closed(self) -> None:
        if self._docker_mode():
            self._stop_docker_log_stream()

    @pyqtSlot(object, str)
    def _on_docker_log_line(self, token: object, line: str) -> None:
        if (token is self._docker_log_token and self._docker_log_worker is not None
                and self._docker_mode() and not self._close_in_progress):
            self._console_panel.append_stream_line(line)

    @pyqtSlot(object, str)
    def _on_docker_log_diagnostic(self, token: object, message: str) -> None:
        if (token is self._docker_log_token and self._docker_log_worker is not None
                and self._docker_mode() and not self._close_in_progress):
            self._console_panel.finish_stream(message)

    @pyqtSlot()
    def _on_docker_log_thread_finished(self) -> None:
        """GUI-affine finalizer: release every exact session, then replace once."""
        thread = self.sender()
        if not isinstance(thread, QThread):
            return
        current = thread is self._docker_log_thread
        if current:
            self._docker_log_worker = None
            self._docker_log_thread = None
            self._docker_log_service = None
            self._docker_log_token = None
        # Stale retained threads still receive exact cleanup; never early-return.
        thread.deleteLater()
        if current:
            pending = self._pending_docker_log_service
            self._pending_docker_log_service = None
            if pending and self._docker_mode() and not self._close_in_progress:
                self._pending_docker_log_service = pending
                QTimer.singleShot(0, self._start_pending_docker_log_stream)
        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    @pyqtSlot()
    def _start_pending_docker_log_stream(self) -> None:
        """Start the one serialized replacement after its exact predecessor ends."""
        pending = self._pending_docker_log_service
        self._pending_docker_log_service = None
        if pending and self._docker_mode() and not self._close_in_progress:
            self._start_docker_log_stream(pending)

    def _on_console_toggled(self, name: str) -> None:
        """StatusBar section click → show/hide console panel for that service."""
        if self._docker_mode():
            if not hasattr(self, "_docker_log_worker"):
                self._docker_unavailable("Docker console logs are not available in this version.")
            elif (self._console_panel.isVisible() and self._docker_log_worker is not None
                  and name == self._docker_log_service
                  and self._pending_docker_log_service is None):
                self._console_panel.stop()
            elif name in {"server", "market"}:
                self._start_docker_log_stream(name)
            return
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            return
        if self._console_panel.isVisible():
            self._console_panel.stop()
            return

        if name == "server":
            log_path = get_server_console_log()
            if log_path.exists():
                self._console_panel.tail(str(log_path))
            else:
                self._console_panel.show()
                self._console_panel.raise_()
        elif name == "market":
            log_path = get_market_console_log()
            if log_path.exists():
                self._console_panel.tail(str(log_path))
            else:
                self._console_panel.clear_content()
                self._console_panel.set_title("Market Server — not started yet")
                self._console_panel.show()
                self._console_panel.raise_()

    # ── Event filter: resize cursors + click-outside-console ──────────

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and self._console_panel.isVisible()
            and isinstance(obj, QWidget)
            and obj.window() is self
        ):
            # A header drag owns the interaction until its matching release.
            # Do not let an application-level event routing quirk turn that
            # drag into an outside click and dismiss the panel mid-resize.
            if not self._console_panel._resizing:
                pos = self._console_panel.mapFromGlobal(
                    event.globalPosition().toPoint()
                )
                if not self._console_panel.rect().contains(pos):
                    self._console_panel.stop()

        if event.type() == QEvent.Type.MouseMove:
            pos = self.mapFromGlobal(QCursor.pos())
            if not self._resizing:
                self._update_cursor_for_edge(pos)

        return super().eventFilter(obj, event)

    # ── Frameless resize (startSystemResize + edge cursors) ───────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge and self.windowHandle():
                self._resizing = True
                self.windowHandle().startSystemResize(edge)
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._resizing = False
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._resizing:
            self._update_cursor_for_edge(event.position().toPoint())

    def _edge_at(self, pos):
        """Return combined Qt.Edge flags if pos is inside the resize margin."""
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        left = x < self._MARGIN
        right = x > w - self._MARGIN
        top = y < self._MARGIN
        bottom = y > h - self._MARGIN

        if left and top:
            return Qt.Edge.LeftEdge | Qt.Edge.TopEdge
        if right and top:
            return Qt.Edge.RightEdge | Qt.Edge.TopEdge
        if left and bottom:
            return Qt.Edge.LeftEdge | Qt.Edge.BottomEdge
        if right and bottom:
            return Qt.Edge.RightEdge | Qt.Edge.BottomEdge
        if left:
            return Qt.Edge.LeftEdge
        if right:
            return Qt.Edge.RightEdge
        if top:
            return Qt.Edge.TopEdge
        if bottom:
            return Qt.Edge.BottomEdge
        return None

    def _update_cursor_for_edge(self, pos) -> None:
        edge = self._edge_at(pos)

        if edge is None:
            if self._cursor_override_active:
                QApplication.restoreOverrideCursor()
                self._cursor_override_active = False
            return

        if edge == (Qt.Edge.LeftEdge | Qt.Edge.TopEdge):
            shape = Qt.CursorShape.SizeFDiagCursor
        elif edge == (Qt.Edge.RightEdge | Qt.Edge.TopEdge):
            shape = Qt.CursorShape.SizeBDiagCursor
        elif edge == (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge):
            shape = Qt.CursorShape.SizeBDiagCursor
        elif edge == (Qt.Edge.RightEdge | Qt.Edge.BottomEdge):
            shape = Qt.CursorShape.SizeFDiagCursor
        elif edge in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            shape = Qt.CursorShape.SizeHorCursor
        elif edge in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            shape = Qt.CursorShape.SizeVerCursor
        else:
            if self._cursor_override_active:
                QApplication.restoreOverrideCursor()
                self._cursor_override_active = False
            return

        if self._cursor_override_active:
            QApplication.changeOverrideCursor(QCursor(shape))
        else:
            QApplication.setOverrideCursor(QCursor(shape))
            self._cursor_override_active = True

    # ── Update handling ──────────────────────────────────────────────

    def _on_update_available(
        self, version: str, changelog: str, download_url: str, published_at: str
    ) -> None:
        """Handler for when an update is found."""
        self._latest_version = version
        self._latest_changelog = changelog
        self._latest_download_url = download_url
        self._latest_published = published_at
        self._title_bar.show_update_available(version)

    def _on_update_clicked(self) -> None:
        """Show the update dialog and handle download/install or skip."""
        from .constants import APP_VERSION

        dlg = UpdateDialog(
            current_version=APP_VERSION,
            new_version=self._latest_version,
            changelog=self._latest_changelog,
            download_url=self._latest_download_url,
            published_at=self._latest_published,
            parent=self,
        )
        dlg.exec()

        if dlg.result() == QDialog.DialogCode.Accepted:
            self._begin_update_install()

        elif dlg.skip_requested:
            # User clicked Skip This Version
            self._update_checker.skip_version(self._latest_version)
            self._title_bar.set_update_up_to_date()

    def _begin_update_install(self) -> None:
        """Show progress before downloading, then retain the worker through teardown."""
        if self._update_install_worker is not None:
            return
        if not self._latest_download_url:
            QMessageBox.warning(
                self,
                "Update Unavailable",
                "This release does not include a downloadable launcher package.",
            )
            return

        dialog = UpdateProgressDialog(self._latest_version, parent=self)
        worker = UpdateInstallWorker(
            self._latest_download_url,
            sys.executable,
            parent=self,
        )
        self._update_progress_dialog = dialog
        self._update_install_worker = worker
        self._update_install_result = None
        self._update_install_thread_finished = False

        worker.stage_changed.connect(dialog.set_stage)
        worker.download_progress.connect(dialog.set_download_progress)
        worker.completed.connect(self._on_update_install_completed)
        worker.finished.connect(
            self._on_update_install_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )

        dialog.show()
        QApplication.processEvents()
        worker.start()

    @pyqtSlot(bool, str)
    def _on_update_install_completed(self, success: bool, error: str) -> None:
        """Record the worker result without racing its QThread teardown."""
        self._update_install_result = (success, error)
        self._finish_update_install_if_ready()

    @pyqtSlot()
    def _on_update_install_thread_finished(self) -> None:
        """Wait for both the result and finished signal before releasing the worker."""
        self._update_install_thread_finished = True
        self._finish_update_install_if_ready()

    def _finish_update_install_if_ready(self) -> None:
        """Surface preparation failures or exit only after the agent has started."""
        if self._update_install_result is None or not self._update_install_thread_finished:
            return

        success, error = self._update_install_result
        worker = self._update_install_worker
        self._update_install_worker = None
        self._update_install_result = None
        self._update_install_thread_finished = False
        if worker is not None:
            worker.deleteLater()

        dialog = self._update_progress_dialog
        if not success:
            if dialog is not None:
                dialog.show_error(error)
            return

        if dialog is not None:
            dialog.set_stage("install", "Switching to the standalone updater…")
        QTimer.singleShot(750, hard_exit)

    def _create_update_checker(self) -> UpdateChecker:
        """Create an update worker whose lifetime is safe during window close."""
        checker = UpdateChecker(self)
        checker.update_available.connect(self._on_update_available)
        checker.up_to_date.connect(self._on_update_up_to_date)
        checker.check_failed.connect(
            lambda msg: log.warning("Update check failed: %s", msg)
        )
        checker.finished.connect(
            self._on_update_checker_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        return checker

    def _start_update_checker(self, checker: UpdateChecker) -> None:
        """Start one checker unless shutdown has begun or it already runs."""
        if self._close_in_progress or checker.isRunning():
            return
        if checker not in self._active_update_checkers:
            self._active_update_checkers.append(checker)
        checker.check()

    def _start_automatic_update_check(self) -> None:
        """Run the reusable startup/periodic checker only while the window is open."""
        self._start_update_checker(self._update_checker)

    def _has_running_update_checker(self) -> bool:
        """Return whether a retained update worker still owns a native thread."""
        return any(checker.isRunning() for checker in self._active_update_checkers)

    @pyqtSlot()
    def _on_update_checker_finished(self) -> None:
        """Release completed checker tracking and resume a deferred window close."""
        self._active_update_checkers = [
            checker
            for checker in self._active_update_checkers
            if checker.isRunning()
        ]
        if self._close_in_progress:
            QTimer.singleShot(0, self.close)

    def _on_update_up_to_date(self, version: str = "") -> None:
        """Handler for when the app is already up to date."""
        self._title_bar.set_update_up_to_date()
        self._cfg["update_last_checked"] = datetime.now(timezone.utc).isoformat()
        config.save(self._cfg)
        self._settings_page.set_update_check_done(True)

    def _announce_shipboard(self, event: VoiceEvent, **context: object) -> bool:
        """Publish one non-blocking local announcement without gating its action."""
        try:
            if getattr(self, "_close_in_progress", False):
                return False
            controller = getattr(self, "_audio_controller", None)
        except RuntimeError:
            # Partially constructed diagnostic/test wrappers have no Qt base
            # state. Optional audio must remain inert rather than gate the
            # lifecycle action they are exercising.
            return False
        announce = getattr(controller, "announce", None)
        if not callable(announce):
            return False
        try:
            return bool(announce(event, **context))
        except Exception:  # noqa: BLE001 - optional platform speech must be isolated
            log.exception("Shipboard announcement failed for %s", event.value)
            return False

    @pyqtSlot()
    def _prepare_shipboard_voice(self) -> None:
        """Initialize the local LYRA clip backend once Qt is event-loop ready."""
        if getattr(self, "_close_in_progress", False):
            return
        page = getattr(self, "_settings_page", None)
        controller = getattr(self, "_audio_controller", None)
        if page is None or controller is None:
            return
        attempts = int(getattr(self, "_voice_prepare_attempts", 0)) + 1
        self._voice_prepare_attempts = attempts
        voice_preview_available = False
        if bool(getattr(controller, "speech_supported", False)):
            try:
                voice_preview_available = bool(
                    controller.prepare_voice_preview()
                )
            except Exception:  # noqa: BLE001 - optional local clip probing
                log.exception("Bundled LYRA catalog capability probe failed")
        if not voice_preview_available and attempts < int(
            getattr(self, "_VOICE_PREPARE_MAX_ATTEMPTS", 3)
        ):
            QTimer.singleShot(
                int(getattr(self, "_VOICE_PREPARE_RETRY_DELAY_MS", 250)),
                self._prepare_shipboard_voice,
            )
            return
        voice_reason = (
            "Bundled LYRA voice catalog is ready."
            if voice_preview_available
            else "Bundled LYRA voice asset unavailable."
        )
        page.set_voice_preview_available(
            voice_preview_available,
            voice_reason,
        )

    @pyqtSlot()
    def _start_launcher_ambience(self) -> None:
        """Start the bundled original loop and publish its truthful UI state."""
        if getattr(self, "_close_in_progress", False):
            return
        controller = getattr(self, "_audio_controller", None)
        start_music = getattr(controller, "start_music", None)
        if not callable(start_music):
            return
        try:
            # ``start_music`` reports request acceptance. Real playback is
            # asynchronous, so only AudioController.music_playback_changed
            # may promote the title capsule to its active state.
            start_music()
        except Exception:  # noqa: BLE001 - optional platform media is isolated
            log.exception("Launcher ambience could not be started")
        track_name = str(
            getattr(controller, "music_track_name", "STATION SOUNDSCAPE")
        )
        self._title_bar.set_audio_status(
            bool(getattr(controller, "music_active", False)),
            track_name,
        )

    @pyqtSlot()
    def _preview_shipboard_voice(self) -> None:
        """Preview LYRA with the unsaved Audio & Voice form values."""
        if getattr(self, "_close_in_progress", False):
            return
        page = getattr(self, "_settings_page", None)
        controller = getattr(self, "_audio_controller", None)
        preview = getattr(controller, "preview_voice", None)
        draft = getattr(page, "audio_preview_settings", None)
        if not callable(preview) or not callable(draft):
            return
        try:
            spoken = bool(preview(draft()))
        except Exception:  # noqa: BLE001 - optional clip playback is isolated
            log.exception("Shipboard voice preview failed")
            spoken = False
        if not spoken:
            # One asynchronous playback rejection is not a capability probe.
            # Keep the verified catalog/backend state so the user can retry.
            log.warning("Shipboard voice preview did not start")

    @pyqtSlot(bool)
    def _on_music_mute_changed(self, muted: bool) -> None:
        """Persist only soundtrack mute without changing LYRA voice output."""
        muted = bool(muted)
        self._cfg["audio_music_muted"] = muted
        try:
            persisted = config.load()
            persisted["audio_music_muted"] = muted
            config.save(persisted)
        except OSError:
            # Music remains safely muted/unmuted for this process even if the
            # preference cannot be written; launcher actions remain unaffected.
            log.exception("Could not persist the soundtrack mute setting")

    def _on_settings_saved(self, cfg: dict) -> None:
        """Refresh in-memory config and character grid after settings save."""
        previous_root = str(self._cfg.get("evejs_root", ""))
        previous_docker_target = self._docker_target_identity()
        previous_monitor = (
            self._cfg.get("runtime_backend"), self._cfg.get("docker_compose_file"),
            self._cfg.get("docker_project_name"), previous_root,
        )
        self._cfg.update(cfg)
        if (
            self._docker_target_identity() != previous_docker_target
            and getattr(self, "_docker_character_token", None) is not None
        ):
            self._abort_docker_character_creation(
                "Docker runtime settings changed while character creation was "
                "finishing. Verify the selected target before trying again."
            )
        audio_controller = getattr(self, "_audio_controller", None)
        apply_audio_settings = getattr(audio_controller, "apply_settings", None)
        if callable(apply_audio_settings):
            apply_audio_settings(self._cfg)
        self._settings_generation = getattr(self, "_settings_generation", 0) + 1
        current_root = str(self._cfg.get("evejs_root", ""))
        current_monitor = (
            self._cfg.get("runtime_backend"), self._cfg.get("docker_compose_file"),
            self._cfg.get("docker_project_name"), current_root,
        )
        if current_monitor != previous_monitor:
            self._cancel_launch_queue()
            if hasattr(self, "_account_thread"):
                self._cancel_data_loads()
            self._data_selection = None
            self._clear_data_load_error()
            self._accounts = []
            self._group_target_identity = None
            self._group_state = TargetGroupState()
            portrait_invalidate = getattr(
                getattr(self, "_characters_page", None),
                "invalidate_portrait_target",
                None,
            )
            if callable(portrait_invalidate):
                portrait_invalidate()
            self._stop_docker_log_stream()
            self._monitor_generation = getattr(self, "_monitor_generation", 0) + 1
            self._service_monitor_restart_pending = True
            if self._docker_mode():
                snapshot = self._docker_unknown_snapshot()
                self._runtime_snapshot = snapshot
                self._apply_runtime_snapshot(snapshot)
            elif hasattr(self, "_runtime_snapshot"):
                self._publish_cached_runtime()
            if self._stop_service_monitor():
                self._service_monitor_restart_pending = False
                if not self._close_in_progress:
                    self._schedule_service_monitor_start()
        if current_root != previous_root:
            clear_solar_system_name_cache()
            PortraitCache.clear()
            runtime_page_context = getattr(self, "_runtime_page_context", None)
            if runtime_page_context is None or runtime_page_context[0] != current_root:
                snapshot = getattr(self, "_runtime_snapshot", None)
                if isinstance(snapshot, RuntimeSnapshot):
                    self._sync_runtime_pages(snapshot)
                else:
                    self._set_runtime_page_roots(current_root)
        if self._docker_mode() and current_monitor == previous_monitor:
            self._publish_cached_runtime()
        self._apply_runtime_settings()
        self._home_page.set_server_mode(self._effective_server_mode_label())
        self._refresh_characters()

    def _on_manual_update_check(self) -> None:
        """Triggered by the Settings page's 'Check for Updates' button."""
        if self._close_in_progress:
            return
        # Visual feedback — title bar spinner + settings button shows checking
        self._title_bar.set_update_checking()
        self._settings_page.set_update_checking()

        # Create a fresh checker (QThread can only start once)
        checker = self._create_update_checker()
        checker.up_to_date.connect(lambda v="": self._settings_page.set_update_check_done(True))
        checker.check_failed.connect(lambda msg: self._on_check_failed_from_settings(msg))
        self._start_update_checker(checker)

    def _on_check_failed_from_settings(self, error: str) -> None:
        """Handle a failed check triggered from Settings."""
        log.warning("Manual update check failed: %s", error)
        self._title_bar.set_update_up_to_date()
        self._settings_page.set_update_check_done(False)

    # ── Resize / close lifecycle ──────────────────────────────────────

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_service_monitor_start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._console_panel.isVisible() and hasattr(self._console_panel, "_reposition"):
            self._console_panel._reposition()
        caption = getattr(self, "_shipboard_caption", None)
        if caption is not None and caption.isVisible():
            caption.reposition()

    def _complete_deferred_close(self) -> None:
        """Request the final close only after its lifecycle worker is released."""
        if getattr(self, "_close_after_lifecycle", False):
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:  # noqa: N802
        settings_page = getattr(self, "_settings_page", None)
        stack = getattr(self, "_stack", None)
        if (
            settings_page is not None
            and stack is not None
            and stack.currentIndex() == int(Page.SETTINGS)
            and settings_page.is_dirty()
        ):
            if getattr(self, "_pending_settings_intent", None) is not None:
                event.ignore()
                return
            answer = self._ask_unsaved_settings()
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                self._pending_settings_intent = ("close", None)
                event.ignore()
                settings_page.save_settings()
                return
            if answer == QMessageBox.StandardButton.Discard:
                settings_page.discard_changes()
            else:
                event.ignore()
                return
        launch_queue = getattr(self, "_launch_queue", None)
        if launch_queue is not None:
            self._close_in_progress = True
            launch_queue.cancel()
        if getattr(self, "_client_launch_thread", None) is not None:
            self._close_in_progress = True
            self._shutdown_audio_for_close()
            event.ignore()
            return
        if (
            getattr(self, "_character_creation_thread", None) is not None
            or getattr(self, "_character_creation_request", None) is not None
            or getattr(self, "_character_deletion_thread", None) is not None
            or getattr(self, "_character_deletion_request", None) is not None
            or getattr(self, "_overview_patch_thread", None) is not None
        ):
            self._close_in_progress = True
            self._shutdown_audio_for_close()
            event.ignore()
            return
        if getattr(self, "_docker_preflight_thread", None) is not None:
            self._close_in_progress = True
            self._shutdown_audio_for_close()
            event.ignore()
            return
        if self._update_install_worker is not None:
            event.ignore()
            return
        if getattr(self, "_docker_log_thread", None) is not None:
            self._close_in_progress = True
            self._shutdown_audio_for_close()
            self._stop_docker_log_stream()
            event.ignore()
            return
        running = self._tracker.running_count
        if running > 0:
            reply = QMessageBox.question(
                self,
                "Clients Running",
                f"{running} client(s) still running.\nKill them and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._tracker.kill_all()
            else:
                self._close_in_progress = False
                event.ignore()
                return
        if hasattr(self, "_account_thread"):
            self._cancel_data_loads()
            portrait_cancel = getattr(
                getattr(self, "_characters_page", None),
                "cancel_portrait_loads",
                None,
            )
            if callable(portrait_cancel):
                portrait_cancel(invalidate=True)
            if self._background_data_active():
                self._close_in_progress = True
                event.ignore()
                return
        # Client handling must precede the Docker policy branch: after a
        # confirmed Kill+Exit, managed stop-on-exit is re-evaluated below.
        if self._docker_mode():
            self._close_in_progress = True
            # Connect-only is observational; it never constructs a lifecycle
            # worker. Managed policy may deliberately leave Compose running.
            managed_stop = self._docker_managed() and not bool(
                self._cfg.get("docker_keep_running_on_exit", True)
            )
            if self._lifecycle_active():
                self._docker_close_pending = managed_stop
                event.ignore()
                return
            if managed_stop and not self._docker_close_stop_started:
                self._docker_close_pending = True
                self._docker_close_stop_started = True
                self._docker_close_stop_succeeded = False
                if self._begin_docker_lifecycle(DockerLifecycleAction.STOP_ALL):
                    event.ignore()
                    return
                self._docker_close_pending = False
                self._docker_close_stop_started = False
                self._close_in_progress = False
                event.ignore()
                return
            if managed_stop and not self._docker_close_stop_succeeded:
                event.ignore()
                return
            if not self._stop_service_monitor():
                event.ignore()
                return
            if self._has_running_update_checker():
                event.ignore()
                return
            self._shutdown_audio_for_close()
            event.accept()
            return
        if getattr(self, "_close_after_lifecycle", False):
            self._close_in_progress = True
            if self._lifecycle_active():
                event.ignore()
                return
            if not self._stop_service_monitor():
                event.ignore()
                return
            if self._has_running_update_checker():
                event.ignore()
                return
            self._shutdown_audio_for_close()
            event.accept()
            return

        if self._lifecycle_active():
            log.info("Ignored close while a lifecycle operation is still active")
            event.ignore()
            return

        owns_game = self._server_process_alive()
        owns_market = (
            self._market_proc is not None and self._market_proc.poll() is None
        )
        if owns_game or owns_market:
            self._close_in_progress = True
            self._close_after_lifecycle = True
            if self._run_stop_sequence(
                stop_game=True,
                stop_market=True,
                on_complete=self._complete_deferred_close,
            ):
                event.ignore()
                return
            self._close_after_lifecycle = False
            self._close_in_progress = False
            event.ignore()
            return

        self._close_in_progress = True
        if not self._stop_service_monitor():
            event.ignore()
            return
        if self._has_running_update_checker():
            event.ignore()
            return
        self._shutdown_audio_for_close()
        event.accept()

    def _shutdown_audio_for_close(self) -> None:
        """Stop optional playback without delaying or initializing backends."""
        audio_controller = getattr(self, "_audio_controller", None)
        if audio_controller is not None:
            audio_controller.shutdown()
