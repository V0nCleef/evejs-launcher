"""Tests for pure server-start script selection behavior."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.server_selection import (
    choose_saved_script,
    discover_server_scripts,
    mode_for_script,
)


def test_missing_root_discovers_no_server_scripts(tmp_path: Path) -> None:
    """A missing EveJS root must produce an empty script list."""
    assert discover_server_scripts(tmp_path / "missing") == []


def test_blank_root_discovers_no_server_scripts() -> None:
    assert discover_server_scripts("") == []


def test_discovery_is_non_recursive_and_ignores_unrelated_files(tmp_path: Path) -> None:
    wanted = tmp_path / "StartServer.bat"
    wanted.write_text("", encoding="utf-8")
    (tmp_path / "OtherServer.bat").write_text("", encoding="utf-8")
    (tmp_path / "StartServer.txt").write_text("", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "StartServerWithMods.bat").write_text("", encoding="utf-8")

    assert discover_server_scripts(tmp_path) == [wanted]


def test_discovery_is_case_insensitive_and_sorted_by_name(tmp_path: Path) -> None:
    modded = tmp_path / "startserverwithmods.BAT"
    vanilla = tmp_path / "STARTSERVER.bat"
    modded.write_text("", encoding="utf-8")
    vanilla.write_text("", encoding="utf-8")

    assert discover_server_scripts(tmp_path) == [vanilla, modded]


def test_zero_scripts_has_no_saved_choice() -> None:
    assert choose_saved_script([], "ask") is None


@pytest.mark.parametrize("filename", ["StartServer.bat", "StartServerWithMods.bat"])
def test_one_script_is_selected_automatically(filename: str, tmp_path: Path) -> None:
    script = tmp_path / filename

    assert choose_saved_script([script], "ask") == script


def test_multiple_scripts_with_ask_requires_prompt(tmp_path: Path) -> None:
    scripts = [tmp_path / "StartServer.bat", tmp_path / "StartServerWithMods.bat"]

    assert choose_saved_script(scripts, "ask") is None


def test_multiple_scripts_use_valid_saved_filename(tmp_path: Path) -> None:
    vanilla = tmp_path / "StartServer.bat"
    modded = tmp_path / "StartServerWithMods.bat"

    assert choose_saved_script([vanilla, modded], modded.name) == modded


def test_saved_filename_matching_is_case_insensitive(tmp_path: Path) -> None:
    vanilla = tmp_path / "StartServer.bat"
    modded = tmp_path / "StartServerWithMods.bat"

    assert choose_saved_script([vanilla, modded], "startserverwithmods.BAT") == modded


def test_missing_saved_filename_requires_prompt(tmp_path: Path) -> None:
    scripts = [tmp_path / "StartServer.bat", tmp_path / "StartServerWithMods.bat"]

    assert choose_saved_script(scripts, "StartServerOld.bat") is None


def test_stock_vanilla_script_maps_to_vanilla() -> None:
    assert mode_for_script(Path("StartServer.bat")) == "vanilla"


def test_stock_modded_script_maps_to_modded() -> None:
    assert mode_for_script(Path("StartServerWithMods.bat")) == "modded"


def test_mode_mapping_is_case_insensitive() -> None:
    assert mode_for_script(Path("STARTSERVERWITHMODS.BAT")) == "modded"


def test_unknown_script_name_is_rejected_without_guessing() -> None:
    with pytest.raises(ValueError, match="Unsupported server start script"):
        mode_for_script(Path("StartServerMegaMods.bat"))
