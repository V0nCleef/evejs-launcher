import json
from pathlib import Path

import pytest

from src.core import server_launcher
from src.core.native_config_preflight import (
    NativeConfigPreflightError,
    NativeConfigPreflightResult,
)


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


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _root(tmp_path: Path, version: str) -> Path:
    root = tmp_path / "evejs-install"
    root.mkdir(parents=True)
    _write(root / "package.json", json.dumps({"name": "evejs-repo", "version": version}))
    _write(root / "server/index.js", "// fixture\n")
    _write(
        root / "server/package.json",
        json.dumps({"name": "eve.js", "version": version}),
    )
    _write(
        root / "server/src/config/manager.js",
        f'const EVEJS_VERSION = "{version}";\nmodule.exports = {{ EVEJS_VERSION }};\n',
    )
    _write(root / "server/src/config/schema/index.js", "module.exports = [];\n")
    if version == "0.12.9":
        _write(root / "server/src/config/dataRoot.js", DATA_ROOT_SOURCE)
        _write(root / "server/src/utils/logger/index.js", LOGGER_SOURCE)
    return root


def test_server_log_path_uses_release_layout(tmp_path):
    legacy = _root(tmp_path / "legacy", "0.12.8")
    beta = _root(tmp_path / "beta", "0.12.9")

    assert server_launcher.get_server_log_path(legacy) == legacy / "server/logs/server.log"
    assert server_launcher.get_server_log_path(beta) == beta / "_local/logs/server.log"


def test_game_command_keeps_legacy_report_argument_for_old_layout(tmp_path):
    root = _root(tmp_path, "0.12.8")

    command = server_launcher.build_game_server_command(root, "vanilla")

    assert "--report-dir=./logs/node-reports" in command


def test_beta_game_command_uses_absolute_report_directory(tmp_path):
    root = _root(tmp_path, "0.12.9")
    expected = root.resolve() / "_local/logs/node-reports"

    command = server_launcher.build_game_server_command(root, "vanilla")

    assert f"--report-dir={expected}" in command
    assert "--report-dir=./logs/node-reports" not in command


def _fake_start_dependencies(monkeypatch, root: Path, *, validator, prepare, spawn_observed):
    server_dir = root / "server"
    console_log = root / "server-console.log"
    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", console_log)
    monkeypatch.setattr(server_launcher, "_LOGS_DIR", root / "launcher-logs")
    monkeypatch.setattr(
        server_launcher,
        "ensure_native_game_dependencies",
        lambda _server_dir: spawn_observed.append("dependencies"),
    )
    monkeypatch.setattr(server_launcher, "validate_native_server_config", validator)
    monkeypatch.setattr(server_launcher, "_prepare_native_game_store_environment", prepare)
    monkeypatch.setattr(server_launcher, "get_graceful_server_process_flags", lambda: {})

    class FakeProcess:
        pid = 8123
        stdout = None

    def fake_spawn(command, _console_log, **kwargs):
        spawn_observed.append("spawn")
        spawn_observed.append((command, kwargs))
        return FakeProcess()

    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server_launcher, "_popen_with_console_log", fake_spawn)
    monkeypatch.setattr(server_launcher.threading, "Thread", FakeThread)
    return server_dir, console_log


def test_start_clears_inherited_data_root_and_preflights_before_store_setup(
    tmp_path,
    monkeypatch,
):
    root = _root(tmp_path, "0.12.9")
    timeline = []
    monkeypatch.setenv("EVEJS_DATA_ROOT", str(tmp_path / "other-install"))

    def validate(selected_root, *, env):
        assert Path(selected_root) == root
        assert "EVEJS_DATA_ROOT" not in env
        timeline.append("preflight")
        return NativeConfigPreflightResult(True, True, "Config passed.")

    def prepare(selected_root, server_dir, env):
        assert selected_root == root
        assert "EVEJS_DATA_ROOT" not in env
        timeline.append("store")

    def validator(selected_root, *, env):
        return validate(selected_root, env=env)

    _server_dir, _console_log = _fake_start_dependencies(
        monkeypatch,
        root,
        validator=validator,
        prepare=prepare,
        spawn_observed=timeline,
    )

    server_launcher.start_game_server(str(root), mode="vanilla")

    assert timeline[:4] == ["dependencies", "preflight", "store", "spawn"]
    command, kwargs = timeline[4]
    assert f"--report-dir={root.resolve() / '_local/logs/node-reports'}" in command
    assert "EVEJS_DATA_ROOT" not in kwargs["env"]
    assert (root / "_local/logs/node-reports").is_dir()


def test_invalid_config_logs_actionable_error_before_store_setup_or_spawn(
    tmp_path,
    monkeypatch,
):
    root = _root(tmp_path, "0.12.9")
    timeline = []
    error = NativeConfigPreflightError(
        "The selected EveJS config rejects config/server.json setting `obsoleteFlag`. No config files were changed.",
        valid=False,
        diagnostics=("unknown_setting|server.json|obsoleteFlag",),
    )

    def validate(_selected_root, *, env):
        assert "EVEJS_DATA_ROOT" not in env
        timeline.append("preflight")
        raise error

    def prepare(*_args):
        timeline.append("store")

    _server_dir, console_log = _fake_start_dependencies(
        monkeypatch,
        root,
        validator=validate,
        prepare=prepare,
        spawn_observed=timeline,
    )

    with pytest.raises(NativeConfigPreflightError, match="obsoleteFlag") as raised:
        server_launcher.start_game_server(str(root), mode="vanilla")

    assert isinstance(raised.value, RuntimeError)
    assert timeline == ["dependencies", "preflight"]
    assert "obsoleteFlag" in console_log.read_text(encoding="utf-8")
    assert "No config files were changed" in console_log.read_text(encoding="utf-8")


def test_legacy_start_skips_beta_config_preflight(tmp_path, monkeypatch):
    root = _root(tmp_path, "0.12.8")
    timeline = []

    def unexpected_validator(*_args, **_kwargs):
        pytest.fail("the 0.12.8 source must skip the beta config preflight")

    def prepare(*_args):
        timeline.append("store")

    server_dir, _console_log = _fake_start_dependencies(
        monkeypatch,
        root,
        validator=unexpected_validator,
        prepare=prepare,
        spawn_observed=timeline,
    )

    server_launcher.start_game_server(str(root), mode="vanilla")

    assert timeline[:3] == ["dependencies", "store", "spawn"]
    command, _kwargs = timeline[3]
    assert "--report-dir=./logs/node-reports" in command
    assert (server_dir / "logs/node-reports").is_dir()
