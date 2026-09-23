"""Focused tests for read-only native EveJS config validation."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from src.core import native_config_preflight as preflight


def _write_fixture_install(root: Path, *, invalid_setting: bool = False) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    server_config = config_dir / "server.json"
    if invalid_setting:
        server_config.write_text(
            '{"presence":{"localChatAuthorityEnabled":true}}\n',
            encoding="utf-8",
        )
    else:
        server_config.write_text('{"server":{"port":26000}}\n', encoding="utf-8")

    config_module_dir = root / "server" / "src" / "config"
    schema_dir = config_module_dir / "schema"
    schema_dir.mkdir(parents=True)
    (root / "server" / "package.json").write_text(
        '{"name":"eve.js","version":"0.12.9"}\n',
        encoding="utf-8",
    )
    (schema_dir / "index.js").write_text('"use strict"; module.exports = [];\n', encoding="utf-8")
    (config_module_dir / "manager.js").write_text(
        r'''"use strict";
const fs = require("fs");
const path = require("path");
class ConfigValidationError extends Error {
  constructor(errors) { super("invalid config"); this.name = "ConfigValidationError"; this.errors = errors; }
}
function createConfigManager({ rootDir, schemaDefinitions }) {
  if (!Array.isArray(schemaDefinitions)) throw new Error("schema missing");
  if (process.env.EVEJS_DATA_ROOT || process.env.EVEJS_SERVER_PORT) {
    throw new Error("config environment should be isolated");
  }
  const configPath = path.join(rootDir, "config", "server.json");
  if (!fs.existsSync(configPath)) return {};
  const raw = JSON.parse(fs.readFileSync(configPath, "utf8"));
  if (raw.presence && Object.prototype.hasOwnProperty.call(raw.presence, "localChatAuthorityEnabled")) {
    throw new ConfigValidationError([
      'server.json contains unknown setting "presence.localChatAuthorityEnabled".',
    ]);
  }
  return {};
}
module.exports = { EVEJS_VERSION: "0.12.9", ConfigValidationError, createConfigManager };
''',
        encoding="utf-8",
    )
    # The preflight must never import this server bootstrap entry point.
    (root / "server" / "index.js").write_text(
        'require("fs").writeFileSync(process.env.BOOTSTRAP_MARKER, "loaded");\n',
        encoding="utf-8",
    )
    return server_config


def _require_node() -> str:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("Node.js is unavailable for the isolated manager integration test")
    return node_path


def test_older_install_without_validator_skips_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "old-install"
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "server.json"
    config_file.write_text('{"preserve":{"value":"untouched"}}\n', encoding="utf-8")
    before = config_file.read_bytes()
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unsupported server started Node"),
    )

    result = preflight.validate_native_server_config(root)

    assert result.supported is False
    assert result.valid is None
    assert "skipped" in result.message
    assert config_file.read_bytes() == before
    assert {path.name for path in config_dir.iterdir()} == {"server.json"}


def test_0128_manager_files_skip_without_starting_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "old-install-with-manager-files"
    config_file = _write_fixture_install(root)
    package_file = root / "server" / "package.json"
    package_file.write_text('{"name":"eve.js","version":"0.12.8"}\n', encoding="utf-8")
    before = config_file.read_bytes()
    monkeypatch.setattr(
        preflight.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("0.12.8 validator was not verified"),
    )

    result = preflight.validate_native_server_config(root)

    assert result.supported is False
    assert result.valid is None
    assert "0.12.9" in result.message
    assert config_file.read_bytes() == before


def test_valid_config_uses_manager_and_does_not_import_server_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_node()
    root = tmp_path / "valid-install"
    config_file = _write_fixture_install(root)
    before = config_file.read_bytes()
    marker = tmp_path / "bootstrap-imported.txt"
    monkeypatch.setenv("BOOTSTRAP_MARKER", str(marker))
    monkeypatch.setenv("EVEJS_DATA_ROOT", str(tmp_path / "redirected-root"))
    monkeypatch.setenv("EVEJS_SERVER_PORT", "not-a-port")

    result = preflight.validate_native_server_config(root)

    assert result.supported is True
    assert result.valid is True
    assert not marker.exists()
    assert config_file.read_bytes() == before
    assert sorted(path.name for path in (root / "config").iterdir()) == ["server.json"]


def test_removed_local_chat_setting_is_actionable_and_preserved(tmp_path: Path) -> None:
    _require_node()
    root = tmp_path / "updated-install"
    config_file = _write_fixture_install(root, invalid_setting=True)
    before = config_file.read_bytes()
    marker = tmp_path / "must-not-exist.txt"
    # This fixture's manager reads root/config and raises the same typed error
    # shape as the official manager when the obsolete setting is present.
    env = {"BOOTSTRAP_MARKER": str(marker)}

    with pytest.raises(preflight.NativeConfigPreflightError) as caught:
        preflight.validate_native_server_config(root, env=env)

    error = caught.value
    assert error.supported is True
    assert error.valid is False
    assert "config/server.json" in error.message
    assert "presence.localChatAuthorityEnabled" in error.message
    assert "migration" in error.message.casefold()
    assert "true" not in error.message
    assert config_file.read_bytes() == before
    assert not marker.exists()
    assert sorted(path.name for path in (root / "config").iterdir()) == ["server.json"]


@pytest.mark.parametrize(
    ("fake_run", "message_fragment"),
    [
        (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["node"], 10)
            ),
            "timed out",
        ),
        (
            lambda *_args, **_kwargs: subprocess.CompletedProcess(
                args=["node"], returncode=0, stdout="not-json\n"
            ),
            "invalid response",
        ),
    ],
)
def test_timeout_and_malformed_node_response_are_typed_execution_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_run,
    message_fragment: str,
) -> None:
    root = tmp_path / "fixture"
    _write_fixture_install(root)
    monkeypatch.setattr(preflight.shutil, "which", lambda *_args, **_kwargs: "node")
    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    with pytest.raises(preflight.NativeConfigPreflightError) as caught:
        preflight.validate_native_server_config(root)

    assert caught.value.supported is True
    assert caught.value.valid is None
    assert message_fragment in caught.value.message


def test_node_call_receives_fixed_script_root_argument_and_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fixture"
    _write_fixture_install(root)
    observed: dict[str, object] = {}
    monkeypatch.setattr(preflight.shutil, "which", lambda *_args, **_kwargs: "node.exe")
    monkeypatch.setattr(preflight, "get_hidden_process_flags", lambda: {"creationflags": 17})

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, '{"status":"valid"}\n')

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)

    result = preflight.validate_native_server_config(
        root,
        env={
            "PATH": "C:/node-bin",
            "EVEJS_DATA_ROOT": "C:/wrong-install",
            "NODE_OPTIONS": "--require injected.js",
            "NODE_PATH": "C:/wrong-modules",
        },
    )

    assert result.valid is True
    assert observed["command"] == ["node.exe", "-", str(root.resolve())]
    kwargs = observed["kwargs"]
    assert kwargs["input"] == preflight._NODE_PREFLIGHT_SCRIPT
    assert '"server", "index.js"' not in kwargs["input"]
    assert "initializeConfigFiles" in kwargs["input"]
    child_env = kwargs["env"]
    assert "EVEJS_DATA_ROOT" not in child_env
    assert "NODE_OPTIONS" not in child_env
    assert "NODE_PATH" not in child_env
    assert kwargs["shell"] is False
    assert kwargs["creationflags"] == 17
