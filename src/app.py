"""Main application window for EveJS Launcher V2.

Composes the frameless top-level window:
    TitleBar (top)
    NavPanel | QStackedWidget (4 pages; CharactersPage owns its DetailPanel)
    StatusBar (bottom)
    ConsolePanel (overlay child of central widget)

Wires together nav, server, character launching, and process tracking.
"""
from __future__ import annotations

from collections.abc import Callable
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
from PyQt6.QtGui import QColor, QCursor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import config
from .constants import APP_TITLE, Page, Ports
from .core.client_launch_queue import ClientLaunchQueue
from .core.dashboard import visible_account_count, visible_character_rows
from .core.db import Account, Character, clear_solar_system_name_cache, load_accounts
from .core.launcher import launch_client
from .core.platform import hard_exit
from .core.process_tracker import ProcessTracker
from .core.profiles import PROFILES_ROOT, create_profile, prefill_username, profile_exists
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
    RuntimeSnapshot,
    ServiceState,
    derive_service_state,
)
from .pages.characters_page import CharactersPage
from .pages.home_page import HomePage
from .pages.mods_page import ModsPage
from .pages.settings_page import SettingsPage
from .utils.cache import PortraitCache
from .utils.logger import setup_logger
from .widgets.console_panel import ConsolePanel
from .widgets.nav_panel import NavPanel
from .widgets.status_bar import StatusBar
from .widgets.title_bar import TitleBar
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


# ═════════════════════════════════════════════════════════════════════════════
# MainWindow
# ═════════════════════════════════════════════════════════════════════════════


