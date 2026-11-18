"""Migration decisions translate without treating installation names as UI."""
import json

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox, QDialog, QPushButton

from src.i18n import set_language, translate_ui_phrase
from src.pages.save_migration_page import SaveMigrationPage
from src.core.save_migration import verify_stock_game
from src.translations_save_migration import SOURCE_PHRASES, UI_PHRASES_BY_LANGUAGE


@pytest.mark.parametrize('language', ['nl','de','fr','ru','zh_CN','ja','ko'])
def test_page_and_review_translate_while_names_and_seed_files_stay_literal(qapp, tmp_path, language):
    set_language('en')
    page = SaveMigrationPage(verifier=verify_stock_game, can_start=lambda: True,
                             installation_label=lambda entry: entry['name'])
    entries = []
    for identity, name in (('first','Copy'),('second','Move')):
        root = tmp_path/identity
        (root/'config').mkdir(parents=True)
        (root/'config/version.json').write_text(json.dumps({'evejsVersion':'261115.2'}))
        entries.append({'id':identity,'name':name,'settings':{'evejs_root':str(root),'runtime_backend':'native'}})
    page.set_installations(entries, 'first')
    page._seed = tmp_path/'Move'
    try:
        set_language(language)
        page.retranslate_ui()
        assert page.copy.text() == translate_ui_phrase('Copy') != 'Copy'
        assert page.page_header.title_label.text() == translate_ui_phrase('Save Migration')
        assert page.page_header.eyebrow_label.text() == translate_ui_phrase('PLAYTHROUGH TRANSFER')
        assert page.seed_detail.text() == 'Move'
        assert page.source.currentText() == 'Copy'
        assert page.target.currentText() == 'Move'
        page.move.setChecked(True)
        assert page.from_label.text() == translate_ui_phrase('MOVE FROM')
        assert page.to_label.text() == translate_ui_phrase('MOVE TO')
        assert not page.review.text().startswith('Review')
        assert '261115.2' in page.source_detail.text()

        observed = []
        def review():
            dialog = next(widget for widget in page.findChildren(QDialog) if widget.isVisible())
            confirm = next(button for button in dialog.findChildren(QPushButton)
                           if button.text() == translate_ui_phrase('Move playthrough'))
            acknowledge = dialog.findChild(QCheckBox)
            observed.append((dialog.windowTitle(), acknowledge.text(), confirm.isEnabled()))
            dialog.reject()
        QTimer.singleShot(0, review)
        page._review()
        assert observed == [(translate_ui_phrase('Review transfer'),
            translate_ui_phrase('I’ve checked my open orders and understand what stays behind.'),False)]
        assert not page.busy
        diagnostic = 'Game still owns this save. Stop it and let its last write lease expire.'
        path = str(tmp_path/'Copy'/'Move')
        page._set_status(diagnostic+'\n'+path)
        assert page.status.text() == translate_ui_phrase(diagnostic)+'\n'+path
        set_language('en')
        page.retranslate_ui()
        assert page.status.text() == diagnostic+'\n'+path
        for phrase in SOURCE_PHRASES:
            assert UI_PHRASES_BY_LANGUAGE[language][phrase].strip()
    finally:
        page.deleteLater()
        set_language('en')
