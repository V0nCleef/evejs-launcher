"""Focused contracts for the complete Russian launcher localization."""
from __future__ import annotations

from collections import Counter
from string import Formatter

import pytest

from src import i18n
from src.translations_ru import UI_PHRASES
from src.translations_source import SOURCE_PHRASES
from src.widgets.status_bar import StatusBar, _language_flag_icon
from src.wizard import SetupWizard


def _format_fields(value: str) -> Counter[tuple[str, str, str | None]]:
    return Counter(
        (field_name, format_spec, conversion)
        for _literal, field_name, format_spec, conversion in Formatter().parse(value)
        if field_name is not None
    )


@pytest.fixture(autouse=True)
def _restore_english() -> None:
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def test_russian_catalog_has_exact_source_and_placeholder_parity() -> None:
    assert len(SOURCE_PHRASES) == len(set(SOURCE_PHRASES))
    assert set(UI_PHRASES) == set(SOURCE_PHRASES)
    assert len(UI_PHRASES) == len(SOURCE_PHRASES)
    for source in SOURCE_PHRASES:
        assert _format_fields(UI_PHRASES[source]) == _format_fields(source), source


def test_russian_metadata_and_painted_flag_are_deterministic(qapp) -> None:
    option = next(option for option in i18n.LANGUAGES if option.code == "ru")
    assert option.native_name == "Русский"
    assert option.display_name == "Русский"

    image = _language_flag_icon("ru").pixmap(24, 16).toImage()
    assert image.pixelColor(12, 3).name() == "#ffffff"
    assert image.pixelColor(12, 8).name() == "#0039a6"
    assert image.pixelColor(12, 13).name() == "#d52b1e"


def test_footer_selector_exposes_russian_and_retranslates_live(qapp) -> None:
    bar = StatusBar()
    try:
        index = bar.language_combo.findData("ru")
        assert index >= 0
        assert bar.language_combo.itemText(index) == "Русский"
        assert not bar.language_combo.itemIcon(index).isNull()

        bar.language_combo.setCurrentIndex(index)

        assert i18n.current_language() == "ru"
        assert bar.language_combo.toolTip() == "Язык лаунчера"
        assert bar.language_combo.accessibleName() == "Язык лаунчера"
    finally:
        bar.close()


def test_wizard_selector_persists_russian_and_retranslates_without_restart(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[dict] = []
    monkeypatch.setattr("src.wizard.load", lambda: {})
    monkeypatch.setattr("src.wizard.save", lambda cfg: saved.append(dict(cfg)))
    wizard = SetupWizard()
    try:
        index = wizard._language_combo.findData("ru")
        assert index >= 0
        assert wizard._language_combo.itemText(index) == "Русский"
        assert not wizard._language_combo.itemIcon(index).isNull()

        wizard._language_combo.setCurrentIndex(index)

        assert i18n.current_language() == "ru"
        assert saved[-1]["language"] == "ru"
        assert wizard._step_label.text() == i18n.translate_ui_phrase(
            "STEP 01 / 04   WELCOME",
            "ru",
        )
        assert wizard._step_label.text() != "STEP 01 / 04   WELCOME"
    finally:
        wizard.close()


def test_russian_preserves_user_values_paths_and_raw_diagnostics() -> None:
    path = r"C:\\Игры\\EveJS\\mods"
    raw = f"{path}: checksum 9A7F does not match"

    assert i18n.translate_ui_phrase(raw, "ru") == raw
    rendered = i18n.format_ui_phrase(
        "The Mods folder could not be opened.\n\nDetails: {details}",
        "ru",
        details=raw,
    )
    assert raw in rendered
    assert "{details}" not in rendered


def test_representative_russian_copy_covers_every_major_ui_surface() -> None:
    expected = {
        # Main shell and pages.
        "Home": "Главная",
        "Start Stack": "Запустить стек",
        "Mods": "Моды",
        # First-run wizard and updater.
        "STEP 01 / 04   WELCOME": "ШАГ 01 / 04   ПРИВЕТСТВИЕ",
        "Preparing the update…": "Подготовка обновления…",
        "Update could not finish": "Не удалось завершить обновление",
        # Mods onboarding and author documentation.
        "Create Mod Folder": "Создать папку модов",
        "Open Mod Author Guide": "Открыть руководство для авторов модов",
        "To add a mod, place the mod's folder inside this folder, then click Refresh.": (
            "Чтобы добавить мод, поместите папку мода в эту папку и "
            "нажмите «Обновить»."
        ),
        # Shipboard caption surface.
        "LYRA online. Shipboard systems ready.": (
            "LYRA на связи. Бортовые системы готовы."
        ),
    }

    for source, russian in expected.items():
        assert UI_PHRASES[source] == russian
        assert i18n.translate_ui_phrase(source, "ru") == russian
