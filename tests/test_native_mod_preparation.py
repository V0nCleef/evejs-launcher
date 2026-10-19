import json
from types import SimpleNamespace

import pytest

from src.core import native_mod_preparation as preparation


def install(root, *, capability=True, active=True, backend="native", kind="loader", name="Example", after=()):
    folder = root / "mods" / name
    folder.mkdir(parents=True)
    (folder / ("loader.js" if active else "loader.js.disabled")).write_text("// fixture\n")
    (folder / "helper.js").write_text("// captured fixture helper\n")
    data = {"schemaVersion": 3, "id": "example.native" if name == "Example" else name, "displayName": name, "version": "1.0.0",
            "kind": kind, "restart": "game_server", "supportedBackends": [backend],
            "activation": {"strategy": "loader_rename"},
            "launcherApi": {"version": 1, "minLauncherVersion": "1.0.74",
                "helper": {"runtime": "node", "path": "helper.js"},
                "capabilities": ["prepare_server_start"] if capability else ["recover"]}}
    if after:
        data["loadAfter"] = list(after)
    if kind == "source-integrated":
        data["activation"] = {"strategy": "json_boolean", "configPath": "config/source.json", "property": "enabled"}
        (root / "config").mkdir(exist_ok=True)
        (root / "config/source.json").write_text(json.dumps({"enabled": active}))
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(data))
    return folder


class Ready:
    contributions = ()
    environment = {}
    arguments = ()
    def require_ready(self):
        return self


def test_declared_native_preparation_calls_only_captured_helper(tmp_path, monkeypatch):
    folder = install(tmp_path)
    captured = preparation.capture_native_mod_preparation(tmp_path)
    assert captured is not None
    calls = []
    monkeypatch.setattr(preparation, "run_mod_helper_locked", lambda descriptor, action, context, **kw:
        calls.append((descriptor.folder, action, context.client_root, kw)) or Ready())
    captured.run_locked()
    assert calls == [(folder, "prepare_server_start", None, {"backend": "native"})]


@pytest.mark.parametrize("values", [{"active": False}, {"capability": False}, {"backend": "docker"}])
def test_disabled_or_unenrolled_mods_never_run_recovery(tmp_path, values):
    install(tmp_path, **values)
    assert preparation.capture_native_mod_preparation(tmp_path) is None


def test_changed_helper_or_activation_invalidates_capture(tmp_path, monkeypatch):
    folder = install(tmp_path)
    captured = preparation.capture_native_mod_preparation(tmp_path)
    assert captured is not None
    (folder / "helper.js").write_text("// changed after capture\n")
    monkeypatch.setattr(preparation, "run_mod_helper_locked", lambda *args, **kw: pytest.fail("changed helper ran"))
    with pytest.raises(RuntimeError, match="changed"):
        captured.run_locked()


def test_preparation_cannot_return_client_launch_changes(tmp_path, monkeypatch):
    install(tmp_path)
    captured = preparation.capture_native_mod_preparation(tmp_path)
    assert captured is not None
    result = Ready()
    result.environment = {"UNEXPECTED": "1"}
    monkeypatch.setattr(preparation, "run_mod_helper_locked", lambda *args, **kw: result)
    with pytest.raises(RuntimeError, match="launch"):
        captured.run_locked()


def test_missing_root_has_no_startup_preparation(tmp_path):
    assert preparation.capture_native_mod_preparation(tmp_path / "missing") is None


def test_json_source_mod_can_opt_in_without_author_specific_support(tmp_path, monkeypatch):
    install(tmp_path, kind="source-integrated")
    calls = []
    monkeypatch.setattr(preparation, "run_mod_helper_locked", lambda descriptor, *args, **kw:
        calls.append(descriptor.id) or Ready())
    preparation.capture_native_mod_preparation(tmp_path).run_locked()
    assert calls == ["example.native"]


def test_dependencies_keep_same_order_at_capture_and_worker_recheck(tmp_path, monkeypatch):
    install(tmp_path, name="a-first-on-disk", after=("z-dependency",))
    install(tmp_path, name="z-dependency")
    calls = []
    monkeypatch.setattr(preparation, "run_mod_helper_locked", lambda descriptor, *args, **kw:
        calls.append(descriptor.id) or Ready())
    captured = preparation.capture_native_mod_preparation(tmp_path)
    captured.run_locked()
    assert calls == ["z-dependency", "a-first-on-disk"]
