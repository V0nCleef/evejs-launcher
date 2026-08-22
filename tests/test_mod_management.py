"""Fail-closed contracts for launcher-managed mod removal."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import winreg

import pytest

from src.core.mod_management import (
    INNO_USER_PROVIDER,
    MANAGED_MOD_SCHEMA_VERSION,
    ManagedModRegistration,
    ManagedModRemovalRequest,
    ModDataPolicy,
    ModManagementError,
    managed_mod_registry_path,
    read_managed_mod_registration,
    remove_managed_mod,
    validate_managed_mod_registration,
)
from src.core.mod_manifest import ActivationKind, Mod


APP_ID = "{3CB3F7D0-7068-4C88-98A9-41A38C52B672}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _integrated_mod(root: Path) -> Mod:
    root.mkdir(parents=True)
    config_path = root / "config" / "mods" / "evejs-temp-npc.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        '{"schemaVersion": 1, "enabled": true}\n',
        encoding="utf-8",
    )
    manifest_path = (
        root
        / "server"
        / "mods"
        / "evejs-temp-npc"
        / "evejs-launcher.mod.json"
    )
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n", encoding="utf-8")
    return Mod(
        name="EveJS Temp NPC",
        path=manifest_path.parent,
        active=True,
        id="evejs-temp-npc",
        version="0.4.2-prototype",
        description="Managed-removal fixture.",
        activation_kind=ActivationKind.JSON_BOOLEAN,
        supported_backends=("native",),
        restart_scope="game_server",
        manifest_path=manifest_path,
        config_path=config_path,
        config_key="enabled",
        allowed_config_schema_versions=(1, 2, 3),
        status_protocol="evejs_mod_status_v1",
        status_transport="server_stdout",
        evejs_root=root,
    )


def _registration_fixture(
    tmp_path: Path,
) -> tuple[
    Mod,
    Path,
    dict[str, tuple[object, int]],
    dict[str, tuple[object, int]],
]:
    root = tmp_path / "eve"
    mod = _integrated_mod(root)
    local_app_data = tmp_path / "local-app-data"
    kit_root = local_app_data / "Programs" / "EveJS Mods" / mod.id
    helper = kit_root / "bootstrap" / "Expand-EmbeddedPackage.ps1"
    helper.parent.mkdir(parents=True)
    bundle = kit_root / f"{mod.id}-package.zip"
    uninstaller = kit_root / "unins000.exe"
    uninstaller_data = kit_root / "unins000.dat"
    inventory = kit_root / f"{mod.id}-removal-inventory.json"
    helper.write_bytes(b"verified helper fixture\n")
    bundle.write_bytes(b"verified package fixture\n")
    uninstaller.write_bytes(b"verified uninstaller fixture\n")
    uninstaller_data.write_bytes(b"verified uninstaller data fixture\n")
    inventory.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "modId": mod.id,
                "entries": [
                    {
                        "path": "server/mods/evejs-temp-npc/evejs-launcher.mod.json",
                        "postRemove": {"kind": "absent"},
                    }
                ],
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    pointer = root / "_local" / mod.id / "install" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_bytes(b'{"schemaVersion": 1}\n')

    values: dict[str, tuple[object, int]] = {
        "SchemaVersion": (MANAGED_MOD_SCHEMA_VERSION, winreg.REG_DWORD),
        "Provider": (INNO_USER_PROVIDER, winreg.REG_SZ),
        "AppId": (APP_ID, winreg.REG_SZ),
        "ModId": (mod.id, winreg.REG_SZ),
        "DisplayName": (mod.name, winreg.REG_SZ),
        "PackageVersion": (mod.version, winreg.REG_SZ),
        "EveJSPath": (str(root.resolve()), winreg.REG_SZ),
        "BundleSha256": (_sha256(bundle), winreg.REG_SZ),
        "ExpandHelperSha256": (_sha256(helper), winreg.REG_SZ),
        "CurrentPointerSha256": (_sha256(pointer), winreg.REG_SZ),
        "RemovalInventorySha256": (_sha256(inventory), winreg.REG_SZ),
        "UninstallerSha256": (_sha256(uninstaller), winreg.REG_SZ),
        "UninstallerDataSha256": (_sha256(uninstaller_data), winreg.REG_SZ),
        "SupportsPurgeState": (1, winreg.REG_DWORD),
    }
    standard = {
        "UninstallString": (f'"{uninstaller.resolve()}"', winreg.REG_SZ),
        "InstallLocation": (str(kit_root.resolve()), winreg.REG_SZ),
        "DisplayVersion": (mod.version, winreg.REG_SZ),
    }
    return mod, local_app_data, values, standard


def _validate_fixture(
    tmp_path: Path,
) -> tuple[Mod, ManagedModRegistration, Path]:
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    registration = validate_managed_mod_registration(
        mod,
        values,
        standard_uninstall_values=standard,
        local_app_data=local_app_data,
    )
    pointer = mod.evejs_root / "_local" / mod.id / "install" / "current.json"
    return mod, registration, pointer


def _remove_kit(registration: ManagedModRegistration) -> None:
    kit_root = registration.uninstaller_path.parent
    (kit_root / "bootstrap" / "Expand-EmbeddedPackage.ps1").unlink()
    (kit_root / "bootstrap").rmdir()
    (kit_root / f"{registration.mod_id}-package.zip").unlink()
    registration.removal_inventory_path.unlink()
    registration.uninstaller_data_path.unlink()
    registration.uninstaller_path.unlink()
    kit_root.rmdir()


def _remove_mod_payload(mod: Mod) -> None:
    assert mod.manifest_path is not None
    mod.manifest_path.unlink()
    mod.manifest_path.parent.rmdir()
    mod.manifest_path.parent.parent.rmdir()


def _rewrite_inventory(
    local_app_data: Path,
    values: dict[str, tuple[object, int]],
    mod_id: str,
    content: bytes,
) -> Path:
    inventory = (
        local_app_data
        / "Programs"
        / "EveJS Mods"
        / mod_id
        / f"{mod_id}-removal-inventory.json"
    )
    inventory.write_bytes(content)
    values["RemovalInventorySha256"] = (_sha256(inventory), winreg.REG_SZ)
    return inventory


def test_valid_registration_binds_exact_root_contract_and_fixed_kit(
    tmp_path: Path,
) -> None:
    mod, registration, _pointer = _validate_fixture(tmp_path)

    assert registration.mod_id == mod.id
    assert registration.evejs_root == mod.evejs_root.resolve()
    assert registration.provider == INNO_USER_PROVIDER
    assert registration.supports_purge_state is True
    assert registration.uninstaller_path == (
        tmp_path
        / "local-app-data"
        / "Programs"
        / "EveJS Mods"
        / mod.id
        / "unins000.exe"
    ).resolve()
    assert registration.uninstaller_data_path.name == "unins000.dat"
    assert registration.removal_inventory[0].expected_state == "absent"
    assert len(registration.activation_contract_sha256) == 64


def test_registration_rejects_tampered_uninstaller_data(
    tmp_path: Path,
) -> None:
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    uninstaller_data = (
        local_app_data
        / "Programs"
        / "EveJS Mods"
        / mod.id
        / "unins000.dat"
    )
    uninstaller_data.write_bytes(b"hostile replacement\n")

    with pytest.raises(ModManagementError, match="uninstaller data hash"):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=standard,
            local_app_data=local_app_data,
        )


def test_registration_rejects_inventory_content_changed_after_enrollment(
    tmp_path: Path,
) -> None:
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    inventory = (
        local_app_data
        / "Programs"
        / "EveJS Mods"
        / mod.id
        / f"{mod.id}-removal-inventory.json"
    )
    inventory.write_bytes(inventory.read_bytes() + b" ")

    with pytest.raises(ModManagementError, match="removal inventory hash"):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=standard,
            local_app_data=local_app_data,
        )


@pytest.mark.parametrize(
    ("entries", "match"),
    [
        (
            [
                {
                    "path": "../outside.exe",
                    "postRemove": {"kind": "absent"},
                }
            ],
            "path is unsafe",
        ),
        (
            [
                {
                    "path": "server/mods/evejs-temp-npc/evejs-launcher.mod.json",
                    "postRemove": {"kind": "absent"},
                },
                {
                    "path": "SERVER/MODS/EVEJS-TEMP-NPC/EVEJS-LAUNCHER.MOD.JSON",
                    "postRemove": {"kind": "absent"},
                },
            ],
            "paths must be unique",
        ),
    ],
)
def test_registration_rejects_unsafe_or_duplicate_inventory_paths(
    tmp_path: Path,
    entries: list[dict[str, object]],
    match: str,
) -> None:
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    content = (
        json.dumps(
            {"schemaVersion": 1, "modId": mod.id, "entries": entries},
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    _rewrite_inventory(local_app_data, values, mod.id, content)

    with pytest.raises(ModManagementError, match=match):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=standard,
            local_app_data=local_app_data,
        )


def test_registration_normalizes_oversized_json_integer_parse_failure(
    tmp_path: Path,
) -> None:
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    content = (
        b'{"schemaVersion":'
        + (b"9" * 5000)
        + b',"modId":"evejs-temp-npc","entries":[]}'
    )
    _rewrite_inventory(local_app_data, values, mod.id, content)

    with pytest.raises(ModManagementError, match="not strict UTF-8 JSON"):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=standard,
            local_app_data=local_app_data,
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda values, _standard, _root: values.__setitem__(
                "Unexpected", ("authority", winreg.REG_SZ)
            ),
            "fields are not exact",
        ),
        (
            lambda values, _standard, _root: values.__setitem__(
                "Provider", ("arbitrary-command-v1", winreg.REG_SZ)
            ),
            "Unsupported launcher removal provider",
        ),
        (
            lambda values, _standard, _root: values.__setitem__(
                "CurrentPointerSha256", ("A" * 64, winreg.REG_SZ)
            ),
            "not SHA-256",
        ),
        (
            lambda values, _standard, _root: values.__setitem__(
                "SupportsPurgeState", (2, winreg.REG_DWORD)
            ),
            "DWORD boolean",
        ),
        (
            lambda _values, standard, _root: standard.__setitem__(
                "UninstallString",
                (str(standard["UninstallString"][0]) + " /SILENT", winreg.REG_SZ),
            ),
            "malformed",
        ),
        (
            lambda values, _standard, root: values.__setitem__(
                "EveJSPath", (str(root), winreg.REG_SZ)
            ),
            "different EveJS root",
        ),
    ],
)
def test_registration_rejects_added_authority_drift_and_command_tails(
    tmp_path: Path,
    mutation,
    match: str,
) -> None:
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    other_root = tmp_path / "other-eve"
    other_root.mkdir()
    mutation(values, standard, other_root.resolve())

    with pytest.raises(ModManagementError, match=match):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=standard,
            local_app_data=local_app_data,
        )


def test_registration_rejects_missing_standard_windows_enrollment(
    tmp_path: Path,
) -> None:
    mod, local_app_data, values, _standard = _registration_fixture(tmp_path)

    with pytest.raises(ModManagementError, match="Windows uninstall registration"):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=None,
            local_app_data=local_app_data,
        )


def test_registration_rejects_missing_local_app_data_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod, _local_app_data, values, standard = _registration_fixture(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    with pytest.raises(ModManagementError, match="LOCALAPPDATA is unavailable"):
        validate_managed_mod_registration(
            mod,
            values,
            standard_uninstall_values=standard,
        )


def test_registry_reader_accepts_identical_hkcu_enrollment_across_windows_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    views = (0x100, 0x200)
    monkeypatch.setattr(implementation, "_REGISTRY_VIEWS", views)
    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    reads: list[tuple[str, int]] = []

    def read_values(path: str, *, registry_view: int = 0, **_kwargs):
        reads.append((path, registry_view))
        if path == managed_mod_registry_path(mod.id):
            return dict(values)
        return dict(standard)

    monkeypatch.setattr(implementation, "_read_registry_values", read_values)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    registration = read_managed_mod_registration(mod)

    assert registration.mod_id == mod.id
    assert registration.evejs_root == mod.evejs_root.resolve()
    assert [view for _path, view in reads] == [*views, *views]


def test_registry_reader_refuses_conflicting_enrollment_across_windows_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    views = (0x100, 0x200)
    monkeypatch.setattr(implementation, "_REGISTRY_VIEWS", views)
    mod, _local_app_data, values, _standard = _registration_fixture(tmp_path)
    conflicting_values = dict(values)
    conflicting_values["BundleSha256"] = ("f" * 64, winreg.REG_SZ)
    standard_reads: list[int] = []

    def read_values(path: str, *, registry_view: int = 0, **_kwargs):
        if path != managed_mod_registry_path(mod.id):
            standard_reads.append(registry_view)
            return _standard
        return values if registry_view == views[0] else conflicting_values

    monkeypatch.setattr(implementation, "_read_registry_values", read_values)

    with pytest.raises(ModManagementError, match="conflicting values"):
        read_managed_mod_registration(mod)

    assert standard_reads == []


def test_registry_reader_refuses_conflicting_uninstall_authority_across_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    views = (0x100, 0x200)
    monkeypatch.setattr(implementation, "_REGISTRY_VIEWS", views)
    mod, _local_app_data, values, standard = _registration_fixture(tmp_path)
    conflicting_standard = dict(standard)
    conflicting_standard["UninstallString"] = (
        str(tmp_path / "other-uninstaller.exe"),
        winreg.REG_SZ,
    )

    def read_values(path: str, *, registry_view: int = 0, **_kwargs):
        if path == managed_mod_registry_path(mod.id):
            return dict(values)
        return standard if registry_view == views[0] else conflicting_standard

    monkeypatch.setattr(implementation, "_read_registry_values", read_values)

    with pytest.raises(ModManagementError, match="uninstall authority.*conflicting"):
        read_managed_mod_registration(mod)


def test_registry_reader_refuses_missing_uninstall_key_in_shared_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    views = (0x100, 0x200)
    monkeypatch.setattr(implementation, "_REGISTRY_VIEWS", views)
    mod, _local_app_data, values, standard = _registration_fixture(tmp_path)

    def read_values(path: str, *, registry_view: int = 0, **_kwargs):
        if path == managed_mod_registry_path(mod.id):
            return dict(values)
        return standard if registry_view == views[0] else None

    monkeypatch.setattr(implementation, "_read_registry_values", read_values)

    with pytest.raises(ModManagementError, match="missing from.*registry view"):
        read_managed_mod_registration(mod)


@pytest.mark.parametrize(
    ("policy", "expected_switch"),
    [
        (ModDataPolicy.KEEP, "/KEEPSTATE"),
        (ModDataPolicy.QUARANTINE, "/PURGESTATE"),
    ],
)
def test_removal_executor_constructs_only_the_fixed_inno_argv_and_verifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: ModDataPolicy,
    expected_switch: str,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, policy)
    captured: dict[str, object] = {}
    retired: list[tuple[Path, str, str]] = []

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])

    @contextmanager
    def lifecycle_lock(root: Path):
        captured["lock_root"] = root
        yield

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)
    monkeypatch.setattr(
        implementation,
        "retire_removed_mod_activation",
        lambda root, mod_id, fingerprint: retired.append(
            (Path(root), mod_id, fingerprint)
        ),
    )

    def run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        pointer.unlink()
        _remove_mod_payload(mod)
        _remove_kit(registration)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(implementation.subprocess, "run", run)

    result = remove_managed_mod(request, timeout=12.5)

    argv = captured["argv"]
    assert argv[:4] == [
        str(registration.uninstaller_path),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
    ]
    assert argv[4].startswith("/LAUNCHERTOKEN=")
    assert len(argv[4].split("=", 1)[1]) == 64
    assert argv[5] == f"/LAUNCHERROOT={registration.evejs_root}"
    assert argv[6] == expected_switch
    assert len(argv) == 8 and argv[7].startswith("/LOG=")
    kwargs = captured["kwargs"]
    assert kwargs["shell"] is False
    assert kwargs["check"] is False
    assert kwargs["timeout"] == 12.5
    assert captured["lock_root"] == registration.evejs_root
    assert retired == [
        (
            registration.evejs_root,
            registration.mod_id,
            registration.activation_contract_sha256,
        )
    ]
    assert result.success is True
    assert result.request == request
    assert result.log_path is not None


def test_production_removal_waits_without_timeout_and_locks_only_after_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Inno child must acquire the shared installer lock before us.

    Holding the launcher side of that lock while waiting for Inno would
    deadlock the PowerShell uninstall backend it starts.
    """
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    events: list[str] = []

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])
    monkeypatch.setattr(
        implementation,
        "retire_removed_mod_activation",
        lambda *_args: events.append("activation-retired"),
    )

    def run(_argv, **kwargs):
        assert events == []
        assert kwargs["timeout"] is None
        events.append("subprocess-started")
        pointer.unlink()
        _remove_mod_payload(mod)
        _remove_kit(registration)
        events.append("subprocess-returned")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(implementation.subprocess, "run", run)

    @contextmanager
    def lifecycle_lock(_root: Path):
        events.append("lifecycle-lock-entered")
        yield
        events.append("lifecycle-lock-exited")

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)

    result = remove_managed_mod(request)

    assert result.success is True
    assert events == [
        "subprocess-started",
        "subprocess-returned",
        "lifecycle-lock-entered",
        "activation-retired",
        "lifecycle-lock-exited",
    ]


