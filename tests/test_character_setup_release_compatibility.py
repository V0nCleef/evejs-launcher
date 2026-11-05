"""The character setup UI must coexist with the public activation journal."""
import json

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtTest import QSignalSpy

from src import config
from src.core.mod_activation_state import mod_activation_state_path, read_mod_activation_state
from src.core.overview_patch import OverviewPatchState, OverviewPatchStatus
from src.widgets.new_character_dialog import NewCharacterDialog


def test_easy_setup_preserves_schema2_public_mod_intent(qapp, tmp_path, monkeypatch):
    root = tmp_path / "EveJS"
    root.mkdir()
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "launcher-profile")
    journal = mod_activation_state_path(root)
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({
        "schemaVersion": 2,
        "root": str(root.resolve()),
        "records": {"path:mods/example": {
            "contractSha256": "a" * 64,
            "desired": True,
            "phase": "pending_restart",
            "errorCode": None,
            "updatedAt": "2026-11-05T20:00:00.000000Z",
        }},
    }), encoding="utf-8")
    before = journal.read_bytes()
    state = read_mod_activation_state(root)
    assert state.schema_version == 2
    assert state.intents[0].id == "path:mods/example"

    dialog = NewCharacterDialog([], OverviewPatchStatus(OverviewPatchState.READY, "Ready"), set())
    try:
        requested = QSignalSpy(dialog.account_login_requested)
        dialog.account_edit.setText("fixture-account")
        dialog.easy_button.click()
        dialog.create_button.click()
        assert list(requested) == [["fixture-account"]]
        assert journal.read_bytes() == before
    finally:
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
