"""Mod manager — scan and toggle EveJS server mods."""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Mod:
    """Represents an EveJS server mod."""
    name: str
    path: Path
    active: bool


def scan_mods(evejs_root: str) -> list[Mod]:
    """Scan the mods/ directory for installed mods.

    A mod is active if loader.js exists.
    It's inactive if loader.js.disabled (or .off, .bak) exists instead.
    """
    mods_dir = Path(evejs_root) / "mods"
    if not mods_dir.exists():
        return []

    mods = []
    for folder in sorted(mods_dir.iterdir(), key=lambda f: f.name.lower()):
        if not folder.is_dir():
            continue

        active_loader = folder / "loader.js"
        has_disabled = any(
            (folder / f).exists()
            for f in ("loader.js.disabled", "loader.js.off", "loader.js.bak")
        )
        active = active_loader.exists() and not has_disabled

        mods.append(Mod(
            name=folder.name,
            path=folder,
            active=active,
        ))

    return mods


def set_mod_active(mod: Mod, active: bool) -> None:
    """Enable or disable a mod by renaming its loader.js.

    Enable: rename loader.js.disabled → loader.js
    Disable: rename loader.js → loader.js.disabled
    """
    active_loader = mod.path / "loader.js"
    disabled_loader = mod.path / "loader.js.disabled"

    if active and not active_loader.exists():
        # Enable: find the disabled file and rename back
        for candidate in (
            disabled_loader,
            mod.path / "loader.js.off",
            mod.path / "loader.js.bak",
        ):
            if candidate.exists():
                candidate.rename(active_loader)
                return
        raise FileNotFoundError(f"No disabled loader found for '{mod.name}'")

    if not active and active_loader.exists():
        # Disable: rename to .disabled
        active_loader.rename(disabled_loader)


def toggle_mod(mod: Mod) -> bool:
    """Toggle a mod on/off. Returns new state."""
    new_state = not mod.active
    set_mod_active(mod, new_state)
    return new_state