def test_removal_revalidates_registration_before_starting_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, _pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: replace(registration, current_pointer_sha256="f" * 64),
    )
    monkeypatch.setattr(
        implementation.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("uninstaller must not start"),
    )

    with pytest.raises(ModManagementError, match="changed before removal"):
        remove_managed_mod(request)


def test_provider_mutex_covers_revalidation_child_and_terminal_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    events: list[str] = []

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))

    @contextmanager
    def operation_mutex(app_id: str):
        assert app_id == registration.app_id
        events.append("operation-enter")
        yield "a" * 64
        events.append("operation-exit")

    def matching(_registration: ManagedModRegistration) -> Mod:
        assert events == ["operation-enter"]
        events.append("contract-revalidated")
        return mod

    def reread(_mod: Mod) -> ManagedModRegistration:
        assert events[-1] == "contract-revalidated"
        events.append("registration-revalidated")
        return registration

    def run(argv, **_kwargs):
        assert events[-1] == "registration-revalidated"
        assert "/LAUNCHERTOKEN=" + "a" * 64 in argv
        assert "/LAUNCHERROOT=" + str(registration.evejs_root) in argv
        events.append("subprocess-started")
        pointer.unlink()
        _remove_mod_payload(mod)
        _remove_kit(registration)
        events.append("subprocess-returned")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    @contextmanager
    def lifecycle_lock(_root: Path):
        assert events[-1] == "subprocess-returned"
        events.append("terminal-proof-enter")
        yield
        events.append("terminal-proof-exit")

    monkeypatch.setattr(implementation, "_managed_mod_operation_mutex", operation_mutex)
    monkeypatch.setattr(implementation, "_matching_current_mod", matching)
    monkeypatch.setattr(implementation, "read_managed_mod_registration", reread)
    monkeypatch.setattr(implementation.subprocess, "run", run)
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])
    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)
    monkeypatch.setattr(
        implementation,
        "retire_removed_mod_activation",
        lambda *_args: events.append("activation-retired"),
    )

    result = remove_managed_mod(request)

    assert result.success is True
    assert events == [
        "operation-enter",
        "contract-revalidated",
        "registration-revalidated",
        "subprocess-started",
        "subprocess-returned",
        "terminal-proof-enter",
        "activation-retired",
        "terminal-proof-exit",
        "operation-exit",
    ]


