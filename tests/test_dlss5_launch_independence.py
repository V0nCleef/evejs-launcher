"""Installed renderer use must survive server/package/journal replacement."""
from pathlib import Path

import pytest

from src.core import dlss5, launcher
from src.core.dlss5_runtime import prepare_installed_dlss5
from src.core.mod_manifest import Mod
from src.core.mod_settings import ModSettingsContext


@pytest.fixture
def renderer(tmp_path):
    client = tmp_path / "client" / "tq"
    binary = client / "bin64"
    binary.mkdir(parents=True)
    for name in ("dxgi.dll", "renodx-dlss5.addon64", "nvngx_dlssnr.dll", "sl.dlss_nr.dll"):
        (binary / name).write_bytes(b"installed renderer fixture")
    (binary / "ReShade.ini").write_text(
        "[ADDON]\nLoadFromDllMain=another.addon64\n"
        "[GENERAL]\nEffectSearchPaths=.\\shaders\nPresetPath=.\\preset.ini\n"
        "[RenoDX.DLSS5]\nNeuralUplift=0\nNRIntensity=0.75\n")
    profile = tmp_path / "profiles" / "pilot" / "tq"
    profile.mkdir(parents=True)
    return client, profile


def test_renderer_needs_no_server_package_receipt_or_pinned_version(renderer, monkeypatch):
    client, profile = renderer
    monkeypatch.setattr(dlss5, "ensure_dlss5_client_mod", lambda *_a: pytest.fail("no installer on launch"))
    env = prepare_installed_dlss5(profile, client)
    assert env["TRINITYPLATFORM"] == "dx12"
    assert env["EVEJS_DLSS5"] == "on"
    assert Path(env["RESHADE_BASE_PATH_OVERRIDE"]) == profile.parent / "DLSS5"
    text = (profile.parent / "DLSS5" / "ReShade.ini").read_text()
    assert "NeuralUplift=0" in text
    assert "another.addon64,renodx-dlss5.addon64" in text
    assert str(client / "bin64" / "shaders") in text


def test_previous_public_profile_preferences_survive_server_root_change(renderer):
    client, profile = renderer
    old = profile.parent / "mods" / ("a" * 24)
    old.mkdir(parents=True)
    (old / "ReShade.ini").write_text("[RenoDX.DLSS5]\nNeuralUplift=1\nNRIntensity=0.42\n")
    env = prepare_installed_dlss5(profile, client)
    text = (Path(env["RESHADE_BASE_PATH_OVERRIDE"]) / "ReShade.ini").read_text()
    assert "NeuralUplift=1" in text
    assert "NRIntensity=0.42" in text
    # Once imported, another root's config cannot reset the stable preference.
    (old / "ReShade.ini").write_text("[RenoDX.DLSS5]\nNeuralUplift=0\n")
    assert prepare_installed_dlss5(profile, client) == env
    assert "NeuralUplift=1" in (Path(env["RESHADE_BASE_PATH_OVERRIDE"]) / "ReShade.ini").read_text()


def test_newer_public_preferences_win_over_an_old_legacy_profile(renderer):
    client, profile = renderer
    legacy = profile.parent / "DLSS5" / "ReShade.ini"
    legacy.parent.mkdir()
    legacy.write_text("[ADDON]\nAddonPath=..\\tq\\bin64\n[RenoDX.DLSS5]\nNeuralUplift=0\n")
    old = profile.parent / "mods" / ("b" * 24) / "ReShade.ini"
    old.parent.mkdir(parents=True)
    old.write_text("[RenoDX.DLSS5]\nNeuralUplift=1\nNRIntensity=0.31\n")
    import os
    os.utime(legacy, (1, 1))
    prepare_installed_dlss5(profile, client)
    assert "NeuralUplift=1" in legacy.read_text()
    assert "NRIntensity=0.31" in legacy.read_text()


