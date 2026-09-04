"""Regression tests for the copied EVE client path contract."""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.discovery import (
    find_client_path,
    find_dlss5_launch_environment,
    resolve_client_tq_path,
)


def _client_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    tq = tmp_path / "SharedCache" / "tq"
    executable = tq / "bin64" / "exefile.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    (tq / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    return tq, executable.parent, executable


@pytest.mark.parametrize(
    "selection",
    ("shared_cache", "tq", "bin64", "executable"),
)
def test_client_path_resolver_maps_supported_selections_to_tq(
    tmp_path: Path,
    selection: str,
) -> None:
    tq, bin64, executable = _client_fixture(tmp_path)
    selected = {
        "shared_cache": tq.parent,
        "tq": tq,
        "bin64": bin64,
        "executable": executable,
    }[selection]

    assert resolve_client_tq_path(selected) == tq


def test_client_path_resolver_rejects_unrelated_or_incomplete_paths(
    tmp_path: Path,
) -> None:
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "exefile.exe").write_bytes(b"fixture")

    assert resolve_client_tq_path(unrelated) is None
    assert resolve_client_tq_path(tmp_path / "missing" / "bin64") is None


def test_find_client_path_canonicalizes_batch_executable_selection(
    tmp_path: Path,
) -> None:
    tq, _bin64, executable = _client_fixture(tmp_path)
    evejs_root = tmp_path / "EveJS"
    config_path = (
        evejs_root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f'  SET "EVEJS_CLIENT_PATH={executable}"\n',
        encoding="utf-8",
    )

    assert find_client_path(str(evejs_root)) == str(tq)


def test_find_client_path_expands_official_repo_root_placeholder(
    tmp_path: Path,
) -> None:
    evejs_root = tmp_path / "EveJS"
    tq = evejs_root / "client" / "EVE" / "tq"
    executable = tq / "bin64" / "exefile.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    (tq / "start.ini").write_text("build=3396210\n", encoding="utf-8")
    config_path = (
        evejs_root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    )
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        'set "EVEJS_CLIENT_PATH=%EVEJS_REPO_ROOT%\\client\\EVE\\tq"\n',
        encoding="utf-8",
    )

    assert find_client_path(str(evejs_root)) == str(tq)


def _write_client_config(
    evejs_root: Path,
    client_path: Path,
    *,
    marker: str | None = None,
    platform: str | None = None,
) -> None:
    config_path = (
        evejs_root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'set "EVEJS_CLIENT_PATH={client_path}"']
    if platform is not None:
        lines.append(f'set "TRINITYPLATFORM={platform}"')
    if marker is not None:
        lines.append(f'set "EVEJS_DLSS5={marker}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_dlss5_launch_environment_is_absent_without_integration_marker(
    tmp_path: Path,
) -> None:
    tq, _bin64, _executable = _client_fixture(tmp_path / "client")
    evejs_root = tmp_path / "EveJS"
    _write_client_config(evejs_root, tq, platform="dx12")

    assert find_dlss5_launch_environment(str(evejs_root), tq) == {}


def test_dlss5_launch_environment_requires_dx12_and_matching_client(
    tmp_path: Path,
) -> None:
    tq, _bin64, _executable = _client_fixture(tmp_path / "client")
    evejs_root = tmp_path / "EveJS"
    _write_client_config(evejs_root, tq, marker="ON", platform="DX12")

    assert find_dlss5_launch_environment(str(evejs_root), tq) == {
        "TRINITYPLATFORM": "dx12",
        "EVEJS_DLSS5": "on",
    }


def test_dlss5_launch_environment_rejects_incomplete_marked_config(
    tmp_path: Path,
) -> None:
    tq, _bin64, _executable = _client_fixture(tmp_path / "client")
    evejs_root = tmp_path / "EveJS"
    _write_client_config(evejs_root, tq, marker="on", platform="dx11")

    with pytest.raises(RuntimeError, match="TRINITYPLATFORM setting is not dx12"):
        find_dlss5_launch_environment(str(evejs_root), tq)


def test_dlss5_launch_environment_rejects_a_different_selected_client(
    tmp_path: Path,
) -> None:
    configured_tq, _bin64, _executable = _client_fixture(
        tmp_path / "configured-client"
    )
    selected_tq, _bin64, _executable = _client_fixture(tmp_path / "selected-client")
    evejs_root = tmp_path / "EveJS"
    _write_client_config(
        evejs_root,
        configured_tq,
        marker="on",
        platform="dx12",
    )

    with pytest.raises(RuntimeError, match="different copied EVE client"):
        find_dlss5_launch_environment(str(evejs_root), selected_tq)


def test_main_window_canonicalizes_legacy_executable_path_for_all_consumers(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tq, _bin64, executable = _client_fixture(tmp_path)
    cfg = deepcopy(config.DEFAULT_CONFIG)
    cfg.update(
        {
            "evejs_root": str(tmp_path / "EveJS"),
            "client_path": str(executable),
            "update_auto_check": False,
            "update_check_interval_hours": 0,
        }
    )
    monkeypatch.setattr(config, "load", lambda: deepcopy(cfg))
    monkeypatch.setattr(config, "save", lambda _cfg: None)
    monkeypatch.setattr(app_module, "load_accounts", lambda _root: [])
    # This checks path normalization, not asynchronous accounts or native audio.
    # Queued startup callbacks must not outlive this fixture and start a worker
    # while the next test's event loop deletes its MainWindow parent.
    monkeypatch.setattr(MainWindow, "_refresh_characters", lambda _self: None)
    monkeypatch.setattr(MainWindow, "_prepare_shipboard_voice", lambda _self: None)
    monkeypatch.setattr(MainWindow, "_start_launcher_ambience", lambda _self: None)

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    try:
        assert window._cfg["client_path"] == str(tq)
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
