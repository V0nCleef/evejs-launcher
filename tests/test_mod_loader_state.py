from pathlib import Path

import pytest

from src.core.mod_loader_state import LoaderStateError, resolve_loader_state
from src.core.mod_manifest import scan_mods, set_mod_active
from src.core.mod_runtime_state import mod_contract_sha256
from src.core.runtime.docker_mods import _validate_active_loaders


def test_active_loader_and_ordinary_backup_work_in_all_consumers(tmp_path):
    folder = tmp_path / "mods/Example"
    folder.mkdir(parents=True)
    (folder / "loader.js").write_bytes(b"// executable payload")
    backup = folder / "loader.js.bak"
    backup.write_bytes(b"// older user backup")
    mod = scan_mods(tmp_path)[0]
    assert mod.valid and mod.active
    fingerprint = mod_contract_sha256(mod)
    _validate_active_loaders(tmp_path, ("Example",))
    backup.write_bytes(b"// changed unrelated backup")
    assert mod_contract_sha256(mod) == fingerprint
    assert set_mod_active(mod, False) is False
    assert backup.read_bytes() == b"// changed unrelated backup"
    assert resolve_loader_state(folder).selected_path.name == "loader.js.disabled"
    assert mod_contract_sha256(mod) == fingerprint
    assert set_mod_active(mod, True) is True
    assert mod_contract_sha256(mod) == fingerprint
    _validate_active_loaders(tmp_path, ("Example",))


@pytest.mark.parametrize("suffix", ["disabled", "off", "bak"])
def test_sole_legacy_disabled_payload_remains_recognized(tmp_path, suffix):
    folder = tmp_path / "mods/Example"
    folder.mkdir(parents=True)
    (folder / f"loader.js.{suffix}").write_bytes(b"// old disabled payload")
    state = resolve_loader_state(folder, root=tmp_path)
    assert state.disabled_path.name == f"loader.js.{suffix}"
    assert not state.active
    assert set_mod_active(scan_mods(tmp_path)[0], True) is True


def test_real_explicit_loader_state_conflict_remains_visible(tmp_path):
    folder = tmp_path / "mods/Example"
    folder.mkdir(parents=True)
    for name in ("loader.js", "loader.js.disabled"):
        (folder / name).write_bytes(b"// ambiguous")
    with pytest.raises(LoaderStateError, match="Both"):
        resolve_loader_state(folder)
    assert not scan_mods(tmp_path)[0].valid


def test_active_loader_does_not_inspect_unrelated_backup_target(tmp_path):
    folder = tmp_path / "mods/Example"
    folder.mkdir(parents=True)
    (folder / "loader.js").write_bytes(b"// active")
    (folder / "loader.js.bak").mkdir()
    assert resolve_loader_state(folder).active
    _validate_active_loaders(tmp_path, ("Example",))