def test_partial_settings_form_draft_overrides_imported_preferences(renderer):
    client, profile = renderer
    draft = profile.parent / "DLSS5" / "ReShade.ini"
    draft.parent.mkdir()
    draft.write_text("[RenoDX.DLSS5]\nNeuralUplift=1\n")
    prepare_installed_dlss5(profile, client)
    assert "NeuralUplift=1" in draft.read_text()
    assert "NRIntensity=0.75" in draft.read_text()


def test_each_pilot_keeps_private_neural_rendering_state(renderer):
    client, first = renderer
    second = first.parent.parent / "second" / "tq"
    second.mkdir(parents=True)
    a = prepare_installed_dlss5(first, client)
    b = prepare_installed_dlss5(second, client)
    config = Path(a["RESHADE_BASE_PATH_OVERRIDE"]) / "ReShade.ini"
    config.write_text(config.read_text().replace("NeuralUplift=0", "NeuralUplift=1"))
    prepare_installed_dlss5(second, client)
    assert a["RESHADE_BASE_PATH_OVERRIDE"] != b["RESHADE_BASE_PATH_OVERRIDE"]
    assert "NeuralUplift=0" in (Path(b["RESHADE_BASE_PATH_OVERRIDE"]) / "ReShade.ini").read_text()


@pytest.mark.parametrize("problem", ["missing", "bad-ini", "write-failure"])
def test_optional_renderer_preparation_cannot_raise_into_character_launch(renderer, monkeypatch, problem):
    client, profile = renderer
    if problem == "missing":
        (client / "bin64" / "nvngx_dlssnr.dll").unlink()
    elif problem == "bad-ini":
        (client / "bin64" / "ReShade.ini").write_text("[RenoDX.DLSS5]\nNeuralUplift=0\nNeuralUplift=1\n")
    else:
        monkeypatch.setattr("src.core.dlss5_runtime._write_text_atomic", lambda *_a: (_ for _ in ()).throw(PermissionError("fixture")))
    env = prepare_installed_dlss5(profile, client)
    assert env.get("TRINITYPLATFORM") in (None, "dx12")


def test_launch_reaches_spawn_despite_invalid_dlss_package_and_unknown_server(renderer, monkeypatch, tmp_path):
    client, profile = renderer
    exe = client / "bin64" / "exefile.exe"
    exe.write_bytes(b"fixture executable")
    root = tmp_path / "EveJS-future"
    root.mkdir()
    (root / "package.json").write_text('{"name":"future-evejs","version":"999999.1"}')
    invalid = Mod(name="Broken DLSS", path=root / "mods" / "DLSS5", active=True,
                  id="evejs-dlss5", valid=False, error="unreadable receipt")
    monkeypatch.setattr(launcher, "get_client_exe_path", lambda *_a: exe)
    monkeypatch.setattr(launcher, "_resolve_client_resource_cache", lambda *_a: tmp_path)
    monkeypatch.setattr(launcher, "prepare_evejs_client_certificate_trust", lambda *_a: False)
    monkeypatch.setattr(launcher, "scan_mods", lambda *_a: [invalid])
    captured = {}
    def prepare(*_a, **kwargs):
        assert kwargs["mods"] == ()
        from src.core.mod_api_runtime import PreparedClientMods
        return PreparedClientMods({}, (), ())
    monkeypatch.setattr(launcher, "prepare_public_client_mods", prepare)
    monkeypatch.setattr(launcher, "launch_eve_client", lambda _exe, env, _cwd, **_kw: captured.update(env=env) or "spawned")
    assert launcher.launch_client(str(root), profile, client_path=str(client)) == "spawned"
    assert captured["env"]["TRINITYPLATFORM"] == "dx12"


def test_dlss_settings_form_uses_same_root_independent_profile_directory(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("old-server", "new-server"):
        root = tmp_path / name
        folder = root / "mods" / "DLSS5"
        folder.mkdir(parents=True)
        context = ModSettingsContext(root, folder, profile_root=profile)
        assert context.mod_data_root == profile / "DLSS5"
