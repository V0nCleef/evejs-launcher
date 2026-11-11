"""User-reviewed ordering, stale previews and policy boundaries."""
import json

import pytest

from src.core.local_mod_packages import LocalModPackages, LocalModPackageError
from src.core.mod_manifest import scan_mods
from src.core.mod_sorting import preview_mod_sort, apply_mod_sort


def package(root, name, *, active=True, **relationships):
    folder = root / "mods" / name
    folder.mkdir(parents=True)
    (folder / ("loader.js" if active else "loader.js.disabled")).write_text("module.exports = {};\n")
    payload = dict(schemaVersion=3, id=name, displayName=name, version="1.0", kind="loader",
        restart="game_server", activation={"strategy": "loader_rename"}, **relationships)
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(payload))
    return folder


def saved_order(root, *names):
    mods = {mod.id: mod for mod in scan_mods(root)}
    LocalModPackages(root).set_order([mods[name] for name in names])


def test_preview_cancel_is_read_only_and_commit_matches_runtime_order(tmp_path):
    package(tmp_path, "dependent", requires=["base"])
    package(tmp_path, "base")
    package(tmp_path, "unrelated")
    saved_order(tmp_path, "unrelated", "dependent", "base")
    registry = LocalModPackages(tmp_path).registry_path
    before = registry.read_bytes()
    preview = preview_mod_sort(tmp_path, backend="native")
    assert not preview.issues and preview.changed
    assert [entry.name for entry in preview.saved] == ["unrelated", "dependent", "base"]
    assert [entry.name for entry in preview.proposed] == ["unrelated", "base", "dependent"]
    assert registry.read_bytes() == before  # Cancelling means no commit call.
    assert apply_mod_sort(preview, root=tmp_path, backend="native")
    fresh = preview_mod_sort(tmp_path, backend="native")
    assert not fresh.changed
    assert [mod.name for mod in LocalModPackages(tmp_path).sort_mods(scan_mods(tmp_path))] == ["unrelated", "base", "dependent"]
    content = registry.read_bytes()
    assert not apply_mod_sort(fresh, root=tmp_path, backend="native")
    assert registry.read_bytes() == content


def test_legacy_and_disabled_mods_are_not_guessed_or_enabled(tmp_path):
    package(tmp_path, "dependent", loadAfter=["base"])
    package(tmp_path, "base")
    package(tmp_path, "disabled", active=False, requires=["missing"])
    for name in ("unknown-one", "unknown-two"):
        folder = tmp_path / "mods" / name
        folder.mkdir()
        (folder / "loader.js").write_text("// Unknown legacy package\n")
    saved_order(tmp_path, "unknown-one", "dependent", "disabled", "unknown-two", "base")
    preview = preview_mod_sort(tmp_path, backend="native")
    assert not preview.issues
    assert [entry.name for entry in preview.proposed if not entry.rules] == ["unknown-one", "unknown-two", "base"]
    assert apply_mod_sort(preview, root=tmp_path, backend="native")
    assert (tmp_path / "mods/disabled/loader.js.disabled").is_file()
    assert not (tmp_path / "mods/disabled/loader.js").exists()


@pytest.mark.parametrize("relationships,other", [
    ({"requires": ["missing"]}, {}),
    ({"loadAfter": ["base"]}, {"loadAfter": ["dependent"]}),
    ({"conflicts": ["base"]}, {}),
    ({"requires": ["base", "BASE"]}, {}),
])
def test_invalid_relationships_do_not_save_a_partial_order(tmp_path, relationships, other):
    package(tmp_path, "dependent", **relationships)
    package(tmp_path, "base", **other)
    preview = preview_mod_sort(tmp_path, backend="native")
    assert preview.issues
    assert not LocalModPackages(tmp_path).registry_path.exists()
    with pytest.raises(LocalModPackageError):
        apply_mod_sort(preview, root=tmp_path, backend="native")
    assert not LocalModPackages(tmp_path).registry_path.exists()


@pytest.mark.parametrize("change", ["loader", "manifest", "enabled", "registry", "added", "removed"])
def test_stale_preview_cannot_overwrite_new_information(tmp_path, change):
    dependent = package(tmp_path, "dependent", requires=["base"])
    base = package(tmp_path, "base")
    saved_order(tmp_path, "dependent", "base")
    preview = preview_mod_sort(tmp_path, backend="native")
    if change == "loader":
        (base / "loader.js").write_text("// New payload\n")
    elif change == "manifest":
        path = dependent / "evejs-launcher.mod.json"
        payload = json.loads(path.read_text())
        payload["version"] = "1.1"
        path.write_text(json.dumps(payload))
    elif change == "enabled":
        (base / "loader.js").rename(base / "loader.js.disabled")
    elif change == "registry":
        saved_order(tmp_path, "base", "dependent")
    elif change == "added":
        package(tmp_path, "new")
    else:
        (base / "loader.js").unlink()
    registry = LocalModPackages(tmp_path).registry_path
    before = registry.read_bytes()
    with pytest.raises(LocalModPackageError, match="changed after preview"):
        apply_mod_sort(preview, root=tmp_path, backend="native")
    assert registry.read_bytes() == before


def test_installation_runtime_and_connect_only_boundaries(tmp_path):
    root, other = tmp_path / "one", tmp_path / "two"
    package(root, "dependent", requires=["base"])
    package(root, "base")
    other.mkdir()
    saved_order(root, "dependent", "base")
    preview = preview_mod_sort(root, backend="docker")
    for kwargs in (dict(root=other, backend="docker", docker_managed=True),
                   dict(root=root, backend="native"), dict(root=root, backend="docker")):
        with pytest.raises(LocalModPackageError):
            apply_mod_sort(preview, **kwargs)
    assert apply_mod_sort(preview, root=root, backend="docker", docker_managed=True)


def test_preview_does_not_create_a_registry_or_lock(tmp_path):
    package(tmp_path, "only")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    preview = preview_mod_sort(tmp_path, backend="native")
    assert not preview.changed and not preview.issues
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
