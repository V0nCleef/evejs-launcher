"""Public, data-only form contract shared by mod settings storage and Qt UI.

Authors provide localized text as a string or language-code mapping. These
declarations contain no Python/Qt code and never execute during discovery.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import PurePosixPath
import re
from typing import Mapping, Sequence

from src.i18n import current_language, translate_ui_phrase

LocalizedText = str | Mapping[str, str]
_RESERVED_PATHS = {"con", "prn", "aux", "nul", "clock$", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def localized_text(value: LocalizedText, language: str | None = None) -> str:
    if isinstance(value, str):
        return value
    language = language or current_language()
    return value.get(language) or value.get("en") or next(iter(value.values()), "")


@dataclass(frozen=True)
class SettingChoice:
    value: str | int | float | bool
    label: LocalizedText


@dataclass(frozen=True)
class ModSetting:
    id: str
    label: LocalizedText
    kind: str
    default: object
    description: LocalizedText = ""
    group: LocalizedText = ""
    advanced: bool = False
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    choices: tuple[SettingChoice, ...] = ()
    restart: str = "none"
    scope: str = "global"
    file_id: str = ""
    key: tuple[str, ...] = ()
    max_length: int = 4096
    storage: str = "native"


@dataclass(frozen=True)
class SettingsFile:
    id: str
    base: str
    path: str
    format: str
    encoding: str | None = None


@dataclass(frozen=True)
class ModSettingsSchema:
    files: tuple[SettingsFile, ...]
    fields: tuple[ModSetting, ...]


class ModSettingsSchemaError(ValueError):
    """The optional author declaration is incomplete or unsupported."""


def _text(value: object, label: str, *, empty: bool = False) -> LocalizedText:
    if isinstance(value, str) and len(value) <= 4096 and (empty or value.strip()):
        return value
    if isinstance(value, dict) and value and len(value) <= 32 and all(
        isinstance(key, str) and len(key) <= 16 and isinstance(text, str)
        and len(text) <= 4096 and (empty or text.strip()) for key, text in value.items()
    ) and "en" in value:
        return dict(value)
    raise ModSettingsSchemaError(f"{label} must be text or a locale map with an English fallback.")


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ModSettingsSchemaError(f"{label} must be a simple unique identifier.")
    return value


def _number(value: object, label: str) -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ModSettingsSchemaError(f"{label} must be a finite number.")
    if not _finite(value):
        raise ModSettingsSchemaError(f"{label} must be a finite number.")
    return value


def parse_settings_schema(payload: object) -> ModSettingsSchema:
    """Parse the versioned public declaration, without accessing any files."""
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        raise ModSettingsSchemaError("Mod settings require schemaVersion 1.")
    if set(payload) - {"schemaVersion", "files", "fields"}:
        raise ModSettingsSchemaError("Unsupported mod settings declaration field.")
    file_rows, field_rows = payload.get("files"), payload.get("fields")
    if not isinstance(file_rows, list) or not 1 <= len(file_rows) <= 32:
        raise ModSettingsSchemaError("Mod settings must declare 1 to 32 files.")
    if not isinstance(field_rows, list) or not 1 <= len(field_rows) <= 256:
        raise ModSettingsSchemaError("Mod settings must declare 1 to 256 fields.")
    files: dict[str, SettingsFile] = {}
    for row in file_rows:
        if not isinstance(row, dict) or set(row) - {"id", "base", "path", "format", "encoding"}:
            raise ModSettingsSchemaError("Unsupported settings file declaration.")
        file_id = _id(row.get("id"), "File id")
        if file_id in files:
            raise ModSettingsSchemaError("Duplicate settings file id.")
        base, path, format = row.get("base"), row.get("path"), row.get("format")
        if not isinstance(base, str) or base not in {"mod", "evejs", "profile", "profile_settings", "client"}:
            raise ModSettingsSchemaError("Unsupported settings file base.")
        if not isinstance(path, str) or not path or "\\" in path or any(c in path for c in ':\x00\r\n*?<>|"'):
            raise ModSettingsSchemaError("A settings file path must be relative and use forward slashes.")
        relative = PurePosixPath(path)
        if relative.is_absolute() or any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in path.split("/")):
            raise ModSettingsSchemaError("A settings file path cannot escape its declared base.")
        if any(part.split(".", 1)[0].casefold() in _RESERVED_PATHS for part in relative.parts):
            raise ModSettingsSchemaError("A settings file path cannot use a reserved device name.")
        if relative.suffix.lower() not in {".json", ".ini", ".yaml", ".yml"}:
            raise ModSettingsSchemaError("Only declared text configuration files can be edited.")
        if base == "evejs" and relative.parts[0] != "config":
            raise ModSettingsSchemaError("EveJS settings targets must be inside config/.")
        if base == "profile" and relative.parts[0].casefold() == "tq":
            raise ModSettingsSchemaError("Profile settings cannot target the shared client junction.")
        if not isinstance(format, str) or format not in {"json", "ini", "yaml"}:
            raise ModSettingsSchemaError("Supported settings formats are json, ini and yaml.")
        encoding = row.get("encoding")
        if encoding is not None and (not isinstance(encoding, str) or encoding not in {"utf-8", "utf-16-le", "utf-16-be", "cp1252"}):
            raise ModSettingsSchemaError("Unsupported settings file encoding.")
        files[file_id] = SettingsFile(file_id, base, path, format, encoding)

    fields: list[ModSetting] = []
    ids, destinations = set(), set()
    allowed = {"id", "label", "type", "default", "description", "group", "advanced", "minimum", "maximum", "step", "choices", "restart", "file", "key", "maxLength", "storage"}
    for row in field_rows:
        if not isinstance(row, dict) or set(row) - allowed or "default" not in row:
            raise ModSettingsSchemaError("Unsupported or incomplete setting declaration.")
        field_id = _id(row.get("id"), "Setting id")
        if field_id in ids:
            raise ModSettingsSchemaError("Duplicate setting id.")
        ids.add(field_id)
        file_id = row.get("file")
        if not isinstance(file_id, str) or file_id not in files:
            raise ModSettingsSchemaError("Setting references an undeclared file.")
        file = files[file_id]
        key = row.get("key")
        if not isinstance(key, list) or not 1 <= len(key) <= 32 or any(not isinstance(item, str) or not item or len(item) > 128 for item in key):
            raise ModSettingsSchemaError("Setting key must be a list of nonempty path components.")
        from .mod_config_documents import canonical_key, DocumentError
        try:
            canonical = canonical_key(file.format, tuple(key))
        except DocumentError as exc:
            raise ModSettingsSchemaError(str(exc)) from exc
        destination = (file.base, file.path.casefold(), canonical)
        if destination in destinations:
            raise ModSettingsSchemaError("Two settings cannot write the same configuration key.")
        destinations.add(destination)
        kind = row.get("type")
        if not isinstance(kind, str) or kind not in {"boolean", "integer", "number", "string", "choice"}:
            raise ModSettingsSchemaError("Unsupported setting type.")
        low, high, step = (_number(row.get(name), name) for name in ("minimum", "maximum", "step"))
        if (low is not None and high is not None and low > high) or (step is not None and step <= 0):
            raise ModSettingsSchemaError("Invalid setting range or step.")
        if kind == "integer" and any(value is not None and type(value) is not int for value in (low, high, step)):
            raise ModSettingsSchemaError("Integer settings require whole-number bounds and steps.")
        choices = []
        choice_rows = row.get("choices", [])
        if not isinstance(choice_rows, list) or len(choice_rows) > 128 or (kind == "choice" and not choice_rows):
            raise ModSettingsSchemaError("Choice settings need a bounded list of options.")
        for choice in choice_rows:
            if not isinstance(choice, dict) or set(choice) != {"value", "label"} or type(choice["value"]) not in (str, int, float, bool):
                raise ModSettingsSchemaError("Each choice needs a scalar value and label.")
            if type(choice["value"]) in (int, float):
                _number(choice["value"], "Choice value")
            if any(type(existing.value) is type(choice["value"]) and existing.value == choice["value"] for existing in choices):
                raise ModSettingsSchemaError("Choice values must be unique.")
            choices.append(SettingChoice(choice["value"], _text(choice["label"], "Choice label")))
        restart = row.get("restart", "none")
        if not isinstance(restart, str) or restart not in {"none", "game_server", "client", "launcher"}:
            raise ModSettingsSchemaError("Unsupported setting restart scope.")
        advanced, max_length, storage = row.get("advanced", False), row.get("maxLength", 4096), row.get("storage", "native")
        if type(advanced) is not bool or type(max_length) is not int or not 1 <= max_length <= 65536:
            raise ModSettingsSchemaError("Invalid advanced flag or text length.")
        if not isinstance(storage, str) or storage not in {"native", "numeric_boolean"} or (storage == "numeric_boolean" and kind != "boolean"):
            raise ModSettingsSchemaError("Unsupported setting storage conversion.")
        field = ModSetting(
            id=field_id, label=_text(row.get("label"), "Setting label"), kind=kind,
            default=row["default"], description=_text(row.get("description", ""), "Description", empty=True),
            group=_text(row.get("group", ""), "Group", empty=True), advanced=advanced,
            minimum=low, maximum=high, step=step, choices=tuple(choices), restart=restart,
            scope="profile" if file.base.startswith("profile") else "global", file_id=file_id,
            key=tuple(key), max_length=max_length, storage=storage,
        )
        if validate_values((field,), {field.id: field.default}):
            raise ModSettingsSchemaError(f"Invalid default for setting {field_id}.")
        fields.append(field)
    return ModSettingsSchema(tuple(files.values()), tuple(fields))


def validate_values(
    fields: Sequence[ModSetting], values: Mapping[str, object]
) -> dict[str, str]:
    """Validate the entire submitted form without coercing or discarding values."""
    errors: dict[str, str] = {}
    for field in fields:
        value = values.get(field.id)
        error = ""
        if field.id not in values:
            error = "A value is required."
        elif field.kind == "boolean" and type(value) is not bool:
            error = "Choose on or off."
        elif field.kind == "integer" and type(value) is not int:
            error = "Enter a whole number."
        elif field.kind == "number" and not _finite(value):
            error = "Enter a finite number."
        elif field.kind == "string" and not isinstance(value, str):
            error = "Enter text."
        elif field.kind == "choice" and not any(
            type(value) is type(choice.value) and value == choice.value
            for choice in field.choices
        ):
            error = "Choose one of the available options."
        elif field.kind not in {"boolean", "integer", "number", "string", "choice"}:
            error = "This setting type is not supported."
        if not error and field.kind in {"integer", "number"}:
            if field.minimum is not None and value < field.minimum:
                error = translate_ui_phrase("The minimum value is {value}.").format(value=field.minimum)
            elif field.maximum is not None and value > field.maximum:
                error = translate_ui_phrase("The maximum value is {value}.").format(value=field.maximum)
        if not error and isinstance(value, str) and len(value) > field.max_length:
            error = translate_ui_phrase("Use at most {count} characters.").format(count=field.max_length)
        if error:
            errors[field.id] = translate_ui_phrase(error)
    unknown = set(values) - {field.id for field in fields}
    if unknown:
        errors["__form__"] = translate_ui_phrase("The settings form has changed. Reopen it and try again.")
    return errors
