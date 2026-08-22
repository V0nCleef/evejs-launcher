"""Archive preservation and source-transform tests for the overview patcher."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import zipfile
import zlib

import pytest

from src.core import overview_patch as patcher


_MINIMAL_SOURCE = """import logging

class OverviewPresetSvc(Service):
    __notifyevents__ = [
     'OnViewStateChanged']

    def Run(self, *args):
        Service.Run(self, *args)
        self._Setup()
        self.Initialize()
        return

    def _HadOverviewSettings(self):
        return False

    def _SetupDefaultOverviews(self):
        self.defaultOverviews = DefaultOverviews(general_settings_loader=(self.hadOverviewSettings or self)._LoadGeneralSettings if 1 else None)
        return

return
"""


def test_eve_process_probe_fails_closed_when_tasklist_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patcher.os, "name", "nt")
    monkeypatch.setattr(
        patcher.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("tasklist failed")),
    )

    assert patcher.is_eve_client_running() is True


def test_eve_process_probe_fails_closed_on_unsuccessful_tasklist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patcher.os, "name", "nt")
    result = subprocess.CompletedProcess([], 1, stdout="", stderr="access denied")
    monkeypatch.setattr(
        patcher.subprocess,
        "run",
        lambda *_args, **_kwargs: result,
    )

    assert patcher.is_eve_client_running() is True


def test_eve_process_probe_does_not_inherit_launcher_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(patcher.os, "name", "nt")
    observed: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess:
        observed.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout="INFO: No tasks are running")

    monkeypatch.setattr(patcher.subprocess, "run", fake_run)
    monkeypatch.setattr(patcher, "get_hidden_process_flags", lambda: {})

    assert patcher.is_eve_client_running() is False
    assert observed["stdin"] is subprocess.DEVNULL


@pytest.mark.skipif(sys.platform != "win32", reason="Windows standard-handle contract")
def test_consoleless_eve_process_probe_survives_invalid_stdin_handle(
    tmp_path: Path,
) -> None:
    """The real probe must not inherit pythonw's stale standard-input handle."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python = Path(sys.executable).with_name("python.exe")
    if not pythonw.is_file() or not python.is_file():
        pytest.skip("python.exe and pythonw.exe are required for this integration test")

    repo_root = Path(__file__).resolve().parents[1]
    result_path = tmp_path / "invalid-stdin-probe.json"
    script = textwrap.dedent(
        f"""
        import ctypes
        import json
        from pathlib import Path
        import subprocess
        import traceback

        from src.core import overview_patch as patcher

        result_path = Path({str(result_path)!r})
        real_run = subprocess.run
        spawned = False

        def deterministic_run(_argv, **kwargs):
            global spawned
            completed = real_run(
                [
                    {str(python)!r},
                    "-B",
                    "-c",
                    "import os; os.write(1, b'INFO: No tasks are running\\\\n')",
                ],
                **kwargs,
            )
            spawned = True
            return completed

        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetStdHandle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
            kernel32.SetStdHandle.restype = ctypes.c_int
            std_input_handle = -10 & 0xFFFFFFFF
            if not kernel32.SetStdHandle(
                std_input_handle,
                ctypes.c_void_p(-1),
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            try:
                real_run(
                    [
                        {str(python)!r},
                        "-B",
                        "-c",
                        "raise SystemExit(0)",
                    ],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except OSError as exc:
                inherited_stdin_error = getattr(exc, "winerror", None)
            else:
                inherited_stdin_error = None

            if inherited_stdin_error != 6:
                raise AssertionError(
                    "invalid STD_INPUT_HANDLE did not reproduce WinError 6; "
                    f"observed {{inherited_stdin_error!r}}"
                )

            patcher.subprocess.run = deterministic_run
            running = patcher.is_eve_client_running()
            payload = {{"running": running, "spawned": spawned}}
        except BaseException:
            payload = {{"error": traceback.format_exc(), "spawned": spawned}}

        result_path.write_text(json.dumps(payload), encoding="utf-8")
        """
    )

    completed = subprocess.run(
        [str(pythonw), "-B", "-c", script],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    assert completed.returncode == 0
    assert result_path.is_file(), "consoleless probe did not write its result"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert "error" not in payload, payload.get("error")
    assert payload == {"running": False, "spawned": True}


def test_source_transform_schedules_non_blocking_one_shot_bridge() -> None:
    source = patcher._patch_source(_MINIMAL_SOURCE)

    assert source.count(patcher._SOURCE_MARKER) == 1
    assert "import os" in source
    assert "import uthread2" in source
    assert "'OnSessionChanged'" not in source
    assert "def OnSessionChanged" not in source
    assert "uthread2.StartTasklet(self._EveJSOverviewBridgeWaitForSession)" in source
    assert "blue.synchro.SleepWallclock(5000)" in source
    assert "def _EveJSOverviewBridgeTryRun" in source
    assert patcher._DECOMPILER_ARTIFACT not in source
    assert patcher._DECOMPILER_REPAIR in source
    assert not source.rstrip().endswith("\nreturn")


def test_repack_preserves_duplicate_entries_by_zipinfo_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "code.ccp"
    stage_path = tmp_path / "stage.ccp"
    client = tmp_path / "client"
    (client / "bin64").mkdir(parents=True)
    original_pyc = patcher._PY27_MAGIC + b"\0\0\0\0fixture"
    original_entry = zlib.compress(original_pyc)
    with zipfile.ZipFile(source_path, "w") as archive:
        archive.writestr("duplicate.pyj", b"first")
        archive.writestr(patcher.TARGET_ENTRY, original_entry)
        archive.writestr("duplicate.pyj", b"second")

    monkeypatch.setattr(patcher, "_decompile_pyc", lambda _pyc: _MINIMAL_SOURCE)
    monkeypatch.setattr(
        patcher,
        "_compile_with_client_python",
        lambda _source, _dll, header: header + b"patched-bytecode",
    )
    monkeypatch.setattr(
        patcher,
        "_graft_original_overview_methods",
        lambda _original, compiled, _dll: compiled,
    )
    marker = patcher._build_patched_archive(source_path, stage_path, client)

    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(stage_path) as staged:
        source_infos = source.infolist()
        staged_infos = staged.infolist()
        assert [staged.read(staged_infos[0]), staged.read(staged_infos[2])] == [
            b"first",
            b"second",
        ]
        assert staged.read(staged_infos[1]) != source.read(source_infos[1])
        stored_marker = json.loads(staged.read(patcher.MARKER_ENTRY))
    assert stored_marker == marker
    assert marker["targetIndex"] == 1


def test_patched_status_requires_verified_marker_entry_and_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = tmp_path / "client"
    client.mkdir()
    (client / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    backup = client / patcher.BACKUP_NAME
    backup.write_bytes(b"original")
    patched_entry = b"patched"
    marker = {
        "patchVersion": patcher.PATCH_VERSION,
        "originalArchiveSHA256": patcher.SUPPORTED_CODE_SHA256,
        "targetEntry": patcher.TARGET_ENTRY,
        "targetIndex": 0,
        "originalEntrySHA256": "0" * 64,
        "patchedEntrySHA256": hashlib.sha256(patched_entry).hexdigest().upper(),
    }
    with zipfile.ZipFile(client / "code.ccp", "w") as archive:
        archive.writestr(patcher.TARGET_ENTRY, patched_entry)
        archive.writestr(patcher.MARKER_ENTRY, json.dumps(marker))
    monkeypatch.setattr(
        patcher,
        "_sha256",
        lambda path: patcher.SUPPORTED_CODE_SHA256
        if Path(path) == backup
        else "not-used",
    )

    status = patcher.inspect_overview_patch(client)

    assert status.state is patcher.OverviewPatchState.PATCHED


@pytest.mark.parametrize("legacy_version", [1, 2])
def test_legacy_blocking_bridge_is_restore_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    legacy_version: int,
) -> None:
    client = tmp_path / "client"
    client.mkdir()
    (client / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    backup = client / patcher.BACKUP_NAME
    backup.write_bytes(b"original")
    patched_entry = b"legacy-patched"
    marker = {
        "patchVersion": legacy_version,
        "originalArchiveSHA256": patcher.SUPPORTED_CODE_SHA256,
        "targetEntry": patcher.TARGET_ENTRY,
        "targetIndex": 0,
        "originalEntrySHA256": "0" * 64,
        "patchedEntrySHA256": hashlib.sha256(patched_entry).hexdigest().upper(),
    }
    with zipfile.ZipFile(client / "code.ccp", "w") as archive:
        archive.writestr(patcher.TARGET_ENTRY, patched_entry)
        archive.writestr(patcher.MARKER_ENTRY, json.dumps(marker))
    monkeypatch.setattr(
        patcher,
        "_sha256",
        lambda path: patcher.SUPPORTED_CODE_SHA256
        if Path(path) == backup
        else "not-used",
    )

    status = patcher.inspect_overview_patch(client)

    assert status.state is patcher.OverviewPatchState.LEGACY
    assert status.can_restore
    assert not status.can_patch
