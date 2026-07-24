"""Main application window for EveJS Launcher V2.

Composes the frameless top-level window:
    TitleBar (top)
    NavPanel | QStackedWidget (4 pages; CharactersPage owns its DetailPanel)
    StatusBar (bottom)
    ConsolePanel (overlay child of central widget)

Wires together nav, server, character launching, and process tracking.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PyQt6.QtCore import (
    QEvent,
    QObject,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor, QCursor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import config
from .constants import APP_TITLE, Page, Ports
from .core.db import Account, load_accounts
from .core.launcher import launch_client
from .core.platform import hard_exit
from .core.process_tracker import ProcessTracker
from .core.profiles import PROFILES_ROOT, create_profile, prefill_username, profile_exists
from .core.server_launcher import (
    detect_server_scripts,
    get_server_console_log,
    get_market_console_log,
    get_server_log_path,
    is_server_running,
    start_game_server,
    start_market_server,
)
from .pages.characters_page import CharactersPage
from .pages.home_page import HomePage
from .pages.mods_page import ModsPage
from .pages.settings_page import SettingsPage
from .utils.logger import setup_logger
from .widgets.console_panel import ConsolePanel
from .widgets.nav_panel import NavPanel
from .widgets.status_bar import ServiceState, StatusBar
from .widgets.title_bar import TitleBar
from .updater.checker import UpdateChecker
from .updater.dialog import UpdateDialog
from .updater.installer import download_and_install

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
        self._resizing = False
        self._cursor_override_active = False
        self._accounts: list[Account] = []

        # ── Update state ───────────────────────────────────────────────
        self._latest_version: str = ""
        self._latest_changelog: str = ""
        self._latest_download_url: str = ""
        self._latest_published: str = ""

        # ── Build UI ───────────────────────────────────────────────────
        self._build_ui()

        # ── Wire signals ───────────────────────────────────────────────
        self._nav.page_changed.connect(self._switch_page)
        self._nav.server_toggled.connect(self._on_server_toggle)
        self._nav.market_toggled.connect(self._on_market_toggle)
        self._nav.kill_all_clicked.connect(self._kill_all_clients)

        self._home_page.launch_all_clicked.connect(self._launch_all)
        self._home_page.start_servers_clicked.connect(self._start_all_servers)
        self._home_page.kill_all_clicked.connect(self._kill_all_clients)

        self._characters_page.launch_character.connect(self._on_character_launch)
        self._characters_page.hide_character.connect(self._on_hide_character)

        self._status_bar.console_toggled.connect(self._on_console_toggled)

        # ── Update system ──────────────────────────────────────────────
        self._title_bar.update_clicked.connect(self._on_update_clicked)

        self._update_checker = UpdateChecker(self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.up_to_date.connect(self._on_update_up_to_date)
        self._update_checker.check_failed.connect(
            lambda msg: log.warning("Update check failed: %s", msg)
        )

        self._settings_page.settings_update_check.connect(self._on_manual_update_check)
        self._settings_page.settings_saved.connect(self._on_settings_saved)

        if self._cfg.get("update_auto_check", True):
            QTimer.singleShot(2000, self._update_checker.check)

        interval_hours = int(self._cfg.get("update_check_interval_hours", 6))
        if interval_hours > 0:
            self._update_timer = QTimer(self)
            self._update_timer.timeout.connect(self._update_checker.check)
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
        central.installEventFilter(self)
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

    # ── Server control ─────────────────────────────────────────────────

    def _on_server_toggle(self) -> None:
        if is_server_running(port=int(Ports.GAME)):
            self._stop_server()
        else:
            self._start_server()

    def _on_market_toggle(self) -> None:
        if self._is_market_running():
            self._stop_market()
        else:
            self._start_market()

    def _start_server(self) -> None:
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS root in Settings first.")
            return
        try:
            self._server_proc = start_game_server(
                evejs_root, mode=self._cfg.get("server_mode", "modded")
            )
            log.info("Started game server (pid=%s)", self._server_proc.pid)
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to start game server")
            QMessageBox.critical(self, "Server Error", str(exc))
        self._update_status_bar()

    def _stop_server(self) -> None:
        if self._server_proc:
            self._graceful_kill(self._server_proc)
            self._server_proc = None
        else:
            self._kill_process_on_port(int(Ports.GAME))
        self._update_status_bar()

    def _start_market(self) -> None:
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS root in Settings first.")
            return
        try:
            self._market_proc = start_market_server(evejs_root)
            log.info("Started market server (pid=%s)", self._market_proc.pid)
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to start market server")
            QMessageBox.critical(self, "Market Error", str(exc))
        self._update_status_bar()

    def _stop_market(self) -> None:
        if self._market_proc:
            self._graceful_kill(self._market_proc)
            self._market_proc = None
        elif is_server_running(port=40111):
            # Market was started outside this launcher — can't kill safely
            QMessageBox.information(
                self, "Market Server",
                "The market server was started outside this launcher.\n\n"
                "Close it manually via Task Manager, or stop the game server\n"
                "and restart both through the launcher."
            )
            return
        self._update_status_bar()

    def _is_market_running(self) -> bool:
        # The actual market server listens on 40110/40111, NOT 26001.
        # Port 26001 is the game server's own market proxy endpoint.
        if self._market_proc is not None and self._market_proc.poll() is None:
            return True
        # Fallback: check the real market RPC port in case market was
        # started outside the launcher.
        return is_server_running(port=40111)

    def _start_all_servers(self) -> None:
        """Start Market then Game server (both auto-discover each other)."""
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS first.")
            return
        started = False
        if not self._is_market_running():
            try:
                self._market_proc = start_market_server(evejs_root)
                started = True
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Market Server Error", str(exc))
        if not is_server_running(port=int(Ports.GAME)):
            try:
                self._server_proc = start_game_server(
                    evejs_root, mode=self._cfg.get("server_mode", "modded")
                )
                started = True
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Game Server Error", str(exc))
        self._update_status_bar()
        if not started:
            QMessageBox.information(self, "Already Running", "Both servers are already online.")

    def _stop_all_servers(self) -> None:
        if self._server_proc and self._server_proc.poll() is None:
            self._stop_server()
        if self._market_proc and self._market_proc.poll() is None:
            self._stop_market()

    @staticmethod
    def _kill_process_on_port(port: int) -> None:
        """Find the process listening on a port and force-kill it using taskkill."""
        try:
            # Use netstat to find PID, then taskkill (avoids PowerShell dependency)
            result = subprocess.run(
                ["cmd", "/c", f"netstat -ano | findstr :{port}"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                if "LISTENING" in line:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", parts[-1]],
                            capture_output=True, timeout=5,
                        )
                        return
        except Exception:
            pass

    @staticmethod
    def _graceful_kill(proc: subprocess.Popen, timeout: int = 15) -> None:
        """Terminate a process gracefully, then force-kill after timeout."""
        pid = proc.pid
        if proc.poll() is not None:
            return

        # Step 1: Try gentle terminate first (works for most processes)
        try:
            proc.terminate()
        except Exception:
            pass

        # Step 2: Wait up to timeout for clean exit
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.5)

        # Step 3: Force kill if still running
        if proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

    # ── Auto-start hook used before client launches ───────────────────

    def _ensure_server_if_needed(self) -> None:
        """Auto-start server/market when config says so."""
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            return

        if self._cfg.get("auto_start_market", False) and not self._is_market_running():
            try:
                self._market_proc = start_market_server(evejs_root)
            except Exception:
                log.exception("Auto-start market failed")

        if self._cfg.get("auto_start_server", False) and not is_server_running(port=int(Ports.GAME)):
            try:
                mode = self._cfg.get("server_mode", "modded")
                if detect_server_scripts(evejs_root):
                    self._server_proc = start_game_server(evejs_root, mode=mode)
            except Exception:
                log.exception("Auto-start server failed")

    # ── Character launching ───────────────────────────────────────────

    def _on_character_launch(self, username: str, character_name: str) -> None:
        evejs_root = self._cfg.get("evejs_root", "")
        client_path = self._cfg.get("client_path", "")

        if not evejs_root or not client_path:
            QMessageBox.warning(self, "Not Configured", "EveJS root or client path not set.")
            return

        if self._tracker.is_account_running(username):
            rc = self._tracker.get_running_character(username)
            QMessageBox.warning(
                self, "Account Already Running",
                f"Account '{username}' is already running character '{rc}'."
            )
            return

        self._ensure_server_if_needed()

        if not profile_exists(username):
            try:
                create_profile(username, client_path)
            except RuntimeError as exc:
                QMessageBox.critical(self, "Profile Error", str(exc))
                return

        profile_path = Path(PROFILES_ROOT) / username / "tq"
        if not profile_path.exists():
            QMessageBox.critical(self, "Error", "Profile junction not found.")
            return

        # Pre-fill username so the EVE client shows it on the login screen.
        prefill_username(username)

        try:
            proc = launch_client(
                evejs_root=evejs_root,
                profile_tq_path=profile_path,
                proxy_url=self._cfg.get("proxy_url", "http://127.0.0.1:26002"),
                client_path=client_path,
            )
            self._tracker.add(username, character_name, proc)
            log.info("Launched client for %s as %s (pid=%s)", username, character_name, proc.pid)
        except FileNotFoundError as exc:
            QMessageBox.critical(self, "Launch Error", str(exc))
            return

        self._refresh_characters()
        self._update_status_bar()

        # ── Eve client window takes 10-15s to appear; restore it once visible ─
        threading.Thread(
            target=_restore_eve_window,
            args=("EVE",),
            daemon=True,
        ).start()

        # ── Serial launch: start next client after a stagger delay ──
        # (Auto-login feature has been removed — user types password manually.)

    def _launch_all(self) -> None:
        """Launch every visible, non-banned, non-running account (staggered)."""
        evejs_root = self._cfg.get("evejs_root", "")
        client_path = self._cfg.get("client_path", "")
        if not evejs_root or not client_path:
            QMessageBox.warning(self, "Not Configured", "Set up EveJS first.")
            return

        self._ensure_server_if_needed()

        stagger = max(0, int(self._cfg.get("stagger_delay_sec", 3)))
        launched = 0

        # Build the full hidden set — INCLUDES auto-hidden test/GM characters
        # (matching the logic in _refresh_characters so the UI and Launch All
        #  filter identically).
        hidden: set[str] = set(self._cfg.get("hidden_characters", []))
        if self._cfg.get("hide_test_characters", True):
            for account in self._accounts:
                ul = account.username.lower()
                if ul.startswith("test") or "gm" in ul:
                    for char in account.characters:
                        hidden.add(char.name)

        for account in self._accounts:
            if account.banned:
                continue
            if self._tracker.is_account_running(account.username) or not account.characters:
                continue

            # Find the first non-hidden character for this account.
            char = next((c for c in account.characters if c.name not in hidden), None)
            if char is None:
                continue  # all characters hidden
            if not profile_exists(account.username):
                try:
                    create_profile(account.username, client_path)
                except RuntimeError:
                    continue
            profile_path = Path(PROFILES_ROOT) / account.username / "tq"

            # Pre-fill the correct username for this account BEFORE launching —
            # otherwise the EVE client uses stale cached values from the real
            # client's settings, causing multiple clients to show the same name.
            prefill_username(account.username)

            try:
                proc = launch_client(
                    evejs_root,
                    profile_path,
                    client_path=client_path,
                )
                self._tracker.add(account.username, char.name, proc)
                launched += 1
                log.info("Launched client %d/%d: %s as %s",
                         launched, len(self._accounts), account.username, char.name)
            except Exception:
                log.exception("Launch failed for %s", account.username)
                continue

            # ── Serial launch: start next client after a stagger delay ──
            self._refresh_characters()
            self._update_status_bar()

            if stagger and launched < len(self._accounts):
                time.sleep(stagger)

        self._refresh_characters()
        self._update_status_bar()
        QMessageBox.information(self, "Launch Complete", f"Launched {launched} client(s).")

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

    def _refresh_characters(self) -> None:
        evejs_root = self._cfg.get("evejs_root", "")
        if not evejs_root:
            self._accounts = []
        else:
            try:
                self._accounts = load_accounts(evejs_root)
            except Exception:
                log.exception("Failed to load accounts")
                self._accounts = []

        hidden = list(self._cfg.get("hidden_characters", []))

        # ── Auto-hide characters belonging to test/GM accounts ───────────
        if self._cfg.get("hide_test_characters", True):
            never_hide: set[str] = set(self._cfg.get("never_hide_characters", []))
            cfg_changed = False
            for account in self._accounts:
                username_lower = account.username.lower()
                is_test = username_lower.startswith("test") or "gm" in username_lower
                if is_test:
                    for char in account.characters:
                        # Skip characters the user explicitly un-hid
                        if char.name in never_hide:
                            continue
                        if char.name not in hidden:
                            hidden.append(char.name)
                            # Persist to config so it shows up in Settings → Hidden Characters
                            if char.name not in self._cfg.get("hidden_characters", []):
                                self._cfg.setdefault("hidden_characters", []).append(char.name)
                                cfg_changed = True
            if cfg_changed:
                config.save(self._cfg)

        try:
            self._characters_page.refresh(self._accounts, hidden, self._tracker, evejs_root)
        except Exception:
            log.exception("Characters page refresh failed")

        # Update home page stats
        try:
            total_chars = sum(len(a.characters) for a in self._accounts if a.username not in hidden)
            visible = sum(1 for a in self._accounts if a.username not in hidden)
            if hasattr(self._home_page, "accounts_card"):
                self._home_page.accounts_card.set_value(str(visible))
            if hasattr(self._home_page, "characters_card"):
                self._home_page.characters_card.set_value(str(total_chars))
        except Exception:
            pass

    def _prune_and_update(self) -> None:
        if self._tracker.prune_dead() > 0:
            self._refresh_characters()
            self._update_status_bar()

    def _update_status_bar(self) -> None:
        sr = is_server_running(port=int(Ports.GAME))
        mr = self._is_market_running()
        cr = self._tracker.running_count

        server_proc_alive = self._server_proc is not None and self._server_proc.poll() is None
        server_starting = server_proc_alive and not sr

        # ── StatusBar dots ─────────────────────────────────────────────
        server_pid = self._server_proc.pid if server_proc_alive else None
        if sr:
            self._status_bar.set_server_state(ServiceState.ONLINE, pid=server_pid)
        elif server_starting:
            self._status_bar.set_server_state(ServiceState.STARTING)
        else:
            self._status_bar.set_server_state(ServiceState.OFFLINE)

        market_pid = self._market_proc.pid if (self._market_proc and self._market_proc.poll() is None) else None
        self._status_bar.set_market_state(
            ServiceState.ONLINE if mr else ServiceState.OFFLINE,
            pid=market_pid,
        )
        self._status_bar.set_client_count(cr)

        # ── NavPanel server/market button text ─────────────────────────
        if sr:
            self._nav.btn_server.setText("■ Stop Server")
        elif server_starting:
            self._nav.btn_server.setText("⏳ Starting Server…")
        else:
            self._nav.btn_server.setText("▶ Start Server")

        self._nav.btn_market.setText("■ Stop Market" if mr else "▶ Start Market")

        # ── Badge count on Characters nav button ───────────────────────
        try:
            self._nav.set_badge_count(int(Page.CHARACTERS), cr)
        except Exception:
            pass

        # ── Home page running-clients stat ─────────────────────────────
        try:
            if hasattr(self._home_page, "running_card"):
                self._home_page.running_card.set_value(str(cr))
        except Exception:
            pass

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
        if event.type() == QEvent.Type.MouseButtonPress:
            if self._console_panel.isVisible():
                pos = self._console_panel.mapFromGlobal(event.globalPosition().toPoint())
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
            # User clicked Download & Install
            current_exe = sys.executable
            success = download_and_install(self._latest_download_url, current_exe)
            if success:
                # Use hard_exit() for an immediate hard exit — no Qt cleanup,
                # no Python atexit handlers, no DLL unloading.  This prevents
                # transient "Failed to load Python DLL" dialogs that can appear
                # during graceful shutdown when PyInstaller's bootloader
                # unloads the Python runtime.
                from .core.platform import hard_exit
                hard_exit()

        elif dlg.skip_requested:
            # User clicked Skip This Version
            self._update_checker.skip_version(self._latest_version)
            self._title_bar.set_update_up_to_date()

    def _on_update_up_to_date(self, version: str = "") -> None:
        """Handler for when the app is already up to date."""
        self._title_bar.set_update_up_to_date()
        self._cfg["update_last_checked"] = datetime.now(timezone.utc).isoformat()
        config.save(self._cfg)
        self._settings_page.set_update_check_done(True)

    def _on_settings_saved(self, cfg: dict) -> None:
        """Refresh in-memory config and character grid after settings save."""
        self._cfg.update(cfg)
        self._refresh_characters()

    def _on_manual_update_check(self) -> None:
        """Triggered by the Settings page's 'Check for Updates' button."""
        # Visual feedback — title bar spinner + settings button shows checking
        self._title_bar.set_update_checking()
        self._settings_page.set_update_checking()

        # Create a fresh checker (QThread can only start once)
        checker = UpdateChecker(self)
        checker.update_available.connect(self._on_update_available)
        checker.up_to_date.connect(self._on_update_up_to_date)
        checker.up_to_date.connect(lambda v="": self._settings_page.set_update_check_done(True))
        checker.check_failed.connect(lambda msg: self._on_check_failed_from_settings(msg))
        checker.check()

    def _on_check_failed_from_settings(self, error: str) -> None:
        """Handle a failed check triggered from Settings."""
        log.warning("Manual update check failed: %s", error)
        self._title_bar.set_update_up_to_date()
        self._settings_page.set_update_check_done(False)

    # ── Resize / close lifecycle ──────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._console_panel.isVisible() and hasattr(self._console_panel, "_reposition"):
            self._console_panel._reposition()

    def closeEvent(self, event) -> None:  # noqa: N802
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

        # Graceful server shutdown — game first, then market
        self._stop_all_servers()
        event.accept()
