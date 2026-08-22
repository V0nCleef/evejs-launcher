"""Tests for explicit game-server command construction."""
from __future__ import annotations

import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from src.core import server_launcher
from src.core.server_launcher import (
    build_game_server_command,
    ensure_native_game_dependencies,
    native_market_database_status,
)


def _write_market_config(root: Path, database_path: str) -> Path:
    market_dir = root / "externalservices" / "market-server"
    config_path = market_dir / "config" / "market-server.local.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        f'[storage]\ndatabase_path = "{database_path}"\n',
        encoding="utf-8",
    )
    return market_dir


def _write_valid_market_database(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO manifest (key, value) VALUES (?, ?)",
            ("manifest_json", "{}"),
        )


def test_vanilla_command_has_no_mod_preloads(tmp_path: Path) -> None:
    command = build_game_server_command(tmp_path, "vanilla")

    assert command[0] == "node"
    assert command[-1] == "."
    assert "--require" not in command
    assert not any(argument.casefold().endswith(".bat") for argument in command)


def test_modded_command_includes_only_active_loader_preloads(tmp_path: Path) -> None:
    active_b = tmp_path / "mods" / "b-mod" / "loader.js"
    active_a = tmp_path / "mods" / "a-mod" / "loader.js"
    disabled = tmp_path / "mods" / "disabled-mod" / "loader.js.disabled"
    active_b.parent.mkdir(parents=True)
    active_a.parent.mkdir(parents=True)
    disabled.parent.mkdir(parents=True)
    active_b.write_text("", encoding="utf-8")
    active_a.write_text("", encoding="utf-8")
    disabled.write_text("", encoding="utf-8")

    command = build_game_server_command(tmp_path, "modded")

    assert command[-1] == "."
    assert command.count("--require") == 2
    assert command.index(str(active_a)) < command.index(str(active_b))
    assert str(disabled) not in command
    assert not any(argument.casefold().endswith(".bat") for argument in command)


def test_unknown_server_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported server mode"):
        build_game_server_command(tmp_path, "automatic")


def test_native_market_database_status_resolves_relative_to_market_dir(
    tmp_path: Path,
) -> None:
    market_dir = _write_market_config(tmp_path, "data/custom/market.sqlite")
    database = market_dir / "data" / "custom" / "market.sqlite"
    _write_valid_market_database(database)

    assert native_market_database_status(tmp_path) == (True, "")


def test_native_market_database_status_respects_absolute_path(tmp_path: Path) -> None:
    database = tmp_path / "shared market" / "market.sqlite"
    _write_valid_market_database(database)
    _write_market_config(tmp_path, database.as_posix())

    assert native_market_database_status(tmp_path) == (True, "")


@pytest.mark.parametrize("config_text", ("", "[storage]\n"))
def test_native_market_database_status_uses_daemon_default_database_path(
    tmp_path: Path,
    config_text: str,
) -> None:
    market_dir = tmp_path / "externalservices" / "market-server"
    config_path = market_dir / "config" / "market-server.local.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(config_text, encoding="utf-8")
    _write_valid_market_database(
        market_dir / "data" / "generated" / "market.sqlite"
    )

    assert native_market_database_status(tmp_path) == (True, "")


def test_native_market_database_status_preserves_configured_path_whitespace(
    tmp_path: Path,
) -> None:
    configured_path = " data/custom/market.sqlite"
    market_dir = _write_market_config(tmp_path, configured_path)
    _write_valid_market_database(market_dir / configured_path)

    assert native_market_database_status(tmp_path) == (True, "")


def test_native_market_database_status_rejects_missing_config(tmp_path: Path) -> None:
    available, reason = native_market_database_status(tmp_path)

    assert available is False
    assert "config" in reason.casefold()
    assert "missing" in reason.casefold()


def test_native_market_database_status_rejects_malformed_config(
    tmp_path: Path,
) -> None:
    market_dir = _write_market_config(tmp_path, "unused.sqlite")
    config_path = market_dir / "config" / "market-server.local.toml"
    config_path.write_text("[storage\n", encoding="utf-8")

    available, reason = native_market_database_status(tmp_path)

    assert available is False
    assert "config" in reason.casefold()
    assert "invalid" in reason.casefold()


