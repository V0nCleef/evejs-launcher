"""Calendar releases retain exact compatibility checks and the selected CA."""
import json
from pathlib import Path

import pytest

from src.core.discovery import get_ca_cert_path, validate_evejs_root
from src.core.evejs_compatibility import inspect_evejs_runtime
from src.core.launcher import build_env
from src.core.mod_evejs_compatibility import (
    installed_evejs_version, normalize_evejs_version, parse_evejs_versions,
)
from src.core import native_config_preflight, platform_win
from test_evejs_compatibility import DATA_ROOT_SOURCE, LOGGER_SOURCE
from test_native_config_preflight import _write_fixture_install, _require_node


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _calendar_install(tmp_path: Path) -> Path:
    root = tmp_path / "renamed-server"
    _write(root / "package.json", json.dumps({"version": "261018.1"}))
    _write(root / "config/version.json", json.dumps({"evejsVersion": "261018.1"}))
    _write(root / "server/package.json", json.dumps({"version": "261018.1"}))
    _write(root / "server/index.js", "// bootstrap must never be executed\n")
    _write(root / "server/src/config/dataRoot.js", DATA_ROOT_SOURCE)
    _write(root / "server/src/utils/logger/index.js", LOGGER_SOURCE)
    _write(root / "server/src/config/manager.js",
           'const EVEJS_VERSION = "261018.1";\nmodule.exports = { EVEJS_VERSION };\n')
    _write(root / "server/src/config/schema/index.js", "module.exports = [];\n")
    _write(root / "server/src/config/certificateFiles.js", """
const { REPO_ROOT, resolveDataRootPath } = require("./dataRoot");
function certificateDirectory() { return resolveDataRootPath("certs"); }
function certificateFilePath(fileName) { return path.join(certificateDirectory(), fileName); }
module.exports = { certificateDirectory, certificateFilePath };
""")
    _write(root / "tools/ClientSETUP/scripts/EvEJSConfig.bat", "@echo off\n")
    _write(root / "_local/gameStore/gamestore.sqlite", "fixture\n")
    _write(root / "_local/certs/xmpp-ca-cert.pem", "SELECTED NEW CA\n")
    _write(root / "server/certs/xmpp-ca-cert.pem", "STALE LEGACY CA\n")
    return root


@pytest.mark.parametrize("version", ["261018.1", "v261018.1", "260228.0", "240229.12"])
def test_calendar_release_normalization(version):
    assert normalize_evejs_version(version) == version.removeprefix("v")


@pytest.mark.parametrize("version", ["1.2", "261332.1", "260229.1", "261018.01", "261018.1-beta", " 261018.1"])
def test_calendar_release_does_not_accept_arbitrary_two_part_or_malformed_versions(version):
    with pytest.raises(ValueError):
        normalize_evejs_version(version)


def test_calendar_metadata_and_manifest_preserve_exact_release_identity(tmp_path):
    root = _calendar_install(tmp_path)
    assert installed_evejs_version(root) == "261018.1"
    assert parse_evejs_versions(["0.12.9", "261018.1"]) == ("0.12.9", "261018.1")
    _write(root / "config/version.json", '{"evejsVersion":"261019.1"}')
    assert installed_evejs_version(root) is None


def test_calendar_layout_uses_current_logs_certs_and_config_validator(tmp_path):
    root = _calendar_install(tmp_path)
    layout = inspect_evejs_runtime(root)
    assert layout.evejs_version == "261018.1"
    assert layout.metadata_status == "valid"
    assert layout.log_directory == root / "_local/logs"
    assert layout.node_report_directories == (root / "_local/logs/node-reports", root / "server/logs/node-reports")
    assert layout.certificate_directory == root / "_local/certs"
    assert layout.config_preflight_supported
    assert validate_evejs_root(str(root)) == (True, "")
    (root / "server/certs/xmpp-ca-cert.pem").unlink()
    assert validate_evejs_root(str(root)) == (True, "")


