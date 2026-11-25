from types import SimpleNamespace

from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMainWindow

from src import app as app_module
from src.app import MainWindow
from src.core import character_rename
from src.core.db import Account, Character


def _window(tmp_path):
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = {
        "evejs_root": str(tmp_path),
        "active_installation_profile": "first",
        "runtime_backend": "native",
    }
    window._data_selection = SimpleNamespace(target_identity="native:fixture")
    window._data_load_error = ""
    window._close_in_progress = False
    window._accounts = [
        Account("one", 11, "0", False, [Character(101, "Sabrine")]),
        Account("two", 12, "0", False, [Character(102, "Sabrine")]),
    ]
    return window


def _dispose(qapp, window):
    window.deleteLater()
    qapp.sendPostedEvents(window, QEvent.Type.DeferredDelete)


def test_rename_revalidates_installation_after_dialog(qapp, tmp_path, monkeypatch):
    window = _window(tmp_path)
    warnings = []

    def prompt(*_args, **_kwargs):
        window._cfg["active_installation_profile"] = "another"
        return "Sabrine Renamed", True

    monkeypatch.setattr(app_module.QInputDialog, "getText", prompt)
    monkeypatch.setattr(app_module.QMessageBox, "warning", lambda _window, _title, text: warnings.append(text))
    try:
        window._on_rename_character_requested("two", "Sabrine", 102)
        assert warnings == ["The selected account or character changed. Refresh and try again."]
        assert getattr(window, "_character_rename_thread", None) is None
    finally:
        _dispose(qapp, window)


def test_rename_runs_retained_worker_for_one_exact_character(qapp, tmp_path, monkeypatch):
    window = _window(tmp_path)
    dispatched = []
    busy = []
    refreshed = []
    message = []
    window._set_operation_controls_busy = busy.append
    window._refresh_characters = lambda: refreshed.append(True)

    def rename(request):
        dispatched.append(request)
        return character_rename.CharacterRenameResult(102, "Sabrine Renamed", True, tmp_path / "backup.json")

    monkeypatch.setattr(app_module, "is_eve_client_running", lambda: False)
    monkeypatch.setattr(app_module.QInputDialog, "getText", lambda *_args, **_kw: ("Sabrine Renamed", True))
    monkeypatch.setattr(app_module.QMessageBox, "information", lambda _window, _title, text: message.append(text))
    monkeypatch.setattr(character_rename, "rename_character", rename)
    try:
        window._on_rename_character_requested("two", "Sabrine", 102)
        for _ in range(300):
            if getattr(window, "_character_rename_thread", None) is None:
                break
            QTest.qWait(10)
        assert window._character_rename_thread is None
        (request,) = dispatched
        assert (request.account_id, request.character_id, request.old_name) == (12, 102, "Sabrine")
        assert busy == [True, False]
        assert refreshed == [True]
        assert message == ["Character renamed to Sabrine Renamed."]
    finally:
        retained_thread = getattr(window, "_character_rename_thread", None)
        if retained_thread is not None:
            retained_thread.wait(1_000)
        _dispose(qapp, window)
