import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from src.core.mod_sorting import ModSortEntry, ModSortPreview
from src.widgets.mod_sort_dialog import ModSortDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def preview(*, issues=(), changed=True):
    one = ModSortEntry("one", "One", "mods/one", True, (("loadAfter", ("two",)),))
    two = ModSortEntry("two", "Two", "mods/two", False, ())
    return ModSortPreview(Path.cwd(), "native", (one, two), (two, one) if changed else (one, two), issues, "a" * 64)


def test_cancel_and_accept_are_distinct(app):
    dialog = ModSortDialog(preview())
    assert dialog.table.item(0, 0).text() == "One"
    assert "Two" in dialog.table.item(0, 1).text()
    dialog.reject()
    assert not dialog.save_requested
    dialog = ModSortDialog(preview())
    dialog.accept()
    assert dialog.save_requested


@pytest.mark.parametrize("value", [preview(issues=("Cycle: one / two",)), preview(changed=False)])
def test_unsafe_or_unchanged_preview_cannot_be_accepted(app, value):
    dialog = ModSortDialog(value)
    assert not dialog.save_button.isEnabled()
    dialog.accept()
    assert not dialog.save_requested


@pytest.mark.parametrize("language", ["zh_CN", "ja", "ko", "fr", "de", "nl", "ru"])
def test_sorter_catalog_and_actual_dialog_controls_are_localized(app, language):
    from src.i18n import set_language, translate_ui_phrase
    from src.translations_mod_sorting import SOURCE_PHRASES
    try:
        set_language(language)
        assert all(translate_ui_phrase(source) != source for source in SOURCE_PHRASES)
        dialog = ModSortDialog(preview())
        assert dialog.windowTitle() != "Sort by dependencies"
        assert dialog.save_button.text() != "Save order"
        assert dialog.table.horizontalHeaderItem(0).text() != "Saved order"
    finally:
        set_language("en")


def test_actual_mods_page_sort_control_obeys_runtime_policy(app, tmp_path, monkeypatch):
    from src.pages.mods_page import ModsPage
    from src.core import mod_management
    from src.core.service_status import RuntimeBackend, DockerControlPolicy
    def unmanaged(*_args):
        raise mod_management.ModNotManagedError("Disposable mod")
    monkeypatch.setattr(mod_management, "read_managed_mod_registration", unmanaged)
    for name in ("one", "two"):
        folder = tmp_path/"mods"/name
        folder.mkdir(parents=True)
        (folder/"loader.js").write_text("// Inert loader\n")
    page = ModsPage(defer_inventory=True)
    page.set_evejs_root(str(tmp_path))
    from src.core.mod_inventory import load_mod_inventory
    page.show_inventory(load_mod_inventory(str(tmp_path)))
    assert len(page.mods()) == 2
    emitted = []
    page.sort_requested.connect(lambda: emitted.append(True))
    page.sort_mods_btn.click()
    assert emitted == [True]
    page.set_runtime_context(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.CONNECT_ONLY)
    assert not page.sort_mods_btn.isEnabled()
    page.set_runtime_context(RuntimeBackend.DOCKER_COMPOSE, DockerControlPolicy.MANAGED)
    assert page.sort_mods_btn.isEnabled()
    page.set_lifecycle_busy(True)
    assert not page.sort_mods_btn.isEnabled()
    page.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("width", [740, 1100])
def test_sort_button_shares_import_row_without_clipping(app, width):
    from src.pages.mods_page import ModsPage
    from src.theme import build_qss
    original = app.styleSheet()
    app.setStyleSheet(build_qss({"header":"Segoe UI", "body":"Segoe UI", "mono":"Consolas"}))
    page = ModsPage(defer_inventory=True)
    page.resize(width, 900)
    page.show()
    app.processEvents()
    try:
        first = page.add_zip_btn.mapTo(page, page.add_zip_btn.rect().topLeft())
        sorter = page.sort_mods_btn.mapTo(page, page.sort_mods_btn.rect().topLeft())
        assert first.y() == sorter.y()
        assert sorter.x() > page.add_folder_btn.mapTo(page, page.add_folder_btn.rect().topRight()).x()
        assert page.sort_mods_btn.width() >= page.sort_mods_btn.fontMetrics().horizontalAdvance(page.sort_mods_btn.text()) + 16
        assert sorter.x() + page.sort_mods_btn.width() <= page.width()
    finally:
        page.close(); page.deleteLater(); app.processEvents(); app.setStyleSheet(original)