class MainWindow(QMainWindow):
    """Top-level frameless window hosting the entire launcher UI."""

    _service_probe_requested = pyqtSignal()
    _service_monitor_stop_requested = pyqtSignal()

    _MARGIN = 8  # px resize-hit border around the frame

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

        # Window drop shadow on the frame itself
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 0x88))
        self.setGraphicsEffect(shadow)

        # ── State ──────────────────────────────────────────────────────
        self._cfg = config.load()
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
        self._service_monitor: ServiceMonitor | None = None
        self._service_monitor_start_pending = False
        self._lifecycle_thread: QThread | None = None
        self._lifecycle_worker: QObject | None = None
        self._lifecycle_start_scope = (False, False)
        self._lifecycle_stop_scope = (False, False)
        self._lifecycle_ready_callback: Callable[[], None] | None = None
        self._lifecycle_stop_callback: Callable[[], None] | None = None
        self._lifecycle_after_thread_callback: Callable[[], None] | None = None
        self._lifecycle_result_received = False
        self._lifecycle_thread_finished = False
        self._close_after_lifecycle = False
        self._close_in_progress = False
        self._launch_queue: ClientLaunchQueue | None = None
        self._resizing = False
        self._cursor_override_active = False
        self._accounts: list[Account] = []

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
        self._apply_runtime_settings()
        self._home_page.set_server_mode(self._effective_server_mode_label())

        # ── Wire signals ───────────────────────────────────────────────
        self._nav.page_changed.connect(self._switch_page)
        self._nav.server_toggled.connect(self._on_server_toggle)
        self._nav.market_toggled.connect(self._on_market_toggle)
        self._nav.kill_all_clicked.connect(self._kill_all_clients)

        self._home_page.launch_all_clicked.connect(self._launch_all)
        self._home_page.cancel_launches_clicked.connect(self._cancel_launch_queue)
        self._home_page.start_servers_clicked.connect(self._start_all_servers)
        self._home_page.stop_servers_clicked.connect(self._stop_all_servers)
        self._home_page.kill_all_clicked.connect(self._kill_all_clients)

        self._characters_page.launch_character.connect(self._on_character_launch)
        self._characters_page.hide_character.connect(self._on_hide_character)
        self._mods_page.apply_restart_clicked.connect(self._restart_server)

        self._status_bar.console_toggled.connect(self._on_console_toggled)
        self._home_page.console_requested.connect(self._on_console_toggled)

        # ── Update system ──────────────────────────────────────────────
        self._title_bar.update_clicked.connect(self._on_update_clicked)

        self._active_update_checkers: list[UpdateChecker] = []
        self._update_checker = self._create_update_checker()

        self._settings_page.settings_update_check.connect(self._on_manual_update_check)
        self._settings_page.settings_saved.connect(self._on_settings_saved)

        if self._cfg.get("update_auto_check", True):
            QTimer.singleShot(2000, self._start_automatic_update_check)

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
        self._settings_page = SettingsPage()
        self._stack.addWidget(self._home_page)        # Page.HOME = 0
        self._stack.addWidget(self._characters_page)  # Page.CHARACTERS = 1
        self._stack.addWidget(self._mods_page)        # Page.MODS = 2
        self._stack.addWidget(self._settings_page)    # Page.SETTINGS = 3
        content.addWidget(self._stack, 1)

        root.addLayout(content, 1)

        # Console overlay (child of central widget — floats above content)
        self._console_panel = ConsolePanel(central)
        self._console_panel.hide()

        # Status bar (bottom)
        self._status_bar = StatusBar(self)
        root.addWidget(self._status_bar)

    # ── Page switching with cross-fade ─────────────────────────────────

    def _switch_page(self, index: int) -> None:
        """Switch the center stack to a different page."""
        if self._stack.currentIndex() == index:
            return

        self._stack.setCurrentIndex(index)
        self._on_page_changed(index)

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
            self._settings_page.load_settings()

    def _apply_runtime_settings(self) -> None:
        """Apply persisted Home animation preferences without restarting the app."""
        try:
            interval_sec = int(self._cfg.get("hero_rotation_interval_sec", 6))
        except (TypeError, ValueError):
            interval_sec = 6
        hero = self._home_page.hero
        hero.set_rotation_interval(interval_sec)
        hero.set_animations_enabled(bool(self._cfg.get("animations_enabled", True)))

    # ── Server control ─────────────────────────────────────────────────

    @staticmethod
    def _server_mode_label(mode: str) -> str:
        """Return a private-safe presentation label for an explicit mode."""
        labels = {"vanilla": "Vanilla", "modded": "Modded"}
        return labels.get(mode.casefold(), "Unsupported")

    def _effective_server_mode_label(self) -> str:
        """Return the private-safe mode that the next start would resolve to."""
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
        if is_server_running(port=int(Ports.GAME_TCP)):
            self._stop_server()
        else:
            self._start_server()

    def _on_market_toggle(self) -> None:
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
        snapshot = self._build_runtime_snapshot()
        self._runtime_snapshot = snapshot
        self._apply_runtime_snapshot(snapshot)
        if getattr(self, "_service_monitor", None) is not None:
            self._service_probe_requested.emit()

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

    def _start_service_sequence(
        self,
        *,
        start_market: bool,
        start_game: bool,
        mode: str | None,
        on_ready: Callable[[], None] | None,
        error_title: str,
    ) -> bool:
        """Start requested services in a worker, waiting Market → Game readiness."""
        if self._lifecycle_active():
            log.info("Ignored service start while another lifecycle operation is active")
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
        self._begin_lifecycle_worker(worker, self._on_service_start_completed)
        self._publish_cached_runtime()
        return True

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
        callback = getattr(self, "_lifecycle_ready_callback", None)
        self._lifecycle_ready_callback = None
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
    ) -> bool:
        """Stop launcher-owned Game then Market processes in a worker."""
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
        worker = ServiceStopWorker(game_process, market_process)
        self._begin_lifecycle_worker(worker, self._on_service_stop_completed)
        self._publish_cached_runtime()
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
        callback = getattr(self, "_lifecycle_stop_callback", None)
        self._lifecycle_stop_callback = None
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
        self._publish_cached_runtime()
        self._lifecycle_result_received = True
        self._finish_lifecycle_if_complete()

    def _start_server(self) -> None:
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
        )

    def _stop_server(self) -> None:
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
        )

    def _restart_server(self) -> None:
        """Resolve the launch mode before stopping, then restart the server."""
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
        )

    def _stop_market(self) -> None:
        if self._market_proc is not None and self._market_proc.poll() is None:
            self._run_stop_sequence(
                stop_game=False,
                stop_market=True,
                on_complete=None,
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

        self._start_service_sequence(
            start_market=start_market,
            start_game=start_game,
            mode=resolved[0] if resolved is not None else None,
            on_ready=None,
            error_title="Service Startup Failed",
        )

    def _stop_all_servers(self) -> None:
        """Stop launcher-owned Game then Market in one ordered worker sequence."""
        self._run_stop_sequence(
            stop_game=True,
            stop_market=True,
            on_complete=None,
        )

    # ── Auto-start hook used before client launches ───────────────────

    def _ensure_server_if_needed(self, on_ready: Callable[[], None]) -> bool:
        """Invoke ``on_ready`` only after configured service auto-starts are ready."""
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

    def _launch_account(
        self,
        username: str,
        character_name: str,
        *,
        show_errors: bool = False,
    ) -> bool:
        """Launch one account through the shared single/bulk-launch path."""
        evejs_root = str(self._cfg.get("evejs_root", ""))
        client_path = str(self._cfg.get("client_path", ""))
        if not evejs_root or not client_path:
            if show_errors:
                QMessageBox.warning(
                    self,
                    "Not Configured",
                    "EveJS root or client path not set.",
                )
            return False
        if self._tracker.is_account_running(username):
            return False

        if not profile_exists(username):
            try:
                create_profile(username, client_path)
            except RuntimeError as exc:
                if show_errors:
                    QMessageBox.critical(self, "Profile Error", str(exc))
                else:
                    log.error("Profile creation failed for %s: %s", username, exc)
                return False

        profile_path = Path(PROFILES_ROOT) / username / "tq"
        if not profile_path.exists():
            message = "Profile junction not found."
            if show_errors:
                QMessageBox.critical(self, "Launch Error", message)
            else:
                log.error("Client launch skipped for %s: %s", username, message)
            return False

        # Pre-fill immediately before every client launch so each profile gets
        # the correct account username rather than a stale cached value.
        prefill_username(username)
        try:
            proc = launch_client(
                evejs_root=evejs_root,
                profile_tq_path=profile_path,
                proxy_url=str(self._cfg.get("proxy_url", "http://127.0.0.1:26002")),
                client_path=client_path,
            )
        except Exception as exc:  # noqa: BLE001 - subprocess errors vary by OS
            if show_errors:
                QMessageBox.critical(self, "Launch Error", str(exc))
            else:
                log.exception("Launch failed for %s", username)
            return False

        self._tracker.add(username, character_name, proc)
        log.info("Launched client for %s as %s (pid=%s)", username, character_name, proc.pid)
        threading.Thread(
            target=_restore_eve_window,
            args=("EVE",),
            daemon=True,
        ).start()
        return True

    def _on_character_launch(self, username: str, character_name: str) -> None:
        if self._tracker.is_account_running(username):
            running_character = self._tracker.get_running_character(username)
            QMessageBox.warning(
                self,
                "Account Already Running",
                f"Account '{username}' is already running character '{running_character}'.",
            )
            return
        if not self._ensure_server_if_needed(
            lambda: self._launch_character_after_services(username, character_name)
        ):
            return

    def _launch_character_after_services(
        self,
        username: str,
        character_name: str,
    ) -> None:
        """Launch one client only after its configured service gate is ready."""
        if self._launch_account(username, character_name, show_errors=True):
            self._refresh_characters()
            self._update_status_bar()

    def _launch_all(self) -> None:
        """Queue every visible, non-banned, non-running account serially."""
        if getattr(self, "_launch_queue", None) is not None:
            return
        evejs_root = self._cfg.get("evejs_root", "")
        client_path = self._cfg.get("client_path", "")
        if not evejs_root or not client_path:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS first.")
            return

        hidden = self._effective_hidden_characters()
        visible_rows = visible_character_rows(self._accounts, hidden)
        candidates: list[tuple[Account, Character]] = []
        seen_accounts: set[str] = set()
        for account, character in visible_rows:
            if account.username in seen_accounts:
                continue
            seen_accounts.add(account.username)
            if not self._tracker.is_account_running(account.username):
                candidates.append((account, character))
        if not candidates:
            self._refresh_character_views()
            return

        self._ensure_server_if_needed(
            lambda: self._begin_client_launch_queue(candidates)
        )

    def _begin_client_launch_queue(
        self,
        candidates: list[tuple[Account, Character]],
    ) -> None:
        """Create the serial Qt launch queue after required services are ready."""
        if self._launch_queue is not None:
            return
        stagger_seconds = max(0, int(self._cfg.get("stagger_delay_sec", 3)))
        queue = ClientLaunchQueue(
            candidates,
            lambda candidate: self._launch_account(
                candidate[0].username,
                candidate[1].name,
            ),
            stagger_ms=stagger_seconds * 1_000,
            parent=self,
        )
        self._launch_queue = queue
        queue.progress.connect(self._on_launch_queue_progress)
        queue.finished.connect(self._on_launch_queue_finished)
        self._home_page.set_launch_progress(0, len(candidates), 0)
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
        self._home_page.set_launch_progress(attempted, total, succeeded)
        self._refresh_character_views()
        self._update_status_bar()

    def _on_launch_queue_finished(
        self,
        attempted: int,
        succeeded: int,
        cancelled: bool,
    ) -> None:
        self._launch_queue = None
        self._home_page.finish_launch_progress(attempted, succeeded, cancelled)
        self._refresh_character_views()
        self._update_status_bar()
        if cancelled:
            message = (
                f"Launched {succeeded} client(s); remaining queued launches were cancelled."
            )
            QMessageBox.information(self, "Launch Cancelled", message)
        else:
            QMessageBox.information(
                self,
                "Launch Complete",
                f"Launched {succeeded} client(s).",
            )

    def _kill_all_clients(self) -> None:
        count = self._tracker.kill_all()
        self._refresh_characters()
        self._update_status_bar()
        if count > 0:
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
        """Reload account data, then refresh the views from the new snapshot."""
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            self._accounts = []
        else:
            try:
                self._accounts = load_accounts(evejs_root)
            except Exception:
                log.exception("Failed to load accounts")
                self._accounts = []
        self._refresh_character_views()

    def _refresh_character_views(self) -> None:
        """Refresh cards and dashboard metrics without another database read."""
        evejs_root = self._cfg.get("evejs_root", "")

        hidden = self._effective_hidden_characters(persist=True)
        rows = visible_character_rows(self._accounts, hidden)

        try:
            self._characters_page.refresh(
                self._accounts,
                sorted(hidden),
                self._tracker,
                evejs_root,
            )
        except Exception:
            log.exception("Characters page refresh failed")

        self._home_page.set_character_stats(
            visible_account_count(rows),
            len(rows),
        )
        visible_usernames = {account.username for account, _character in rows}
        eligible_usernames = {
            username
            for username in visible_usernames
            if not self._tracker.is_account_running(username)
        }
        if not evejs_root or not self._cfg.get("client_path", ""):
            self._home_page.set_launch_available(
                False,
                "Configure the EveJS root and EVE client path first",
            )
        elif not visible_usernames:
            self._home_page.set_launch_available(
                False,
                "No visible accounts available",
            )
        elif not eligible_usernames:
            self._home_page.set_launch_available(
                False,
                "All visible accounts are already running",
            )
        else:
            self._home_page.set_launch_available(True)

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

    @staticmethod
    def _service_action_text(
        service: str,
        state: ServiceState,
        owned: bool,
    ) -> str:
        if state is ServiceState.ONLINE and not owned:
            return f"{service}: External"
        labels = {
            ServiceState.OFFLINE: f"▶ Start {service}",
            ServiceState.STARTING: f"⏳ Starting {service}…",
            ServiceState.ONLINE: f"■ Stop {service}",
            ServiceState.STOPPING: f"⏳ Stopping {service}…",
            ServiceState.FAILED: f"↻ Retry {service}",
        }
        return labels[state]

    @staticmethod
    def _service_action_enabled(state: ServiceState, owned: bool) -> bool:
        """Keep launcher controls inactive for external or transitional services."""
        if state in {ServiceState.STARTING, ServiceState.STOPPING}:
            return False
        return state is not ServiceState.ONLINE or owned

    @staticmethod
    def _service_action_tooltip(service: str, state: ServiceState, owned: bool) -> str:
        """Explain why a service action is unavailable without exposing paths."""
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

    def _apply_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        """Fan one snapshot out to footer, navigation, and Home."""
        self._status_bar.set_server_state(snapshot.game, pid=snapshot.game_pid)
        self._status_bar.set_market_state(snapshot.market, pid=snapshot.market_pid)
        self._status_bar.set_client_count(snapshot.running_clients)

        self._nav.btn_server.setText(
            self._service_action_text(
                "Server",
                snapshot.game,
                snapshot.game_owned,
            )
        )
        self._nav.btn_market.setText(
            self._service_action_text(
                "Market",
                snapshot.market,
                snapshot.market_owned,
            )
        )
        self._nav.btn_server.setEnabled(
            self._service_action_enabled(snapshot.game, snapshot.game_owned)
        )
        self._nav.btn_market.setEnabled(
            self._service_action_enabled(snapshot.market, snapshot.market_owned)
        )
        self._nav.btn_server.setToolTip(
            self._service_action_tooltip(
                "Server",
                snapshot.game,
                snapshot.game_owned,
            )
        )
        self._nav.btn_market.setToolTip(
            self._service_action_tooltip(
                "Market",
                snapshot.market,
                snapshot.market_owned,
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

    def _on_service_probe(self, probe: ServiceProbe) -> None:
        """Receive one worker observation and fan it out without re-probing."""
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
        monitor = ServiceMonitor(interval_ms=5_000)
        monitor.moveToThread(thread)
        thread.started.connect(monitor.start)
        monitor.probe_changed.connect(self._on_service_probe)
        self._service_probe_requested.connect(monitor.probe_now)
        self._service_monitor_stop_requested.connect(monitor.stop)
        thread.finished.connect(monitor.deleteLater)
        thread.finished.connect(
            self._on_service_monitor_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._service_thread = thread
        self._service_monitor = monitor
        thread.start()

    @pyqtSlot()
    def _on_service_monitor_thread_finished(self) -> None:
        """Retry a deferred window close once the retained worker has stopped."""
        if self._close_in_progress and self._service_thread is not None:
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

    def _on_console_toggled(self, name: str) -> None:
        """StatusBar section click → show/hide console panel for that service."""
        if self._console_panel.isVisible():
            self._console_panel.stop()
            return

        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
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

    def _on_settings_saved(self, cfg: dict) -> None:
        """Refresh in-memory config and character grid after settings save."""
        previous_root = str(self._cfg.get("evejs_root", ""))
        self._cfg.update(cfg)
        if str(self._cfg.get("evejs_root", "")) != previous_root:
            clear_solar_system_name_cache()
            PortraitCache.clear()
            self._mods_page.refresh_mods()
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

    def _complete_deferred_close(self) -> None:
        """Request the final close only after its lifecycle worker is released."""
        if getattr(self, "_close_after_lifecycle", False):
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._update_install_worker is not None:
            event.ignore()
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
            event.accept()
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
                event.ignore()
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
        event.accept()
