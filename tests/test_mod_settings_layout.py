import json
import os
from pathlib import Path
import zipfile

import pytest

from src.core.local_mod_packages import LocalModPackages
from src.core.mod_api_manifest import read_api_manifest
from src.core.mod_settings import ModSettingsContext, ModSettingsSession
from src.core.mod_settings_layout import (
    MAX_LAYOUT_BYTES,
    SettingsLayoutError,
    SettingsLayoutSection,
    load_settings_layout,
    parse_settings_layout,
)
from src.core.mod_settings_schema import (
    SETTINGS_LAYOUT_FILENAME,
    ModSetting,
    ModSettingsSchemaError,
    parse_settings_schema,
)


FIELDS = (
    ModSetting("amount", "Amount", "number", 5),
    ModSetting("volume", "Volume", "number", 25),
    ModSetting("pilot", "Pilot label", "string", "", scope="profile"),
)
DEMO = Path(__file__).resolve().parents[1] / "examples/mods/configure-layout-demo"


def _section(*rows, id="amounts", title="Amounts"):
    return {"id": id, "title": title, "rows": rows}


def _layout(*sections):
    return json.dumps({"schemaVersion": 1, "sections": sections}).encode("utf-8")


@pytest.mark.parametrize("invalid", [
    b'{"schemaVersion":1,"schemaVersion":1,"sections":[]}',
    b'{"schemaVersion":true,"sections":[]}',
    b'{"schemaVersion":2,"sections":[]}',
    b'{"schemaVersion":1,"sections":[],"unknown":null}',
    b"\xff",
    b"[" * 1200 + b"]" * 1200,
    b" " * (MAX_LAYOUT_BYTES + 1),
    _layout(),
    _layout(*(_section(["amount"], id=f"group{i}") for i in range(65))),
    _layout(_section(["amount"], id="invalid section id")),
    _layout(_section(["amount"], title={"nl": "Hoeveelheden"})),
    _layout(_section(["amount"], title="A" * 129)),
    _layout(_section(["amount"], title="Before\nafter")),
    _layout(_section([])),
    _layout(_section(["amount", "volume", "pilot", "ghost"])),
    _layout(_section(["amount", ["volume"]])),
    _layout(_section(["ghost"])),
    _layout(_section(["amount"], ["amount"])),
    _layout(_section(["amount"], ["pilot"])),
    _layout(_section(["amount"]), _section(["volume"])),
    _layout(_section(["amount"]), _section(["amount"], id="other")),
], ids=[
    "duplicate-json-key", "boolean-version", "future-version", "unknown-key", "invalid-utf8",
    "deeply-nested", "too-large", "no-sections", "too-many-sections", "invalid-section-id",
    "missing-title-fallback", "too-long-title", "multiline-title", "empty-row", "too-wide",
    "invalid-field-id", "stale-field", "duplicate-field", "mixed-scopes", "duplicate-section",
    "duplicate-field-across-sections",
])
def test_layout_declines_malformed_or_outdated_hints(invalid):
    with pytest.raises(SettingsLayoutError):
        parse_settings_layout(invalid, FIELDS)


def test_load_projects_valid_sections_after_whole_declaration_validation(tmp_path):
    root = tmp_path.resolve()
    mod = root / "mods" / "my-mod"
    mod.mkdir(parents=True)
    file = mod / SETTINGS_LAYOUT_FILENAME
    assert load_settings_layout(root, mod, FIELDS, scope="global") == ((), False)

    title = {"en": "Quantities", "fr": "Quantités"}
    file.write_bytes(_layout(_section(["volume", "amount"], title=title),
                             _section(["pilot"], id="profile", title="Profile")))
    assert load_settings_layout(root, mod, FIELDS, scope="global") == (
        (SettingsLayoutSection("amounts", title, (("volume", "amount"),)),), False
    )
    assert load_settings_layout(root, mod, FIELDS, scope="profile") == (
        (SettingsLayoutSection("profile", "Profile", (("pilot",),)),), False
    )

    file.write_bytes(_layout(_section(["amount"]), _section(["renamed"], id="profile")))
    assert load_settings_layout(root, mod, FIELDS, scope="global") == ((), True)


def test_loader_declines_redirected_and_non_file_sidecars(tmp_path):
    root = (tmp_path / "runtime").resolve()
    mod = root / "mods" / "my-mod"
    mod.mkdir(parents=True)
    file = mod / SETTINGS_LAYOUT_FILENAME
    file.mkdir()
    assert load_settings_layout(root, mod, FIELDS, scope="global") == ((), True)
    assert load_settings_layout(root, root / "..", FIELDS, scope="global") == ((), True)


@pytest.mark.parametrize("create_link", [os.link, os.symlink], ids=["hard-link", "symbolic-link"])
def test_loader_declines_links(tmp_path, create_link):
    root = (tmp_path / "runtime").resolve()
    mod = root / "mods" / "my-mod"
    mod.mkdir(parents=True)
    file = mod / SETTINGS_LAYOUT_FILENAME
    outside = tmp_path / SETTINGS_LAYOUT_FILENAME
    outside.write_bytes(_layout(_section(["amount"])))
    try:
        create_link(outside, file)
    except OSError as exc:
        if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 1314:
            pytest.skip("This environment does not permit creating links.")
        raise
    assert load_settings_layout(root, mod, FIELDS, scope="global") == ((), True)


@pytest.mark.parametrize("spelling", [SETTINGS_LAYOUT_FILENAME, SETTINGS_LAYOUT_FILENAME.upper()])
def test_package_sidecar_cannot_be_declared_as_a_settings_destination(spelling):
    declaration = json.loads((DEMO / "evejs-launcher.mod.json").read_text(encoding="utf-8"))["settings"]
    declaration["files"][0]["path"] = spelling
    with pytest.raises(ModSettingsSchemaError, match="reserved"):
        parse_settings_schema(declaration)


@pytest.mark.parametrize("package_kind", ["folder", "zip"])
def test_imported_demo_applies_layout_and_saves_only_user_preferences(tmp_path, package_kind):
    root = tmp_path / "runtime"
    root.mkdir()
    package = DEMO
    if package_kind == "zip":
        package = tmp_path / "demo.zip"
        with zipfile.ZipFile(package, "w") as archive:
            for source in DEMO.iterdir():
                archive.write(source, f"{DEMO.name}/{source.name}")
    record = LocalModPackages(root).import_package(package)
    folder = root / record.relative_path
    descriptor = read_api_manifest(root, folder)
    context = ModSettingsContext(root, folder)
    sidecar = folder / SETTINGS_LAYOUT_FILENAME
    original_layout = sidecar.read_bytes()
    session = ModSettingsSession.open(context, descriptor.settings, manifest_path=descriptor.manifest_path)
    assert not session.layout_error
    assert session.layout[0].rows == (("array1Chance", "array2Chance", "array3Chance"), ("notes",))
    assert session.values["previewEnabled"] is True
    assert not (folder / "preferences.json").exists()

    edited = dict(session.values, array2Chance=17.5, notes="Narrow window")
    updated = session.save(edited)
    assert updated.layout == session.layout
    assert updated.values == edited
    assert sidecar.read_bytes() == original_layout

    sidecar.write_bytes(_layout(_section(["noLongerInstalled"])))
    reopened = ModSettingsSession.open(context, descriptor.settings, manifest_path=descriptor.manifest_path)
    assert reopened.layout == ()
    assert reopened.layout_error
    assert reopened.values == edited
    revised = dict(edited, array2Chance=12.5)
    reopened = reopened.save(revised)
    assert reopened.layout_error
    assert reopened.values == revised
