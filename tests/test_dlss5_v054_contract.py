"""0.5.4 trust-pin additions without changing historical launch/removal policy.

Real immutable contract assertions are separate from deliberately tiny synthetic
receipt fixtures. No fixture starts a manager, GUI, game, or real renderer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import dlss5
import test_dlss5_standalone as standalone_fixture


OLD_MANAGERS = frozenset({
    "AFD954362BF35141FA7E615E41A3394F87C618EE83F59BD2071971B27C40A9E6",
    "8AFF0AE3FBEB22D95FD853A4A8FB832580F47C1FF62281DA6D11300F09E1FACE",
    "8C3310AFACC7BB0AED36BE8923BFD10CBB13A706B3549A2498A425281A434D30",
    "6723D31BF55C2E221453038412E0DF19EF781F20A9020D5DF9B91CA36F0784DA",
    "2321744C25719313C520774659E63F02C0080DE97A90031E7F05E36A899ED3D4",
})
V054_MANAGER = "64275F97BDA248FC5C35BED90E6E3EBB1B330F6F49AA13AF8D4F28B52BC4DECF"
V054_DXGI = (5594112, "26EBDD0C2AE67EED8D305BC8B7A3A67B606F74D19979EFDFE6E584ACB27B78BF")
V054_GUARD = (30763542, "BC8DD57471B376D3CC37A1908CEE64174E98EDB6D3D94B9F04437BDCE33686CC")


def test_v054_manager_is_exact_addition_and_all_previous_managers_remain_trusted():
    # The separate v055 contract test pins the complete new allowlist.
    assert OLD_MANAGERS | {V054_MANAGER} <= dlss5._TRUSTED_MANAGER_SHA256


def test_v054_payload_contract_is_exact_and_every_old_version_remains_unchanged():
    native = {
        "bin64/nvngx_dlssnr.dll": (165840496, "E16BCF15E16E13F527491CDF7845B2FE6521A738D8F7C9C721866A8496E1FC8E"),
        "bin64/sl.dlss_nr.dll": (401024, "9F6672E5E0170DC118A3188D21BDA187E1FC1AA3502895B21AB846D23165C11D"),
        "bin64/renodx-dlss5.addon64": (1732608, "D5ADF82EB44B065F4C590AC91FE824BAB07AFEA0EB9F994BDE936710C8593952"),
        "bin64/dxgi.dll": (5591040, "77C3168A7661FA2230D494C4982FE7EAFEDC9370BD1DA48EBFDF9C2A51662CC8"),
    }
    previous_guards = {
        "0.5.0-dev": (30760908, "04B27D576BD897091140D86A65AD57FDEF2ABEEB3DA3FDF9A8348EB5560951E3"),
        "0.5.1-dev": (30761488, "595A756A91B3C6E40DD55432682B2595CF07FB87531C3C22F56FD7E3EE28B5D8"),
        "0.5.2-dev": (30766379, "C980719606DDCF58D218991255FE390672FBC82E3EA89C572D460C158AD7CD44"),
        "0.5.3-dev": (30766379, "C980719606DDCF58D218991255FE390672FBC82E3EA89C572D460C158AD7CD44"),
    }
    expected = {version: {**native, "code.ccp": guard}
                for version, guard in previous_guards.items()}
    expected["0.5.3-dev"]["bin64/dxgi.dll"] = (
        5591552, "8BAD71B96C4CB92CE04E18D661DCC508B30258C196F4CF01B639E58326BD6471")
    expected["0.5.4-dev"] = {**native, "bin64/dxgi.dll": V054_DXGI, "code.ccp": V054_GUARD}
    assert {version: dlss5._STANDALONE_PAYLOADS_BY_VERSION[version] for version in expected} == expected
    assert dlss5._STANDALONE_NATIVE_FILES == native
    assert dlss5._STANDALONE_EXE == (
        1003152, "2AAF7A9A8DFCDE85E4ADB50C1ECCD3756A4D29AEB854DFE69629846BA56EE979")


def _snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def _no_execution_or_profiles(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Launch-only verification must not start helpers or create profile state")
    monkeypatch.setattr(dlss5, "_run_dlss5_manager", forbidden)
    monkeypatch.setattr(dlss5.subprocess, "Popen", forbidden)
    monkeypatch.setattr(dlss5, "prepare_dlss5_profile_environment", forbidden)


@pytest.mark.parametrize("status", [None, "restored", "rolledBack"])
def test_no_dlss_and_terminal_receipts_keep_historical_empty_environment_without_writes(
    tmp_path, monkeypatch, status
):
    root, client = tmp_path / "EveJS", tmp_path / "Client/tq"
    root.mkdir()
    client.mkdir(parents=True)
    (root / "package.json").write_text('{"version":"0.12.7"}', encoding="utf-8")
    if status:
        state = root / "_local/dlss5/install"
        state.mkdir(parents=True)
        (state / "active-install.json").write_text(
            json.dumps({"status": status, "integrationVersion": "0.5.4-dev"}), encoding="utf-8")
    _no_execution_or_profiles(monkeypatch)
    before = _snapshot(tmp_path)
    assert dlss5.discover_dlss5_client_mod(root) is None
    assert dlss5.ensure_dlss5_client_mod(root, client) == {}
    assert _snapshot(tmp_path) == before
    assert not (root / "mods").exists()
    assert not (tmp_path / "Profiles").exists()


@pytest.fixture(params=("0.5.3-dev", "0.5.4-dev"))
def synthetic_install(tmp_path, monkeypatch, request):
    """Reuse real validation fixture, substituting only explicit test payloads."""
    original_payload = standalone_fixture._fixture_payload
    def payload(name, version):
        if version == "0.5.4-dev" and name in ("bin64/dxgi.dll", "code.ccp"):
            return (name + ":pid-state-v12").encode()
        return original_payload(name, version)
    monkeypatch.setattr(standalone_fixture, "REVIEWED_VERSIONS", ("0.5.3-dev", "0.5.4-dev"))
    monkeypatch.setattr(standalone_fixture, "_fixture_payload", payload)
    installed = standalone_fixture.installed.__wrapped__(
        tmp_path, monkeypatch, SimpleNamespace(param=request.param))
    _no_execution_or_profiles(monkeypatch)
    return installed, payload


def test_synthetic_v053_and_v054_launch_only_validation_are_read_only(synthetic_install):
    (root, client, _path, _receipt), _payload = synthetic_install
    before = _snapshot(root.parent)
    assert dlss5.ensure_dlss5_client_mod(root, client) == {
        "TRINITYPLATFORM": "dx12", "EVEJS_DLSS5": "on"}
    assert _snapshot(root.parent) == before
    assert not (root / "mods").exists()


@pytest.mark.parametrize("name", ["bin64/dxgi.dll", "code.ccp"])
@pytest.mark.parametrize("rewrite_receipt", [False, True])
def test_synthetic_mixed_v053_v054_files_are_rejected_even_with_matching_receipt_claim(
    synthetic_install, name, rewrite_receipt
):
    (root, client, path, receipt), payload = synthetic_install
    other = "0.5.4-dev" if receipt["integrationVersion"] == "0.5.3-dev" else "0.5.3-dev"
    data = payload(name, other)
    (client / name).write_bytes(data)
    if rewrite_receipt:
        operation = next(op for op in receipt["operations"]
                         if op["destination"].replace("\\", "/") == name)
        operation.update(installedBytes=len(data), installedSha256=hashlib.sha256(data).hexdigest().upper())
        path.write_text(json.dumps(receipt), encoding="utf-8")
    before = _snapshot(root.parent)
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)
    assert _snapshot(root.parent) == before


@pytest.mark.parametrize("rewrite_operations", [False, True])
def test_synthetic_v053_v054_receipt_cannot_relabel_installed_version(
    synthetic_install, rewrite_operations
):
    (root, client, path, receipt), payload = synthetic_install
    other = "0.5.4-dev" if receipt["integrationVersion"] == "0.5.3-dev" else "0.5.3-dev"
    receipt["integrationVersion"] = other
    if rewrite_operations:
        for operation in receipt["operations"]:
            data = payload(operation["destination"].replace("\\", "/"), other)
            operation.update(installedBytes=len(data), installedSha256=hashlib.sha256(data).hexdigest().upper())
    path.write_text(json.dumps(receipt), encoding="utf-8")
    before = _snapshot(root.parent)
    with pytest.raises(dlss5.DLSS5ClientModError):
        dlss5.ensure_dlss5_client_mod(root, client)
    assert _snapshot(root.parent) == before
