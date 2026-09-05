"""Standalone installer launches must not require or create a MOD package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import dlss5


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


REVIEWED_VERSIONS = (
    "0.5.0-dev", "0.5.1-dev", "0.5.2-dev", "0.5.3-dev",
    "0.5.4-dev", "0.5.5-dev", "0.5.5", "0.5.6",
)


def _fixture_payload(name: str, version: str) -> bytes:
    if version in ("0.5.5-dev", "0.5.5", "0.5.6"):
        version = "0.5.4-dev"  # Packaging-only release; exact same runtime.
    if name == "code.ccp":
        # Native F6 isolation changes ReShade, not the accepted V11 client guard.
        return (name + ":" + ("0.5.2-dev" if version == "0.5.3-dev" else version)).encode()
    if name == "bin64/dxgi.dll" and version == "0.5.3-dev":
        return (name + ":foreground-f6").encode()
    if name == "bin64/dxgi.dll" and version == "0.5.4-dev":
        return (name + ":process-local-nr").encode()
    return name.encode()


@pytest.fixture(params=REVIEWED_VERSIONS)
def installed(tmp_path, monkeypatch, request):
    version = request.param
    root = tmp_path / "EveJS - Installer"
    client = tmp_path / "Shared Client" / "tq"
    root.mkdir()
    (client / "bin64").mkdir(parents=True)
    evejs_version = "7.3.1-next.4" if version == "0.5.6" else "0.12.7.1"
    (root / "package.json").write_text(
        json.dumps({"name": "eve.js", "version": evejs_version})
    )
    (client / "start.ini").write_text("[main]\nbuild=3396210\nserver=127.0.0.1\n")
    for name in ("blue.dll", "_trinity_dx12.dll"):
        (client / "bin64" / name).write_bytes(b"fixture")
    payload = {}
    operations = []
    for name in ("bin64/nvngx_dlssnr.dll", "bin64/sl.dlss_nr.dll",
                 "bin64/renodx-dlss5.addon64", "bin64/dxgi.dll", "code.ccp"):
        data = _fixture_payload(name, version)
        (client / name).write_bytes(data)
        payload[name] = (len(data), _digest(data))
        operations.append({"destination": name.replace("/", "\\"),
                           "installedBytes": len(data), "installedSha256": _digest(data),
                           "kind": "replace" if name == "code.ccp" else "add", "applied": True})
    exe = b"original executable"
    (client / "bin64/exefile.exe").write_bytes(exe)
    versions = {}
    for candidate_version in REVIEWED_VERSIONS:
        versions[candidate_version] = {}
        for name in payload:
            data = _fixture_payload(name, candidate_version)
            versions[candidate_version][name] = (len(data), _digest(data))
    monkeypatch.setattr(dlss5, "_STANDALONE_PAYLOADS_BY_VERSION", versions, raising=False)
    monkeypatch.setattr(dlss5, "_STANDALONE_EXE", (len(exe), _digest(exe)), raising=False)
    config = root / "tools/ClientSETUP/scripts/EvEJSConfig.bat"
    config.parent.mkdir(parents=True)
    config.write_text(f'set "EVEJS_CLIENT_PATH={client}"\n'
                      'set "TRINITYPLATFORM=dx12"\nset "EVEJS_DLSS5=on"\n')
    (client / "bin64/ReShade.ini").write_text(
        "[ADDON]\nLoadFromDllMain=renodx-dlss5.addon64\n"
        "[RenoDX.DLSS5]\nEnableHooks=2\nNeuralUplift=1\n")
    state = (
        client.parent / "_evejs/dlss5/install"
        if version == "0.5.6"
        else root / "_local/dlss5/install"
    )
    state.mkdir(parents=True)
    receipt = {"schemaVersion": 5 if version == "0.5.6" else 4,
               "integrationVersion": version, "status": "installed",
               "profile": "DLSS5", "evejsRoot": str(root), "clientRoot": str(client),
               "stateRoot": str(state), "operations": operations,
               "executable": {"path": "bin64\\exefile.exe", "sha256": _digest(exe), "modified": False},
               "config": {"path": str(config), "installedSha256": _digest(config.read_bytes()), "applied": True},
               "reshadeConfig": {"schemaVersion": 2, "path": "bin64\\ReShade.ini"}}
    if version == "0.5.6":
        receipt.update(
            stateScope="client",
            workspaceRoot=str(root.parent),
        )
    receipt_path = state / "active-install.json"
    receipt_path.write_text(json.dumps(receipt))

    def forbidden(*args, **kwargs):
        pytest.fail("Standalone validation must not launch a manager or any subprocess")

    monkeypatch.setattr(dlss5, "_run_dlss5_manager", forbidden)
    monkeypatch.setattr(dlss5.subprocess, "Popen", forbidden)
    return root, client, receipt_path, receipt


@pytest.fixture
def installed_v056(tmp_path, monkeypatch):
    return installed.__wrapped__(
        tmp_path,
        monkeypatch,
        SimpleNamespace(param="0.5.6"),
    )


def _snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_installed_standalone_launches_read_only_without_mod_package(installed):
    root, client, receipt_path, _ = installed
    before = _snapshot(root.parent)
    assert dlss5.ensure_dlss5_client_mod(root, client) == {
        "TRINITYPLATFORM": "dx12", "EVEJS_DLSS5": "on"}
    assert dlss5.discover_dlss5_client_mod(root) is None
    assert not (root / "mods").exists()
    assert _snapshot(root.parent) == before


@pytest.mark.parametrize("field,value", [
    ("schemaVersion", True), ("schemaVersion", 3), ("integrationVersion", "999"),
    ("integrationVersion", None), ("integrationVersion", []), ("integrationVersion", {}),
    ("integrationVersion", 501),
    ("status", "installing"), ("status", "restored"), ("profile", "Vanilla"),
    ("evejsRoot", "C:/wrong"), ("clientRoot", "C:/wrong"), ("stateRoot", "C:/wrong"),
    ("operations", []), ("operations", None), ("config", None), ("executable", None),
    ("reshadeConfig", None),
])
def test_bad_receipt_is_rejected(installed, field, value):
    root, client, path, receipt = installed
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


@pytest.mark.parametrize("field,value", [
    ("stateScope", "evejs"),
    ("stateScope", None),
    ("workspaceRoot", "C:/wrong"),
    ("stateDirectory", "state"),
])
def test_bad_client_scoped_receipt_is_rejected(installed_v056, field, value):
    root, client, path, receipt = installed_v056
    receipt[field] = value
    path.write_text(json.dumps(receipt))

    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


def test_client_receipt_for_another_root_does_not_authorize_launch(installed_v056):
    old_root, client, _path, _receipt = installed_v056
    new_root = old_root.parent / "EveJS - New Version"
    new_root.mkdir()
    (new_root / "package.json").write_text(
        '{"name":"eve.js","version":"99.0.0"}', encoding="utf-8"
    )
    config = new_root / "tools/ClientSETUP/scripts/EvEJSConfig.bat"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'set "EVEJS_CLIENT_PATH={client}"\n'
        'set "TRINITYPLATFORM=dx12"\n'
        'set "EVEJS_DLSS5=on"\n',
        encoding="utf-8",
    )

    with pytest.raises(dlss5.DLSS5ClientModError, match="different installation"):
        dlss5.ensure_dlss5_client_mod(new_root, client)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "traversal", "hash", "size", "flag", "kind"])
def test_untrusted_operation_claims_are_rejected(installed, mutation):
    root, client, path, receipt = installed
    ops = receipt["operations"]
    if mutation == "missing":
        ops.pop()
    elif mutation == "duplicate":
        ops[-1] = ops[0]
    elif mutation == "traversal":
        ops[0]["destination"] = "../nvngx_dlssnr.dll"
    elif mutation == "hash":
        # Changing both file and receipt must not move the launcher's trust anchor.
        data = b"untrusted replacement"
        (client / "bin64/nvngx_dlssnr.dll").write_bytes(data)
        ops[0].update(installedSha256=_digest(data), installedBytes=len(data))
    elif mutation == "size":
        ops[0]["installedBytes"] += 1
    elif mutation == "flag":
        ops[0]["applied"] = 1
    else:
        ops[0]["kind"] = "delete"
    path.write_text(json.dumps(receipt))
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


@pytest.mark.parametrize("name", ["code.ccp", "bin64/dxgi.dll", "bin64/exefile.exe",
                                   "bin64/renodx-dlss5.addon64", "bin64/sl.dlss_nr.dll",
                                   "bin64/nvngx_dlssnr.dll", "bin64/_trinity_dx12.dll"])
def test_missing_client_file_is_rejected(installed, name):
    root, client, _, _ = installed
    (client / name).unlink()
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


def test_config_drift_is_rejected(installed):
    root, client, _, receipt = installed
    with Path(receipt["config"]["path"]).open("a") as stream:
        stream.write("rem changed after installation\n")
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


@pytest.mark.parametrize("data", [b"{}", b"{", b'{"status":"installed","status":"installed"}', b"x" * 262145],
                         ids=["empty", "invalid-json", "duplicate-key", "oversized"])
def test_malformed_receipt_is_rejected(installed, data):
    root, client, path, _ = installed
    path.write_bytes(data)
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


def test_f6_off_preference_does_not_block_standalone_verification(installed):
    root, client, _, _ = installed
    ini = client / "bin64/ReShade.ini"
    ini.write_text(ini.read_text().replace("NeuralUplift=1", "NeuralUplift=0"))
    assert dlss5.ensure_dlss5_client_mod(root, client)["EVEJS_DLSS5"] == "on"


@pytest.mark.parametrize("change", ["base", "hooks", "duplicate", "missing"])
def test_broken_reshade_settings_are_rejected(installed, change):
    root, client, _, _ = installed
    ini = client / "bin64/ReShade.ini"
    text = ini.read_text()
    if change == "base":
        text += "\n[INSTALL]\nBasePath=C:/somewhere\n"
    elif change == "hooks":
        text = text.replace("EnableHooks=2", "EnableHooks=0")
    elif change == "duplicate":
        text += "NeuralUplift=1\n"
    else:
        ini.unlink()
    if change != "missing":
        ini.write_text(text)
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)


def test_replacing_existing_dll_is_a_valid_installation(installed):
    root, client, path, receipt = installed
    receipt["operations"][0]["kind"] = "replace"
    path.write_text(json.dumps(receipt))
    assert dlss5.ensure_dlss5_client_mod(root, client)["EVEJS_DLSS5"] == "on"


def test_installed_receipt_with_missing_marker_is_rejected(installed):
    root, client, _, receipt = installed
    config = Path(receipt["config"]["path"])
    config.write_text(config.read_text().replace('set "EVEJS_DLSS5=on"', ""))
    with pytest.raises(dlss5.DLSS5ClientModError, match="marker is missing"):
        dlss5.ensure_dlss5_client_mod(root, client)


@pytest.mark.parametrize("status", ["restored", "rolledBack"])
def test_completed_uninstall_preserves_ordinary_launch(installed, status):
    root, client, path, receipt = installed
    receipt["status"] = status
    path.write_text(json.dumps(receipt))
    config = Path(receipt["config"]["path"])
    config.write_text(f'set "EVEJS_CLIENT_PATH={client}"\n')
    for operation in receipt["operations"][:-1]:
        (client / operation["destination"]).unlink()
    (client / "code.ccp").write_bytes(b"restored baseline")
    assert dlss5.ensure_dlss5_client_mod(root, client) == {}


@pytest.mark.parametrize("variable,value", [("EVEJS_DLSS5", "off"), ("TRINITYPLATFORM", "dx11"),
                                            ("EVEJS_CLIENT_PATH", "C:/different")])
def test_duplicate_batch_assignments_are_rejected_even_if_receipt_hash_matches(installed, variable, value):
    root, client, path, receipt = installed
    config = Path(receipt["config"]["path"])
    config.write_text(config.read_text() + f'set "{variable}={value}"\n')
    receipt["config"]["installedSha256"] = _digest(config.read_bytes())
    path.write_text(json.dumps(receipt))
    with pytest.raises(dlss5.DLSS5ClientModError, match="requires one"):
        dlss5.ensure_dlss5_client_mod(root, client)


@pytest.mark.parametrize("parent", ["state-top", "state-middle", "state", "tools",
                                   "tools/ClientSETUP", "tools/ClientSETUP/scripts"])
def test_linked_receipt_and_config_ancestors_are_rejected(installed, monkeypatch, parent):
    root, client, receipt_path, _ = installed
    state = receipt_path.parent
    state_boundary = client.parent if state.is_relative_to(client.parent) else root
    target = {
        "state-top": state_boundary / ("_evejs" if state_boundary == client.parent else "_local"),
        "state-middle": state.parent,
        "state": state,
        "tools": root / "tools",
        "tools/ClientSETUP": root / "tools/ClientSETUP",
        "tools/ClientSETUP/scripts": root / "tools/ClientSETUP/scripts",
    }[parent]
    original = dlss5._is_reparse_point
    monkeypatch.setattr(dlss5, "_is_reparse_point", lambda p: p == target or original(p))
    with pytest.raises(dlss5.DLSS5ClientModError, match="unlinked directory"):
        dlss5.ensure_dlss5_client_mod(root, client)


@pytest.mark.parametrize("name", ["code.ccp", "bin64/dxgi.dll", "bin64/exefile.exe"])
def test_same_size_file_tampering_is_rejected(installed, name):
    root, client, _, _ = installed
    target = client / name
    target.write_bytes(b"x" * target.stat().st_size)
    with pytest.raises(dlss5.DLSS5ClientModError, match="reviewed bytes"):
        dlss5.ensure_dlss5_client_mod(root, client)


def test_receipt_cannot_relabel_a_different_runtime(installed):
    root, client, path, receipt = installed
    original_version = receipt["integrationVersion"]
    for other in REVIEWED_VERSIONS:
        if other == original_version:
            continue
        receipt["integrationVersion"] = other
        path.write_text(json.dumps(receipt))
        same_state_contract = (other == "0.5.6") == (original_version == "0.5.6")
        if (same_state_contract and
                dlss5._STANDALONE_PAYLOADS_BY_VERSION[other] ==
                dlss5._STANDALONE_PAYLOADS_BY_VERSION[original_version]):
            assert dlss5.ensure_dlss5_client_mod(root, client) == {"TRINITYPLATFORM": "dx12", "EVEJS_DLSS5": "on"}
            continue
        with pytest.raises(dlss5.DLSS5ClientModError):
            dlss5.ensure_dlss5_client_mod(root, client)


def test_receipt_cannot_claim_other_version_bytes_without_replacing_file(installed):
    root, client, path, receipt = installed
    original_version = receipt["integrationVersion"]
    for other in REVIEWED_VERSIONS:
        if other == original_version:
            continue
        receipt["integrationVersion"] = other
        for operation in receipt["operations"]:
            name = operation["destination"].replace("\\", "/")
            size, digest = dlss5._STANDALONE_PAYLOADS_BY_VERSION[other][name]
            operation.update(installedBytes=size, installedSha256=digest)
        path.write_text(json.dumps(receipt))
        same_state_contract = (other == "0.5.6") == (original_version == "0.5.6")
        if (same_state_contract and
                dlss5._STANDALONE_PAYLOADS_BY_VERSION[other] ==
                dlss5._STANDALONE_PAYLOADS_BY_VERSION[original_version]):
            assert dlss5.ensure_dlss5_client_mod(root, client) == {"TRINITYPLATFORM": "dx12", "EVEJS_DLSS5": "on"}
            continue
        with pytest.raises(dlss5.DLSS5ClientModError):
            dlss5.ensure_dlss5_client_mod(root, client)


def test_reviewed_guard_versions_have_distinct_exact_pins():
    versions = dlss5._STANDALONE_PAYLOADS_BY_VERSION
    assert set(versions) == set(REVIEWED_VERSIONS)
    assert versions["0.5.0-dev"]["code.ccp"] == (
        30760908, "04B27D576BD897091140D86A65AD57FDEF2ABEEB3DA3FDF9A8348EB5560951E3"
    )
    assert versions["0.5.1-dev"]["code.ccp"] == (
        30761488, "595A756A91B3C6E40DD55432682B2595CF07FB87531C3C22F56FD7E3EE28B5D8"
    )
    assert versions["0.5.2-dev"]["code.ccp"] == (
        30766379, "C980719606DDCF58D218991255FE390672FBC82E3EA89C572D460C158AD7CD44"
    )
    assert versions["0.5.3-dev"]["code.ccp"] == versions["0.5.2-dev"]["code.ccp"]
    assert versions["0.5.3-dev"]["bin64/dxgi.dll"] == (
        5591552, "8BAD71B96C4CB92CE04E18D661DCC508B30258C196F4CF01B639E58326BD6471"
    )
    assert versions["0.5.4-dev"]["code.ccp"] == (
        30763542, "BC8DD57471B376D3CC37A1908CEE64174E98EDB6D3D94B9F04437BDCE33686CC"
    )
    assert versions["0.5.4-dev"]["bin64/dxgi.dll"] == (
        5594112, "26EBDD0C2AE67EED8D305BC8B7A3A67B606F74D19979EFDFE6E584ACB27B78BF"
    )
    assert len({value["code.ccp"] for value in versions.values()}) == 4
    assert versions["0.5.5-dev"] == versions["0.5.4-dev"]
    assert versions["0.5.5"] == versions["0.5.5-dev"]
    assert versions["0.5.6"] == versions["0.5.5"]
    native = {k: v for k, v in versions["0.5.0-dev"].items() if k != "code.ccp"}
    for version in REVIEWED_VERSIONS[:3]:
        value = versions[version]
        assert {k: v for k, v in value.items() if k != "code.ccp"} == native
    assert versions["0.5.2-dev"]["bin64/dxgi.dll"] == (
        5591040, "77C3168A7661FA2230D494C4982FE7EAFEDC9370BD1DA48EBFDF9C2A51662CC8"
    )
    assert {name for name in versions["0.5.3-dev"]
            if versions["0.5.3-dev"][name] != versions["0.5.2-dev"][name]} == {"bin64/dxgi.dll"}
    assert {name for name in versions["0.5.4-dev"]
            if versions["0.5.4-dev"][name] != versions["0.5.3-dev"][name]} == {"bin64/dxgi.dll", "code.ccp"}
