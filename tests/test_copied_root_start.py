"""Copied registries remain read-only during startup validation."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from PyQt6.QtWidgets import QMainWindow

from src.app import MainWindow
from src import config
from src.core.mod_lifecycle_lock import acquire_mod_lifecycle_lease


def test_malformed_copied_registry_reports_failure_and_releases_lease(qapp, monkeypatch, tmp_path: Path):
    root = tmp_path / "renamed-install"
    registry = root / "_local/launcher-mods/registry.json"
    registry.parent.mkdir(parents=True)
    active_mod = root / "mods/Example"
    active_mod.mkdir(parents=True)
    (active_mod / "loader.js").write_text("// active fixture loader", encoding="utf-8")
    original = json.dumps({"schemaVersion": 999, "root": str(tmp_path / "original-install"),
                           "order": [], "records": {}})
    registry.write_text(original, encoding="utf-8")
    window = MainWindow.__new__(MainWindow)
    QMainWindow.__init__(window)
    window._cfg = deepcopy(config.DEFAULT_CONFIG)
    window._cfg.update(evejs_root=str(root), runtime_backend="native")
    window._server_proc = None
    window._market_proc = None
    window._lifecycle_thread = None
    dialogs = []
    monkeypatch.setattr("src.app.QMessageBox.critical", lambda *args: dialogs.append(args[-1]))
    monkeypatch.setattr("src.app.start_game_server", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    monkeypatch.setattr("src.app.start_market_server", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
    try:
        assert not window._start_service_sequence(start_market=True, start_game=True,
                                                  mode="vanilla", on_ready=None,
                                                  error_title="Game Server Error")
        assert len(dialogs) == 1
        assert "unsupported schema or invalid root metadata" in dialogs[0]
        assert window._server_error
        assert window._mod_lifecycle_lease is None
        assert window._lifecycle_thread is None
        assert registry.read_text(encoding="utf-8") == original
        lease = acquire_mod_lifecycle_lease(root)
        lease.release()
    finally:
        window._release_mod_lifecycle_lease()
        window.deleteLater()


@pytest.mark.parametrize("backend", ["native", "docker_compose"])
def test_read_only_game_scan_keeps_undeclared_support_as_a_start_choice(tmp_path, backend):
    (tmp_path / "package.json").write_text('{"version":"0.12.9"}', encoding="utf-8")
    folder = tmp_path / "mods/example"
    folder.mkdir(parents=True)
    manifest = {"schemaVersion": 3, "id": "example", "displayName": "Example Mod",
                "version": "1.0.0", "kind": "loader", "restart": "game_server",
                "activation": {"strategy": "loader_rename"},
                "compatibility": {"evejsVersions": ["0.12.8"]}}
    payload = json.dumps(manifest)
    (folder / "evejs-launcher.mod.json").write_text(payload, encoding="utf-8")
    loader = folder / "loader.js"
    loader.write_text("// fixture loader", encoding="utf-8")
    mods = MainWindow._applicable_runtime_mods(str(tmp_path), backend=backend)
    assert len(mods) == 1
    assert mods[0].active
    from src.core.mod_evejs_compatibility import (
        EvejsModCompatibilityStatus,
        assess_active_runtime_mods,
    )
    compatibility = assess_active_runtime_mods(mods, "0.12.9")
    assert len(compatibility) == 1
    assert compatibility[0].status is EvejsModCompatibilityStatus.UNSUPPORTED
    assert "does not prove the mod is broken" in compatibility[0].message
    assert loader.read_text(encoding="utf-8") == "// fixture loader"
    assert (folder / "evejs-launcher.mod.json").read_text(encoding="utf-8") == payload
    # The same unchanged mod remains applicable on its declared older release.
    (tmp_path / "package.json").write_text('{"version":"0.12.8"}', encoding="utf-8")
    older_mods = MainWindow._applicable_runtime_mods(str(tmp_path), backend=backend)
    assert len(older_mods) == 1
    assert assess_active_runtime_mods(older_mods, "0.12.8")[0].status is EvejsModCompatibilityStatus.SUPPORTED
