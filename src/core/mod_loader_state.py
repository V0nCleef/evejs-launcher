"""One interpretation of active loaders, disabled loaders and ordinary backups."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat


LOADER_FILENAMES = ("loader.js", "loader.js.disabled", "loader.js.off", "loader.js.bak")
DISABLED_LOADER_FILENAMES = LOADER_FILENAMES[1:]


class LoaderStateError(ValueError):
    pass


@dataclass(frozen=True)
class LoaderState:
    active_path: Path | None = None
    disabled_path: Path | None = None

    @property
    def selected_path(self) -> Path | None:
        return self.active_path or self.disabled_path

    @property
    def active(self) -> bool:
        return self.active_path is not None


def resolve_loader_state(folder: str | Path, *, root: Path | None = None) -> LoaderState:
    """An active loader wins over .bak; conflicting explicit states stay visible.

    With no active/explicit disabled file, a sole .bak remains the old disabled
    convention. Only the selected payload is inspected or hashed by consumers.
    """
    folder = Path(folder)
    active = folder / LOADER_FILENAMES[0]
    explicit = [folder / name for name in DISABLED_LOADER_FILENAMES[:2]
                if os.path.lexists(folder / name)]
    active_exists = os.path.lexists(active)
    if active_exists and explicit:
        raise LoaderStateError("Both active and disabled loader files exist.")
    if len(explicit) > 1:
        raise LoaderStateError("Multiple disabled loader files exist.")
    if active_exists:
        state = LoaderState(active_path=active)
    elif explicit:
        state = LoaderState(disabled_path=explicit[0])
    else:
        backup = folder / "loader.js.bak"
        state = LoaderState(disabled_path=backup if os.path.lexists(backup) else None)
    selected = state.selected_path
    if selected is not None:
        try:
            info = selected.lstat()
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise LoaderStateError("The selected loader is unsafe or not a regular file.")
            if root is not None:
                selected.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            if isinstance(exc, LoaderStateError):
                raise
            raise LoaderStateError("The selected loader is unavailable or escapes the EveJS root.") from exc
    return state
