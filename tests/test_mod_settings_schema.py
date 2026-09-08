from copy import deepcopy

import pytest

from src.core.mod_settings_schema import ModSettingsSchemaError, localized_text, parse_settings_schema


def declaration():
    return {
        "schemaVersion": 1,
        "files": [{"id": "preferences", "base": "profile", "path": "settings.ini", "format": "ini"}],
        "fields": [{
            "id": "reconstruction", "file": "preferences", "key": ["Rendering", "Enabled"],
            "type": "boolean", "default": True, "storage": "numeric_boolean",
            "label": {"en": "Reconstruction", "nl": "Reconstructie"}, "restart": "client",
        }],
    }


def test_declaration_is_data_only_localized_and_profile_scoped():
    schema = parse_settings_schema(declaration())
    assert schema.fields[0].scope == "profile" and schema.fields[0].storage == "numeric_boolean"
    assert localized_text(schema.fields[0].label, "nl") == "Reconstructie"
    assert localized_text(schema.fields[0].label, "ja") == "Reconstruction"


@pytest.mark.parametrize("path", ["../escape.ini", "C:/escape.ini", "tq/config.ini", "x/../config.ini", "x//settings.ini", "account.dat", "data.sqlite"])
def test_unsafe_or_non_configuration_targets_are_rejected(path):
    raw = declaration()
    raw["files"][0]["path"] = path
    with pytest.raises(ModSettingsSchemaError):
        parse_settings_schema(raw)


@pytest.mark.parametrize("field,value", [("base", []), ("format", []), ("encoding", []), ("id", None)])
def test_malformed_file_metadata_has_a_typed_error(field, value):
    raw = declaration()
    raw["files"][0][field] = value
    with pytest.raises(ModSettingsSchemaError):
        parse_settings_schema(raw)


@pytest.mark.parametrize("field,value", [("file", []), ("type", []), ("restart", []), ("storage", []), ("default", "true"), ("advanced", 1), ("label", {"nl": "Geen Engelse terugval"})])
def test_malformed_setting_metadata_is_not_coerced(field, value):
    raw = declaration()
    raw["fields"][0][field] = value
    with pytest.raises(ModSettingsSchemaError):
        parse_settings_schema(raw)


def test_same_ini_destination_is_rejected_even_with_distinct_labels():
    raw = declaration()
    second = deepcopy(raw["fields"][0])
    second.update(id="other", key=["rendering", "enabled"])
    raw["fields"].append(second)
    with pytest.raises(ModSettingsSchemaError, match="same configuration key"):
        parse_settings_schema(raw)
