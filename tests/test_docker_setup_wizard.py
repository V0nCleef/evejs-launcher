"""Backend-aware first-run setup and pure Docker draft tests."""
from __future__ import annotations

from pathlib import Path
import time

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog

from src import config
from src.i18n import current_language, set_language
from src.core.discovery import validate_docker_evejs_root, validate_evejs_root
from src.core.runtime.docker_compose import PreflightReport
from src.core.runtime.docker_setup import (
    DockerSetupDraft,
    build_compose_target,
    docker_draft_fingerprint,
)
from src.wizard import SetupWizard
from src.workers.docker_preflight_worker import DockerPreflightWorker


def _write_native_base_files(root: Path) -> None:
    for relative in (
        "server/certs/xmpp-ca-cert.pem",
        "tools/ClientSETUP/scripts/EvEJSConfig.bat",
        "server/index.js",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _write_fresh_native_static_data(root: Path) -> Path:
    _write_native_base_files(root)
    game_store = root / "_local" / "gameStore"
    (game_store / "data" / "accounts").mkdir(parents=True)
    (game_store / "manifest.json").write_text(
        '{"version": 1, "generatedTables": ["accounts"]}\n',
        encoding="utf-8",
    )
    (game_store / "data" / "accounts" / "data.json").write_text(
        "[]\n",
        encoding="utf-8",
    )
    return game_store


def test_native_validation_remains_strict_for_pristine_root(tmp_path: Path) -> None:
    root = tmp_path / "pristine"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    valid, diagnostic = validate_evejs_root(str(root))

    assert valid is False
    assert "Missing" in diagnostic


def test_native_validation_still_accepts_existing_layout(tmp_path: Path) -> None:
    root = tmp_path / "native"
    _write_native_base_files(root)
    database = root / "_local" / "gameStore" / "gamestore.sqlite"
    database.parent.mkdir(parents=True)
    database.touch()

    assert validate_evejs_root(str(root)) == (True, "")


def test_native_validation_accepts_populated_static_data_before_first_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fresh-native"
    game_store = _write_fresh_native_static_data(root)

    assert not (game_store / "gamestore.sqlite").exists()
    assert validate_evejs_root(str(root)) == (True, "")


@pytest.mark.parametrize("incomplete_part", ("manifest", "data"))
def test_native_validation_rejects_incomplete_static_data_without_database(
    tmp_path: Path,
    incomplete_part: str,
) -> None:
    root = tmp_path / "incomplete-native"
    _write_native_base_files(root)
    game_store = root / "_local" / "gameStore"
    (game_store / "data").mkdir(parents=True)
    if incomplete_part != "manifest":
        (game_store / "manifest.json").write_text("{}\n", encoding="utf-8")
    if incomplete_part != "data":
        (game_store / "data" / "accounts.json").write_text(
            "[]\n",
            encoding="utf-8",
        )

    valid, diagnostic = validate_evejs_root(str(root))

    assert valid is False
    assert "game store" in diagnostic.casefold()


def test_docker_validation_accepts_pristine_compose_root(tmp_path: Path) -> None:
    root = tmp_path / "EveJS pristine project"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    valid, diagnostic = validate_docker_evejs_root(str(root), str(compose))

    assert valid is True
    assert diagnostic == ""


@pytest.mark.parametrize(
    ("root_value", "compose_value", "message"),
    (
        ("relative/root", "relative/root/compose.yaml", "absolute"),
        ("{missing}", "{missing}/compose.yaml", "does not exist"),
    ),
)
def test_docker_validation_rejects_relative_or_missing_roots(
    tmp_path: Path,
    root_value: str,
    compose_value: str,
    message: str,
) -> None:
    missing = tmp_path / "missing"
    root = root_value.format(missing=missing)
    compose = compose_value.format(missing=missing)

    valid, diagnostic = validate_docker_evejs_root(root, compose)

    assert valid is False
    assert message in diagnostic


def test_docker_target_preserves_paths_with_spaces_as_single_argv_elements(
    tmp_path: Path,
) -> None:
    root = tmp_path / "EveJS project with spaces"
    root.mkdir()
    compose = root / "compose file.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    draft = DockerSetupDraft(
        evejs_root=str(root),
        compose_file=str(compose),
        project_name="evejs-test",
        control_policy="connect_only",
        keep_running_on_exit=True,
        client_path="",
    )

    target = build_compose_target(draft)
    argv = target.base_argv("docker.exe")

    assert str(root.resolve()) in argv
    assert str(compose.resolve()) in argv
    assert argv[argv.index("-f") + 1] == str(compose.resolve())
    assert argv[argv.index("--project-directory") + 1] == str(root.resolve())