def test_nonzero_uninstaller_exit_is_reported_without_terminal_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(
        implementation.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=7,
            stdout="",
            stderr="first line\nfixture refusal",
        ),
    )
    monkeypatch.setattr(
        implementation,
        "_registry_key_exists",
        lambda _path: pytest.fail("terminal verification must not run"),
    )

    with pytest.raises(ModManagementError, match="exit code 7.*fixture refusal"):
        remove_managed_mod(request)

    assert pointer.is_file()


def test_successful_exit_is_not_claimed_as_removal_while_journal_remains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(
        implementation.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])
    _remove_kit(registration)

    @contextmanager
    def lifecycle_lock(_root: Path):
        yield

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)

    with pytest.raises(ModManagementError, match="active journal is still present"):
        remove_managed_mod(request)

    assert pointer.is_file()


def test_successful_exit_is_not_claimed_as_removal_while_kit_remains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(
        implementation.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])
    monkeypatch.setattr(implementation, "SELF_DELETE_WAIT_SECONDS", 0.0)
    pointer.unlink()

    @contextmanager
    def lifecycle_lock(_root: Path):
        yield

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)

    with pytest.raises(ModManagementError, match="uninstall kit remains"):
        remove_managed_mod(request)

    assert registration.uninstaller_path.is_file()