def test_native_market_database_status_rejects_missing_database(
    tmp_path: Path,
) -> None:
    _write_market_config(tmp_path, "data/generated/market.sqlite")

    available, reason = native_market_database_status(tmp_path)

    assert available is False
    assert "database" in reason.casefold()
    assert "missing" in reason.casefold()


@pytest.mark.parametrize("contents", (b"", b"not a sqlite database"))
def test_native_market_database_status_rejects_empty_or_corrupt_database(
    tmp_path: Path,
    contents: bytes,
) -> None:
    market_dir = _write_market_config(tmp_path, "data/generated/market.sqlite")
    database = market_dir / "data" / "generated" / "market.sqlite"
    database.parent.mkdir(parents=True)
    database.write_bytes(contents)

    available, reason = native_market_database_status(tmp_path)

    assert available is False
    assert "readable seeded sqlite database" in reason.casefold()
    assert "Tools > Market Seed Builder" in reason


def test_native_market_database_status_rejects_database_without_manifest_row(
    tmp_path: Path,
) -> None:
    market_dir = _write_market_config(tmp_path, "data/generated/market.sqlite")
    database = market_dir / "data" / "generated" / "market.sqlite"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO manifest (key, value) VALUES (?, ?)",
            ("some_other_key", "{}"),
        )

    available, reason = native_market_database_status(tmp_path)

    assert available is False
    assert "incomplete" in reason.casefold()
    assert "manifest_json" in reason


def _write_native_game_manifest(
    server_dir: Path,
    *,
    allow_scripts: dict[str, bool] | None = None,
) -> Path:
    server_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "dependencies": {
            "better-sqlite3": "^12.11.1",
            "protobufjs": "^8.0.1",
        }
    }
    if allow_scripts is not None:
        manifest["allowScripts"] = allow_scripts
    package_json = server_dir / "package.json"
    package_json.write_text(json.dumps(manifest), encoding="utf-8")
    return package_json


def _write_installed_better_sqlite3(server_dir: Path) -> Path:
    package_json = server_dir / "node_modules" / "better-sqlite3" / "package.json"
    package_json.parent.mkdir(parents=True, exist_ok=True)
    package_json.write_text(
        json.dumps({"name": "better-sqlite3", "version": "12.11.1"}),
        encoding="utf-8",
    )
    return package_json


class _BootstrapProcess:
    def __init__(
        self,
        return_code: int,
        pid: int = 4321,
        output: str = "",
    ) -> None:
        self.return_code = return_code
        self.returncode = return_code
        self.pid = pid
        self.output = output
        self.wait_timeouts: list[float] = []

    def wait(self, *, timeout: float) -> int:
        self.wait_timeouts.append(timeout)
        return self.return_code

    def communicate(self, *, timeout: float) -> tuple[str, None]:
        self.wait_timeouts.append(timeout)
        return self.output, None


def test_native_game_dependency_probe_skips_repair_when_sqlite_is_usable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    _write_native_game_manifest(server_dir)
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> _BootstrapProcess:
        commands.append(command)
        return _BootstrapProcess(0)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})

    ensure_native_game_dependencies(server_dir)

    assert len(commands) == 1
    assert commands[0][:2] == ["node", "-e"]
    assert "new Database(':memory:')" in commands[0][2]
    assert "SELECT 1 AS value" in commands[0][2]
    assert "database.close()" in commands[0][2]
    assert "npm" not in " ".join(commands[0]).casefold()


