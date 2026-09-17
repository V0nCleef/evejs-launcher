"""Offline guide navigation works without opening external apps or a browser."""
from pathlib import Path
import json
import re
import pytest

from PyQt6.QtCore import QEvent, QUrl, Qt
from PyQt6.QtWidgets import QPushButton, QLabel
from PyQt6.QtGui import QImage, QTextDocument

from src.widgets.mod_authoring_guide import ModAuthoringGuide
from src.core.mod_guide import catalog_paths, render_page
from src.i18n import LANGUAGES, current_language, set_language

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def dispose_guide_windows(qapp):
    """Finish Qt's deferred window deletion before the next test starts."""
    yield
    for widget in qapp.topLevelWidgets():
        if isinstance(widget, ModAuthoringGuide):
            widget.close()
            widget.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_guide_navigation_examples_search_and_back_are_local(qapp, tmp_path):
    app = qapp
    source = Path(__file__).resolve().parents[1]
    guide = ModAuthoringGuide(bundle_root=source)
    assert "Build an EveJS Mod" in guide.browser.toPlainText()
    guide._follow_link(QUrl("mod-authoring/helper-intro.md"))
    assert guide.location.text() == "docs/mod-authoring/helper-intro.md"
    guide.search.setText("preparation")
    guide._find()
    assert guide.browser.textCursor().selectedText().casefold() == "preparation"
    before = guide.browser.toPlainText()
    guide._follow_link(QUrl("../../examples/mods/profile-options/helper.js"))
    assert guide.browser.toPlainText() == before
    guide._follow_link(QUrl('example-files.md'))
    assert guide.navigation.currentItem().text() == '15. Example files'
    guide._back()
    assert guide.location.text() == "docs/mod-authoring/helper-intro.md"
    before = guide.browser.toPlainText()
    guide._follow_link(QUrl.fromLocalFile(str(tmp_path / "outside.md")))
    assert guide.browser.toPlainText() == before
    guide.close()
    app.processEvents()


def test_all_walkthrough_languages_keep_code_and_links_and_english_export_in_sync():
    source = json.loads((ROOT / "docs/mod-authoring/guide-content.json").read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "docs/mod-authoring/navigation.json").read_text(encoding="utf-8"))
    allowed = {(ROOT / path).resolve() for path in catalog_paths(catalog)}
    for page in source["pages"]:
        english = render_page(source, page, "en")
        assert (ROOT / page["path"]).read_text(encoding="utf-8") == english
        code = re.findall(r"```[\s\S]*?```", english)
        for option in LANGUAGES:
            translated = render_page(source, page, option.code)
            assert re.findall(r"```[\s\S]*?```", translated) == code
            for link in re.findall(r"\]\(([^)]+)\)", translated):
                assert (ROOT / page["path"]).parent.joinpath(link).resolve() in allowed
            if option.code != "en":
                assert translated != english


def test_guide_switches_language_without_changing_launcher_or_losing_page(qapp):
    previous = current_language()
    set_language("nl")
    guide = ModAuthoringGuide(bundle_root=ROOT)
    try:
        assert guide.language.currentData() == "nl"
        guide._display(ROOT / "docs/mod-authoring/configure.md")
        before_history = list(guide._history)
        assert "Wat de speler ziet" in guide.browser.toPlainText()
        guide.language.setCurrentIndex(guide.language.findData("ja"))
        assert current_language() == "nl"
        assert guide._history == before_history
        assert guide._current.name == "configure.md"
        assert "プレイヤーに見えるもの" in guide.browser.toPlainText()
        assert not hasattr(guide, 'show_code')
        assert '"scanInterval"' in guide.browser.toPlainText()
        guide._back()
        assert guide._current.name == "MOD_AUTHORING.md"
        assert not hasattr(guide, 'section')
        previous_page = guide._current
        guide._display(ROOT / 'docs/mod-authoring/reference.md')
        assert guide._current == previous_page
    finally:
        guide.close()
        set_language(previous)


def test_zoom_changes_rendered_text_with_launcher_styles_and_survives_navigation(qapp):
    from src import theme
    previous = qapp.styleSheet()
    qapp.setStyleSheet(theme.build_qss(theme.load_fonts()))
    guide = ModAuthoringGuide(bundle_root=ROOT)
    guide.show()
    qapp.processEvents()
    try:
        assert guide.minimumWidth() == 1140 and guide.minimumHeight() == 800
        assert guide.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint
        def size():
            return next(f.charFormat().fontPointSize() for f in guide._fragments()
                        if not f.charFormat().isImageFormat())
        initial = size()
        height = guide.browser.document().size().height()
        guide.zoom_in.click()
        qapp.processEvents()
        assert size() > initial
        assert guide.browser.document().size().height() > height
        guide.zoom_out.click()
        assert abs(size() - initial) < 0.01
        guide.zoom_in.click()
        guide._display(ROOT / 'docs/mod-authoring/mod-updates.md')
        assert size() > initial
    finally:
        guide.close()
        qapp.setStyleSheet(previous)


