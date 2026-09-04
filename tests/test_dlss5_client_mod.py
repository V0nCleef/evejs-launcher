from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.core import dlss5
from src.core.mod_manifest import ActivationKind


def _client(root: Path) -> Path:
    tq = root / "Copied Client" / "tq"
    (tq / "bin64").mkdir(parents=True)
    (tq / "bin64" / "exefile.exe").write_bytes(b"fixture")
    (tq / "start.ini").write_text(
        "[main]\nserver=127.0.0.1\nbuild=3396210\n",
        encoding="utf-8",
    )
    return tq


def _config(root: Path, tq: Path, *, marked: bool) -> None:
    path = root / "tools" / "ClientSETUP" / "scripts" / "EvEJSConfig.bat"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "@echo off",
        f'set "EVEJS_CLIENT_PATH={tq}"',
        'set "EVEJS_CLIENT_EXE=bin64\\exefile.exe"',
    ]
    if marked:
        lines.extend(
            (
                'set "TRINITYPLATFORM=dx12"',
                'set "EVEJS_DLSS5=on"',
            )
        )
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _package(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    manager_bytes: bytes = b"Write-Host fixture manager\r\n",
    evejs_version: str = "0.12.6",
) -> tuple[Path, Path, dict[str, object]]:
    (root / "package.json").write_text(
        json.dumps({"name": "eve.js", "version": evejs_version}),
        encoding="utf-8",
    )
    package = root / "mods" / "DLSS5"
    manager = package / "EveJS-Integration" / "Manage-EveJSDLSS5.ps1"
    manager.parent.mkdir(parents=True)
    manager.write_bytes(manager_bytes)
    digest = hashlib.sha256(manager_bytes).hexdigest().upper()
    monkeypatch.setattr(dlss5, "_TRUSTED_MANAGER_SHA256", frozenset({digest}))
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "id": "evejs-dlss5",
        "displayName": "EveJS DLSS5",
        "version": "0.4.0-test",
        "description": "Fixture automatic client package.",
        "kind": "client-package",
        "activation": "automatic",
        "restart": "client_launch",
        "manager": {
            "path": "EveJS-Integration/Manage-EveJSDLSS5.ps1",
            "protocol": "evejs_dlss5_manager_v1",
            "sha256": digest,
        },
        "compatibility": {
            "evejsVersion": evejs_version,
            "clientBuild": 3396210,
            "profile": "DLSS5",
        },
    }
    manifest_path = package / "evejs-launcher.client-mod.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return package, manager, manifest


