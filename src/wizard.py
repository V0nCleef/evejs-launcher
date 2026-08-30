"""First-run setup wizard — prompts for EveJS installation path on initial launch."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, QThread, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import load, save
from .constants import COLORS as C, SEMANTIC_COLORS as S, SPACING
from .i18n import (
    LANGUAGES,
    current_language,
    format_ui_phrase,
    set_language,
    translate,
    translate_discovery_diagnostic,
    translate_ui_phrase,
)
from .core.discovery import (
    find_client_path,
    resolve_client_tq_path,
    validate_docker_evejs_root,
    validate_evejs_root,
)
from .core.runtime.docker_setup import (
    DockerPreflightRequest,
    DockerPreflightResult,
    DockerSetupDraft,
    create_preflight_request,
    docker_draft_fingerprint,
)
from .workers.docker_preflight_worker import DockerPreflightWorker
from .widgets.deep_signal_background import DeepSignalBackground
from .widgets.glass_panel import GlassPanel
from .widgets.localized_dialogs import LocalizedFileDialog as QFileDialog
from .widgets.page_header import PageHeader
from .widgets.status_bar import _language_flag_icon
from .widgets.ui_translation import (
    register_translatable_widget_tree,
    retranslate_widget_tree,
    set_translatable_accessible_description,
    set_translatable_text,
    set_translatable_text_template,
    set_translatable_tooltip,
    set_translatable_tooltip_template,
)


NATIVE_RUNTIME_HELP = (
    "Runs the EveJS Game and Market services directly on Windows from the "
    "selected EveJS folder. Docker Desktop is not required."
)
DOCKER_RUNTIME_HELP = (
    "Uses an existing EveJS Compose project through Docker Desktop in "
    "Linux-container mode."
)
RUNTIME_DATA_NOTICE = (
    "Changing runtime does not move characters, market data, or server data."
)
COMPOSE_FILE_HELP = (
    "Recommended: leave this blank. The launcher automatically uses compose.yaml "
    "from EveJS Root. Select a file only when it has a different name or location."
)
PROJECT_NAME_HELP = (
    "Most users should leave this blank. Set a project name only to reconnect to "
    "a stack created with a custom -p name, keep a stable name after moving the "
    "folder, or separate multiple stacks. Changing it may target a different "
    "Docker stack. If set, use lowercase ASCII letters, digits, hyphens, and "
    "underscores, starting with a letter or digit."
)
CONNECT_ONLY_HELP = (
    "Connect only: the launcher shows status and logs but never starts, stops, "
    "or changes the Docker stack."
)
MANAGED_HELP = (
    "Managed: the launcher can start, stop, restart, and maintain this Docker stack."
)
DOCKER_TEST_HELP = (
    "Testing is read-only. It checks Docker and the Compose project without "
    "starting containers or initializing data."
)


_WIZARD_STEP_LABELS = (
    "STEP 01 / 04   WELCOME",
    "STEP 02 / 04   RUNTIME",
    "STEP 03 / 04   VERIFY",
    "STEP 04 / 04   READY",
)

_DOCKER_REVIEW_TEMPLATE = (
    "Runtime: Docker Compose\n"
    "EveJS Root: {evejs_root}\n"
    "Compose File: {compose_path}{compose_suffix}\n"
    "Control: {policy}\n"
    "Project Name: {project}\n"
    "CLIENT Path: {client_path}\n\n"
    "Docker configuration is valid. Game-data initialization is a separate "
    "confirmed action. Market seeding/rebuild is also separate and is never "
    "selected or run automatically.\n\n"
    "Click Next to review completion."
)

_NATIVE_REVIEW_TEMPLATE = (
    "Runtime: Native — directly on Windows\n"
    "EveJS Root: {evejs_root}\n"
    "CLIENT Path: {client_path}\n\n"
    "Click Next to save these settings."
)


def _wizard_qss() -> str:
    """Return the self-contained Deep Signal treatment for first run.

    The application theme is already installed in production, but the wizard
    also appears in focused tests and maintenance tools.  Keeping the compact
    dialog-specific rules here makes those entry points visually deterministic
    while still consuming the same semantic colour contract as the main UI.
    """
    return f"""
        QDialog#setupWizard {{
            background-color: {S['background']};
        }}
        QWidget#setupWizardShell,
        QWidget[deepSignal="true"],
        QStackedWidget#wizardStack,
        QScrollArea#wizardPageScroll,
        QScrollArea#wizardPageScroll > QWidget > QWidget {{
            background-color: transparent;
        }}
        QScrollArea#wizardPageScroll {{
            border: none;
        }}
        QLabel {{
            background-color: transparent;
            color: {S['text_primary']};
        }}
        QFrame[class="glassPanel"] {{
            background-color: rgba(8, 20, 31, 224);
            border: 1px solid {S['border']};
            border-radius: 12px;
        }}
        QFrame[class="glassPanel"][variant="quiet"] {{
            background-color: rgba(7, 17, 29, 196);
            border-color: rgba(52, 88, 106, 170);
        }}
        QLabel[class="pageEyebrow"] {{
            color: {S['accent']};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        QLabel[class="pageTitle"] {{
            color: {S['text_primary']};
            font-size: 25px;
            font-weight: 600;
        }}
        QLabel[class="pageSubtitle"] {{
            color: {S['text_secondary']};
            font-size: 12px;
        }}
        QFrame[class="wizardSection"] {{
            background-color: rgba(5, 14, 23, 178);
            border: 1px solid {S['border']};
            border-radius: 8px;
        }}
        QLabel[class="wizardSectionTitle"] {{
            color: {S['accent']};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        QLabel[class="wizardFieldLabel"] {{
            color: {S['text_secondary']};
            font-size: 11px;
            font-weight: 600;
        }}
        QLabel[class="wizardHint"] {{
            color: {S['text_muted']};
            font-size: 11px;
        }}
        QLabel[class="wizardHint"][tone="warning"] {{
            color: {S['warning']};
        }}
        QLabel[class="wizardStatus"] {{
            color: {S['text_muted']};
            font-size: 11px;
            padding: 2px 0;
        }}
        QLabel[class="wizardStatus"][state="ready"] {{
            color: {S['success']};
        }}
        QLabel[class="wizardStatus"][state="busy"],
        QLabel[class="wizardStatus"][state="notice"] {{
            color: {S['warning']};
        }}
        QLabel[class="wizardStatus"][state="error"] {{
            color: {S['danger']};
        }}
        QLabel[class="wizardMilestone"] {{
            background-color: rgba(7, 17, 29, 160);
            border: 1px solid {S['border']};
            border-radius: 7px;
            color: {S['text_secondary']};
            padding: 11px 13px;
            font-size: 12px;
        }}
        QLabel[class="wizardReview"] {{
            background-color: rgba(5, 14, 23, 190);
            border: 1px solid {S['border_bright']};
            border-radius: 8px;
            color: {S['text_secondary']};
            padding: 15px;
            font-size: 12px;
        }}
        QLabel[class="wizardCheck"] {{
            background-color: rgba(79, 224, 127, 18);
            border: 1px solid rgba(79, 224, 127, 80);
            border-radius: 7px;
            color: {S['success']};
            padding: 10px 13px;
            font-size: 12px;
            font-weight: 600;
        }}
        QLineEdit,
        QComboBox {{
            min-height: 24px;
            background-color: rgba(5, 13, 22, 220);
            border: 1px solid {S['border_bright']};
            border-radius: 6px;
            color: {S['text_primary']};
            padding: 6px 10px;
            selection-background-color: {S['accent_dim']};
        }}
        QLineEdit:hover,
        QComboBox:hover {{
            border-color: {S['accent_dim']};
        }}
        QLineEdit:focus,
        QComboBox:focus {{
            border-color: {S['accent']};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 26px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {S['surface']};
            border: 1px solid {S['border_bright']};
            color: {S['text_primary']};
            selection-background-color: {S['accent_dim']};
            selection-color: {S['background']};
        }}
        QPushButton[class="signalPrimary"] {{
            min-height: 20px;
            background-color: {S['accent']};
            border: 1px solid {S['accent']};
            border-radius: 6px;
            color: {S['background']};
            padding: 8px 18px;
            font-weight: 700;
        }}
        QPushButton[class="signalPrimary"]:hover {{
            background-color: {S['text_primary']};
            border-color: {S['text_primary']};
        }}
        QPushButton[class="signalSecondary"] {{
            min-height: 20px;
            background-color: rgba(7, 17, 29, 196);
            border: 1px solid {S['border_bright']};
            border-radius: 6px;
            color: {S['text_primary']};
            padding: 8px 15px;
            font-weight: 600;
        }}
        QPushButton[class="signalSecondary"]:hover {{
            background-color: rgba(22, 74, 87, 150);
            border-color: {S['accent']};
        }}
        QPushButton[class="signalPrimary"]:disabled,
        QPushButton[class="signalSecondary"]:disabled {{
            background-color: {S['surface']};
            border-color: {S['surface_elevated']};
            color: {S['text_muted']};
        }}
        QCheckBox {{
            color: {S['text_secondary']};
            spacing: 8px;
        }}
        QCheckBox:disabled {{
            color: {S['text_muted']};
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            background-color: {S['surface']};
            border: 1px solid {S['border_bright']};
            border-radius: 3px;
        }}
        QCheckBox::indicator:checked {{
            background-color: {S['accent']};
            border-color: {S['accent']};
        }}
        QFrame#wizardNavigation {{
            background-color: rgba(4, 11, 18, 236);
            border: none;
            border-top: 1px solid {S['border']};
        }}
        QLabel#wizardStepLabel {{
            color: {S['text_secondary']};
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        }}
        QProgressBar#wizardProgress {{
            min-height: 5px;
            max-height: 5px;
            background-color: {S['surface_elevated']};
            border: none;
            border-radius: 2px;
        }}
        QProgressBar#wizardProgress::chunk {{
            background-color: {S['accent']};
            border-radius: 2px;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 7px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {S['border_bright']};
            border-radius: 3px;
            min-height: 24px;
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0;
        }}
    """


class _WizardPage(QWidget):
    """A scroll-safe Deep Signal page with one focused glass surface."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        eyebrow: str = "SETUP SEQUENCE",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("deepSignal", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING["xl"] + 8,
            SPACING["xl"],
            SPACING["xl"] + 8,
            SPACING["lg"],
        )
        layout.setSpacing(SPACING["lg"])

        self.header = PageHeader(title, subtitle, eyebrow)
        layout.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("wizardPageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)

        body = QWidget()
        body.setProperty("deepSignal", True)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, SPACING["xs"], 0)
        body_layout.setSpacing(0)

        self.panel = GlassPanel(variant="quiet", padding=SPACING["lg"])
        self.content = self.panel.content_layout
        self.content.setSpacing(SPACING["md"])
        body_layout.addWidget(self.panel)
        body_layout.addStretch(1)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)