def test_update_screenshot_opens_zoomable_local_original(qapp):
    guide = ModAuthoringGuide(bundle_root=ROOT)
    guide._display(ROOT / 'docs/mod-authoring/mod-updates.md')
    image_path = ROOT / 'docs/mod-authoring/images/update-available.png'
    image = QImage(str(image_path))
    assert image.width() >= 2 * int(image.text('logicalWidth'))
    guide._follow_link(QUrl.fromLocalFile(str(image_path)))
    viewer = guide._image_viewer
    try:
        assert viewer is not None and viewer.isVisible()
        label = next(label for label in viewer.findChildren(QLabel) if label.pixmap() is not None and not label.pixmap().isNull())
        before = label.width()
        next(b for b in viewer.findChildren(QPushButton) if b.text() == 'A+').click()
        assert label.width() > before
        next(b for b in viewer.findChildren(QPushButton) if b.text() == '100%').click()
        assert label.width() == image.width()
    finally:
        viewer.close()
        guide.close()


def test_local_images_load_on_first_visit_fit_and_reject_unlisted_resources(qapp, tmp_path):
    guide = ModAuthoringGuide(bundle_root=ROOT)
    guide.resize(850, 600)
    guide.show()
    guide._display(ROOT / "docs/mod-authoring/configure.md")
    qapp.processEvents()
    try:
        formats = [fragment.charFormat().toImageFormat() for fragment in guide._fragments()
                   if fragment.charFormat().isImageFormat()]
        assert len(formats) >= 7
        for style in formats:
            url = guide.browser.document().baseUrl().resolved(QUrl(style.name()))
            image = guide.browser.document().resource(QTextDocument.ResourceType.ImageResource, url)
            assert isinstance(image, QImage) and not image.isNull()
            assert 0 < style.width() <= guide.browser.viewport().width()
        assert guide.browser.loadResource(QTextDocument.ResourceType.ImageResource,
                                          QUrl("https://example.invalid/image.png")) is None
        assert guide.browser.loadResource(QTextDocument.ResourceType.ImageResource,
                                          QUrl.fromLocalFile(str(tmp_path / "private.png"))) is None
    finally:
        guide.close()


def test_walkthrough_links_are_visible_topics_and_instructions_have_screenshots():
    source = json.loads((ROOT / 'docs/mod-authoring/guide-content.json').read_text(encoding='utf-8'))
    catalog = json.loads((ROOT / 'docs/mod-authoring/navigation.json').read_text(encoding='utf-8'))
    topics = {(ROOT / p['path']).resolve() for p in catalog['pages']}
    images = {(ROOT / p).resolve() for p in catalog['assets']}
    for page in source['pages']:
        text = render_page(source, page, 'en', include_code=False)
        assert '![' in text, page['id']
        for link in re.findall(r'\]\(([^)]+)\)', text):
            destination = (ROOT / page['path']).parent.joinpath(link).resolve()
            assert destination in topics | images, (page['id'], link)
        if page['id'] == 'start':
            continue  # The numbered contents list is navigation, not actions.
        for block in page['blocks']:
            if 'code' in block or block.get('detailOnly'):
                continue
            paragraphs = re.sub(r'\n(?=\d+\. )', '\n\n', block['en']).split('\n\n')
            for index, paragraph in enumerate(paragraphs):
                if re.match(r'\d+\. ', paragraph):
                    assert block.get('visuals', {}).get(str(index)) or block.get('afterImages'), (page['id'], paragraph)


def test_plain_chinese_prose_and_opt_in_handoff_preserve_code(qapp, tmp_path):
    guide = ModAuthoringGuide(bundle_root=ROOT)
    guide.language.setCurrentIndex(guide.language.findData('zh_CN'))
    guide._display(ROOT / 'docs/mod-authoring/configure.md')
    try:
        prose = guide.browser.toPlainText().split('代码示例')[0]
        for word in ('Configure', 'Save', 'Cancel', 'settings', 'label', 'minimum', 'maximum'):
            assert word not in prose, word
        assert 'preferences.json' in prose
        handoff = guide._handoff_text()
        assert '"scanInterval"' in handoff
        assert '"schemaVersion": 1' in handoff
        assert 'docs/mod-authoring/settings-reference.md' in handoff
        assert len(guide.browser.originals) <= 12
        archive = tmp_path / 'examples.zip'
        guide._write_examples(archive)
        import zipfile
        with zipfile.ZipFile(archive) as bundle:
            assert 'examples/tools/build_mod_update_metadata.py' in bundle.namelist()
            assert all(name.startswith('examples/') and '..' not in name for name in bundle.namelist())
    finally:
        guide.close()