def test_absent_package_preserves_historical_launch_environment(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "v0.12.6"
    root.mkdir(parents=True)
    tq = _client(workspace)
    _config(root, tq, marked=False)

    assert dlss5.discover_dlss5_client_mod(root) is None
    assert dlss5.ensure_dlss5_client_mod(root, tq) == {}


def test_marked_install_without_package_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    tq = _client(root)
    _config(root, tq, marked=True)

    with pytest.raises(dlss5.DLSS5ClientModError, match="no standalone installation receipt"):
        dlss5.ensure_dlss5_client_mod(root, tq)


@pytest.mark.parametrize("evejs_version", ("0.12.6", "0.12.7"))
def test_supported_package_is_detected_enabled_and_automatic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evejs_version: str,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    package, manager, _manifest = _package(
        root,
        monkeypatch,
        evejs_version=evejs_version,
    )

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert mod.valid
    assert mod.active
    assert mod.activation_kind is ActivationKind.CLIENT_PACKAGE
    assert mod.supported_backends == ("client",)
    assert mod.restart_scope == "client_launch"
    assert mod.path == package
    assert mod.manager_path == manager
    assert mod.evejs_version == evejs_version


def test_unsupported_evejs_package_is_visible_as_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    _package(root, monkeypatch, evejs_version="0.12.8")

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert not mod.valid
    assert not mod.active
    assert "0.12.6, 0.12.7" in (mod.error or "")
    assert "0.12.8" in (mod.error or "")


def test_multi_version_package_matches_the_selected_evejs_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    package, _manager, manifest = _package(
        root,
        monkeypatch,
        evejs_version="0.12.7",
    )
    manifest["schemaVersion"] = 2
    compatibility = manifest["compatibility"]
    assert isinstance(compatibility, dict)
    compatibility.pop("evejsVersion")
    compatibility["evejsVersions"] = ["0.12.6", "0.12.7"]
    (package / "evejs-launcher.client-mod.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert mod.valid
    assert mod.evejs_version == "0.12.7"


def test_manifest_version_must_match_the_selected_evejs_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    package, _manager, manifest = _package(
        root,
        monkeypatch,
        evejs_version="0.12.7",
    )
    compatibility = manifest["compatibility"]
    assert isinstance(compatibility, dict)
    compatibility["evejsVersion"] = "0.12.6"
    (package / "evejs-launcher.client-mod.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert not mod.valid
    assert "selected EveJS 0.12.7 root" in (mod.error or "")


@pytest.mark.parametrize("malformed_version", ([], {}, None))
def test_malformed_evejs_version_type_is_visible_as_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    malformed_version: object,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    package, _manager, manifest = _package(root, monkeypatch)
    compatibility = manifest["compatibility"]
    assert isinstance(compatibility, dict)
    compatibility["evejsVersion"] = malformed_version
    (package / "evejs-launcher.client-mod.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert not mod.valid
    assert "evejsVersion must be a string" in (mod.error or "")


def test_dlss_package_requires_an_actual_evejs_root_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    _package(root, monkeypatch)
    (root / "package.json").unlink()

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert not mod.valid
    assert "EveJS package manifest" in (mod.error or "")


def test_untrusted_manager_is_visible_as_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    _package(root, monkeypatch)
    monkeypatch.setattr(dlss5, "_TRUSTED_MANAGER_SHA256", frozenset())

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert not mod.valid
    assert not mod.active
    assert "not trusted" in (mod.error or "")


def test_unknown_manifest_field_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    package, _manager, manifest = _package(root, monkeypatch)
    manifest["command"] = "please execute arbitrary nonsense"
    (package / "evejs-launcher.client-mod.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    mod = dlss5.discover_dlss5_client_mod(root)

    assert mod is not None
    assert not mod.valid
    assert "unknown command" in (mod.error or "")


def test_ensure_calls_shared_manager_with_durable_state_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "v0.12.6"
    root.mkdir(parents=True)
    tq = _client(workspace)
    _config(root, tq, marked=True)
    _package(root, monkeypatch)
    captured: dict[str, object] = {}

    def fake_run(
        package,
        *,
        workspace_root,
        evejs_root,
        client_root,
        state_root,
    ) -> None:
        captured.update(
            package=package,
            workspace_root=workspace_root,
            evejs_root=evejs_root,
            client_root=client_root,
            state_root=state_root,
        )

    monkeypatch.setattr(dlss5, "_run_dlss5_manager", fake_run)

    environment = dlss5.ensure_dlss5_client_mod(root, tq)

    assert environment == {
        "TRINITYPLATFORM": "dx12",
        "EVEJS_DLSS5": "on",
    }
    assert captured["workspace_root"] == root.resolve().parent
    assert captured["evejs_root"] == root.resolve()
    assert captured["client_root"] == tq.resolve()
    assert captured["state_root"] == root.resolve() / "_local" / "dlss5" / "install"


def test_manager_invocation_is_fixed_and_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "v0.12.6"
    root.mkdir(parents=True)
    tq = _client(workspace)
    package_path, _manager, _manifest = _package(root, monkeypatch)
    package = dlss5.discover_dlss5_client_mod(root)
    assert package is not None and package.valid
    powershell = tmp_path / "WindowsPowerShell" / "powershell.exe"
    powershell.parent.mkdir()
    powershell.write_bytes(b"fixture")
    (powershell.parent / "Modules").mkdir()
    monkeypatch.setattr(dlss5, "_windows_powershell_path", lambda: powershell)
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "verified"
        stderr = ""

    def fake_preparation_process(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return Completed()

    monkeypatch.setattr(dlss5, "_run_dlss5_preparation_process", fake_preparation_process)
    state_root = root / "_local" / "dlss5" / "install"

    dlss5._run_dlss5_manager(
        package,
        workspace_root=root.parent,
        evejs_root=root,
        client_root=tq,
        state_root=state_root,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == str(powershell)
    assert command[1:7] == [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    assert command[8:] == [
        "-Action",
        "Ensure",
        "-Profile",
        "DLSS5",
        "-WorkspaceRoot",
        str(root.parent),
        "-EveJSRootPath",
        str(root),
        "-ClientRoot",
        str(tq),
        "-StateRootPath",
        str(state_root),
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["cwd"] == package_path
    assert kwargs["timeout"] == 3_600
    assert kwargs["environment"]["PSModulePath"] == str(powershell.parent / "Modules")


@pytest.mark.parametrize("digest", [
    "AFD954362BF35141FA7E615E41A3394F87C618EE83F59BD2071971B27C40A9E6",
    "8AFF0AE3FBEB22D95FD853A4A8FB832580F47C1FF62281DA6D11300F09E1FACE",
    "8C3310AFACC7BB0AED36BE8923BFD10CBB13A706B3549A2498A425281A434D30",
    "6723D31BF55C2E221453038412E0DF19EF781F20A9020D5DF9B91CA36F0784DA",
    "2321744C25719313C520774659E63F02C0080DE97A90031E7F05E36A899ED3D4",
])
def test_public_bootstrap_manager_is_an_explicit_trust_anchor(digest: str) -> None:
    assert digest in dlss5._TRUSTED_MANAGER_SHA256


def test_manager_timeout_diagnostic_matches_preparation_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "EveJS"
    root.mkdir()
    _package(root, monkeypatch)
    package = dlss5.discover_dlss5_client_mod(root)
    assert package is not None and package.valid
    monkeypatch.setattr(dlss5, "_windows_powershell_path", lambda: tmp_path / "powershell.exe")
    monkeypatch.setattr(dlss5, "_windows_powershell_environment", lambda _path: {})

    def timed_out(command, *, cwd, environment, timeout):
        assert timeout == 3_600
        raise dlss5.subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(dlss5, "_run_dlss5_preparation_process", timed_out)
    with pytest.raises(dlss5.DLSS5ClientModError, match="exceeded one hour"):
        dlss5._run_dlss5_manager(
            package,
            workspace_root=tmp_path,
            evejs_root=root,
            client_root=tmp_path / "client",
            state_root=root / "_local" / "dlss5" / "install",
        )
