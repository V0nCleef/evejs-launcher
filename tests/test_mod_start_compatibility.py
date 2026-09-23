from pathlib import Path
from types import SimpleNamespace

from src.core.mod_evejs_compatibility import (
    EvejsModCompatibilityStatus,
    assess_active_runtime_mods,
)
from src.core.mod_manifest import ActivationKind, Mod


def _mod(
    name,
    evejs_versions,
    *,
    active=True,
    valid=True,
    kind=ActivationKind.LOADER_RENAME,
    version="1.2.3",
):
    descriptor = (
        None
        if evejs_versions is None
        else SimpleNamespace(evejs_versions=evejs_versions)
    )
    return Mod(
        name=name,
        path=Path("mods") / name,
        active=active,
        id=name.casefold().replace(" ", "-"),
        version=version,
        activation_kind=kind,
        valid=valid,
        api_descriptor=descriptor,
    )


def test_assess_active_runtime_mods_reports_declared_support_and_mismatch():
    results = assess_active_runtime_mods(
        (
            _mod("Compatible Loader", ("0.12.9",)),
            _mod("Old Loader", ("0.12.8",), version="1.0.6"),
            _mod(
                "Compatible Integrated Mod",
                ("0.12.9",),
                kind=ActivationKind.JSON_BOOLEAN,
            ),
            _mod("Legacy Loader", None),
            _mod("Disabled Loader", ("0.12.8",), active=False),
            _mod("Invalid Loader", ("0.12.8",), valid=False),
            _mod(
                "Client Package",
                ("0.12.8",),
                kind=ActivationKind.CLIENT_PACKAGE,
            ),
        ),
        "v0.12.9",
    )

    assert [result.status for result in results] == [
        EvejsModCompatibilityStatus.SUPPORTED,
        EvejsModCompatibilityStatus.UNSUPPORTED,
        EvejsModCompatibilityStatus.SUPPORTED,
        EvejsModCompatibilityStatus.UNDECLARED,
    ]
    assert [result.blocking for result in results] == [False, True, False, False]
    assert results[1].installed_version == "0.12.9"
    assert results[1].declared_versions == ("0.12.8",)
    assert "Old Loader (mod version 1.0.6)" in results[1].message
    assert "EveJS 0.12.8" in results[1].message
    assert "EveJS 0.12.9" in results[1].message
    assert "author manifest" in results[1].message
    assert "compatibility.evejsVersions" in results[1].message
    assert "not listed" in results[1].message
    assert "not a runtime test" in results[1].message
    assert "does not prove the mod is broken" in results[1].message
    assert "existing startup behavior is retained" in results[3].message


def test_explicit_support_with_unknown_installed_version_is_blocking():
    results = assess_active_runtime_mods(
        (
            _mod("Declared Loader", ("0.12.8",)),
            _mod("Legacy Loader", None),
        ),
        None,
    )

    assert [result.status for result in results] == [
        EvejsModCompatibilityStatus.UNKNOWN_INSTALLED_VERSION,
        EvejsModCompatibilityStatus.UNDECLARED,
    ]
    assert [result.blocking for result in results] == [True, False]
    assert results[0].installed_version is None
    assert "version could not be determined" in results[0].message
    assert "package.json" in results[0].message
    assert "config/version.json" in results[0].message


def test_invalid_installed_version_is_treated_as_unknown():
    results = assess_active_runtime_mods(
        (_mod("Declared Loader", ("0.12.8",)),),
        "0.12.9-beta",
    )

    assert results[0].status is EvejsModCompatibilityStatus.UNKNOWN_INSTALLED_VERSION
    assert results[0].blocking