def test_installed_broken_native_dependency_skips_ci_and_uses_only_pinned_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    package_json = _write_native_game_manifest(server_dir)
    _write_installed_better_sqlite3(server_dir)
    untracked_mod_dependency = server_dir / "node_modules" / "custom-mod" / "keep.txt"
    untracked_mod_dependency.parent.mkdir(parents=True)
    untracked_mod_dependency.write_text("preserve me", encoding="utf-8")
    commands: list[list[str]] = []
    return_codes = iter((1, 0, 0, 0, 0))

    def fake_popen(command: list[str], **_kwargs: object) -> _BootstrapProcess:
        commands.append(command)
        if command[1:3] == ["install-scripts", "approve"]:
            manifest = json.loads(package_json.read_text(encoding="utf-8"))
            manifest["allowScripts"] = {"better-sqlite3@12.11.1": True}
            package_json.write_text(json.dumps(manifest), encoding="utf-8")
        output = "12.0.1\n" if command[1:] == ["--version"] else ""
        return _BootstrapProcess(next(return_codes), output=output)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(server_launcher, "_npm_executable", lambda: "npm")

    ensure_native_game_dependencies(server_dir)

    assert commands[0][:2] == ["node", "-e"]
    assert commands[1] == ["npm", "--version"]
    assert commands[2] == [
        "npm",
        "install-scripts",
        "approve",
        "better-sqlite3@12.11.1",
        "--allow-scripts-pin=true",
    ]
    assert commands[3] == [
        "npm",
        "rebuild",
        "better-sqlite3",
        "--ignore-scripts=false",
        "--dangerously-allow-all-scripts=false",
        "--foreground-scripts",
    ]
    assert commands[4][:2] == ["node", "-e"]
    assert not any(command[1:2] == ["ci"] for command in commands)
    assert untracked_mod_dependency.read_text(encoding="utf-8") == "preserve me"
    assert all("protobufjs" not in command for command in commands[1:])
    assert all("--all" not in command for command in commands)
    log_text = (tmp_path / "game.log").read_text(encoding="utf-8")
    assert "starting the safe npm dependency repair" in log_text
    assert "Native Game dependencies repaired and verified" in log_text


def test_missing_native_dependency_uses_scriptless_ci_before_pinned_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    package_json = _write_native_game_manifest(server_dir)
    commands: list[list[str]] = []
    return_codes = iter((1, 0, 0, 0, 0, 0))

    def fake_popen(command: list[str], **_kwargs: object) -> _BootstrapProcess:
        commands.append(command)
        if command[1:2] == ["ci"]:
            _write_installed_better_sqlite3(server_dir)
        if command[1:3] == ["install-scripts", "approve"]:
            manifest = json.loads(package_json.read_text(encoding="utf-8"))
            manifest["allowScripts"] = {"better-sqlite3@12.11.1": True}
            package_json.write_text(json.dumps(manifest), encoding="utf-8")
        output = "12.0.1\n" if command[1:] == ["--version"] else ""
        return _BootstrapProcess(next(return_codes), output=output)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(server_launcher, "_npm_executable", lambda: "npm")

    ensure_native_game_dependencies(server_dir)

    assert commands[2] == [
        "npm",
        "ci",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ]
    assert commands[3][1:3] == ["install-scripts", "approve"]
    assert commands[4][1:3] == ["rebuild", "better-sqlite3"]


@pytest.mark.parametrize(
    "denial",
    (
        {"better-sqlite3": False},
        {"better-sqlite3@12.11.1": False},
    ),
)
def test_native_game_dependency_repair_preserves_existing_denial(
    denial: dict[str, bool],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    package_json = _write_native_game_manifest(
        server_dir,
        allow_scripts=denial,
    )
    original_manifest = package_json.read_text(encoding="utf-8")
    commands: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> _BootstrapProcess:
        commands.append(command)
        return _BootstrapProcess(1)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})

    with pytest.raises(RuntimeError, match="preserved that denial"):
        ensure_native_game_dependencies(server_dir)

    assert len(commands) == 1
    assert commands[0][:2] == ["node", "-e"]
    assert package_json.read_text(encoding="utf-8") == original_manifest


def test_native_game_dependency_repair_surfaces_npm_ci_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    _write_native_game_manifest(server_dir)
    commands: list[list[str]] = []
    return_codes = iter((1, 0, 17))

    def fake_popen(command: list[str], **_kwargs: object) -> _BootstrapProcess:
        commands.append(command)
        output = "12.0.1\n" if command[1:] == ["--version"] else ""
        return _BootstrapProcess(next(return_codes), output=output)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(server_launcher, "_npm_executable", lambda: "npm")

    with pytest.raises(RuntimeError, match=r"npm ci \(exit code 17\)") as error:
        ensure_native_game_dependencies(server_dir)

    assert "Game Console" in str(error.value)
    assert len(commands) == 3
    assert commands[1] == ["npm", "--version"]
    assert commands[2][1:3] == ["ci", "--ignore-scripts"]


