"""Visual contracts for the Deep Signal first-run setup wizard."""
from __future__ import annotations

from pathlib import Path

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QScrollArea

from src.i18n import (
    UI_PHRASES_BY_LANGUAGE,
    format_ui_phrase,
    set_language,
    translate_ui_phrase,
)
from src.translations_source import SOURCE_PHRASE_SET
from src.widgets.deep_signal_background import DeepSignalBackground
from src.widgets.glass_panel import GlassPanel
from src.widgets.page_header import PageHeader
from src.wizard import SetupWizard


_FINAL_REVIEW_WIZARD_PHRASES = (
    "Changing runtime does not move characters, market data, or server data.",
    "This tool manages your local EveJS services and EVE clients.\n\n"
    "You will choose whether EveJS runs directly on Windows or through "
    "Docker Desktop. The launcher never switches this choice automatically.",
    "01   Choose the runtime that owns your EveJS services",
    "02   Locate the project and optional copied EVE client",
    "03   Verify the route, then save the launcher profile",
    "Choose how EveJS runs, then select the matching EveJS project folder.",
)


@pytest.fixture(autouse=True)
def _reset_ui_language_between_tests():
    set_language("en")
    yield
    set_language("en")


def _make_native_root(root: Path) -> Path:
    for relative in (
        "server/certs/xmpp-ca-cert.pem",
        "_local/gameStore/gamestore.sqlite",
        "tools/ClientSETUP/scripts/EvEJSConfig.bat",
        "server/index.js",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return root


def test_wizard_uses_deep_signal_foundation_on_every_step(qapp) -> None:
    wizard = SetupWizard()

    assert wizard.objectName() == "setupWizard"
    assert isinstance(wizard._signal_background, DeepSignalBackground)
    assert wizard._signal_background.is_animating() is False
    assert wizard._stack.objectName() == "wizardStack"
    assert wizard._stack.count() == 4
    assert wizard.minimumWidth() == 700
    assert wizard.minimumHeight() == 560
    assert wizard.minimumWidth() >= wizard.minimumSizeHint().width()

    for index in range(wizard._stack.count()):
        page = wizard._stack.widget(index)
        header = page.findChild(PageHeader)
        panel = page.findChild(GlassPanel)
        scroll = page.findChild(QScrollArea, "wizardPageScroll")

        assert header is not None
        assert header.eyebrow_label.text().startswith("DEEP SIGNAL //")
        assert panel is not None
        assert panel.variant == "quiet"
        assert scroll is not None
        assert scroll.widgetResizable() is True
        assert (
            scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    assert wizard._back_btn.property("class") == "signalSecondary"
    assert wizard._next_btn.property("class") == "signalPrimary"
    assert wizard._test_docker_btn.property("class") == "signalSecondary"
    assert wizard._docker_fields.property("class") == "wizardSection"
    assert wizard._results.property("class") == "wizardReview"


def test_wizard_progress_chrome_tracks_navigation(qapp) -> None:
    wizard = SetupWizard()

    assert wizard._progress.value() == 0
    assert wizard._step_label.text() == "STEP 01 / 04   WELCOME"
    assert wizard._back_btn.isHidden()

    wizard._go_next()

    assert wizard._stack.currentIndex() == 1
    assert wizard._progress.value() == 1
    assert wizard._step_label.text() == "STEP 02 / 04   RUNTIME"
    assert not wizard._back_btn.isHidden()

    wizard._go_back()

    assert wizard._stack.currentIndex() == 0
    assert wizard._progress.value() == 0
    assert wizard._step_label.text() == "STEP 01 / 04   WELCOME"
    assert wizard._back_btn.isHidden()


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_wizard_dynamic_progress_labels_use_active_language(
    qapp,
    language: str,
) -> None:
    set_language(language)
    wizard = SetupWizard()
    sources = (
        "STEP 01 / 04   WELCOME",
        "STEP 02 / 04   RUNTIME",
        "STEP 03 / 04   VERIFY",
        "STEP 04 / 04   READY",
    )
    try:
        rendered: list[str] = []
        for index, source in enumerate(sources):
            wizard._sync_progress_chrome(index)
            rendered.append(wizard._step_label.text())
            assert wizard._step_label.text() == translate_ui_phrase(source)
        assert rendered != list(sources)
    finally:
        set_language("en")
        wizard.close()


def test_final_review_wizard_copy_is_reviewed_in_every_catalog() -> None:
    assert set(_FINAL_REVIEW_WIZARD_PHRASES).issubset(SOURCE_PHRASE_SET)

    for language, catalog in UI_PHRASES_BY_LANGUAGE.items():
        for source in _FINAL_REVIEW_WIZARD_PHRASES:
            assert source in catalog, (language, source)
            assert catalog[source] != source, (language, source)


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_final_review_wizard_copy_retranslates_live(
    qapp,
    monkeypatch,
    language: str,
) -> None:
    set_language("en")
    monkeypatch.setattr("src.wizard.load", lambda: {})
    monkeypatch.setattr("src.wizard.save", lambda _config: None)
    wizard = SetupWizard()
    sources = set(_FINAL_REVIEW_WIZARD_PHRASES)
    try:
        assert sources.issubset(
            {label.text() for label in wizard.findChildren(QLabel)}
        )

        wizard._language_combo.setCurrentIndex(
            wizard._language_combo.findData(language)
        )

        translated = {
            translate_ui_phrase(source, language)
            for source in _FINAL_REVIEW_WIZARD_PHRASES
        }
        rendered = {label.text() for label in wizard.findChildren(QLabel)}
        assert translated.issubset(rendered)
        assert sources.isdisjoint(rendered)
    finally:
        set_language("en")
        wizard.close()


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_wizard_review_templates_localize_framing_and_preserve_user_values(
    qapp,
    language: str,
) -> None:
    set_language(language)
    wizard = SetupWizard()
    native_template = (
        "Runtime: Native — directly on Windows\n"
        "EveJS Root: {evejs_root}\n"
        "CLIENT Path: {client_path}\n\n"
        "Click Next to save these settings."
    )
    docker_template = (
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
    evejs_root = r"C:\用户\EveJS Settings"
    client_path = r"C:\ユーザー\EVE Market\tq"
    compose_path = r"C:\사용자\EveJS Settings\compose.custom.yaml"
    project_name = "用户-Settings-project"
    try:
        wizard._evejs_root = evejs_root
        wizard._client_path = client_path
        wizard._review_route = "native"
        wizard._refresh_review_summary()

        assert wizard._results.text() == format_ui_phrase(
            native_template,
            evejs_root=evejs_root,
            client_path=client_path,
        )
        assert evejs_root in wizard._results.text()
        assert client_path in wizard._results.text()
        assert wizard._results.text() != native_template.format(
            evejs_root=evejs_root,
            client_path=client_path,
        )

        wizard._backend_combo.setCurrentIndex(
            wizard._backend_combo.findData("docker_compose")
        )
        wizard._policy_combo.setCurrentIndex(
            wizard._policy_combo.findData("managed")
        )
        wizard._compose_input.setText(compose_path)
        wizard._project_input.setText(project_name)
        wizard._review_route = "docker"
        wizard._refresh_review_summary()

        policy = translate_ui_phrase("Managed — launcher controls the stack")
        assert wizard._results.text() == format_ui_phrase(
            docker_template,
            evejs_root=evejs_root,
            compose_path=compose_path,
            compose_suffix="",
            policy=policy,
            project=project_name,
            client_path=client_path,
        )
        for value in (evejs_root, client_path, compose_path, project_name):
            assert value in wizard._results.text()
        assert policy in wizard._results.text()
        assert wizard._results.text() != docker_template.format(
            evejs_root=evejs_root,
            compose_path=compose_path,
            compose_suffix="",
            policy="Managed — launcher controls the stack",
            project=project_name,
            client_path=client_path,
        )
    finally:
        set_language("en")
        wizard.close()


def test_wizard_language_selector_retranslates_existing_review_summary(
    qapp,
    monkeypatch,
) -> None:
    set_language("en")
    monkeypatch.setattr("src.wizard.load", lambda: {})
    monkeypatch.setattr("src.wizard.save", lambda _config: None)
    wizard = SetupWizard()
    evejs_root = r"C:\用户\EveJS Settings"
    client_path = r"C:\ユーザー\EVE Market\tq"
    native_template = (
        "Runtime: Native — directly on Windows\n"
        "EveJS Root: {evejs_root}\n"
        "CLIENT Path: {client_path}\n\n"
        "Click Next to save these settings."
    )
    try:
        wizard._evejs_root = evejs_root
        wizard._client_path = client_path
        wizard._review_route = "native"
        wizard._refresh_review_summary()
        english = wizard._results.text()

        wizard._language_combo.setCurrentIndex(
            wizard._language_combo.findData("ja")
        )

        assert wizard._results.text() == format_ui_phrase(
            native_template,
            evejs_root=evejs_root,
            client_path=client_path,
        )
        assert wizard._results.text() != english
        assert evejs_root in wizard._results.text()
        assert client_path in wizard._results.text()
    finally:
        set_language("en")
        wizard.close()


def test_wizard_validation_uses_semantic_status_states(
    qapp,
    tmp_path: Path,
) -> None:
    wizard = SetupWizard()
    wizard._stack.setCurrentIndex(1)

    wizard._path_input.setText(str(_make_native_root(tmp_path / "native")))

    assert wizard._path_status.property("class") == "wizardStatus"
    assert wizard._path_status.property("state") == "ready"
    assert wizard._path_status.styleSheet() == ""
    assert wizard._next_btn.isEnabled() is True

    wizard._path_input.setText(str(tmp_path / "missing"))

    assert wizard._path_status.property("state") == "error"
    assert wizard._next_btn.isEnabled() is False


def test_wizard_renders_expanded_docker_form_at_minimum_size(
    qapp,
    tmp_path: Path,
) -> None:
    root = tmp_path / "docker project"
    root.mkdir()
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    wizard = SetupWizard()
    wizard.resize(wizard.minimumSize())
    wizard._stack.setCurrentIndex(1)
    wizard._backend_combo.setCurrentIndex(
        wizard._backend_combo.findData("docker_compose")
    )
    wizard._path_input.setText(str(root))
    wizard._advanced_toggle.setChecked(True)
    wizard.show()
    qapp.processEvents()

    page = wizard._stack.currentWidget()
    scroll = page.findChild(QScrollArea, "wizardPageScroll")
    captured = wizard.grab()

    assert scroll is not None
    assert scroll.verticalScrollBar().maximum() > 0
    assert wizard._advanced_docker_fields.isVisible()
    assert wizard._docker_fields.width() <= scroll.viewport().width()
    assert not captured.isNull()
    assert captured.width() == wizard.minimumWidth()
    assert captured.height() == wizard.minimumHeight()
