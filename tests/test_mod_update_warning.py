from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from src.i18n import LANGUAGES, set_language, translate_ui_phrase as tr
from src.widgets.mod_update_dialog import ModUpdateDialog
from src.widgets.mod_update_warning import ModUpdateWarningDialog


def example():
    return (
        SimpleNamespace(name='<b>Example Mod</b>', version='1.0.0'),
        SimpleNamespace(version='1.1.0', notes='',
                        page_url='https://github.com/ExampleAuthor/ExampleMod/releases/tag/v1.1.0'),
    )


def test_unlisted_evejs_release_shows_advisory_without_disabling_update(qapp, tmp_path):
    set_language('en')
    mod, release = example()
    mod.evejs_root = tmp_path
    (tmp_path / 'package.json').write_text('{"version":"261018.1"}')
    release.evejs_versions = ('0.12.9',)
    dialog = ModUpdateDialog(mod, release)
    try:
        assert not dialog.compatibility_notice.isHidden()
        assert '261018.1' in dialog.compatibility_notice.text()
        assert '0.12.9' in dialog.compatibility_notice.text()
        assert 'still try' in dialog.compatibility_notice.text()
        assert dialog.update_btn.isEnabled()
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize('language', [option.code for option in LANGUAGES])
def test_warning_translates_and_preserves_source_as_plain_text(qapp, language):
    set_language(language)
    dialog = ModUpdateWarningDialog(*example())
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.windowTitle() == tr('Third-party mod update')
        assert dialog.mod_label.text() == '<b>Example Mod</b>'
        assert dialog.mod_label.textFormat() == Qt.TextFormat.PlainText
        assert dialog.source_label.text() == example()[1].page_url
        assert dialog.source_label.textFormat() == Qt.TextFormat.PlainText
        assert dialog.cancel_btn.isDefault()
        assert not dialog.confirm_btn.autoDefault()
        assert dialog.confirm_btn.text() == tr('I understand — update mod')
        if language != 'en':
            assert dialog.windowTitle() != 'Third-party mod update'
            assert not dialog.warning_labels[1].text().startswith('This launcher')
        assert dialog.rect().contains(dialog.confirm_btn.geometry())
    finally:
        dialog.close()
        dialog.deleteLater()
        set_language('en')


@pytest.mark.parametrize('accepted', [False, True])
def test_update_signal_requires_explicit_warning_acceptance(qapp, monkeypatch, accepted):
    dialog = ModUpdateDialog(*example())
    starts, warnings = [], []
    dialog.start_requested.connect(lambda: starts.append(True))

    def answer(warning):
        warnings.append(warning.source_label.text())
        assert not starts
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(ModUpdateWarningDialog, 'exec', answer)
    dialog.update_btn.click()
    assert len(warnings) == 1
    assert starts == ([True] if accepted else [])
    if not accepted:
        dialog.update_btn.click()
        assert len(warnings) == 2  # Consent is never remembered or bypassed.
        assert starts == []
    dialog.close()
    dialog.deleteLater()


@pytest.mark.parametrize('key', [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_enter_and_escape_default_to_cancel(qapp, key):
    dialog = ModUpdateWarningDialog(*example())
    dialog.show()
    qapp.processEvents()
    QTest.keyClick(dialog.cancel_btn, key)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.isVisible()
    dialog.deleteLater()
