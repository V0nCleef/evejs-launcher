import json
from pathlib import Path

from src.core.evejs_compatibility import inspect_evejs_runtime


DATA_ROOT_SOURCE = """
const path = require("path");
const REPO_ROOT = path.resolve(__dirname, "../../..");
const DEFAULT_DATA_ROOT = path.join(REPO_ROOT, "_local");
function resolveDataRoot(env = process.env) {
  const configured = String((env && env.EVEJS_DATA_ROOT) || "").trim();
  return configured ? path.resolve(configured) : DEFAULT_DATA_ROOT;
}
function resolveDataRootPath(area, ...segments) {
  return path.join(resolveDataRoot(), area, ...segments);
}
"""

LOGGER_SOURCE = """
const { resolveDataRootPath } = require("../../config/dataRoot");
const LOG_DIR = resolveDataRootPath("logs");
const SERVER_LOG_PATH = path.join(LOG_DIR, "server.log");
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _runtime_root(
    tmp_path: Path,
    *,
    version: str | None = "0.12.9",
    root_name: str = "renamed-install",
) -> Path:
    root = tmp_path / root_name
    root.mkdir(parents=True)
    if version is not None:
        _write(root / "package.json", json.dumps({"name": "fixture", "version": version}))
        server_version = version
    else:
        server_version = "0.12.9"
    _write(
        root / "server/package.json",
        json.dumps({"name": "eve.js", "version": server_version}),
    )
    _write(
        root / "server/src/config/manager.js",
        f'const EVEJS_VERSION = "{server_version}";\nmodule.exports = {{ EVEJS_VERSION }};\n',
    )
    _write(root / "server/src/config/schema/index.js", "module.exports = [];\n")
    if version == "0.12.9":
        _write(root / "server/src/config/dataRoot.js", DATA_ROOT_SOURCE)
        _write(root / "server/src/utils/logger/index.js", LOGGER_SOURCE)
        _write(
            root / "tools/ClientSETUP/scripts/Install-EvEJSCerts.ps1",
            "param(\n  [string]$ClientPath,\n  [switch]$CheckOnly\n)\n",
        )
    else:
        _write(
            root / "tools/ClientSETUP/scripts/Install-EvEJSCerts.ps1",
            "param(\n  [string]$ClientPath\n)\n",
        )
    return root


def test_legacy_layout_uses_server_logs_and_existing_capabilities(tmp_path):
    root = _runtime_root(tmp_path, version="0.12.8")

    layout = inspect_evejs_runtime(root)

    assert layout.evejs_version == "0.12.8"
    assert layout.metadata_status == "valid"
    assert layout.log_directory == root / "server/logs"
    assert layout.node_report_directory == root / "server/logs/node-reports"
    assert layout.node_report_directories == (root / "server/logs/node-reports",)
    assert layout.legacy_node_report_directory is None
    assert layout.uses_data_root_logs is False
    assert layout.config_preflight_supported is False
    assert layout.certificate_check_only_supported is False


def test_beta_routes_new_logs_and_reports_while_retaining_legacy_report_candidate(tmp_path):
    root = _runtime_root(tmp_path)

    layout = inspect_evejs_runtime(root)

    assert layout.uses_data_root_logs is True
    assert layout.log_directory == root / "_local/logs"
    assert layout.node_report_directory == root / "_local/logs/node-reports"
    assert layout.legacy_node_report_directory == root / "server/logs/node-reports"
    assert layout.node_report_directories == (
        root / "_local/logs/node-reports",
        root / "server/logs/node-reports",
    )
    assert layout.certificate_check_only_supported is True
    assert layout.config_preflight_supported is True
    assert layout.certificate_directory == root / "server/certs"
    assert layout.game_store_default_directory == root / "_local/gameStore"
    assert not layout.log_directory.exists()
    assert not layout.node_report_directory.exists()


def test_explicit_relative_data_root_uses_child_server_working_directory(tmp_path):
    root = _runtime_root(tmp_path)

    layout = inspect_evejs_runtime(root, data_root_override="operator-data")

    assert layout.log_directory == root / "server/operator-data/logs"
    assert layout.node_report_directory == root / "server/operator-data/logs/node-reports"
    assert layout.game_store_default_directory == root / "_local/gameStore"


def test_explicit_absolute_data_root_and_empty_override(tmp_path):
    root = _runtime_root(tmp_path)
    external = tmp_path / "separate-data"

    explicit = inspect_evejs_runtime(root, data_root_override=external)
    empty = inspect_evejs_runtime(root, data_root_override="  ")

    assert explicit.log_directory == external / "logs"
    assert empty.log_directory == root / "_local/logs"


def test_default_does_not_inherit_process_data_root(tmp_path, monkeypatch):
    root = _runtime_root(tmp_path)
    monkeypatch.setenv("EVEJS_DATA_ROOT", str(tmp_path / "unselected"))

    layout = inspect_evejs_runtime(root)

    assert layout.log_directory == root / "_local/logs"


def test_missing_metadata_keeps_safe_legacy_layout_even_with_data_root_sources(tmp_path):
    root = _runtime_root(tmp_path)
    (root / "package.json").unlink()

    layout = inspect_evejs_runtime(root)

    assert layout.evejs_version is None
    assert layout.metadata_status == "missing"
    assert layout.uses_data_root_logs is False
    assert layout.log_directory == root / "server/logs"


def test_conflicting_metadata_keeps_safe_legacy_layout(tmp_path):
    root = _runtime_root(tmp_path)
    _write(root / "config/version.json", json.dumps({"evejsVersion": "0.12.8"}))

    layout = inspect_evejs_runtime(root)

    assert layout.evejs_version is None
    assert layout.metadata_status == "invalid_or_conflicting"
    assert layout.uses_data_root_logs is False
    assert layout.log_directory == root / "server/logs"


def test_folder_name_does_not_determine_runtime_layout(tmp_path):
    renamed_root = _runtime_root(
        tmp_path,
        version="0.12.9",
        root_name="EveJS-v0.12.8",
    )

    layout = inspect_evejs_runtime(renamed_root)

    assert layout.evejs_version == "0.12.9"
    assert layout.uses_data_root_logs is True
    assert layout.log_directory == renamed_root / "_local/logs"


def test_malformed_metadata_is_typed_and_does_not_adopt_beta_paths(tmp_path):
    root = _runtime_root(tmp_path)
    _write(root / "package.json", "{ broken json")

    layout = inspect_evejs_runtime(root)

    assert layout.evejs_version is None
    assert layout.metadata_status == "invalid_or_conflicting"
    assert layout.uses_data_root_logs is False
    assert layout.log_directory == root / "server/logs"


def test_config_preflight_requires_confirmed_beta_metadata_and_manager_export(tmp_path):
    root = _runtime_root(tmp_path)
    assert inspect_evejs_runtime(root).config_preflight_supported is True

    _write(root / "server/package.json", json.dumps({"version": "0.12.8"}))
    assert inspect_evejs_runtime(root).config_preflight_supported is False

    _write(root / "server/package.json", json.dumps({"version": "0.12.9"}))
    _write(
        root / "server/src/config/manager.js",
        'const EVEJS_VERSION = "0.12.8";\nmodule.exports = { EVEJS_VERSION };\n',
    )
    assert inspect_evejs_runtime(root).config_preflight_supported is False
