import pytest

from src.core import mod_management
from src.core.local_mod_packages import LocalModPackages
from src.core.mod_manifest import scan_mods


@pytest.mark.parametrize("name", ["fourModeAsteroidBelts", "My Local Mod"])
def test_legacy_folder_identity_does_not_require_installer_grammar(tmp_path, monkeypatch, name):
    folder = tmp_path / "mods" / name
    folder.mkdir(parents=True)
    (folder / "loader.js").write_text("module.exports = {};\n")
    monkeypatch.setattr(mod_management, "_read_registry_values", lambda *_args, **_kwargs: None)
    mod = scan_mods(tmp_path)[0]
    assert LocalModPackages(tmp_path).can_manage(mod)
    record = LocalModPackages(tmp_path).remove(mod)
    assert not folder.exists()
    LocalModPackages(tmp_path).restore(record.record_id)
    assert (folder / "loader.js").is_file()


def test_mixed_case_legacy_identity_still_checks_installer_ownership(tmp_path, monkeypatch):
    folder = tmp_path / "mods" / "fourModeAsteroidBelts"
    folder.mkdir(parents=True)
    (folder / "loader.js").write_text("module.exports = {};\n")
    paths = []
    def damaged_registration(path, **_kwargs):
        paths.append(path)
        return {"damaged": "enrollment"}
    monkeypatch.setattr(mod_management, "_read_registry_values", damaged_registration)
    assert not LocalModPackages(tmp_path).can_manage(scan_mods(tmp_path)[0])
    assert paths and all(path.endswith("\\fourmodeasteroidbelts") for path in paths)
    assert (folder / "loader.js").is_file()