def test_native_game_dependency_repair_supports_pre_npm12_without_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    _write_native_game_manifest(server_dir)
    commands: list[list[str]] = []
    return_codes = iter((1, 0, 0, 0, 0))

    def fake_popen(command: list[str], **_kwargs: object) -> _BootstrapProcess:
        commands.append(command)
        if command[1:2] == ["ci"]:
            _write_installed_better_sqlite3(server_dir)
        output = "11.8.0\n" if command[1:] == ["--version"] else ""
        return _BootstrapProcess(next(return_codes), output=output)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(server_launcher, "_npm_executable", lambda: "npm")

    ensure_native_game_dependencies(server_dir)

    assert len(commands) == 5
    assert commands[1:4] == [
        ["npm", "--version"],
        ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        [
            "npm",
            "rebuild",
            "better-sqlite3",
            "--ignore-scripts=false",
            "--foreground-scripts",
        ],
    ]
    assert commands[0][:2] == ["node", "-e"]
    assert commands[4][:2] == ["node", "-e"]
    assert not any("install-scripts" in command for command in commands)


def test_native_game_dependency_timeout_terminates_exact_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimedOutProcess(_BootstrapProcess):
        def wait(self, *, timeout: float) -> int:
            raise subprocess.TimeoutExpired("node", timeout)

    terminated: list[int] = []
    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(
        server_launcher.subprocess,
        "Popen",
        lambda *_args, **_kwargs: TimedOutProcess(1, pid=9876),
    )
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(
        server_launcher,
        "terminate_process_tree",
        lambda pid: terminated.append(pid) is None,
    )

    with pytest.raises(RuntimeError, match="process tree terminated"):
        server_launcher._run_game_dependency_command(
            ["node", "-e", "process.exit(0)"],
            tmp_path,
            purpose="test the timeout guard",
        )

    assert terminated == [9876]


def _write_legacy_migration_entrypoint(server_dir: Path) -> Path:
    migration = (
        server_dir / "src" / "gameStore" / "migrateLegacyNewDatabase.js"
    )
    migration.parent.mkdir(parents=True, exist_ok=True)
    migration.write_text("// test migration entrypoint\n", encoding="utf-8")
    return migration


def test_native_store_runs_official_legacy_migration_before_selecting_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    migration = _write_legacy_migration_entrypoint(server_dir)
    legacy_store = tmp_path / "_local" / "newDatabase"
    (legacy_store / "data").mkdir(parents=True)
    environment = {
        "EVEJS_GAMESTORE_DATA_DIR": "C:/another-install/data",
        "PRESERVE_ME": "yes",
    }
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(
        command: list[str],
        _server_dir: Path,
        **kwargs: object,
    ) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        legacy_store.rename(tmp_path / "_local" / "gameStore")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher, "_run_game_dependency_command", fake_run)

    server_launcher._prepare_native_game_store_environment(
        tmp_path,
        server_dir,
        environment,
    )

    canonical_data = tmp_path / "_local" / "gameStore" / "data"
    assert not legacy_store.exists()
    assert canonical_data.is_dir()
    assert environment["EVEJS_GAMESTORE_DATA_DIR"] == str(canonical_data.resolve())
    assert environment["PRESERVE_ME"] == "yes"
    assert calls == [
        (
            ["node", str(migration.resolve())],
            {
                "purpose": "migrate legacy EveJS _local/newDatabase data",
                "timeout_sec": 120,
                "env": {"PRESERVE_ME": "yes"},
            },
        )
    ]


