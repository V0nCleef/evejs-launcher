"""Persistent generic batch entries and one-shot server startup behavior."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import subprocess

import pytest
from PyQt6.QtCore import QEvent

from src.core import startup_scripts as scripts


@pytest.fixture(autouse=True)
def complete_qt_deletion(qapp):
    yield
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def entry(tmp_path, name="Example Tool", **values):
    filename = tmp_path / "Example Tool" / "Start.bat"
    filename.parent.mkdir(exist_ok=True)
    filename.write_text("@echo off\n", encoding="utf-8")
    return scripts.ScriptEntry.create(name, str(filename), **values)


class Process:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


def test_entries_persist_per_installation_and_edit_remove(tmp_path):
    root, other = tmp_path / "EveJS", tmp_path / "Other EveJS"
    root.mkdir()
    other.mkdir()
    filename = tmp_path / "launcher" / "startup_scripts.json"
    store = scripts.ScriptStore(filename)
    original = entry(tmp_path, arguments='start --name "a b"', auto_start=True)
    store.save(root, original)
    assert scripts.ScriptStore(filename).list(root) == [original]
    assert store.list(other) == []
    edited = replace(original, name="Edited", auto_start=False)
    store.save(root, edited)
    assert store.list(root) == [edited]
    store.remove(root, edited.id)
    assert store.list(root) == []
    assert Path(original.filename).is_file()


def test_corrupt_store_is_not_overwritten(tmp_path):
    root = tmp_path / "EveJS"
    root.mkdir()
    filename = tmp_path / "startup_scripts.json"
    filename.write_text("broken json", encoding="utf-8")
    store = scripts.ScriptStore(filename)
    with pytest.raises(scripts.ScriptError):
        store.save(root, entry(tmp_path))
    assert filename.read_text() == "broken json"


def test_failed_atomic_replace_preserves_previous_entries(tmp_path, monkeypatch):
    root = tmp_path / "EveJS"
    root.mkdir()
    filename = tmp_path / "startup_scripts.json"
    store = scripts.ScriptStore(filename)
    original = entry(tmp_path)
    store.save(root, original)
    monkeypatch.setattr(scripts.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(OSError):
        store.save(root, replace(original, name="Changed"))
    assert store.list(root) == [original]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("values", [
    {"name": ""}, {"filename": "relative.bat"}, {"filename": "C:/Tools/a.exe"},
    {"arguments": "start\nwhoami"}, {"auto_start": "yes"},
])
def test_invalid_entries_are_rejected(tmp_path, values):
    original = entry(tmp_path)
    with pytest.raises(scripts.ScriptError):
        scripts.validate_entry(replace(original, **values))


def test_auto_runs_only_enabled_entries_once_per_server_session(qapp, tmp_path):
    root = tmp_path / "EveJS"
    root.mkdir()
    store = scripts.ScriptStore(tmp_path / "startup_scripts.json")
    enabled = entry(tmp_path, auto_start=True)
    disabled = replace(enabled, id="b" * 32, name="Manual", arguments="manual", auto_start=False)
    store.save(root, enabled)
    store.save(root, disabled)
    launched = []
    process = Process()
    controller = scripts.StartupScriptController(store, launcher=lambda item: launched.append(item) or process)
    try:
        controller.start_for_server(str(root), "session-1")
        controller.start_for_server(str(root), "session-1")
        assert launched == [enabled]
        assert controller.status(root, enabled.id)[0] == "RUNNING"
        process.code = 0
        controller.poll()
        assert controller.status(root, enabled.id)[0] == "COMPLETED"
        controller.start_for_server(str(root), "session-1")
        assert launched == [enabled]
        controller.start_for_server(str(root), "session-2")
        assert launched == [enabled, enabled]
    finally:
        controller.deleteLater()


def test_running_script_is_not_spawned_twice_and_failure_is_independent(qapp, tmp_path):
    root = tmp_path / "EveJS"
    root.mkdir()
    good = entry(tmp_path, auto_start=True)
    bad = replace(good, id="c" * 32, name="Broken", arguments="bad")
    store = scripts.ScriptStore(tmp_path / "startup_scripts.json")
    store.save(root, bad)
    store.save(root, good)
    calls = []
    def launch(item):
        calls.append(item.id)
        if item.id == bad.id:
            raise OSError("fixture failure")
        return Process()
    controller = scripts.StartupScriptController(store, launcher=launch)
    try:
        controller.start_for_server(str(root), "session")
        controller.run(str(root), good)
        assert calls == [bad.id, good.id]
        assert controller.status(root, bad.id) == ("FAILED", "fixture failure")
        assert controller.status(root, good.id)[0] == "RUNNING"
    finally:
        controller.deleteLater()


def test_real_batch_preserves_working_directory_and_literal_arguments(tmp_path, monkeypatch):
    folder = tmp_path / "Tools %PATH% & wow! (demo)"
    folder.mkdir()
    filename = folder / "Start %PATH% & wow!.bat"
    filename.write_text('@echo off\n> receipt.txt echo "%CD%"\n>> receipt.txt echo %~1\n>> receipt.txt echo %~2\n>> receipt.txt echo %~3\n', encoding="utf-8")
    original_popen = subprocess.Popen
    observed_flags = []
    def quiet_test_process(*args, **kwargs):
        observed_flags.append(kwargs["creationflags"])
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return original_popen(*args, **kwargs)
    monkeypatch.setattr(scripts.subprocess, "Popen", quiet_test_process)
    item = scripts.ScriptEntry.create("Fixture", str(filename), arguments='"a b" "100%" "wow!"')
    process = scripts.launch_batch(item)
    assert process.wait(timeout=10) == 0
    assert (folder / "receipt.txt").read_text().splitlines() == ['"' + str(folder) + '"', "a b", "100%", "wow!"]
    assert observed_flags == [subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP]


def test_shell_operators_in_arguments_are_literal(tmp_path, monkeypatch):
    filename = tmp_path / "Start.bat"
    filename.write_text('@echo off\n> receipt.txt echo "%~1"\n', encoding="utf-8")
    original = subprocess.Popen
    def quiet(*args, **kwargs):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return original(*args, **kwargs)
    monkeypatch.setattr(scripts.subprocess, "Popen", quiet)
    item = scripts.ScriptEntry.create("Fixture", str(filename), arguments='"safe & echo injected > injected.txt"')
    process = scripts.launch_batch(item)
    assert process.wait(timeout=10) == 0
    assert not (tmp_path / "injected.txt").exists()
    assert "safe & echo injected > injected.txt" in (tmp_path / "receipt.txt").read_text()
