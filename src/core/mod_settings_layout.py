"""Read optional, package-owned presentation hints for Configure forms."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
from typing import Sequence

from .mod_settings_schema import (
    SETTINGS_LAYOUT_FILENAME,
    LocalizedText,
    ModSetting,
    ModSettingsSchemaError,
    _id,
    _text,
)

MAX_LAYOUT_BYTES = 64 * 1024


class SettingsLayoutError(ValueError):
    """The optional layout cannot be applied to this mod's settings."""


@dataclass(frozen=True)
class SettingsLayoutSection:
    id: str
    title: LocalizedText
    rows: tuple[tuple[str, ...], ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SettingsLayoutError("Duplicate layout key.")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise SettingsLayoutError("Non-finite layout value.")


def parse_settings_layout(raw: bytes, fields: Sequence[ModSetting]) -> tuple[SettingsLayoutSection, ...]:
    """Validate the entire sidecar against the current settings declaration."""
    if len(raw) > MAX_LAYOUT_BYTES:
        raise SettingsLayoutError("Settings layout exceeds 64 KiB.")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise SettingsLayoutError("Settings layout must be valid UTF-8 JSON with unique keys.") from exc
    if (not isinstance(payload, dict) or set(payload) != {"schemaVersion", "sections"}
            or type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1):
        raise SettingsLayoutError("Settings layout requires schemaVersion 1 and sections.")
    sections = payload["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 64:
        raise SettingsLayoutError("Settings layout must declare 1 to 64 sections.")

    field_scopes = {field.id: field.scope for field in fields}
    section_ids: set[str] = set()
    used_fields: set[str] = set()
    result: list[SettingsLayoutSection] = []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"id", "title", "rows"}:
            raise SettingsLayoutError("A layout section requires id, title and rows.")
        try:
            section_id = _id(section["id"], "Section id")
            title = _text(section["title"], "Section title")
        except ModSettingsSchemaError as exc:
            raise SettingsLayoutError(str(exc)) from exc
        labels = (title,) if isinstance(title, str) else title.values()
        if any(len(label) > 128 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in label)
               for label in labels):
            raise SettingsLayoutError("Section titles must be plain text of at most 128 characters.")
        if section_id in section_ids:
            raise SettingsLayoutError("Duplicate layout section ID.")
        section_ids.add(section_id)
        rows = section["rows"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
            raise SettingsLayoutError("A layout section must contain 1 to 256 rows.")
        parsed_rows: list[tuple[str, ...]] = []
        scopes: set[str] = set()
        for row in rows:
            if not isinstance(row, list) or not 1 <= len(row) <= 3:
                raise SettingsLayoutError("A layout row must contain 1 to 3 field IDs.")
            for field_id in row:
                if not isinstance(field_id, str) or field_id not in field_scopes or field_id in used_fields:
                    raise SettingsLayoutError("Layout field IDs must exist and be unique in the layout.")
                used_fields.add(field_id)
                scopes.add(field_scopes[field_id])
            parsed_rows.append(tuple(row))
        if len(scopes) != 1:
            raise SettingsLayoutError("A layout section must use one settings scope.")
        result.append(SettingsLayoutSection(section_id, title, tuple(parsed_rows)))
    return tuple(result)


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _require_package_directory(root: Path, package: Path) -> None:
    try:
        canonical = root.is_absolute() and root.resolve(strict=True) == root
    except RuntimeError as exc:
        raise SettingsLayoutError("Layout requires a captured absolute installation path.") from exc
    if not canonical:
        raise SettingsLayoutError("Layout requires a captured absolute installation path.")
    relative = package.relative_to(root)
    if ".." in relative.parts:
        raise SettingsLayoutError("Layout requires a package inside the selected installation.")
    path = root
    for part in ("", *relative.parts):
        path = path / part
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            raise SettingsLayoutError("Layout requires an ordinary package directory.")


def _file_snapshot(info: os.stat_result) -> tuple:
    if (not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_nlink != 1
            or info.st_size > MAX_LAYOUT_BYTES):
        raise SettingsLayoutError("Layout requires an ordinary file no larger than 64 KiB.")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_sidecar(root: Path, package: Path) -> bytes | None:
    _require_package_directory(root, package)
    path = package / SETTINGS_LAYOUT_FILENAME
    try:
        before_info = path.lstat()
    except FileNotFoundError:
        return None
    before = _file_snapshot(before_info)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if _file_snapshot(os.fstat(stream.fileno())) != before:
            raise SettingsLayoutError("The settings layout changed while opening.")
        content = stream.read(MAX_LAYOUT_BYTES + 1)
        if _file_snapshot(os.fstat(stream.fileno())) != before or len(content) != before_info.st_size:
            raise SettingsLayoutError("The settings layout changed while reading.")
    _require_package_directory(root, package)
    after_info = path.lstat()
    # On Windows, handle and path stats can report different creation times.
    if _file_snapshot(after_info) != before or after_info.st_ctime_ns != before_info.st_ctime_ns:
        raise SettingsLayoutError("The settings layout changed while reading.")
    return content


def load_settings_layout(
    root: Path, package: Path, fields: Sequence[ModSetting], *, scope: str,
) -> tuple[tuple[SettingsLayoutSection, ...], bool]:
    """Return the selected scope's hints and whether automatic layout needs a notice."""
    try:
        raw = _read_sidecar(root, package)
        if raw is None:
            return (), False
        sections = parse_settings_layout(raw, fields)
    except (OSError, ValueError):
        return (), True
    visible_ids = {field.id for field in fields if field.scope == scope}
    return tuple(section for section in sections if section.rows[0][0] in visible_ids), False
