"""Regression tests for the copied EVE client path contract."""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy

import pytest

from src import app as app_module
from src import config
from src.app import MainWindow
from src.core.discovery import find_client_path, resolve_client_tq_path


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

    window = MainWindow()
    window._status_timer.stop()
    window._prune_timer.stop()
    try:
        assert window._cfg["client_path"] == str(tq)
    finally:
        window.deleteLater()