def test_zero_exit_cannot_hide_executable_integration_left_behind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    # Deliberately lie at the discovery layer: terminal inventory proof must
    # still catch the manifest/file left on disk.
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])

    def run(*_args, **_kwargs):
        pointer.unlink()
        _remove_kit(registration)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(implementation.subprocess, "run", run)

    @contextmanager
    def lifecycle_lock(_root: Path):
        yield

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)

    with pytest.raises(ModManagementError, match="executable integration behind"):
        remove_managed_mod(request)

    assert mod.manifest_path is not None and mod.manifest_path.is_file()


def test_zero_exit_must_restore_enrolled_source_to_exact_original_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, local_app_data, values, standard = _registration_fixture(tmp_path)
    source = mod.evejs_root / "server" / "src" / "services" / "fixture.js"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original upstream source\n")
    original_sha256 = _sha256(source)
    entries = [
        {
            "path": "server/mods/evejs-temp-npc/evejs-launcher.mod.json",
            "postRemove": {"kind": "absent"},
        },
        {
            "path": "server/src/services/fixture.js",
            "postRemove": {"kind": "sha256", "sha256": original_sha256},
        },
    ]
    _rewrite_inventory(
        local_app_data,
        values,
        mod.id,
        (
            json.dumps(
                {"schemaVersion": 1, "modId": mod.id, "entries": entries},
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"),
    )
    registration = validate_managed_mod_registration(
        mod,
        values,
        standard_uninstall_values=standard,
        local_app_data=local_app_data,
    )
    pointer = mod.evejs_root / "_local" / mod.id / "install" / "current.json"
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])

    def run(*_args, **_kwargs):
        pointer.unlink()
        _remove_mod_payload(mod)
        source.write_bytes(b"not the enrolled original\n")
        _remove_kit(registration)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(implementation.subprocess, "run", run)

    @contextmanager
    def lifecycle_lock(_root: Path):
        yield

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)

    with pytest.raises(ModManagementError, match="not restored to its original hash"):
        remove_managed_mod(request)

    assert _sha256(source) != original_sha256


