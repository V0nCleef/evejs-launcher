"""Read-only EveJS native-server config preflight.

The 0.12.9 config manager validates persisted config during construction. Keep
this probe in a short-lived Node subprocess so importing the selected server's
config code cannot contaminate the launcher's Python process.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Mapping

from .platform import get_hidden_process_flags


_PREFLIGHT_TIMEOUT_SECONDS = 10
_MAX_PROTOCOL_OUTPUT_BYTES = 16_384
_MAX_DIAGNOSTICS = 20
_MAX_DIAGNOSTIC_LENGTH = 200
_CONFIG_FILES = frozenset(
    {"server.json", "gameplay.json", "world.json", "mining.json", "npc.json", "economy.json", "version.json"}
)


@dataclass(frozen=True)
class NativeConfigPreflightResult:
    """Outcome of probing the selected installation's config validator.

    ``valid`` is ``None`` when the selected server has no validator or the
    validator could not be run. That is deliberately different from a config
    which the validator rejected.
    """

    supported: bool
    valid: bool | None
    message: str


class NativeConfigPreflightError(RuntimeError):
    """A supported preflight failed or could not be completed."""

    def __init__(
        self,
        message: str,
        *,
        valid: bool | None,
        diagnostics: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.result = NativeConfigPreflightResult(
            supported=True,
            valid=valid,
            message=message,
        )
        self.diagnostics = diagnostics

    @property
    def supported(self) -> bool:
        return self.result.supported

    @property
    def valid(self) -> bool | None:
        return self.result.valid

    @property
    def message(self) -> str:
        return self.result.message


_NODE_PREFLIGHT_SCRIPT = r'''
"use strict";

const path = require("path");
const protocolWrite = process.stdout.write.bind(process.stdout);
// Config/schema imports should be silent. Reserve stdout for this bounded
// protocol response and discard any incidental console output from the module.
process.stdout.write = () => true;
for (const name of ["log", "info", "warn", "error", "debug", "trace"]) {
  console[name] = () => {};
}

// With ``node - <root>``, argv[1] is the stdin marker and argv[2] is the
// selected install path.
const rootDir = path.resolve(process.argv[2]);
const configModuleDir = path.join(rootDir, "server", "src", "config");

function safeDiagnostic(error) {
  const message = String(error);
  const domainFiles = new Set([
    "server.json", "gameplay.json", "world.json", "mining.json",
    "npc.json", "economy.json", "version.json",
  ]);
  const unknown = message.match(/^([a-z]+\.json) contains unknown setting "([A-Za-z0-9_.-]{1,180})"\.$/u);
  if (unknown && domainFiles.has(unknown[1])) {
    return { file: unknown[1], key: unknown[2], issue: "unknown_setting" };
  }

  const invalidValue = message.match(/^([a-z]+\.json) ([A-Za-z0-9_.-]{1,180}):/u);
  if (invalidValue && domainFiles.has(invalidValue[1])) {
    return { file: invalidValue[1], key: invalidValue[2], issue: "invalid_value" };
  }

  const invalidJson = message.match(/(?:^|[\\/])config[\\/]([a-z]+\.json)(?::|$)/iu);
  if (invalidJson && domainFiles.has(invalidJson[1])) {
    return { file: invalidJson[1], key: null, issue: "invalid_json" };
  }

  if (/^Config schema \d+ is newer than supported schema \d+\.$/u.test(message)) {
    return { file: "version.json", key: "configSchemaVersion", issue: "schema_version" };
  }
  if (/^version\.json /u.test(message)) {
    return { file: "version.json", key: null, issue: "invalid_version_metadata" };
  }
  return { file: null, key: null, issue: "cross_setting_or_unknown" };
}

function finish(response) {
  const encoded = JSON.stringify(response);
  protocolWrite(`${encoded}\n`);
}

try {
  const manager = require(path.join(configModuleDir, "manager.js"));
  if (manager.EVEJS_VERSION !== "0.12.9") {
    finish({ status: "unsupported" });
    process.exitCode = 0;
  } else {
  const schemaDefinitions = require(path.join(configModuleDir, "schema", "index.js"));
  // Construction calls the manager's read-only loadState path. Never call
  // initializeConfigFiles, saveConfig, or import the server entry point here.
  manager.createConfigManager({ rootDir, schemaDefinitions });
  finish({ status: "valid" });
  }
} catch (error) {
  if (error && error.name === "ConfigValidationError") {
    const rawErrors = Array.isArray(error.errors) ? error.errors : [];
    const diagnostics = rawErrors.slice(0, 20).map(safeDiagnostic);
    finish({ status: "invalid", diagnostics });
  } else {
    finish({ status: "error", reason: "validator_load_failed" });
  }
}
'''


def validate_native_server_config(
    root: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None = None,
) -> NativeConfigPreflightResult:
    """Run the selected EveJS config manager without initializing or writing.

    ``env`` overlays the current process environment for Node discovery and
    launch. EveJS overrides and Node preload/module-path variables are removed
    from the child so inherited settings cannot redirect or alter validation
    of the selected installation's persisted config.

    Older installations without both 0.12.9 validator files return an
    unsupported result and are left to the launcher's existing startup path.
    A rejected config raises :class:`NativeConfigPreflightError` with
    ``valid=False``; execution failures use the same exception with
    ``valid=None``.
    """

    try:
        selected_root = Path(root).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise _preflight_error(
            "Could not resolve the selected EveJS installation for config validation.",
            valid=None,
        ) from error

    config_module_dir = selected_root / "server" / "src" / "config"
    manager_path = config_module_dir / "manager.js"
    schema_path = config_module_dir / "schema" / "index.js"
    package_path = selected_root / "server" / "package.json"
    if (
        not manager_path.is_file()
        or not schema_path.is_file()
        or not _has_verified_0129_package(package_path)
    ):
        return NativeConfigPreflightResult(
            supported=False,
            valid=None,
            message=(
                "The selected EveJS server does not expose the verified 0.12.9 config "
                "validator; config preflight was skipped."
            ),
        )

    child_env = _build_child_environment(env)
    node_path = shutil.which("node", path=_environment_value(child_env, "PATH"))
    if not node_path:
        raise _preflight_error(
            "Node.js is unavailable, so the selected EveJS config validator could not run.",
            valid=None,
        )

    command = [node_path, "-", str(selected_root)]
    try:
        completed = subprocess.run(
            command,
            input=_NODE_PREFLIGHT_SCRIPT,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(selected_root),
            env=child_env,
            timeout=_PREFLIGHT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
            **get_hidden_process_flags(),
        )
    except subprocess.TimeoutExpired as error:
        raise _preflight_error(
            "EveJS config validation timed out after 10 seconds. No config files were changed.",
            valid=None,
        ) from error
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise _preflight_error(
            "The selected EveJS config validator could not be started. Check Node.js and the selected server files.",
            valid=None,
        ) from error

    output = completed.stdout or ""
    if len(output.encode("utf-8", errors="replace")) > _MAX_PROTOCOL_OUTPUT_BYTES:
        raise _preflight_error(
            "The EveJS config validator returned an oversized response; validation was stopped.",
            valid=None,
        )
    if completed.returncode != 0:
        raise _preflight_error(
            "The selected EveJS config validator failed to load. Check Node.js and the selected server's config files.",
            valid=None,
        )

    try:
        response = json.loads(output)
    except (json.JSONDecodeError, TypeError) as error:
        raise _preflight_error(
            "The EveJS config validator returned an invalid response; no config files were changed.",
            valid=None,
        ) from error

    if not isinstance(response, dict):
        raise _preflight_error(
            "The EveJS config validator returned an invalid response; no config files were changed.",
            valid=None,
        )
    status = response.get("status")
    if status == "valid":
        return NativeConfigPreflightResult(
            supported=True,
            valid=True,
            message="The selected EveJS config passed its native config validation.",
        )
    if status == "unsupported":
        return NativeConfigPreflightResult(
            supported=False,
            valid=None,
            message=(
                "The selected EveJS config manager does not match the verified "
                "0.12.9 validator contract; config preflight was skipped."
            ),
        )
    if status == "invalid":
        diagnostics = _validated_diagnostics(response.get("diagnostics"))
        message = _format_invalid_config_message(diagnostics)
        raise _preflight_error(message, valid=False, diagnostics=diagnostics)
    if status == "error" and response.get("reason") == "validator_load_failed":
        raise _preflight_error(
            "The selected EveJS config validator failed to load. Check the selected server's config code and Node.js runtime.",
            valid=None,
        )
    raise _preflight_error(
        "The EveJS config validator returned an invalid response; no config files were changed.",
        valid=None,
    )


def _build_child_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    child_env = os.environ.copy()
    if env is not None:
        child_env.update({str(key): str(value) for key, value in env.items()})
    return {
        key: value
        for key, value in child_env.items()
        if not key.upper().startswith("EVEJS_")
        and key.upper() not in {"NODE_OPTIONS", "NODE_PATH"}
    }


def _has_verified_0129_package(package_path: Path) -> bool:
    try:
        package_data = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(package_data, dict) and package_data.get("version") == "0.12.9"


def _environment_value(env: Mapping[str, str], name: str) -> str | None:
    normalized_name = name.casefold()
    for key, value in env.items():
        if key.casefold() == normalized_name:
            return value
    return None


def _validated_diagnostics(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    diagnostics: list[str] = []
    for item in value[:_MAX_DIAGNOSTICS]:
        if not isinstance(item, dict):
            continue
        file_name = item.get("file")
        key = item.get("key")
        issue = item.get("issue")
        if file_name not in _CONFIG_FILES:
            file_name = None
        if not isinstance(key, str) or not key or len(key) > _MAX_DIAGNOSTIC_LENGTH:
            key = None
        elif any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in key):
            key = None
        if issue not in {
            "unknown_setting",
            "invalid_value",
            "invalid_json",
            "schema_version",
            "invalid_version_metadata",
            "cross_setting_or_unknown",
        }:
            issue = "cross_setting_or_unknown"
        file_part = file_name if isinstance(file_name, str) else ""
        key_part = key if isinstance(key, str) else ""
        diagnostics.append("|".join((str(issue), file_part, key_part)))
    return tuple(diagnostics)


def _format_invalid_config_message(diagnostics: tuple[str, ...]) -> str:
    if not diagnostics:
        return (
            "The selected EveJS config is invalid. Review config/*.json against "
            "the selected EveJS schema; the preflight did not change any files."
        )

    issue, file_name, key = diagnostics[0].split("|", 2)
    file_label = f"config/{file_name}" if file_name else "config/*.json"
    if issue == "unknown_setting" and key == "presence.localChatAuthorityEnabled":
        return (
            f"The selected EveJS config manager rejects {file_label} setting "
            "`presence.localChatAuthorityEnabled`. This setting is no longer "
            "accepted by the 0.12.9 schema; apply the release's migration guidance "
            "to this exact key. No config files were changed."
        )
    if issue == "unknown_setting":
        return (
            f"The selected EveJS config manager rejects unknown setting "
            f"`{key}` in {file_label}. Review this exact key against the selected "
            "EveJS schema and migration guidance. No config files were changed."
        )
    if issue == "invalid_value" and key:
        return (
            f"EveJS config validation rejected setting `{key}` in {file_label}. "
            "Check that setting against the selected EveJS schema. No config "
            "files were changed."
        )
    if issue == "invalid_json":
        return (
            f"EveJS config validation could not parse {file_label}. Check its JSON "
            "format; no config files were changed."
        )
    if issue in {"schema_version", "invalid_version_metadata"}:
        return (
            "EveJS config validation rejected `config/version.json`. Check the "
            "selected server's config schema version; no config files were changed."
        )
    return (
        "The selected EveJS config failed a validation rule. Review the selected "
        "server's config files and migration guidance; no config files were changed."
    )


def _preflight_error(
    message: str,
    *,
    valid: bool | None,
    diagnostics: tuple[str, ...] = (),
) -> NativeConfigPreflightError:
    return NativeConfigPreflightError(
        message,
        valid=valid,
        diagnostics=diagnostics,
    )
