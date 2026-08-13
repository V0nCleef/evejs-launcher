"""Visual contracts for the Deep Signal first-run setup wizard."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QScrollArea

from src.widgets.deep_signal_background import DeepSignalBackground
from src.widgets.glass_panel import GlassPanel
from src.widgets.page_header import PageHeader
from src.wizard import SetupWizard


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