def test_native_store_refuses_ambiguous_legacy_and_canonical_saves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    _write_legacy_migration_entrypoint(server_dir)
    legacy_store = tmp_path / "_local" / "newDatabase"
    canonical_store = tmp_path / "_local" / "gameStore"
    legacy_store.mkdir(parents=True)
    canonical_store.mkdir()
    environment: dict[str, str] = {}
    monkeypatch.setattr(
        server_launcher,
        "_run_game_dependency_command",
        lambda *_args, **_kwargs: pytest.fail("ambiguous saves reached migration"),
    )

    with pytest.raises(RuntimeError, match="contains both legacy data"):
        server_launcher._prepare_native_game_store_environment(
            tmp_path,
            server_dir,
            environment,
        )

    assert legacy_store.is_dir()
    assert canonical_store.is_dir()


def test_native_store_leaves_older_layout_alone_without_migration_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (tmp_path / "_local" / "newDatabase").mkdir(parents=True)
    (tmp_path / "_local" / "gameStore").mkdir()
    environment = {"EVEJS_GAMESTORE_DATA_DIR": "C:/legacy/owned/path"}
    monkeypatch.setattr(
        server_launcher,
        "_run_game_dependency_command",
        lambda *_args, **_kwargs: pytest.fail("older layout reached migration"),
    )

    server_launcher._prepare_native_game_store_environment(
        tmp_path,
        server_dir,
        environment,
    )

    assert "EVEJS_GAMESTORE_DATA_DIR" not in environment


def test_native_store_migrates_legacy_sqlite_name_inside_canonical_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    _write_legacy_migration_entrypoint(server_dir)
    canonical_store = tmp_path / "_local" / "gameStore"
    canonical_data = canonical_store / "data"
    canonical_data.mkdir(parents=True)
    legacy_sqlite = canonical_store / "newdatabase.sqlite"
    legacy_sqlite.write_bytes(b"legacy")
    environment: dict[str, str] = {}

    def fake_run(
        command: list[str],
        _server_dir: Path,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess:
        legacy_sqlite.rename(canonical_store / "gamestore.sqlite")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "game.log")
    monkeypatch.setattr(server_launcher, "_run_game_dependency_command", fake_run)

    server_launcher._prepare_native_game_store_environment(
        tmp_path,
        server_dir,
        environment,
    )

    assert not legacy_sqlite.exists()
    assert (canonical_store / "gamestore.sqlite").is_file()
    assert environment["EVEJS_GAMESTORE_DATA_DIR"] == str(canonical_data.resolve())


def test_native_store_does_not_create_a_missing_game_store(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "server"
    _write_legacy_migration_entrypoint(server_dir)
    environment = {"EVEJS_GAMESTORE_DATA_DIR": "C:/another-install/data"}

    server_launcher._prepare_native_game_store_environment(
        tmp_path,
        server_dir,
        environment,
    )

    assert not (tmp_path / "_local" / "gameStore").exists()
    assert "EVEJS_GAMESTORE_DATA_DIR" not in environment


def test_start_game_server_requires_an_explicit_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "index.js").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        server_launcher,
        "build_game_server_command",
        lambda *_args, **_kwargs: pytest.fail("implicit mode reached command building"),
    )

    with pytest.raises(TypeError):
        server_launcher.start_game_server(str(tmp_path))


