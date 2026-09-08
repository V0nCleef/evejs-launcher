"""Offline guide navigation works without opening external apps or a browser."""
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

from src.widgets.mod_authoring_guide import ModAuthoringGuide


def test_guide_navigation_examples_search_and_back_are_local(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = Path(__file__).resolve().parents[1]
    guide = ModAuthoringGuide(bundle_root=source)
    assert "Make a mod work" in guide.browser.toPlainText()
    guide._follow_link(QUrl("mod-authoring/helpers.md"))
    assert guide.location.text() == "docs/mod-authoring/helpers.md"
    guide.search.setText("Contributions")
    guide._find()
    assert guide.browser.textCursor().selectedText().casefold() == "contributions"
    guide._follow_link(QUrl("../../examples/mods/profile-options/helper.js"))
    assert "const PROTOCOL" in guide.browser.toPlainText()
    guide._back()
    assert guide.location.text() == "docs/mod-authoring/helpers.md"
    before = guide.browser.toPlainText()
    guide._follow_link(QUrl.fromLocalFile(str(tmp_path / "outside.md")))
    assert guide.browser.toPlainText() == before
    guide.close()
    app.processEvents()
