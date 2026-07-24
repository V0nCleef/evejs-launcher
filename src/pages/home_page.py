"""Home page for EveJS Launcher V2.

Layout
------
+---------------------------------------------------------------+
|  HeroBanner (200 px)                                          |
+---------------------------------------------------------------+
|  [Accounts] [Characters] [Running Clients] [Server Status]    |
+---------------------------------------------------------------+
|  [Launch All]  [Start All Servers]  [Kill All]                |
+-------------------------------+-------------------------------+
|  Changelog (60 %)             |  Discord card (40 %)          |
+-------------------------------+-------------------------------+
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.constants import COLORS
from src.widgets.hero_banner import HeroBanner

DISCORD_INVITE_URL = "https://discord.gg/HVTfKeqX3t"
_CHANGELOG_PATH = Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md"


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
    """Mini card showing a green/grey status dot plus Online/Offline text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(84)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {COLORS['grey']}; font-size: 22px;")
        row.addWidget(self._dot)

        self._state_label = QLabel("Offline")
        self._state_label.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 22px; font-weight: 700;"
        )
        row.addWidget(self._state_label)
        row.addStretch()
        layout.addLayout(row)

        name_label = QLabel("SERVER STATUS")
        name_label.setProperty("class", "muted")
        layout.addWidget(name_label)

    def set_online(self, online: bool) -> None:
        color = COLORS["green"] if online else COLORS["grey"]
        self._dot.setStyleSheet(f"color: {color}; font-size: 22px;")
        self._state_label.setText("Online" if online else "Offline")


class DiscordCard(QFrame):
    """EveJS-branded Discord invite card (teal theme)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            DiscordCard {{
                background-color: {COLORS['carbon']};
                border: 1px solid {COLORS['steel']};
                border-radius: 6px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        # EveJS logo icon
        _logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.png"
        icon_label = QLabel()
        icon_label.setFixedSize(48, 48)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if _logo_path.exists():
            from PyQt6.QtGui import QPixmap
            pix = QPixmap(str(_logo_path)).scaled(
                48, 48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon_label.setPixmap(pix)
        else:
            icon_label.setText("⬡")
            icon_label.setStyleSheet(
                f"color: {COLORS['teal']}; font-size: 34px; background: transparent;"
            )
        layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("Join the EveJS Community")
        title.setStyleSheet(
            f"color: {COLORS['white']}; font-size: 17px; font-weight: 700; background: transparent;"
        )
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        blurb = QLabel(
            "Get help, share fits, and follow development on the official Discord server."
        )
        blurb.setStyleSheet(
            f"color: {COLORS['grey']}; font-size: 12px; background: transparent;"
        )
        blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(blurb)

        layout.addStretch()

        join_btn = QPushButton("Open Discord")
        join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        join_btn.setFixedHeight(36)
        join_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLORS['teal']};
                color: {COLORS['void_black']};
                border: none;
                border-radius: 4px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background-color: {COLORS['teal_dim']}; }}
            """
        )
        join_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(DISCORD_INVITE_URL))
        )
        layout.addWidget(join_btn)


class HomePage(QWidget):
    """Landing page with hero banner, stats, quick actions, changelog, Discord."""

    launch_all_clicked = pyqtSignal()
    start_servers_clicked = pyqtSignal()
    kill_all_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_changelog()

    # ── UI construction ──────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 0, 16, 16)
        root.setSpacing(14)

        # Hero banner
        self.hero = HeroBanner(self)
        self.hero.setFixedHeight(200)
        root.addWidget(self.hero)

        # Stats row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.accounts_card = StatCard("Accounts")
        self.characters_card = StatCard("Characters")
        self.running_card = StatCard("Running Clients")
        self.server_card = ServerStatusCard()
        for card in (
            self.accounts_card,
            self.characters_card,
            self.running_card,
            self.server_card,
        ):
            stats_row.addWidget(card)
        root.addLayout(stats_row)

        # Quick actions row
        actions = QHBoxLayout()
        actions.setSpacing(12)

        self.btn_launch_all = QPushButton("Launch All")
        self.btn_launch_all.setProperty("class", "primary")
        self.btn_launch_all.setFixedHeight(48)
        self.btn_launch_all.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_launch_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch_all.clicked.connect(self.launch_all_clicked.emit)
        actions.addWidget(self.btn_launch_all)

        self.btn_start_servers = QPushButton("Start All Servers")
        self.btn_start_servers.setProperty("class", "primary")
        self.btn_start_servers.setFixedHeight(48)
        self.btn_start_servers.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_start_servers.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start_servers.clicked.connect(self.start_servers_clicked.emit)
        actions.addWidget(self.btn_start_servers)

        self.btn_kill_all = QPushButton("Kill All")
        self.btn_kill_all.setProperty("class", "danger")
        self.btn_kill_all.setFixedHeight(48)
        self.btn_kill_all.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_kill_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_kill_all.clicked.connect(self.kill_all_clicked.emit)
        actions.addWidget(self.btn_kill_all)

        root.addLayout(actions)

        # Split panel: changelog (60%) + discord (40%)
        split = QHBoxLayout()
        split.setSpacing(14)

        changelog_frame = QFrame()
        changelog_frame.setProperty("class", "card")
        changelog_layout = QVBoxLayout(changelog_frame)
        changelog_layout.setContentsMargins(16, 12, 16, 12)
        changelog_layout.setSpacing(8)
        changelog_title = QLabel("CHANGELOG")
        changelog_title.setStyleSheet(
            f"color: {COLORS['teal']}; font-size: 13px; font-weight: 700;"
        )
        changelog_layout.addWidget(changelog_title)

        self.changelog_view = QTextEdit()
        self.changelog_view.setReadOnly(True)
        self.changelog_view.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {COLORS['deep_space']};
                color: {COLORS['white']};
                border: 1px solid {COLORS['steel']};
                border-radius: 4px;
                padding: 8px;
            }}
            """
        )
        changelog_layout.addWidget(self.changelog_view)
        split.addWidget(changelog_frame, stretch=60)

        self.discord_card = DiscordCard()
        split.addWidget(self.discord_card, stretch=40)

        root.addLayout(split, stretch=1)

    # ── Data ─────────────────────────────────────────────────────────────────
    def _load_changelog(self) -> None:
        """Load CHANGELOG.md from the repo root into the read-only viewer."""
        try:
            if _CHANGELOG_PATH.exists():
                text = _CHANGELOG_PATH.read_text(encoding="utf-8")
            else:
                text = "_No changelog found._"
        except OSError:
            text = "_Failed to load changelog._"
        self.changelog_view.setMarkdown(text)

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

    def set_server_online(self, online: bool) -> None:
        """Update only the server status mini card."""
        self.server_card.set_online(online)
