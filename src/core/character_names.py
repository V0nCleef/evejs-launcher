"""Official display names, shared by Native and Docker presentation."""
from functools import lru_cache
import json
from pathlib import Path
import sys

from src.i18n import current_language


@lru_cache(maxsize=1)
def _catalog() -> dict:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    try:
        return json.loads((root / "assets/data/character_names.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _language() -> str:
    return {"zh_CN": "zh", "nl": "en"}.get(current_language(), current_language())


def ship_display_name(name: str, type_id: int = 0) -> str:
    language = _language()
    if language == "en":
        return name
    names = _catalog().get("ships", {}).get(str(type_id), {})
    # Never translate a renamed ship, even if its name matches another hull.
    if name not in names.values():
        return name
    return names.get(language, name)


def location_display_name(location: str) -> str:
    language = _language()
    if language == "en":
        return location
    name, separator, security = location.partition(" · ")
    names = _catalog().get("systems", {}).get(name, {})
    return names.get(language, name) + separator + security