def test_activation_record_cleanup_failure_becomes_warning_after_real_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.core import mod_management as implementation

    mod, registration, pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(registration, ModDataPolicy.KEEP)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setattr(implementation, "_matching_current_mod", lambda _reg: mod)
    monkeypatch.setattr(
        implementation,
        "read_managed_mod_registration",
        lambda _mod: registration,
    )
    monkeypatch.setattr(implementation, "_registry_key_exists", lambda _path: False)
    monkeypatch.setattr(implementation, "scan_mods", lambda _root: [])

    @contextmanager
    def lifecycle_lock(_root: Path):
        yield

    monkeypatch.setattr(implementation, "acquire_mod_lifecycle_lock", lifecycle_lock)

    def run(*_args, **_kwargs):
        pointer.unlink()
        _remove_mod_payload(mod)
        _remove_kit(registration)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(implementation.subprocess, "run", run)

    def fail_retirement(*_args) -> None:
        raise RuntimeError("fixture activation journal is read-only")

    monkeypatch.setattr(
        implementation,
        "retire_removed_mod_activation",
        fail_retirement,
    )

    result = remove_managed_mod(request)

    assert result.success is True
    assert "old launcher activation record" in result.warning
    assert "read-only" in result.warning


def test_quarantine_policy_is_rejected_when_provider_does_not_support_it(
    tmp_path: Path,
) -> None:
    _mod, registration, _pointer = _validate_fixture(tmp_path)
    request = ManagedModRemovalRequest(
        replace(registration, supports_purge_state=False),
        ModDataPolicy.QUARANTINE,
    )

    with pytest.raises(ModManagementError, match="cannot quarantine"):
        remove_managed_mod(request)


@pytest.mark.parametrize("mod_id", ["", "UPPER", "has space", "../escape", "x" * 65])
def test_managed_registry_path_accepts_only_bounded_machine_ids(mod_id: str) -> None:
    with pytest.raises(ModManagementError, match="managed mod id"):
        managed_mod_registry_path(mod_id)
