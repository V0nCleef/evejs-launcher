"""Pure server-start script discovery and preference resolution."""
from __future__ import annotations

from pathlib import Path

ASK_EVERY_TIME = "ask"


def discover_server_scripts(evejs_root: str | Path) -> list[Path]:
    """Return sorted StartServer*.bat files directly under the root."""
    if not evejs_root:
        return []

    root = Path(evejs_root)
    if not root.is_dir():
        return []

    scripts = [
        candidate
        for candidate in root.iterdir()
        if candidate.is_file()
        and candidate.name.casefold().startswith("startserver")
        and candidate.suffix.casefold() == ".bat"
    ]
    return sorted(scripts, key=lambda script: script.name.casefold())


def choose_saved_script(scripts: list[Path], preference: str) -> Path | None:
    """Return the sole/valid saved script; None means prompt or no scripts."""
    if len(scripts) == 1:
        return scripts[0]
    if not scripts or not preference or preference.casefold() == ASK_EVERY_TIME:
        return None

    wanted = preference.casefold()
    for script in scripts:
        if script.name.casefold() == wanted:
            return script
    return None


def mode_for_script(script: Path) -> str:
    """Map a supported stock script filename to its direct-Node mode."""
    name = script.name.casefold()
    if name == "startserver.bat":
        return "vanilla"
    if name == "startserverwithmods.bat":
        return "modded"
    raise ValueError(f"Unsupported server start script: {script.name}")
