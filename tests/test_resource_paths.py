"""Tests for source and frozen resource-path resolution."""
from __future__ import annotations

from pathlib import Path

from src.widgets.nav_panel import logo_asset_path


def test_nav_logo_path_resolves_from_the_source_module_location() -> None:
    module_file = Path(__file__).resolve().parent.parent / "src" / "widgets" / "nav_panel.py"

    assert logo_asset_path(module_file) == module_file.parent.parent.parent / "assets" / "logo.png"


def test_nav_logo_path_resolves_from_a_simulated_frozen_module_location(
    tmp_path: Path,
) -> None:
    module_file = tmp_path / "_internal" / "src" / "widgets" / "nav_panel.py"

    assert logo_asset_path(module_file) == tmp_path / "_internal" / "assets" / "logo.png"