def test_blank_compose_and_project_use_automatic_compose_defaults(
    tmp_path: Path,
) -> None:
    root = tmp_path / "automatic-project"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    draft = DockerSetupDraft(
        evejs_root=str(root),
        compose_file="",
        project_name="",
        control_policy="managed",
        keep_running_on_exit=True,
        client_path="",
    )

    target = build_compose_target(draft)
    argv = target.base_argv("docker.exe")

    assert target.compose_file == compose.resolve()
    assert target.project_name is None
    assert "-p" not in argv


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    return config_file


def _select_docker(wizard: SetupWizard, root: Path, compose: Path) -> None:
    wizard._stack.setCurrentIndex(1)
    wizard._backend_combo.setCurrentIndex(
        wizard._backend_combo.findData("docker_compose")
    )
    wizard._path_input.setText(str(root))
    wizard._compose_input.setText(str(compose))


def test_wizard_language_selector_switches_and_persists_without_restart(
    qapp,
    isolated_config: Path,
) -> None:
    set_language("en")
    wizard = SetupWizard()
    try:
        assert [
            (
                wizard._language_combo.itemData(combo_index),
                wizard._language_combo.itemText(combo_index),
            )
            for combo_index in range(wizard._language_combo.count())
        ] == [
            ("en", "English"),
            ("zh_CN", "简体中文"),
            ("ja", "日本語"),
            ("ko", "한국어"),
            ("fr", "Français"),
            ("de", "Deutsch"),
            ("nl", "Nederlands"),
            ("ru", "Русский"),
        ]
        index = wizard._language_combo.findData("ru")
        assert index >= 0
        assert wizard._language_combo.itemText(index) == "Русский"

        wizard._language_combo.setCurrentIndex(index)

        assert current_language() == "ru"
        assert config.load()["language"] == "ru"
    finally:
        wizard.close()
        set_language("en")


