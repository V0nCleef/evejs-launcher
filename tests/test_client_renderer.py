"""Renderer policy and its final Native/Docker client-spawn boundary."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import launcher
from src.core.client_renderer import configure_client_renderer, resolve_client_renderer
from src.core.mod_api_runtime import PreparedClientMods


def _config(root: Path, text: str, *, relative: str = "tools/ClientSETUP/scripts/EvEJSConfig.bat", encoding: str = "utf-8") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


@pytest.fixture
def renderer_files(tmp_path):
    root, binary = tmp_path / "EveJS", tmp_path / "Copied Client" / "tq" / "bin64"
    binary.mkdir(parents=True)
    (binary / "_trinity_dx12.dll").write_bytes(b"fixture")
    return root, binary


@pytest.mark.parametrize("environment,expected", [
    ({}, "dx11"),
    ({"TRINITYPLATFORM": "dx11"}, "dx11"),
    ({"TRINITYPLATFORM": "DX12"}, "dx12"),
    ({"EVEJS_CLIENT_RENDERER": " dx12 "}, "dx12"),
    ({"EVEJS_CLIENT_RENDERER": "dx11", "TRINITYPLATFORM": "dx12"}, "dx11"),
    ({"trinityplatform": "dx12"}, "dx12"),
])
def test_default_and_inherited_preferences(renderer_files, environment, expected):
    root, binary = renderer_files
    original = dict(environment)
    choice = resolve_client_renderer(root, binary, environment)
    assert choice.requested == choice.effective == expected
    assert environment == original


@pytest.mark.parametrize("saved,environment,expected", [
    ('set "EVEJS_CLIENT_RENDERER=dx12"', {"TRINITYPLATFORM": "dx11"}, "dx12"),
    ('set "EVEJS_CLIENT_RENDERER=dx11"', {"EVEJS_CLIENT_RENDERER": "dx12"}, "dx11"),
    ('set "TRINITYPLATFORM=dx12"', {"TRINITYPLATFORM": "dx11"}, "dx12"),
    ('set "TRINITYPLATFORM=dx12"', {"EVEJS_CLIENT_RENDERER": "dx11"}, "dx11"),
    ('set "EVEJS_CLIENT_RENDERER="\nset "TRINITYPLATFORM=dx11"', {"EVEJS_CLIENT_RENDERER": "dx12"}, "dx11"),
    ('set "TRINITYPLATFORM="', {"TRINITYPLATFORM": "dx12"}, "dx11"),
    ('set "EVEJS_CLIENT_RENDERER=dx11"\nset "EVEJS_CLIENT_RENDERER=dx12"', {}, "dx12"),
    ('set "EVEJS_CLIENT_RENDERER=dx12"\nset "EVEJS_CLIENT_RENDERER="', {}, "dx11"),
])
def test_saved_precedence_and_clear_match_batch(renderer_files, saved, environment, expected):
    root, binary = renderer_files
    config = _config(root, saved + "\n")
    original = config.read_bytes()
    choice = resolve_client_renderer(root, binary, environment)
    assert choice.requested == choice.effective == expected
    assert config.read_bytes() == original


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", pytest.param("cp1252", marks=pytest.mark.skipif(os.name != "nt", reason="Windows ANSI code page"))])
def test_windows_config_encoding_comments_and_case(renderer_files, encoding):
    root, binary = renderer_files
    _config(root, 'rem café\nrem set "EVEJS_CLIENT_RENDERER=dx11"\n:: ignored\n  @SeT "evejs_client_renderer=DX12"\n', encoding=encoding)
    choice = resolve_client_renderer(root, binary, {})
    assert choice.effective == "dx12"
    assert choice.source == "EvEJSConfig.bat:EVEJS_CLIENT_RENDERER"


@pytest.mark.parametrize("relative", ["scripts/windows/EvEJSConfig.bat", "scripts/EvEJSConfig.bat", "EvEJSConfig.bat"])
def test_legacy_configuration_locations(renderer_files, relative):
    root, binary = renderer_files
    _config(root, "set TRINITYPLATFORM=dx12\n", relative=relative)
    assert resolve_client_renderer(root, binary, {}).effective == "dx12"


def test_current_config_wins_and_batch_commands_are_never_run(renderer_files, tmp_path):
    root, binary = renderer_files
    marker = tmp_path / "should-not-exist.txt"
    _config(root, "set TRINITYPLATFORM=dx11\n", relative="EvEJSConfig.bat")
    _config(root, f'echo executed > "{marker}"\ncall anything.bat\nset "TRINITYPLATFORM=dx12"\n')
    assert resolve_client_renderer(root, binary, {}).effective == "dx12"
    assert not marker.exists()


@pytest.mark.parametrize("value", ["dx9", "%RENDERER%", "!RENDERER!", "dx12 & echo secret"])
def test_invalid_or_dynamic_renderer_is_not_executed_or_echoed(renderer_files, value):
    root, binary = renderer_files
    _config(root, f'set "EVEJS_CLIENT_RENDERER={value}"\n')
    with pytest.raises(ValueError, match="must be dx11 or dx12") as error:
        resolve_client_renderer(root, binary, {})
    assert value not in str(error.value)


def test_missing_dx12_dll_has_an_explained_dx11_fallback(renderer_files):
    root, binary = renderer_files
    _config(root, 'set "EVEJS_CLIENT_RENDERER=dx12"\n')
    (binary / "_trinity_dx12.dll").unlink()
    choice = resolve_client_renderer(root, binary, {})
    assert (choice.requested, choice.effective) == ("dx12", "dx11")
    assert "_trinity_dx12.dll is missing" in choice.fallback
    (binary / "_trinity_dx12.dll").mkdir()
    assert resolve_client_renderer(root, binary, {}).effective == "dx11"


def test_existing_graphics_override_does_not_depend_on_server_config(renderer_files):
    root, binary = renderer_files
    _config(root, 'set "EVEJS_CLIENT_RENDERER=%INVALID_SERVER_VALUE%"\n')
    choice = resolve_client_renderer(root, binary, {}, override="dx12", override_source="installed DLSS5")
    assert choice.effective == "dx12"
    assert choice.source == "installed DLSS5"


def test_applying_choice_changes_only_the_child_environment(renderer_files, monkeypatch):
    root, binary = renderer_files
    _config(root, "set EVEJS_CLIENT_RENDERER=dx12\n")
    monkeypatch.setenv("TRINITYPLATFORM", "dx11")
    child = {"TRINITYPLATFORM": "dx11", "trinityplatform": "dx11"}

    choice = configure_client_renderer(root, binary, child)

    assert choice.effective == "dx12"
    assert child == {"TRINITYPLATFORM": "dx12"}
    assert os.environ["TRINITYPLATFORM"] == "dx11"


@pytest.fixture
def spawn_boundary(tmp_path, monkeypatch):
    root, tq = tmp_path / "EveJS", tmp_path / "Client Space" / "tq"
    exe = tq / "bin64" / "exefile.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fixture")
    (exe.parent / "_trinity_dx12.dll").write_bytes(b"fixture")
    (tq.parent / "ResFiles").mkdir()
    (tq.parent / "index_tranquility.txt").write_text("fixture", encoding="utf-8")
    for key in ("TRINITYPLATFORM", "EVEJS_CLIENT_RENDERER", "EVEJS_DLSS5"):
        monkeypatch.delenv(key, raising=False)
    captured = {"spawned": [], "notifications": [], "mods": {}, "installed": {}}
    monkeypatch.setattr(launcher, "_validate_client_resource_cache_contents", lambda _path: None)
    monkeypatch.setattr(launcher, "scan_mods", lambda _root: [])
    monkeypatch.setattr(launcher, "serialize_evejs_client_trust_and_spawn", nullcontext)
    monkeypatch.setattr(launcher, "acquire_mod_lifecycle_lock", lambda _path: nullcontext())
    monkeypatch.setattr(launcher, "prepare_evejs_client_certificate_trust", lambda *_args: False)
    monkeypatch.setattr(launcher, "prepare_installed_dlss5", lambda *_args: captured["installed"])
    def prepare(*_args, **kwargs):
        captured["prepared_backend"] = kwargs["backend"]
        return PreparedClientMods(captured["mods"], (), ())
    monkeypatch.setattr(launcher, "prepare_public_client_mods", prepare)
    def spawn(path, environment, cwd, *, arguments):
        captured["spawned"].append((path, dict(environment), cwd, arguments))
        return SimpleNamespace(pid=12345)
    monkeypatch.setattr(launcher, "launch_eve_client", spawn)
    monkeypatch.setattr(launcher, "start_client_notifications", lambda *_args, **kwargs: captured["notifications"].append(kwargs))
    return root, tq, captured


@pytest.mark.parametrize("backend", ["native", "docker"])
@pytest.mark.parametrize("setting,expected", [("EVEJS_CLIENT_RENDERER=dx12", "dx12"), ("TRINITYPLATFORM=dx12", "dx12"), ("EVEJS_CLIENT_RENDERER=dx11", "dx11")])
def test_renderer_reaches_final_spawn_for_both_backends(spawn_boundary, backend, setting, expected, caplog):
    root, tq, captured = spawn_boundary
    _config(root, f'set "{setting}"\n')
    context = launcher.ClientLaunchContext.native()
    if backend == "docker":
        context = replace(context, target_identity="docker:fixture", game_port=32600, proxy_url="http://127.0.0.1:32602")
    with caplog.at_level(logging.INFO):
        launcher.launch_client(str(root), tq, client_path=str(tq), launch_context=context)
    _exe, env, _cwd, arguments = captured["spawned"][0]
    assert env["TRINITYPLATFORM"] == expected
    assert env["HTTP_PROXY"] == context.proxy_url
    assert arguments == (f"/port:{context.game_port}",)
    assert captured["prepared_backend"] == backend
    assert f"effective={expected}" in caplog.text


def test_missing_renderer_falls_back_before_spawn(spawn_boundary, caplog):
    root, tq, captured = spawn_boundary
    _config(root, "set EVEJS_CLIENT_RENDERER=dx12\n")
    (tq / "bin64" / "_trinity_dx12.dll").unlink()
    launcher.launch_client(str(root), tq, client_path=str(tq))
    assert captured["spawned"][0][1]["TRINITYPLATFORM"] == "dx11"
    assert "_trinity_dx12.dll is missing" in caplog.text


def test_invalid_preference_does_not_spawn_and_notifies_prepared_mods(spawn_boundary):
    root, tq, captured = spawn_boundary
    _config(root, "set EVEJS_CLIENT_RENDERER=invalid\n")
    with pytest.raises(ValueError, match="must be dx11 or dx12"):
        launcher.launch_client(str(root), tq, client_path=str(tq))
    assert captured["spawned"] == []
    assert captured["notifications"] == [{"backend": "native", "error_type": "ValueError"}]


@pytest.mark.parametrize("config", ["set EVEJS_CLIENT_RENDERER=dx11", "set EVEJS_CLIENT_RENDERER=invalid"])
def test_installed_dlss5_remains_independent_of_server_settings(spawn_boundary, config):
    root, tq, captured = spawn_boundary
    _config(root, config + "\n")
    captured["installed"] = {"TRINITYPLATFORM": "dx12", "EVEJS_DLSS5": "on"}
    launcher.launch_client(str(root), tq, client_path=str(tq))
    assert captured["spawned"][0][1]["TRINITYPLATFORM"] == "dx12"


def test_client_mod_override_and_windows_environment_casing_survive(spawn_boundary):
    root, tq, captured = spawn_boundary
    _config(root, "set EVEJS_CLIENT_RENDERER=dx11\n")
    captured["mods"] = {"trinityplatform": "dx12"}
    launcher.launch_client(str(root), tq, client_path=str(tq))
    env = captured["spawned"][0][1]
    assert env["TRINITYPLATFORM"] == "dx12"
    assert [key for key in env if key.upper() == "TRINITYPLATFORM"] == ["TRINITYPLATFORM"]