@pytest.mark.parametrize("mode", ["vanilla", "modded"])
def test_start_game_server_honors_explicit_mode(
    mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "index.js").write_text("", encoding="utf-8")
    local_game_data = tmp_path / "_local" / "gameStore" / "data"
    local_game_data.mkdir(parents=True)
    observed: dict[str, object] = {}

    def fake_build(root: str | Path, selected_mode: str) -> list[str]:
        observed["root"] = Path(root)
        observed["mode"] = selected_mode
        return ["node", f"mode={selected_mode}", "."]

    class FakeProcess:
        pid = 1234
        stdout = object()

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        observed["command"] = command
        observed["popen_kwargs"] = kwargs
        return FakeProcess()

    class FakeThread:
        def __init__(self, **kwargs: object) -> None:
            observed["thread_kwargs"] = kwargs

        def start(self) -> None:
            observed["thread_started"] = True

    monkeypatch.setattr(server_launcher, "build_game_server_command", fake_build)
    monkeypatch.setattr(
        server_launcher,
        "ensure_native_game_dependencies",
        lambda _server_dir: server_launcher._append_game_console(
            "Dependency bootstrap preserved before Game output."
        ),
    )
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(server_launcher, "SERVER_CONSOLE_LOG", tmp_path / "server.log")
    monkeypatch.setattr(
        server_launcher,
        "get_graceful_server_process_flags",
        lambda: {},
    )
    monkeypatch.setenv("EVEJS_GAMESTORE_DATA_DIR", "C:/wrong/root/data")

    process = server_launcher.start_game_server(str(tmp_path), mode=mode)

    assert process.pid == 1234
    assert observed["root"] == tmp_path
    assert observed["mode"] == mode
    assert observed["command"] == ["node", f"mode={mode}", "."]
    assert observed["popen_kwargs"]["cwd"] == str(server_dir)
    assert observed["popen_kwargs"]["stdin"] is subprocess.DEVNULL
    assert observed["popen_kwargs"]["env"]["EVEJS_GAMESTORE_DATA_DIR"] == str(
        local_game_data.resolve()
    )
    assert (server_dir / "logs" / "node-reports").is_dir()
    assert observed["thread_kwargs"]["args"] == (
        process.stdout,
        tmp_path / "server.log",
        "a",
    )
    assert "Dependency bootstrap preserved" in (tmp_path / "server.log").read_text(
        encoding="utf-8"
    )
    assert observed["thread_started"] is True


def test_start_market_server_appends_attempt_pid_and_child_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market_dir = tmp_path / "externalservices" / "market-server"
    binary = market_dir / "target" / "release" / "market-server.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture")
    (market_dir / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    console_log = tmp_path / "market console.log"
    console_log.write_text("previous attempt\n", encoding="utf-8")
    observed: dict[str, object] = {}

    class FakeProcess:
        pid = 4321
        stdout = io.BytesIO(b"child output\n")

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        observed["command"] = command
        observed["popen_kwargs"] = kwargs
        return FakeProcess()

    class ImmediateThread:
        def __init__(self, *, target, args, daemon: bool) -> None:
            observed["thread_args"] = args
            self._target = target
            self._args = args
            assert daemon is True

        def start(self) -> None:
            self._target(*self._args)

    monkeypatch.setattr(server_launcher, "MARKET_CONSOLE_LOG", console_log)
    monkeypatch.setattr(server_launcher, "get_market_binary_name", lambda: binary.name)
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(server_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server_launcher.threading, "Thread", ImmediateThread)

    process = server_launcher.start_market_server(str(tmp_path))

    assert process.pid == 4321
    assert observed["command"] == [
        str(binary),
        "--config",
        "config/market-server.local.toml",
        "serve",
    ]
    assert observed["popen_kwargs"]["cwd"] == str(market_dir)
    assert observed["popen_kwargs"]["stdin"] is subprocess.DEVNULL
    assert observed["thread_args"] == (process.stdout, console_log, "a")
    output = console_log.read_text(encoding="utf-8")
    assert "previous attempt" in output
    assert "Market start attempt:" in output
    assert str(binary) in output
    assert "Market process started with PID 4321" in output
    assert "child output" in output


def test_start_market_server_preserves_prior_log_and_records_spawn_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    market_dir = tmp_path / "externalservices" / "market-server"
    market_dir.mkdir(parents=True)
    (market_dir / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    console_log = tmp_path / "market.log"
    console_log.write_text("known-good startup\n", encoding="utf-8")
    monkeypatch.setattr(server_launcher, "MARKET_CONSOLE_LOG", console_log)
    monkeypatch.setattr(server_launcher, "get_market_binary_name", lambda: "missing.exe")
    monkeypatch.setattr(server_launcher, "get_hidden_process_flags", lambda: {})
    monkeypatch.setattr(
        server_launcher.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FileNotFoundError("cargo is missing")
        ),
    )

    with pytest.raises(FileNotFoundError, match="cargo is missing"):
        server_launcher.start_market_server(str(tmp_path))

    output = console_log.read_text(encoding="utf-8")
    assert "known-good startup" in output
    assert "Market start attempt: cargo run --release" in output
    assert "Market process creation failed: FileNotFoundError" in output
    assert "cargo is missing" in output