def test_current_ca_wins_over_existing_legacy_ca_and_inherited_data_root(tmp_path, monkeypatch):
    root = _calendar_install(tmp_path)
    monkeypatch.setenv("EVEJS_DATA_ROOT", str(tmp_path / "another-install"))
    current_ca = root / "_local/certs/xmpp-ca-cert.pem"
    assert get_ca_cert_path(str(root)) == current_ca
    env = build_env(str(root))
    assert {env[key] for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")} == {str(current_ca)}
    client = tmp_path / "client/tq"
    _write(client / "bin64/packages/certifi/cacert.pem", current_ca.read_text())
    assert platform_win._selected_ca_is_in_client_bundles(root, client)


def test_new_certificate_directory_requires_actual_integration_and_valid_metadata(tmp_path):
    root = _calendar_install(tmp_path)
    cert_source = root / "server/src/config/certificateFiles.js"
    cert_source.write_text('// resolveDataRootPath("certs") is only a comment\n')
    assert get_ca_cert_path(str(root)) == root / "server/certs/xmpp-ca-cert.pem"


def test_native_validation_does_not_admit_unreviewed_calendar_release(tmp_path, monkeypatch):
    root = _calendar_install(tmp_path)
    _write(root / "server/package.json", '{"version":"261019.1"}')
    assert inspect_evejs_runtime(root).config_preflight_supported is False
    monkeypatch.setattr(native_config_preflight.subprocess, "run",
                        lambda *_args, **_kwargs: pytest.fail("Unreviewed config manager must not run"))
    result = native_config_preflight.validate_native_server_config(root)
    assert not result.supported and result.valid is None


def test_certificate_check_ignores_inherited_data_root(tmp_path, monkeypatch):
    import subprocess

    root = _calendar_install(tmp_path)
    _write(root / "tools/ClientSETUP/scripts/Install-EvEJSCerts.ps1",
           'param([string]$ClientPath, [switch]$CheckOnly)\n')
    client = tmp_path / "client/tq"
    client.mkdir(parents=True)
    monkeypatch.setenv("EVEJS_DATA_ROOT", str(tmp_path / "another-install"))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "passed", "")

    monkeypatch.setattr(platform_win.subprocess, "run", run)
    assert platform_win.prepare_evejs_client_certificate_trust(root, client)
    assert len(calls) == 1 and calls[0][0][-1] == "-CheckOnly"
    assert "EVEJS_DATA_ROOT" not in calls[0][1]["env"]


def test_explicit_data_root_moves_certificate_location_with_logs(tmp_path):
    root = _calendar_install(tmp_path)
    layout = inspect_evejs_runtime(root, data_root_override="operator-data")
    assert layout.certificate_directory == root / "server/operator-data/certs"
    assert layout.log_directory == root / "server/operator-data/logs"


def test_calendar_native_config_validation_is_read_only(tmp_path):
    _require_node()
    root = tmp_path / "native-validator"
    _write_fixture_install(root)
    for path in (root / "server/package.json", root / "server/src/config/manager.js"):
        path.write_text(path.read_text().replace("0.12.9", "261018.1"), encoding="utf-8")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = native_config_preflight.validate_native_server_config(root)
    assert result.supported and result.valid is True
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_public_powershell_helper_has_one_native_module_path(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.core import mod_api_runtime

    windows = tmp_path / "Windows"
    executable = windows / "System32/WindowsPowerShell/v1.0/powershell.exe"
    _write(executable, "fixture")
    (executable.parent / "Modules").mkdir()
    monkeypatch.setattr(mod_api_runtime.os, "environ", {
        "SystemRoot": str(windows), "PSMODULEPATH": "PowerShell7", "PSModulePath": "inherited"})
    descriptor = SimpleNamespace(launcher_api=SimpleNamespace(
        helper=SimpleNamespace(runtime="powershell", path=tmp_path / "helper.ps1")))
    command, environment = mod_api_runtime._command(descriptor, tmp_path / "request", tmp_path / "result")
    assert command[0] == str(executable)
    assert [key for key in environment if key.casefold() == "psmodulepath"] == ["PSModulePath"]
    assert environment["PSModulePath"] == str(executable.parent / "Modules")
