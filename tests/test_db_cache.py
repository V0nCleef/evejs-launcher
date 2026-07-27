"""Tests for root-dependent database lookup cache invalidation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core import db


def _write_solar_system_data(root: Path, name: str) -> None:
    path = root / "_local" / "gameStore" / "data" / "solarSystems" / "data.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "solarSystems": [
                    {"solarSystemID": 30000001, "solarSystemName": name}
                ]
            }
        ),
        encoding="utf-8",
    )


def test_clear_solar_system_name_cache_allows_a_new_root_to_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_solar_system_data(first_root, "First System")
    _write_solar_system_data(second_root, "Second System")
    monkeypatch.setattr(db, "_SOLAR_SYSTEM_NAMES", None)

    assert db._load_solar_system_names(str(first_root))[30000001] == "First System"

    db.clear_solar_system_name_cache()

    assert db._load_solar_system_names(str(second_root))[30000001] == "Second System"