def test_wizard_accepts_fresh_native_root_before_first_server_run(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "fresh-native-wizard"
    game_store = _write_fresh_native_static_data(root)
    wizard = SetupWizard()
    wizard._stack.setCurrentIndex(1)
    wizard._backend_combo.setCurrentIndex(
        wizard._backend_combo.findData("native")
    )

    wizard._path_input.setText(str(root))

    assert not (game_store / "gamestore.sqlite").exists()
    assert wizard._next_btn.isEnabled() is True
    assert "Valid EveJS installation" in wizard._path_status.text()


def test_wizard_explains_runtime_compose_and_advanced_project_defaults(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "plain-language-project"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    wizard = SetupWizard()

    assert "directly on Windows" in wizard._backend_help.text()
    assert "does not move characters" in wizard._runtime_data_notice.text()

    wizard._stack.setCurrentIndex(1)
    wizard._backend_combo.setCurrentIndex(
        wizard._backend_combo.findData("docker_compose")
    )
    wizard._path_input.setText(str(root))

    assert "Docker Desktop" in wizard._backend_help.text()
    assert "leave this blank" in wizard._compose_help.text().casefold()
    assert str(root / "compose.yaml") in wizard._compose_resolved.text()
    assert "(automatic)" in wizard._compose_resolved.text()
    assert wizard._compose_input.text() == ""
    assert wizard._advanced_toggle.isChecked() is False
    assert wizard._advanced_docker_fields.isHidden()
    assert "Most users should leave this blank" in wizard._project_help.text()
    assert wizard._keep_running_check.isEnabled() is False
    assert "never starts, stops, or changes" in wizard._policy_help.text()

    wizard._advanced_toggle.setChecked(True)
    wizard._policy_combo.setCurrentIndex(
        wizard._policy_combo.findData("managed")
    )

    assert not wizard._advanced_docker_fields.isHidden()
    assert wizard._keep_running_check.isEnabled()
    assert "start, stop, restart" in wizard._policy_help.text()


def test_wizard_compose_browse_uses_yaml_filter(
    qapp,
    isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wizard = SetupWizard()
    captured: list[object] = []
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args: captured.extend(args) or ("", ""),
    )

    wizard._browse_compose()

    assert "*.yaml" in captured[3]
    assert "*.yml" in captured[3]
    assert "All Files" in captured[3]


def test_wizard_client_browse_canonicalizes_bin64_to_tq(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tq = tmp_path / "SharedCache" / "tq"
    bin64 = tq / "bin64"
    bin64.mkdir(parents=True)
    (tq / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    (bin64 / "exefile.exe").write_bytes(b"fixture")
    wizard = SetupWizard()
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args: str(bin64),
    )

    wizard._browse_client()

    assert wizard._client_input.text() == str(tq)


def test_wizard_review_calls_blank_compose_and_project_automatic(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "automatic-review"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    wizard = SetupWizard()
    wizard._stack.setCurrentIndex(1)
    wizard._backend_combo.setCurrentIndex(
        wizard._backend_combo.findData("docker_compose")
    )
    wizard._path_input.setText(str(root))
    wizard._policy_combo.setCurrentIndex(
        wizard._policy_combo.findData("managed")
    )
    draft = wizard._collect_docker_draft()
    wizard._validated_docker_fingerprint = docker_draft_fingerprint(draft)
    wizard._on_path_changed(str(root))

    wizard._go_next()

    assert "Runtime: Docker Compose" in wizard._results.text()
    assert f"Compose File: {root / 'compose.yaml'} (automatic)" in wizard._results.text()
    assert "Project Name: Automatic" in wizard._results.text()
    assert "Control: Managed" in wizard._results.text()


def test_wizard_docker_pristine_flow_preflights_then_persists_exact_draft(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "pristine project"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    tq = tmp_path / "copied client" / "tq"
    (tq / "bin64").mkdir(parents=True)
    (tq / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    (tq / "bin64" / "exefile.exe").write_bytes(b"fixture")
    client_config = root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    client_config.parent.mkdir(parents=True)
    client_config.write_text(
        f'set "EVEJS_CLIENT_PATH={tq}"\n',
        encoding="utf-8",
    )
    observed: list[QThread] = []

    class Inspector:
        def preflight(self, _target):
            observed.append(QThread.currentThread())
            return PreflightReport(True, ("ready",))

    wizard = SetupWizard()
    wizard._docker_preflight_worker_factory = lambda request: (
        DockerPreflightWorker(request, inspector_factory=Inspector)
    )
    _select_docker(wizard, root, compose)
    wizard._project_input.setText("wizard-fixture")
    wizard._policy_combo.setCurrentIndex(
        wizard._policy_combo.findData("managed")
    )
    wizard._keep_running_check.setChecked(False)

    assert wizard._next_btn.isEnabled() is False
    wizard._test_docker_btn.click()
    for _ in range(100):
        qapp.processEvents()
        if wizard._docker_preflight_thread is None:
            break
        QTest.qWait(5)

    assert observed and observed[0] is not qapp.thread()
    assert wizard._next_btn.isEnabled() is True
    assert wizard._client_input.text() == str(tq)
    wizard._go_next()
    wizard._go_next()
    wizard._go_next()

    saved = config.load()
    assert saved["runtime_backend"] == "docker_compose"
    assert saved["evejs_root"] == str(root)
    assert saved["docker_compose_file"] == str(compose)
    assert saved["docker_project_name"] == "wizard-fixture"
    assert saved["docker_control_policy"] == "managed"
    assert saved["docker_keep_running_on_exit"] is False
    assert saved["client_path"] == str(tq)
    assert "separate" in wizard._results.text().casefold()


def test_wizard_docker_selection_never_calls_native_validator_and_edit_invalidates(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "pristine"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.wizard.validate_evejs_root",
        lambda _path: (_ for _ in ()).throw(AssertionError("native validator called")),
    )

    class Inspector:
        def preflight(self, _target):
            return PreflightReport(True, ("ready",))

    wizard = SetupWizard()
    wizard._docker_preflight_worker_factory = lambda request: (
        DockerPreflightWorker(request, inspector_factory=Inspector)
    )
    _select_docker(wizard, root, compose)
    wizard._test_docker_btn.click()
    for _ in range(100):
        qapp.processEvents()
        if wizard._docker_preflight_thread is None:
            break
        QTest.qWait(5)
    assert wizard._next_btn.isEnabled() is True

    wizard._project_input.setText("changed")

    assert wizard._next_btn.isEnabled() is False
    assert not isolated_config.exists()


def test_wizard_recommends_docker_without_switching_backend(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "pristine"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    wizard = SetupWizard()
    wizard._stack.setCurrentIndex(1)

    wizard._path_input.setText(str(root))

    assert wizard._backend_combo.currentData() == "native"
    assert "Docker" in wizard._path_status.text()
    assert wizard._next_btn.isEnabled() is False


def test_wizard_close_during_preflight_defers_until_worker_drains(
    qapp,
    isolated_config: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "pristine"
    root.mkdir()
    compose = root / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")

    class Inspector:
        def preflight(self, _target):
            time.sleep(0.05)
            return PreflightReport(True, ("ready",))

    wizard = SetupWizard()
    wizard._docker_preflight_worker_factory = lambda request: (
        DockerPreflightWorker(request, inspector_factory=Inspector)
    )
    _select_docker(wizard, root, compose)
    wizard._test_docker_btn.click()

    class Event:
        ignored = False

        def ignore(self) -> None:
            self.ignored = True

    event = Event()
    wizard.closeEvent(event)
    assert event.ignored is True
    assert wizard._close_after_docker_preflight is True

    for _ in range(100):
        qapp.processEvents()
        if wizard._docker_preflight_thread is None:
            break
        QTest.qWait(5)

    assert wizard._docker_preflight_thread is None
    assert wizard.result() == 0
    assert not isolated_config.exists()