class SetupWizard(QDialog):
    """Modal wizard shown when no EveJS root is configured."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("setupWizard")
        self.setWindowTitle("EveJS Launcher — Setup")
        self.resize(820, 720)
        # Native Windows font metrics need a little more room than Qt's
        # offscreen backend.  Keep the declared minimum above the composed
        # layout's size hint so the Deep Signal headings never elide.
        self.setMinimumSize(700, 560)
        self.setWindowFlags(Qt.WindowType.Dialog)
        self.setStyleSheet(_wizard_qss())

        self._evejs_root = ""
        self._client_path = ""
        self._review_route: str | None = None
        self._docker_preflight_token = 0
        self._docker_preflight_thread: QThread | None = None
        self._docker_preflight_worker: DockerPreflightWorker | None = None
        self._docker_preflight_result_received = False
        self._docker_preflight_thread_finished = False
        self._validated_docker_fingerprint: str | None = None
        self._close_after_docker_preflight = False
        self._build()
        register_translatable_widget_tree(self)

    # ── UI ────────────────────────────────────────────────────────────

    def _build(self) -> None:
        layers = QStackedLayout(self)
        layers.setContentsMargins(0, 0, 0, 0)
        layers.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self._signal_background = DeepSignalBackground(self, seed=8_104)
        layers.addWidget(self._signal_background)

        shell = QWidget(self)
        shell.setObjectName("setupWizardShell")
        shell.setProperty("deepSignal", True)
        root = QVBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        layers.addWidget(shell)
        layers.setCurrentWidget(shell)

        # Stacked pages
        self._stack = QStackedWidget()
        self._stack.setObjectName("wizardStack")

        # Page 0: Welcome
        p0 = _WizardPage(
            "Welcome to EveJS Launcher",
            "This tool manages your local EveJS services and EVE clients.\n\n"
            "You will choose whether EveJS runs directly on Windows or through "
            "Docker Desktop. The launcher never switches this choice automatically.",
            eyebrow="DEEP SIGNAL // INITIALIZATION 01",
        )
        welcome_intro = QLabel(
            "Establish the launcher connection in three short checks. "
            "Nothing is started or changed until setup is complete."
        )
        welcome_intro.setProperty("class", "wizardHint")
        welcome_intro.setWordWrap(True)
        self._allow_label_shrink(welcome_intro)
        p0.content.addWidget(welcome_intro)
        for milestone in (
            "01   Choose the runtime that owns your EveJS services",
            "02   Locate the project and optional copied EVE client",
            "03   Verify the route, then save the launcher profile",
        ):
            item = QLabel(milestone)
            item.setProperty("class", "wizardMilestone")
            item.setWordWrap(True)
            self._allow_label_shrink(item)
            p0.content.addWidget(item)
        self._stack.addWidget(p0)

        # Page 1: runtime and path selection
        p1 = _WizardPage(
            "Choose Your EveJS Runtime",
            "Choose how EveJS runs, then select the matching EveJS project folder.",
            eyebrow="DEEP SIGNAL // RUNTIME ROUTE 02",
        )

        p1.content.addWidget(self._make_section_label("RUNTIME MODE"))

        backend_row = QHBoxLayout()
        backend_row.setSpacing(SPACING["md"])
        backend_row.addWidget(self._make_field_label("How should EveJS run?"))
        self._backend_combo = QComboBox()
        self._allow_control_shrink(self._backend_combo)
        self._backend_combo.setAccessibleName("EveJS runtime")
        self._backend_combo.addItem(
            "Native — run directly on Windows",
            "native",
        )
        self._backend_combo.addItem(
            "Docker Compose — use Docker Desktop",
            "docker_compose",
        )
        self._backend_combo.setToolTip(NATIVE_RUNTIME_HELP)
        self._backend_combo.setAccessibleDescription(NATIVE_RUNTIME_HELP)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        backend_row.addWidget(self._backend_combo, 1)
        p1.content.addLayout(backend_row)

        self._backend_help = self._make_help_label(NATIVE_RUNTIME_HELP)
        self._backend_help.setObjectName("wizardRuntimeHelp")
        p1.content.addWidget(self._backend_help)
        self._runtime_data_notice = self._make_help_label(
            RUNTIME_DATA_NOTICE,
            color=C["gold"],
        )
        self._runtime_data_notice.setObjectName("wizardRuntimeDataNotice")
        p1.content.addWidget(self._runtime_data_notice)

        p1.content.addSpacing(SPACING["xs"])
        p1.content.addWidget(self._make_section_label("PROJECT PATHS"))

        ph = QHBoxLayout()
        ph.setSpacing(SPACING["sm"])
        ph.addWidget(self._make_field_label("EveJS Root:"))
        self._path_input = QLineEdit()
        self._allow_control_shrink(self._path_input)
        self._path_input.setAccessibleName("EveJS root folder")
        self._path_input.setPlaceholderText("Select your EveJS project folder")
        self._path_input.textChanged.connect(self._on_path_changed)
        ph.addWidget(self._path_input, 1)

        browse = QPushButton("Browse…")
        browse.setProperty("class", "signalSecondary")
        browse.clicked.connect(self._browse)
        ph.addWidget(browse)
        p1.content.addLayout(ph)

        self._path_status = QLabel("Enter the path to your EveJS folder")
        self._path_status.setProperty("class", "wizardStatus")
        self._path_status.setProperty("state", "idle")
        self._path_status.setWordWrap(True)
        self._allow_label_shrink(self._path_status)
        p1.content.addWidget(self._path_status)

        client_row = QHBoxLayout()
        client_row.setSpacing(SPACING["sm"])
        client_row.addWidget(self._make_field_label("EVE Client:"))
        self._client_input = QLineEdit()
        self._allow_control_shrink(self._client_input)
        self._client_input.setAccessibleName("Copied EVE client folder")
        self._client_input.setPlaceholderText("Optional copied EVE client tq folder")
        self._client_input.textChanged.connect(self._invalidate_docker_preflight)
        client_row.addWidget(self._client_input, 1)
        client_browse = QPushButton("Browse…")
        client_browse.setProperty("class", "signalSecondary")
        client_browse.clicked.connect(self._browse_client)
        client_row.addWidget(client_browse)
        p1.content.addLayout(client_row)

        self._docker_fields = QFrame()
        self._docker_fields.setProperty("class", "wizardSection")
        docker_layout = QVBoxLayout(self._docker_fields)
        docker_layout.setContentsMargins(
            SPACING["md"], SPACING["md"], SPACING["md"], SPACING["md"]
        )
        docker_layout.setSpacing(SPACING["sm"])
        docker_layout.addWidget(self._make_section_label("DOCKER LINK"))

        compose_row = QHBoxLayout()
        compose_row.setSpacing(SPACING["sm"])
        compose_row.addWidget(self._make_field_label("Compose File (optional):"))
        self._compose_input = QLineEdit()
        self._allow_control_shrink(self._compose_input)
        self._compose_input.setAccessibleName("Docker Compose file")
        self._compose_input.setPlaceholderText(
            "Leave blank to use <EveJS Root>\\compose.yaml"
        )
        self._compose_input.textChanged.connect(self._invalidate_docker_preflight)
        self._compose_input.textChanged.connect(
            lambda _text: self._update_docker_guidance()
        )
        self._compose_input.setToolTip(COMPOSE_FILE_HELP)
        self._compose_input.setAccessibleDescription(COMPOSE_FILE_HELP)
        compose_row.addWidget(self._compose_input, 1)
        compose_browse = QPushButton("Browse…")
        compose_browse.setProperty("class", "signalSecondary")
        compose_browse.clicked.connect(self._browse_compose)
        compose_row.addWidget(compose_browse)
        docker_layout.addLayout(compose_row)

        self._compose_help = self._make_help_label(COMPOSE_FILE_HELP)
        self._compose_help.setObjectName("wizardComposeHelp")
        docker_layout.addWidget(self._compose_help)
        self._compose_resolved = self._make_help_label(
            "Using: <EveJS Root>\\compose.yaml after a root is selected "
            "(automatic)"
        )
        self._compose_resolved.setObjectName("wizardComposeResolved")
        docker_layout.addWidget(self._compose_resolved)

        policy_row = QHBoxLayout()
        policy_row.setSpacing(SPACING["sm"])
        policy_row.addWidget(self._make_field_label("Control Policy:"))
        self._policy_combo = QComboBox()
        self._allow_control_shrink(self._policy_combo)
        self._policy_combo.setAccessibleName("Docker control policy")
        self._policy_combo.addItem(
            "Connect only — observe an existing stack",
            "connect_only",
        )
        self._policy_combo.addItem(
            "Managed — launcher controls the stack (recommended)",
            "managed",
        )
        self._policy_combo.setToolTip(CONNECT_ONLY_HELP)
        self._policy_combo.setAccessibleDescription(CONNECT_ONLY_HELP)
        self._policy_combo.currentIndexChanged.connect(
            self._invalidate_docker_preflight
        )
        self._policy_combo.currentIndexChanged.connect(
            lambda _index: self._update_docker_guidance()
        )
        policy_row.addWidget(self._policy_combo, 1)
        docker_layout.addLayout(policy_row)
        self._policy_help = self._make_help_label(CONNECT_ONLY_HELP)
        self._policy_help.setObjectName("wizardPolicyHelp")
        docker_layout.addWidget(self._policy_help)

        self._keep_running_check = QCheckBox("Keep stack running on exit")
        self._keep_running_check.setChecked(True)
        self._keep_running_check.setEnabled(False)
        self._keep_running_check.toggled.connect(
            self._invalidate_docker_preflight
        )
        docker_layout.addWidget(self._keep_running_check)

        self._advanced_toggle = QCheckBox("Show advanced Docker options")
        self._advanced_toggle.setObjectName("wizardAdvancedToggle")
        self._advanced_toggle.toggled.connect(
            lambda _checked: self._update_docker_guidance()
        )
        docker_layout.addWidget(self._advanced_toggle)

        self._advanced_docker_fields = QWidget()
        self._advanced_docker_fields.setProperty("deepSignal", True)
        advanced_layout = QVBoxLayout(self._advanced_docker_fields)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)
        project_row = QHBoxLayout()
        project_row.setSpacing(SPACING["sm"])
        project_row.addWidget(
            self._make_field_label("Compose Project Name (optional):")
        )
        self._project_input = QLineEdit()
        self._allow_control_shrink(self._project_input)
        self._project_input.setAccessibleName("Docker Compose project name")
        self._project_input.setPlaceholderText(
            "Optional — example: evejs-local"
        )
        self._project_input.setToolTip(PROJECT_NAME_HELP)
        self._project_input.setAccessibleDescription(PROJECT_NAME_HELP)
        self._project_input.textChanged.connect(self._invalidate_docker_preflight)
        project_row.addWidget(self._project_input, 1)
        advanced_layout.addLayout(project_row)
        self._project_help = self._make_help_label(PROJECT_NAME_HELP)
        self._project_help.setObjectName("wizardProjectHelp")
        advanced_layout.addWidget(self._project_help)
        self._advanced_docker_fields.hide()
        docker_layout.addWidget(self._advanced_docker_fields)

        self._test_docker_btn = QPushButton("Test Docker setup")
        self._test_docker_btn.setProperty("class", "signalSecondary")
        self._test_docker_btn.setAccessibleDescription(DOCKER_TEST_HELP)
        self._test_docker_btn.clicked.connect(self._start_docker_preflight)
        docker_layout.addWidget(self._test_docker_btn)
        self._test_docker_help = self._make_help_label(DOCKER_TEST_HELP)
        self._test_docker_help.setObjectName("wizardDockerTestHelp")
        docker_layout.addWidget(self._test_docker_help)
        self._docker_status = QLabel("")
        self._docker_status.setProperty("class", "wizardStatus")
        self._docker_status.setProperty("state", "idle")
        self._docker_status.setWordWrap(True)
        self._allow_label_shrink(self._docker_status)
        docker_layout.addWidget(self._docker_status)
        self._docker_fields.hide()
        p1.content.addWidget(self._docker_fields)
        self._stack.addWidget(p1)

        # Page 2: Validation
        p2 = _WizardPage(
            "Installation Verified",
            "We found a working EveJS installation. Here's what was detected:",
            eyebrow="DEEP SIGNAL // CONFIGURATION REVIEW 03",
        )
        self._review_badge = QLabel("ROUTE VERIFIED")
        self._review_badge.setProperty("class", "wizardSectionTitle")
        p2.content.addWidget(self._review_badge)
        self._results = QLabel("")
        self._results.setProperty("class", "wizardReview")
        self._results.setWordWrap(True)
        self._allow_label_shrink(self._results)
        self._results.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        p2.content.addWidget(self._results)
        self._stack.addWidget(p2)

        # Page 3: Done
        p3 = _WizardPage(
            "Ready!",
            "Setup is complete. The launcher will scan your accounts and show your characters.",
            eyebrow="DEEP SIGNAL // CONNECTION READY 04",
        )
        for line in (
            "✓ EveJS project validated",
            "✓ Runtime settings reviewed",
            "✓ Accounts will be loaded",
        ):
            check = QLabel(line)
            check.setProperty("class", "wizardCheck")
            p3.content.addWidget(check)
        self._stack.addWidget(p3)

        root.addWidget(self._stack, 1)

        # Bottom nav bar
        nav = QFrame()
        nav.setObjectName("wizardNavigation")
        nav.setFixedHeight(76)
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(32, 10, 32, 10)
        nl.setSpacing(SPACING["sm"])

        progress_copy = QVBoxLayout()
        progress_copy.setContentsMargins(0, 0, 0, 0)
        progress_copy.setSpacing(SPACING["xs"])
        self._step_label = QLabel("STEP 01 / 04   WELCOME")
        self._step_label.setObjectName("wizardStepLabel")
        progress_copy.addWidget(self._step_label)
        self._progress = QProgressBar()
        self._progress.setObjectName("wizardProgress")
        self._progress.setFixedWidth(248)
        self._progress.setMaximum(3)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setAccessibleName("Setup progress")
        progress_copy.addWidget(self._progress)
        nl.addLayout(progress_copy)
        nl.addStretch()

        self._language_combo = QComboBox(nav)
        self._language_combo.setObjectName("wizardLanguageSelector")
        self._language_combo.setProperty("i18nIgnore", True)
        self._language_combo.setAccessibleName(translate("nav.language_tooltip"))
        self._language_combo.setToolTip(translate("nav.language_tooltip"))
        self._language_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._language_combo.setFixedSize(190, 32)
        self._language_combo.setIconSize(QSize(24, 16))
        self._language_combo.setMaxVisibleItems(len(LANGUAGES))
        for option in LANGUAGES:
            self._language_combo.addItem(
                _language_flag_icon(option.code),
                option.display_name,
                option.code,
            )
        language_index = self._language_combo.findData(current_language())
        self._language_combo.setCurrentIndex(max(0, language_index))
        self._language_combo.currentIndexChanged.connect(
            self._on_language_selected
        )
        nl.addWidget(self._language_combo)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setProperty("class", "signalSecondary")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setVisible(False)
        nl.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setProperty("class", "signalPrimary")
        self._next_btn.setMinimumWidth(112)
        self._next_btn.clicked.connect(self._go_next)
        nl.addWidget(self._next_btn)

        root.addWidget(nav)

    def _on_language_selected(self, index: int) -> None:
        """Apply and persist the first-run language without restarting setup."""
        code = self._language_combo.itemData(index)
        selected = set_language(code)
        cfg = load()
        cfg["language"] = selected
        try:
            save(cfg)
        except OSError:
            pass
        retranslate_widget_tree(self, selected)
        self._sync_progress_chrome(self._stack.currentIndex())
        self._refresh_review_summary()
        tooltip = translate("nav.language_tooltip")
        self._language_combo.setAccessibleName(tooltip)
        self._language_combo.setToolTip(tooltip)

    @staticmethod
    def _make_section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("class", "wizardSectionTitle")
        label.setAccessibleName(text)
        return label

    @staticmethod
    def _allow_control_shrink(control: QWidget) -> None:
        """Keep long paths and combo copy from widening a scroll page."""
        control.setMinimumWidth(96)
        control.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )

    @staticmethod
    def _allow_label_shrink(label: QLabel) -> None:
        """Let wrapped diagnostics contain long Windows paths without clipping."""
        label.setMinimumWidth(0)
        label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

    @staticmethod
    def _make_field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("class", "wizardFieldLabel")
        label.setWordWrap(True)
        label.setFixedWidth(168)
        label.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        return label

    @staticmethod
    def _make_help_label(
        text: str,
        *,
        color: str | None = None,
    ) -> QLabel:
        """Create one consistent inline explanation for setup choices."""
        label = QLabel(text)
        label.setWordWrap(True)
        SetupWizard._allow_label_shrink(label)
        label.setProperty("class", "wizardHint")
        label.setProperty(
            "tone",
            "warning" if color in {C["gold"], S["warning"]} else "default",
        )
        label.setAccessibleDescription(text)
        return label

    @staticmethod
    def _set_status(
        label: QLabel,
        text: str,
        state: str = "idle",
        *,
        allow_templates: bool = False,
    ) -> None:
        """Update status copy and its semantic visual state together."""
        setter = (
            set_translatable_text_template
            if allow_templates
            else set_translatable_text
        )
        setter(label, text)
        if label.property("state") == state:
            return
        label.setProperty("state", state)
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.update()

    def _sync_progress_chrome(self, index: int) -> None:
        safe_index = max(0, min(int(index), len(_WIZARD_STEP_LABELS) - 1))
        self._progress.setValue(safe_index)
        set_translatable_text(
            self._step_label,
            _WIZARD_STEP_LABELS[safe_index],
        )

    def _refresh_review_summary(self) -> None:
        """Render reviewed framing while leaving paths and project names intact."""
        if self._review_route == "docker":
            explicit_compose = self._compose_input.text().strip()
            compose_path = (
                explicit_compose
                or str(Path(self._evejs_root) / "compose.yaml")
            )
            compose_suffix = (
                ""
                if explicit_compose
                else f" {translate_ui_phrase('(automatic)')}"
            )
            policy = translate_ui_phrase(
                "Managed — launcher controls the stack"
                if self._policy_combo.currentData() == "managed"
                else "Connect only — observe an existing stack"
            )
            project = self._project_input.text().strip()
            if not project:
                project = translate_ui_phrase("Automatic")
            client_path = self._client_path or translate_ui_phrase(
                "(not detected)"
            )
            self._results.setText(
                format_ui_phrase(
                    _DOCKER_REVIEW_TEMPLATE,
                    evejs_root=self._evejs_root,
                    compose_path=compose_path,
                    compose_suffix=compose_suffix,
                    policy=policy,
                    project=project,
                    client_path=client_path,
                )
            )
            return

        if self._review_route == "native":
            self._results.setText(
                format_ui_phrase(
                    _NATIVE_REVIEW_TEMPLATE,
                    evejs_root=self._evejs_root,
                    client_path=(
                        self._client_path
                        or translate_ui_phrase("(not detected)")
                    ),
                )
            )

    # ── Navigation ────────────────────────────────────────────────────

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select EveJS Root Folder", "G:\\")
        if path:
            self._path_input.setText(path)

    def _browse_compose(self) -> None:
        """Select a non-default Compose file without making it mandatory."""
        start = self._compose_input.text().strip()
        if not start:
            root = self._path_input.text().strip()
            start = str(Path(root) / "compose.yaml") if root else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Docker Compose File",
            start,
            "Compose files (*.yaml *.yml);;YAML files (*.yaml *.yml);;All Files (*)",
        )
        if path:
            self._compose_input.setText(path)

    def _browse_client(self) -> None:
        """Browse for the copied client and display its canonical tq folder."""
        start = self._client_input.text().strip()
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Copied EVE Client Folder",
            start,
        )
        if not path:
            return
        resolved = resolve_client_tq_path(path, self._path_input.text().strip())
        self._client_input.setText(str(resolved) if resolved else path)

    def _on_path_changed(self, text: str) -> None:
        self._update_docker_guidance()
        if self._docker_mode():
            valid, msg = validate_docker_evejs_root(
                text.strip(),
                self._compose_input.text().strip(),
            )
            if valid:
                self._set_status(
                    self._path_status,
                    "Docker project found. Run Test Docker setup to continue.",
                    "notice",
                )
            elif text:
                localized_message = translate_discovery_diagnostic(msg)
                self._set_status(
                    self._path_status,
                    format_ui_phrase(
                        "Docker setup: {message}",
                        message=localized_message,
                    ),
                    "error",
                )
            else:
                self._set_status(
                    self._path_status,
                    "Enter the path to your EveJS folder",
                    "idle",
                )
            self._test_docker_btn.setEnabled(
                valid and self._docker_preflight_thread is None
            )
            self._next_btn.setEnabled(
                valid
                and self._validated_docker_fingerprint
                == docker_draft_fingerprint(self._collect_docker_draft())
            )
            return

        valid, msg = validate_evejs_root(text)
        if valid:
            self._set_status(
                self._path_status,
                "✓ Valid EveJS installation",
                "ready",
            )
            self._next_btn.setEnabled(True)
        elif text:
            localized_message = translate_discovery_diagnostic(msg)
            self._set_status(
                self._path_status,
                f"✗ {localized_message}",
                "error",
            )
            self._next_btn.setEnabled(False)
        else:
            self._set_status(
                self._path_status,
                "Enter the path to your EveJS folder",
                "idle",
            )
            self._next_btn.setEnabled(False)

        compose = Path(text) / "compose.yaml" if text else None
        if text and not valid and compose is not None and compose.is_file():
            self._set_status(
                self._path_status,
                localized_message
                + "\n"
                + translate_ui_phrase(
                    "Docker Compose is available here; select Docker Compose to validate it."
                ),
                "notice",
            )

    def _docker_mode(self) -> bool:
        return self._backend_combo.currentData() == "docker_compose"

    def _collect_docker_draft(self) -> DockerSetupDraft:
        return DockerSetupDraft(
            evejs_root=self._path_input.text(),
            compose_file=self._compose_input.text(),
            project_name=self._project_input.text(),
            control_policy=self._policy_combo.currentData() or "connect_only",
            keep_running_on_exit=self._keep_running_check.isChecked(),
            client_path=self._client_input.text(),
        )

    def _on_backend_changed(self, _index: int) -> None:
        docker = self._docker_mode()
        self._docker_fields.setVisible(docker)
        runtime_help = DOCKER_RUNTIME_HELP if docker else NATIVE_RUNTIME_HELP
        set_translatable_text(self._backend_help, runtime_help)
        set_translatable_tooltip(self._backend_combo, runtime_help)
        set_translatable_accessible_description(
            self._backend_combo,
            runtime_help,
        )
        self._invalidate_docker_preflight()
        self._update_docker_guidance()
        self._on_path_changed(self._path_input.text())

    def _update_docker_guidance(self) -> None:
        """Explain Docker defaults without changing the user's draft."""
        docker = self._docker_mode()
        managed = docker and self._policy_combo.currentData() == "managed"
        policy_help = MANAGED_HELP if managed else CONNECT_ONLY_HELP
        set_translatable_text(self._policy_help, policy_help)
        set_translatable_tooltip(self._policy_combo, policy_help)
        set_translatable_accessible_description(
            self._policy_combo,
            policy_help,
        )
        self._keep_running_check.setEnabled(managed)
        set_translatable_tooltip(
            self._keep_running_check,
            "Leave managed Compose services running when the launcher closes."
            if managed
            else "Available only with Managed control."
        )
        self._advanced_docker_fields.setVisible(
            docker and self._advanced_toggle.isChecked()
        )

        explicit = self._compose_input.text().strip()
        root = self._path_input.text().strip()
        if explicit:
            resolved = f"Using: {explicit}"
        elif root:
            resolved = f"Using: {Path(root) / 'compose.yaml'} (automatic)"
        else:
            resolved = (
                "Using: <EveJS Root>\\compose.yaml after a root is selected "
                "(automatic)"
            )
        set_translatable_text_template(self._compose_resolved, resolved)
        set_translatable_tooltip_template(self._compose_resolved, resolved)

    def _invalidate_docker_preflight(self, *_args: object) -> None:
        self._validated_docker_fingerprint = None
        if hasattr(self, "_next_btn") and self._docker_mode():
            self._next_btn.setEnabled(False)
        if hasattr(self, "_test_docker_btn") and self._docker_mode():
            valid, _ = validate_docker_evejs_root(
                self._path_input.text().strip(),
                self._compose_input.text().strip(),
            )
            self._test_docker_btn.setEnabled(
                valid and self._docker_preflight_thread is None
            )
        if (
            hasattr(self, "_docker_status")
            and self._docker_mode()
            and self._docker_preflight_thread is None
            and self._docker_status.property("state") == "ready"
        ):
            self._set_status(
                self._docker_status,
                "Configuration changed. Run Test Docker setup again.",
                "notice",
            )

    def _start_docker_preflight(self) -> None:
        if not self._docker_mode() or self._docker_preflight_thread is not None:
            return
        self._canonicalize_client_input()
        self._docker_preflight_token += 1
        request = create_preflight_request(
            self._collect_docker_draft(),
            token=self._docker_preflight_token,
        )
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
        self._test_docker_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._set_status(
            self._docker_status,
            "Checking Docker CLI, engine, Compose, services, endpoints, "
            "and data state...",
            "busy",
        )
        thread.start()

    @pyqtSlot(object)
    def _on_docker_preflight_completed(self, result: object) -> None:
        if isinstance(result, DockerPreflightResult):
            current = docker_draft_fingerprint(self._collect_docker_draft())
            if (
                result.draft_fingerprint == current
                and result.report.ok
            ):
                self._validated_docker_fingerprint = current
                self._set_status(
                    self._docker_status,
                    "Docker setup is valid. Runtime and data initialization remain separate.",
                    "ready",
                )
                self._next_btn.setEnabled(True)
            elif result.draft_fingerprint != current:
                self._set_status(
                    self._docker_status,
                    "Docker fields changed during validation. Test again.",
                    "notice",
                )
            else:
                diagnostic = (
                    result.report.diagnostics[0]
                    if result.report.diagnostics
                    else "Docker setup validation failed."
                )
                localized_diagnostic = translate_discovery_diagnostic(diagnostic)
                self._set_status(
                    self._docker_status,
                    format_ui_phrase(
                        "Docker setup: {message}",
                        message=localized_diagnostic,
                    ),
                    "error",
                )
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
        self._on_path_changed(self._path_input.text())
        if self._close_after_docker_preflight:
            self._close_after_docker_preflight = False
            super().reject()

    def _go_back(self) -> None:
        cur = self._stack.currentIndex()
        if cur <= 0:
            return
        prev = cur - 1
        self._stack.setCurrentIndex(prev)
        self._sync_progress_chrome(prev)
        set_translatable_text(self._next_btn, "Next →")
        if prev == 1:
            self._on_path_changed(self._path_input.text())
        else:
            self._next_btn.setEnabled(True)
        self._back_btn.setVisible(prev > 0)

    def _go_next(self) -> None:
        cur = self._stack.currentIndex()

        if cur == 1:  # Path → Validation
            self._canonicalize_client_input()
            if self._docker_mode():
                fingerprint = docker_draft_fingerprint(
                    self._collect_docker_draft()
                )
                if fingerprint != self._validated_docker_fingerprint:
                    self._next_btn.setEnabled(False)
                    return
            self._evejs_root = self._path_input.text().strip()
            selected_client = self._client_input.text().strip()
            if selected_client:
                resolved_client = resolve_client_tq_path(
                    selected_client,
                    self._evejs_root,
                )
                if resolved_client is None:
                    self._set_status(
                        self._path_status,
                        "✗ EVE Client must be the copied tq folder containing "
                        "start.ini and bin64\\exefile.exe.",
                        "error",
                    )
                    return
                self._client_path = str(resolved_client)
                self._client_input.setText(self._client_path)
            else:
                self._client_path = ""
            if self._docker_mode():
                self._review_route = "docker"
                set_translatable_text(
                    self._review_badge,
                    "DOCKER ROUTE VERIFIED",
                )
            else:
                self._review_route = "native"
                set_translatable_text(
                    self._review_badge,
                    "NATIVE ROUTE VERIFIED",
                )
            self._refresh_review_summary()

        nxt = cur + 1
        if nxt < self._stack.count():
            self._stack.setCurrentIndex(nxt)
            self._sync_progress_chrome(nxt)
            self._back_btn.setVisible(True)

            if nxt == self._stack.count() - 1:
                set_translatable_text(self._next_btn, "✓ Finish")
            elif nxt == 1:
                set_translatable_text(self._next_btn, "Next →")
                self._on_path_changed(self._path_input.text())
            else:
                set_translatable_text(self._next_btn, "Next →")
                self._next_btn.setEnabled(True)
        else:
            self._save_and_accept()

    def _canonicalize_client_input(self) -> None:
        """Autofill and normalize the client before draft fingerprinting."""
        evejs_root = self._path_input.text().strip()
        selected = self._client_input.text().strip()
        if not selected:
            selected = find_client_path(evejs_root) or ""
        if not selected:
            return
        resolved = resolve_client_tq_path(selected, evejs_root)
        if resolved is not None and self._client_input.text() != str(resolved):
            self._client_input.setText(str(resolved))

    def _save_and_accept(self) -> None:
        cfg = load()
        cfg["evejs_root"] = self._evejs_root
        cfg["client_path"] = self._client_path
        if self._docker_mode():
            draft = self._collect_docker_draft()
            if (
                docker_draft_fingerprint(draft)
                != self._validated_docker_fingerprint
            ):
                return
            cfg.update(
                {
                    "runtime_backend": "docker_compose",
                    "docker_compose_file": draft.compose_file,
                    "docker_project_name": draft.project_name,
                    "docker_control_policy": draft.control_policy,
                    "docker_keep_running_on_exit": draft.keep_running_on_exit,
                }
            )
        else:
            cfg["runtime_backend"] = "native"
        save(cfg)
        self.accept()

    def reject(self) -> None:
        """Drain an active bounded preflight before destroying the dialog."""
        if self._docker_preflight_thread is not None:
            self._close_after_docker_preflight = True
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._docker_preflight_thread is not None:
            self._close_after_docker_preflight = True
            event.ignore()
            return
        super().closeEvent(event)
