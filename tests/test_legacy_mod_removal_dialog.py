from src.core.mod_management import LegacyRemovalConflict
from src.widgets.legacy_mod_removal_dialog import LegacyModRemovalDialog
from PyQt6.QtWidgets import QPushButton


def test_readonly_conflict_review_has_no_force_remove_action(qapp):
    review = (
        LegacyRemovalConflict("shared.json", "overlap", "sha256", "a" * 64, b'{"manual":1}'),
        LegacyRemovalConflict("shared.dll", "overlap", "absent", None, b"\x00\xff"),
    )
    dialog = LegacyModRemovalDialog(review)
    assert dialog.current.isReadOnly() and dialog.expected.isReadOnly()
    assert dialog.current.toPlainText() == '{"manual":1}'
    assert "a" * 64 in dialog.expected.toPlainText()
    assert "Replacement contents" in dialog.expected.toPlainText()
    assert len(dialog.findChildren(QPushButton)) == 1
    dialog.files.setCurrentIndex(1)
    assert "Binary file" in dialog.current.toPlainText()
    assert dialog.expected.toPlainText() == "File does not exist"
    dialog.close()
