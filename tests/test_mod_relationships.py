import json
from dataclasses import replace

import pytest

from src.core import mod_management
from src.core.mod_manifest import scan_mods
from src.core.mod_relationships import plan_mod_order
from src.core.mod_operations import change_mod_state, remove_local_mod, ModOperationContext


def package(root, name, **relations):
    folder = root / "mods" / name
    folder.mkdir(parents=True)
    (folder / "loader.js").write_text("module.exports = {};\n")
    payload = dict(schemaVersion=3, id=name, displayName=name, version="1.0", kind="loader",
                   restart="game_server", activation={"strategy": "loader_rename"}, **relations)
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(payload))
    return next(m for m in scan_mods(root) if m.path == folder)


def test_stable_order_only_moves_declared_prerequisites(tmp_path):
    a = package(tmp_path, "a", requires=["b"])
    b = package(tmp_path, "b")
    c = package(tmp_path, "c")
    assert [m.id for m in plan_mod_order([c, a, b]).require_valid()] == ["c", "b", "a"]
    assert [m.id for m in plan_mod_order([c, b, replace(a, active=False)]).require_valid()] == ["c", "b", "a"]


@pytest.mark.parametrize("field", ["requires", "loadBefore", "loadAfter", "conflicts"])
def test_manifest_rejects_ambiguous_relationship_syntax(tmp_path, field):
    mod = package(tmp_path, "a", **{field: ["b", "B"]})
    assert mod.api_descriptor is None
    assert "repeat" in mod.descriptor_error


def test_optional_order_reference_does_not_require_installation(tmp_path):
    mod = package(tmp_path, "a", loadBefore=["absent"])
    assert plan_mod_order([mod]).require_valid() == (mod,)


def test_missing_dependency_cycle_and_conflict_explain_affected_mods(tmp_path):
    a = package(tmp_path, "a", requires=["missing"], loadBefore=["b"])
    b = package(tmp_path, "b", loadBefore=["a"], conflicts=["a"])
    plan = plan_mod_order([a, b])
    messages = "\n".join(issue.message for issue in plan.issues)
    assert "requires 'missing'" in messages and "cycle" in messages and "conflicts with" in messages
    with pytest.raises(ValueError):
        plan.require_valid()


def test_duplicate_reference_does_not_guess_between_folders(tmp_path):
    a = package(tmp_path, "a", requires=["b"])
    b = package(tmp_path, "b")
    other = package(tmp_path, "other")
    with pytest.raises(ValueError, match="multiple mod folders"):
        plan_mod_order([a, b, replace(other, id="b")]).require_valid()


def test_backend_specific_dependency_is_not_silently_used(tmp_path):
    a = package(tmp_path, "a", requires=["b"])
    b = replace(package(tmp_path, "b"), supported_backends=("native",))
    plan_mod_order([a, b], backend="native").require_valid()
    with pytest.raises(ValueError, match="this backend"):
        plan_mod_order([a, b], backend="docker").require_valid()


def test_prerequisite_cannot_be_disabled_or_removed_while_dependent_active(tmp_path, monkeypatch):
    monkeypatch.setattr(mod_management, "_read_registry_values", lambda *_args, **_kwargs: None)
    dependent = package(tmp_path, "dependent", requires=["base"])
    base = package(tmp_path, "base")
    operation = ModOperationContext(tmp_path)
    for action in (lambda: change_mod_state(base, False, operation), lambda: remove_local_mod(base, operation)):
        with pytest.raises(ValueError, match="dependent requires 'base'"):
            action()
        assert (base.path / "loader.js").is_file()
    assert change_mod_state(dependent, False, operation) is False
    remove_local_mod(base, operation)
    assert not base.path.exists()
    assert dependent.path.exists()


def test_different_roots_cannot_share_a_plan(tmp_path):
    with pytest.raises(ValueError, match="different EveJS"):
        plan_mod_order([package(tmp_path / "one", "a"), package(tmp_path / "two", "b")])


@pytest.mark.parametrize("backend", ["native", "docker-compose"])
def test_server_plan_consumes_declared_loader_order(tmp_path, backend):
    from src.app import MainWindow
    from src.core.mod_runtime_state import NATIVE_BACKEND, DOCKER_BACKEND
    from src.core.local_mod_packages import LocalModPackages
    first = package(tmp_path, "a", requires=["z"])
    prerequisite = package(tmp_path, "z")
    LocalModPackages(tmp_path).set_order([first, prerequisite])
    selected = MainWindow._applicable_runtime_mods(tmp_path, backend=DOCKER_BACKEND if backend == "docker-compose" else NATIVE_BACKEND)
    assert [mod.id for mod in selected] == ["z", "a"]
