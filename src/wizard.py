"""First-run setup wizard — prompts for EveJS installation path on initial launch."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QComboBox, QLineEdit, QFileDialog, QStackedWidget,
    QProgressBar, QWidget,
)

from .config import load, save
from .constants import COLORS as C
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
    "Docker stack."
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


class _WizardPage(QWidget):
    """A single page in the setup wizard."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)

        tl = QLabel(title)
        tl.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {C['white']};")
        layout.addWidget(tl)

        if subtitle:
            sl = QLabel(subtitle)
            sl.setStyleSheet(f"color: {C['grey']}; font-size: 13px;")
            sl.setWordWrap(True)
            layout.addWidget(sl)

        layout.addSpacing(8)
        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        layout.addLayout(self.content)
        layout.addStretch()


class SetupWizard(QDialog):
    """Modal wizard shown when no EveJS root is configured."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("EveJS Launcher — Setup")
        self.resize(720, 680)
        self.setMinimumSize(640, 600)
        self.setWindowFlags(Qt.WindowType.Dialog)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {C['bg']}; }}
            QLabel {{ color: {C['white']}; }}
            QLineEdit {{
                background-color: {C['card']};
                border: 1px solid {C['steel']};
                padding: 8px;
                color: {C['white']};
                font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {C['teal']}; }}
            QPushButton {{
                background-color: {C['card']};
                border: 1px solid {C['steel']};
                padding: 8px 20px;
                color: {C['white']};
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {C['steel']}; }}
            QProgressBar {{
                border: 1px solid {C['steel']};
                background: {C['card']};
                height: 6px;
                text-align: center;
            }}
            QProgressBar::chunk {{ background: {C['teal']}; }}
        """)

        self._evejs_root = ""
        self._client_path = ""
        self._docker_preflight_token = 0
        self._docker_preflight_thread: QThread | None = None
        self._docker_preflight_worker: DockerPreflightWorker | None = None
        self._docker_preflight_result_received = False
        self._docker_preflight_thread_finished = False
        self._validated_docker_fingerprint: str | None = None
        self._close_after_docker_preflight = False
        self._build()

    # ── UI ────────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Stacked pages
        self._stack = QStackedWidget()

        # Page 0: Welcome
        p0 = _WizardPage(
            "Welcome to EveJS Launcher",
            "This tool manages your local EveJS services and EVE clients.\n\n"
            "You will choose whether EveJS runs directly on Windows or through "
            "Docker Desktop. The launcher never switches this choice automatically."
        )
        self._stack.addWidget(p0)

        # Page 1: runtime and path selection
        p1 = _WizardPage(
            "Choose Your EveJS Runtime",
            "Choose how EveJS runs, then select the matching EveJS project folder."
        )

        backend_row = QHBoxLayout()
        backend_row.addWidget(QLabel("How should EveJS run?"))
        self._backend_combo = QComboBox()
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

        ph = QHBoxLayout()
        ph.addWidget(QLabel("EveJS Root:"))
        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("Select your EveJS project folder")
        self._path_input.textChanged.connect(self._on_path_changed)
        ph.addWidget(self._path_input, 1)

        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        ph.addWidget(browse)
        p1.content.addLayout(ph)

        self._path_status = QLabel("Enter the path to your EveJS folder")
        self._path_status.setStyleSheet(f"font-size: 12px; color: {C['grey']};")
        p1.content.addWidget(self._path_status)

        client_row = QHBoxLayout()
        client_row.addWidget(QLabel("EVE Client:"))
        self._client_input = QLineEdit()
        self._client_input.setPlaceholderText("Optional copied EVE client tq folder")
        self._client_input.textChanged.connect(self._invalidate_docker_preflight)
        client_row.addWidget(self._client_input, 1)
        client_browse = QPushButton("Browse…")
        client_browse.clicked.connect(self._browse_client)
        client_row.addWidget(client_browse)
        p1.content.addLayout(client_row)

        self._docker_fields = QWidget()
        docker_layout = QVBoxLayout(self._docker_fields)
        docker_layout.setContentsMargins(0, 0, 0, 0)
        docker_layout.setSpacing(6)

        compose_row = QHBoxLayout()
        compose_row.addWidget(QLabel("Compose File (optional):"))
        self._compose_input = QLineEdit()
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
        policy_row.addWidget(QLabel("Control Policy:"))
        self._policy_combo = QComboBox()
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
        advanced_layout = QVBoxLayout(self._advanced_docker_fields)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)
        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("Compose Project Name (optional):"))
        self._project_input = QLineEdit()
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
        self._test_docker_btn.clicked.connect(self._start_docker_preflight)
        docker_layout.addWidget(self._test_docker_btn)
        self._test_docker_help = self._make_help_label(DOCKER_TEST_HELP)
        self._test_docker_help.setObjectName("wizardDockerTestHelp")
        docker_layout.addWidget(self._test_docker_help)
        self._docker_status = QLabel("")
        self._docker_status.setWordWrap(True)
        docker_layout.addWidget(self._docker_status)
        self._docker_fields.hide()
        p1.content.addWidget(self._docker_fields)
        self._stack.addWidget(p1)

        # Page 2: Validation
        p2 = _WizardPage(
            "Installation Verified",
            "We found a working EveJS installation. Here's what was detected:"
        )
        self._results = QLabel("")
        self._results.setStyleSheet(
            f"color: {C['grey']}; font-size: 12px; background-color: {C['card']};"
            "padding: 12px; border-radius: 4px;"
        )
        self._results.setWordWrap(True)
        p2.content.addWidget(self._results)
        self._stack.addWidget(p2)

        # Page 3: Done
        p3 = _WizardPage(
            "Ready!",
            "Setup is complete. The launcher will scan your accounts and show your characters."
        )
        for line in (
            "✓ EveJS project validated",
            "✓ Runtime settings reviewed",
            "✓ Accounts will be loaded",
        ):
            check = QLabel(line)
            check.setStyleSheet(f"color: {C['green']}; font-size: 13px;")
            p3.content.addWidget(check)
        self._stack.addWidget(p3)

        root.addWidget(self._stack, 1)

        # Bottom nav bar
        nav = QWidget()
        nav.setFixedHeight(56)
        nav.setStyleSheet(f"background-color: {C['card']};")
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(16, 8, 16, 8)

        self._progress = QProgressBar()
        self._progress.setMaximum(3)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        nl.addWidget(self._progress)
        nl.addStretch()

        self._back_btn = QPushButton("← Back")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setVisible(False)
        nl.addWidget(self._back_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {C['teal']};
                border: none;
                color: {C['bg']};
                font-weight: bold;
                padding: 8px 20px;
            }}
            QPushButton:hover {{ background-color: #00D8F0; }}
            QPushButton:disabled {{ background-color: {C['steel']}; color: {C['grey']}; }}
        """)
        self._next_btn.clicked.connect(self._go_next)
        nl.addWidget(self._next_btn)

        root.addWidget(nav)

    @staticmethod
    def _make_help_label(
        text: str,
        *,
        color: str | None = None,
    ) -> QLabel:
        """Create one consistent inline explanation for setup choices."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: {color or C['grey']}; font-size: 11px;"
        )
        label.setAccessibleDescription(text)
        return label

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
                self._path_status.setText(
                    "Docker project found. Run Test Docker setup to continue."
                )
                self._path_status.setStyleSheet(
                    f"color: {C['grey']}; font-size: 12px;"
                )
            elif text:
                self._path_status.setText(f"Docker setup: {msg}")
                self._path_status.setStyleSheet(
                    f"color: {C['red']}; font-size: 12px;"
                )
            else:
                self._path_status.setText("Enter the path to your EveJS folder")
                self._path_status.setStyleSheet(
                    f"color: {C['grey']}; font-size: 12px;"
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
            self._path_status.setText("✓ Valid EveJS installation")
            self._path_status.setStyleSheet(f"color: {C['green']}; font-size: 12px;")
            self._next_btn.setEnabled(True)
        elif text:
            self._path_status.setText(f"✗ {msg}")
            self._path_status.setStyleSheet(f"color: {C['red']}; font-size: 12px;")
            self._next_btn.setEnabled(False)
        else:
            self._path_status.setText("Enter the path to your EveJS folder")
            self._path_status.setStyleSheet(f"color: {C['grey']}; font-size: 12px;")
            self._next_btn.setEnabled(False)

        compose = Path(text) / "compose.yaml" if text else None
        if text and not valid and compose is not None and compose.is_file():
            self._path_status.setText(
                f"{msg}\nDocker Compose is available here; select Docker Compose to validate it."
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
        self._backend_help.setText(runtime_help)
        self._backend_combo.setToolTip(runtime_help)
        self._backend_combo.setAccessibleDescription(runtime_help)
        self._invalidate_docker_preflight()
        self._update_docker_guidance()
        self._on_path_changed(self._path_input.text())

    def _update_docker_guidance(self) -> None:
        """Explain Docker defaults without changing the user's draft."""
        docker = self._docker_mode()
        managed = docker and self._policy_combo.currentData() == "managed"
        policy_help = MANAGED_HELP if managed else CONNECT_ONLY_HELP
        self._policy_help.setText(policy_help)
        self._policy_combo.setToolTip(policy_help)
        self._policy_combo.setAccessibleDescription(policy_help)
        self._keep_running_check.setEnabled(managed)
        self._keep_running_check.setToolTip(
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
        self._compose_resolved.setText(resolved)
        self._compose_resolved.setToolTip(resolved)

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
        self._docker_status.setText(
            "Checking Docker CLI, engine, Compose, services, endpoints, and data state..."
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
                self._docker_status.setText(
                    "Docker setup is valid. Runtime and data initialization remain separate."
                )
                self._next_btn.setEnabled(True)
            elif result.draft_fingerprint != current:
                self._docker_status.setText(
                    "Docker fields changed during validation. Test again."
                )
            else:
                diagnostic = (
                    result.report.diagnostics[0]
                    if result.report.diagnostics
                    else "Docker setup validation failed."
                )
                self._docker_status.setText(diagnostic)
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
        self._progress.setValue(prev)
        self._next_btn.setText("Next →")
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
                    self._path_status.setText(
                        "✗ EVE Client must be the copied tq folder containing "
                        "start.ini and bin64\\exefile.exe."
                    )
                    self._path_status.setStyleSheet(
                        f"color: {C['red']}; font-size: 12px;"
                    )
                    return
                self._client_path = str(resolved_client)
                self._client_input.setText(self._client_path)
            else:
                self._client_path = ""
            if self._docker_mode():
                explicit_compose = self._compose_input.text().strip()
                compose_path = (
                    explicit_compose
                    or str(Path(self._evejs_root) / "compose.yaml")
                )
                compose_suffix = "" if explicit_compose else " (automatic)"
                policy = (
                    "Managed — launcher controls the stack"
                    if self._policy_combo.currentData() == "managed"
                    else "Connect only — observe an existing stack"
                )
                project = self._project_input.text().strip() or "Automatic"
                self._results.setText(
                    "Runtime: Docker Compose\n"
                    f"EveJS Root: {self._evejs_root}\n"
                    f"Compose File: {compose_path}{compose_suffix}\n"
                    f"Control: {policy}\n"
                    f"Project Name: {project}\n"
                    f"CLIENT Path: {self._client_path or '(not detected)'}\n\n"
                    "Docker configuration is valid. Game-data initialization is a "
                    "separate confirmed action. Market seeding/rebuild is also "
                    "separate and is never selected or run automatically.\n\n"
                    "Click Next to review completion."
                )
            else:
                self._results.setText(
                    "Runtime: Native — directly on Windows\n"
                    f"EveJS Root: {self._evejs_root}\n"
                    f"CLIENT Path: {self._client_path or '(not detected)'}\n\n"
                    "Click Next to save these settings."
                )

        nxt = cur + 1
        if nxt < self._stack.count():
            self._stack.setCurrentIndex(nxt)
            self._progress.setValue(nxt)
            self._back_btn.setVisible(True)

            if nxt == self._stack.count() - 1:
                self._next_btn.setText("✓ Finish")
            elif nxt == 1:
                self._next_btn.setText("Next →")
                self._on_path_changed(self._path_input.text())
            else:
                self._next_btn.setText("Next →")
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
