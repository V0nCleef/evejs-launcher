from __future__ import annotations

from pathlib import Path

import pytest

from src.core.dlss5 import prepare_dlss5_profile_environment


def _fixture_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    profile_tq = tmp_path / "Profiles" / "Pilot" / "tq"
    profile_tq.mkdir(parents=True)
    client_tq = tmp_path / "Copied Client" / "tq"
    bin64 = client_tq / "bin64"
    bin64.mkdir(parents=True)
    (bin64 / "dxgi.dll").write_bytes(b"reshade")
    (bin64 / "renodx-dlss5.addon64").write_bytes(b"renodx")
    return profile_tq, client_tq, bin64


def test_profile_runtime_isolates_mutable_reshade_state(tmp_path: Path) -> None:
    profile_tq, client_tq, bin64 = _fixture_paths(tmp_path)
    (bin64 / "ReShade.ini").write_text(
        "[GENERAL]\r\nPresetPath=.\\Existing.ini\r\n\r\n"
        "[RenoDX.DLSS5]\r\nNeuralUplift=0\r\nNRPreset=3\r\n",
        encoding="utf-8",
    )

    environment = prepare_dlss5_profile_environment(profile_tq, client_tq)

    profile_base = profile_tq.parent / "DLSS5"
    assert environment == {"RESHADE_BASE_PATH_OVERRIDE": str(profile_base)}
    profile_config = profile_base / "ReShade.ini"
    text = profile_config.read_text(encoding="utf-8")
    assert "AddonPath=..\\tq\\bin64" in text
    assert "LoadFromDllMain=renodx-dlss5.addon64" in text
    assert "EnableHooks=2" in text
    assert "NeuralUplift=0" in text
    assert "NRPreset=3" in text
    assert "PresetPath=.\\Existing.ini" in text
    assert not (profile_tq / "bin64" / "DLSS5TransitionGuard.ini").exists()


def test_existing_profile_keeps_manual_f6_preference(tmp_path: Path) -> None:
    profile_tq, client_tq, bin64 = _fixture_paths(tmp_path)
    (bin64 / "ReShade.ini").write_text(
        "[RenoDX.DLSS5]\nNeuralUplift=1\n",
        encoding="utf-8",
    )
    prepare_dlss5_profile_environment(profile_tq, client_tq)
    profile_config = profile_tq.parent / "DLSS5" / "ReShade.ini"
    profile_config.write_text(
        profile_config.read_text(encoding="utf-8").replace(
            "NeuralUplift=1",
            "NeuralUplift=0",
        ),
        encoding="utf-8",
    )
    before = profile_config.read_bytes()

    prepare_dlss5_profile_environment(profile_tq, client_tq)

    assert profile_config.read_bytes() == before


def test_profile_runtime_creates_safe_minimal_config(tmp_path: Path) -> None:
    profile_tq, client_tq, _bin64 = _fixture_paths(tmp_path)

    prepare_dlss5_profile_environment(profile_tq, client_tq)

    text = (profile_tq.parent / "DLSS5" / "ReShade.ini").read_text(
        encoding="utf-8"
    )
    assert "AddonPath=..\\tq\\bin64" in text
    assert "LoadFromDllMain=renodx-dlss5.addon64" in text
    assert "EnableHooks=2" in text
    assert "NeuralUplift=1" in text


def test_profile_runtime_rejects_missing_mod_payload(tmp_path: Path) -> None:
    profile_tq, client_tq, bin64 = _fixture_paths(tmp_path)
    (bin64 / "renodx-dlss5.addon64").unlink()

    with pytest.raises(RuntimeError, match="required client files are missing"):
        prepare_dlss5_profile_environment(profile_tq, client_tq)


def test_profile_runtime_rejects_shared_base_path_override(tmp_path: Path) -> None:
    profile_tq, client_tq, bin64 = _fixture_paths(tmp_path)
    (bin64 / "ReShade.ini").write_text(
        "[INSTALL]\nBasePath=C:\\SharedState\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"defines \[INSTALL\] BasePath"):
        prepare_dlss5_profile_environment(profile_tq, client_tq)
