"""Build-time guards for producing a self-consistent Windows bundle."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence


# The Codex desktop runtime prepends document/media toolchains to PATH.  Those
# folders contain their own ICU, OpenSSL, and UCRT DLLs, which PyInstaller may
# mistake for application dependencies.  Qt then loads the wrong ABI at startup
# and fails before the launcher can display its own diagnostics.
FORBIDDEN_BUILD_PATH_MARKERS = (
    "\\.cache\\codex-runtimes\\",
    "\\dependencies\\native\\poppler\\",
    "\\dependencies\\native\\libheif\\",
    "\\program files\\amazon corretto\\",
)


def _path_identity(value: object) -> str:
    """Return a case-insensitive Windows-style identity for one path."""
    return str(value).strip().strip('"').replace("/", "\\").casefold()


def is_forbidden_build_path(value: object) -> bool:
    """Return whether *value* belongs to a known foreign DLL toolchain."""
    identity = _path_identity(value)
    return any(marker in identity for marker in FORBIDDEN_BUILD_PATH_MARKERS)


def sanitize_build_path(
    value: str,
    *,
    separator: str = os.pathsep,
) -> tuple[str, tuple[str, ...]]:
    """Remove foreign dependency roots from PATH and report what was removed."""
    kept: list[str] = []
    removed: list[str] = []
    for raw_entry in value.split(separator):
        entry = raw_entry.strip()
        if not entry:
            continue
        if is_forbidden_build_path(entry):
            removed.append(entry)
        else:
            kept.append(entry)
    return separator.join(kept), tuple(removed)


def reject_contaminated_binaries(
    binaries: Iterable[Sequence[object]],
) -> None:
    """Fail closed if PyInstaller selected a binary from a foreign toolchain."""
    contaminated: list[tuple[str, str]] = []
    for entry in binaries:
        if len(entry) < 2:
            continue
        destination = str(entry[0])
        source = str(entry[1])
        if is_forbidden_build_path(source):
            contaminated.append((destination, source))

    if not contaminated:
        return

    details = "\n".join(
        f"  {destination} <- {source}"
        for destination, source in contaminated[:20]
    )
    remainder = len(contaminated) - 20
    if remainder > 0:
        details += f"\n  ... and {remainder} more"
    raise RuntimeError(
        "PyInstaller selected DLLs from a foreign build toolchain:\n"
        f"{details}\n"
        "Build from a sanitized environment instead of shipping a mixed DLL set."
    )
