"""Node-level regression coverage for offline GameStore ownership and cleanup."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


NODE = shutil.which("node")
HELPERS = Path(__file__).resolve().parents[1] / "src" / "core" / "helpers"
MAINTENANCE_HELPER = HELPERS / "game_store_maintenance.js"
DELETE_HELPER = HELPERS / "delete_character.js"


pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def _run_script(
    tmp_path: Path,
    source: str,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "probe.js"
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    env = os.environ.copy()
    env.update(environment or {})
    return subprocess.run(
        [str(NODE), str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )


def _last_json(stdout: str) -> dict:
    return json.loads(stdout.splitlines()[-1])


def test_v0125_maintenance_acquires_and_publicly_releases_before_success(
    tmp_path: Path,
) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const events = [];
        const database = {{
          acquirePersistenceOwnerLease(options) {{
            events.push(`acquire:${{options.recover}}:${{process.env.EVEJS_GAMESTORE_OWNER_ROLE}}`);
          }},
          async shutdown(reason) {{
            events.push(`shutdown-start:${{reason}}`);
            await Promise.resolve();
            events.push("shutdown-finish");
            return {{ success: true, released: true }};
          }},
          _shutdownPersistenceWorkerForTests() {{ events.push("legacy-worker"); }},
          _closeSqliteForTests() {{ events.push("legacy-close"); }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{ events.push("prepare"); }},
          async () => {{
            events.push("work");
            return 7;
          }},
        ).then((value) => {{
          events.push("success");
          process.stdout.write(JSON.stringify({{ events, value }}));
        }}).catch((error) => {{
          process.stderr.write(error.stack || String(error));
          process.exitCode = 1;
        }});
        """,
        environment={"EVEJS_GAMESTORE_OWNER_ROLE": "reader"},
    )

    assert completed.returncode == 0, completed.stderr
    result = _last_json(completed.stdout)
    assert result == {
        "events": [
            "acquire:false:maintenance",
            "prepare",
            "work",
            "shutdown-start:fixture",
            "shutdown-finish",
            "success",
        ],
        "value": 7,
    }


def test_acquisition_conflict_is_marked_before_the_operation_starts(
    tmp_path: Path,
) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const events = [];
        const database = {{
          acquirePersistenceOwnerLease() {{
            events.push("acquire");
            const error = new Error(
              "persistence owner maintenance conflicts with active scheduler, wallet, world",
            );
            error.code = "PERSISTENCE_OWNER_CONFLICT";
            throw error;
          }},
          async shutdown() {{
            events.push("shutdown");
            return {{ success: true, released: true }};
          }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{}},
          async () => {{ events.push("work"); }},
        ).then(() => {{
          process.exitCode = 2;
        }}).catch((error) => {{
          process.stdout.write(JSON.stringify({{
            events,
            result: lifecycle.failureResult(error),
          }}));
        }});
        """,
    )

    assert completed.returncode == 0, completed.stderr
    assert _last_json(completed.stdout) == {
        "events": ["acquire", "shutdown"],
        "result": {
            "ok": False,
            "error": (
                "persistence owner maintenance conflicts with active "
                "scheduler, wallet, world"
            ),
            "code": "PERSISTENCE_OWNER_CONFLICT",
            "operationStarted": False,
        },
    }


def test_stale_backup_checkpoint_fails_before_mutation_and_still_shuts_down(
    tmp_path: Path,
) -> None:
    sqlite_module = (
        tmp_path / "server" / "node_modules" / "better-sqlite3" / "index.js"
    )
    sqlite_module.parent.mkdir(parents=True)
    sqlite_module.write_text(
        textwrap.dedent(
            """
            "use strict";
            module.exports = class FixtureSqlite {
              prepare() {
                return {
                  all() { return JSON.parse(process.env.FIXTURE_OWNER_ROWS); },
                  get() { return { count: Number(process.env.FIXTURE_OUTBOX || 0) }; },
                };
              }
              close() {}
            };
            """
        ),
        encoding="utf-8",
    )
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const events = [];
        const database = {{
          acquirePersistenceOwnerLease() {{ events.push("acquire"); }},
          async shutdown() {{
            events.push("shutdown");
            return {{ success: true, released: true }};
          }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{
            lifecycle.assertOwnerCheckpoint(
              database,
              {{ maintenance: 4, world: 9 }},
            );
          }},
          async () => {{ events.push("mutation"); }},
        ).then(() => {{
          process.exitCode = 2;
        }}).catch((error) => {{
          process.stdout.write(JSON.stringify({{
            events,
            result: lifecycle.failureResult(error),
          }}));
        }});
        """,
        environment={
            "EVEJS_GAMESTORE_SQLITE_PATH": str(tmp_path / "store.sqlite"),
            "FIXTURE_OWNER_ROWS": json.dumps(
                [
                    {"owner_role": "maintenance", "epoch": 5},
                    {"owner_role": "world", "epoch": 10},
                ]
            ),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert _last_json(completed.stdout) == {
        "events": ["acquire", "shutdown"],
        "result": {
            "ok": False,
            "error": (
                "EveJS persistence ownership changed after the maintenance "
                "backup. No character changes were made; try again with the "
                "game service stopped."
            ),
            "code": "PERSISTENCE_OWNER_CHECKPOINT_STALE",
            "operationStarted": False,
        },
    }


def test_owner_schema_rejects_a_missing_backup_checkpoint(tmp_path: Path) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const database = {{ acquirePersistenceOwnerLease() {{}} }};
        try {{
          lifecycle.assertOwnerCheckpoint(database, null);
          process.exitCode = 2;
        }} catch (error) {{
          process.stdout.write(JSON.stringify(lifecycle.failureResult(error)));
        }}
        """,
    )

    assert completed.returncode == 0, completed.stderr
    assert _last_json(completed.stdout) == {
        "ok": False,
        "error": "A maintenance owner checkpoint is required for this EveJS store.",
        "code": "PERSISTENCE_OWNER_CHECKPOINT_REQUIRED",
        "operationStarted": False,
    }


def test_pending_outbox_fails_before_character_mutation(tmp_path: Path) -> None:
    sqlite_module = (
        tmp_path / "server" / "node_modules" / "better-sqlite3" / "index.js"
    )
    sqlite_module.parent.mkdir(parents=True)
    sqlite_module.write_text(
        textwrap.dedent(
            """
            "use strict";
            module.exports = class FixtureSqlite {
              prepare(sql) {
                return sql.includes("COUNT")
                  ? { get() { return { count: 1 }; } }
                  : { all() { return []; } };
              }
              close() {}
            };
            """
        ),
        encoding="utf-8",
    )
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const database = {{ acquirePersistenceOwnerLease() {{}} }};
        try {{
          lifecycle.assertOwnerCheckpoint(database, {{ maintenance: 1 }});
          process.exitCode = 2;
        }} catch (error) {{
          process.stdout.write(JSON.stringify(lifecycle.failureResult(error)));
        }}
        """,
        environment={
            "EVEJS_GAMESTORE_SQLITE_PATH": str(tmp_path / "store.sqlite"),
        },
    )

    assert completed.returncode == 0, completed.stderr
    result = _last_json(completed.stdout)
    assert result["ok"] is False
    assert result["code"] == "PERSISTENCE_OUTBOX_PENDING"
    assert result["operationStarted"] is False


def test_v0124_legacy_cleanup_completes_before_success(tmp_path: Path) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const events = [];
        const database = {{
          flushAllSync() {{ events.push("flush"); return {{ success: true }}; }},
          async _shutdownPersistenceWorkerForTests() {{
            events.push("worker-start");
            await Promise.resolve();
            events.push("worker-finish");
            return {{ active: false, error: null, errors: [], writeErrors: [] }};
          }},
          _closeSqliteForTests() {{ events.push("close"); }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{ lifecycle.assertOwnerCheckpoint(database, null); }},
          async () => {{
            events.push("work");
            return "done";
          }},
        ).then((value) => {{
          events.push("success");
          process.stdout.write(JSON.stringify({{ events, value }}));
        }}).catch((error) => {{
          process.stderr.write(error.stack || String(error));
          process.exitCode = 1;
        }});
        """,
    )

    assert completed.returncode == 0, completed.stderr
    assert _last_json(completed.stdout) == {
        "events": [
            "work",
            "flush",
            "worker-start",
            "worker-finish",
            "close",
            "success",
        ],
        "value": "done",
    }


def test_work_error_survives_a_second_shutdown_failure(tmp_path: Path) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const database = {{
          acquirePersistenceOwnerLease() {{}},
          async shutdown() {{
            return {{ success: false, errorMsg: "CLEANUP_FAILURE" }};
          }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{}},
          async () => {{ throw new Error("WORK_FAILURE"); }},
        ).then(() => {{
          process.exitCode = 2;
        }}).catch((error) => {{
          process.stdout.write(JSON.stringify({{
            message: error.message,
            shutdownError: error.shutdownError && error.shutdownError.message,
          }}));
        }});
        """,
    )

    assert completed.returncode == 0, completed.stderr
    assert _last_json(completed.stdout) == {
        "message": "WORK_FAILURE",
        "shutdownError": "GameStore maintenance shutdown failed: CLEANUP_FAILURE",
    }


def test_v0125_shutdown_must_release_its_maintenance_lease(tmp_path: Path) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const database = {{
          acquirePersistenceOwnerLease() {{}},
          async shutdown() {{ return {{ success: true, released: false }}; }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{}},
          async () => "done",
        ).then(() => {{
          process.exitCode = 2;
        }}).catch((error) => {{
          process.stdout.write(JSON.stringify(lifecycle.failureResult(error)));
        }});
        """,
    )

    assert completed.returncode == 0, completed.stderr
    result = _last_json(completed.stdout)
    assert result["ok"] is False
    assert "maintenance shutdown failed" in result["error"].lower()
    assert result["operationStarted"] is True


def test_lifecycle_phase_overrides_stale_error_metadata(tmp_path: Path) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const database = {{
          acquirePersistenceOwnerLease() {{}},
          async shutdown() {{ return {{ success: true, released: true }}; }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{}},
          async () => {{
            const error = new Error("PARTIAL_MUTATION");
            error.operationStarted = false;
            throw error;
          }},
        ).catch((error) => {{
          process.stdout.write(JSON.stringify(lifecycle.failureResult(error)));
        }});
        """,
    )

    assert completed.returncode == 0, completed.stderr
    assert _last_json(completed.stdout)["operationStarted"] is True


def test_v0124_close_still_runs_after_worker_shutdown_failure(tmp_path: Path) -> None:
    completed = _run_script(
        tmp_path,
        f"""
        const lifecycle = require({json.dumps(str(MAINTENANCE_HELPER))});
        const events = [];
        const database = {{
          flushAllSync() {{ events.push("flush"); return {{ success: true }}; }},
          async _shutdownPersistenceWorkerForTests() {{
            events.push("worker");
            return {{
              active: true,
              terminated: false,
              error: "WORKER_FAILURE",
              errors: [{{ error: "WORKER_FAILURE" }}],
              writeErrors: [],
            }};
          }},
          _closeSqliteForTests() {{ events.push("close"); }},
        }};
        lifecycle.runMaintenanceOperation(
          database,
          "fixture",
          async () => {{}},
          async () => {{ events.push("work"); }},
        ).then(() => {{
          process.exitCode = 2;
        }}).catch((error) => {{
          process.stdout.write(JSON.stringify({{ events, message: error.message }}));
        }});
        """,
    )

    assert completed.returncode == 0, completed.stderr
    result = _last_json(completed.stdout)
    assert result["events"] == ["work", "flush", "worker", "close"]
    assert "WORKER_FAILURE" in result["message"]


def _write_deletion_fixture(root: Path) -> None:
    game_store = root / "server" / "src" / "gameStore"
    character_service = root / "server" / "src" / "services" / "character"
    sqlite_module = root / "server" / "node_modules" / "better-sqlite3" / "index.js"
    game_store.mkdir(parents=True)
    character_service.mkdir(parents=True)
    sqlite_module.parent.mkdir(parents=True)
    sqlite_module.write_text(
        textwrap.dedent(
            """
            "use strict";
            module.exports = class FixtureSqlite {
              prepare(sql) {
                return sql.includes("COUNT")
                  ? { get() { return { count: 0 }; } }
                  : { all() { return [{ owner_role: "maintenance", epoch: 5 }]; } };
              }
              close() {}
            };
            """
        ),
        encoding="utf-8",
    )
    (game_store / "index.js").write_text(
        textwrap.dedent(
            """
            "use strict";
            const accountPresent = process.env.FIXTURE_ACCOUNT_PRESENT !== "0";
            const accounts = accountPresent
              ? { "fixture-account": { id: 7, isGM: false, banned: false } }
              : {};
            const characters = {
              "140000007": {
                accountId: 7,
                characterName: "Fixture One",
                isDeleted: false,
              },
            };
            function event(name) { process.stdout.write(`EVENT=${name}\n`); }
            global.__fixtureStore = { accounts, characters };
            global.__fixtureEvent = event;
            module.exports = {
              acquirePersistenceOwnerLease(options) {
                event(`acquire:${options.recover}:${process.env.EVEJS_GAMESTORE_OWNER_ROLE}`);
              },
              read(table) {
                event(`read:${table}`);
                return { success: true, data: table === "accounts" ? accounts : characters };
              },
              remove(table, pointer) {
                event(`remove:${table}:${pointer}`);
                delete accounts["fixture-account"];
                return { success: true };
              },
              flushTablesSync(tables) {
                event(`flush:${tables.join(",")}`);
                return { success: true };
              },
              async shutdown(reason) {
                event(`shutdown:${reason}`);
                if (process.env.FIXTURE_SHUTDOWN_FAIL === "1") {
                  return { success: false, errorMsg: "CLEANUP_FAILURE" };
                }
                return { success: true, released: true };
              },
              _shutdownPersistenceWorkerForTests() { event("legacy-worker"); },
              _closeSqliteForTests() { event("legacy-close"); },
            };
            """
        ),
        encoding="utf-8",
    )
    (character_service / "characterState.js").write_text(
        textwrap.dedent(
            """
            "use strict";
            module.exports.updateCharacterRecord = (characterID, update) => {
              global.__fixtureEvent("prepare");
              const key = String(characterID);
              global.__fixtureStore.characters[key] = update(
                global.__fixtureStore.characters[key],
              );
              return { success: true };
            };
            """
        ),
        encoding="utf-8",
    )
    (character_service / "characterDeletionRuntime.js").write_text(
        textwrap.dedent(
            """
            "use strict";
            module.exports.deleteCharacter = (characterID, accountID) => {
              global.__fixtureEvent("delete");
              const record = global.__fixtureStore.characters[String(characterID)];
              Object.assign(record, {
                accountId: null,
                deletedByAccountId: accountID,
                isDeleted: true,
              });
              return { success: true };
            };
            """
        ),
        encoding="utf-8",
    )


def _run_delete_helper(
    root: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(environment or {})
    return subprocess.run(
        [str(NODE), str(DELETE_HELPER)],
        cwd=root,
        env=env,
        input=json.dumps(
            {
                "scope": "account",
                "username": "fixture-account",
                "accountId": 7,
                "characterId": 140000007,
                "characterName": "Fixture One",
                "ownerCheckpoint": {"maintenance": 4},
            }
        ),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )


def _terminal_results(stdout: str) -> list[dict]:
    prefix = "EVEJS_LAUNCHER_RESULT="
    return [
        json.loads(line[len(prefix) :])
        for line in stdout.splitlines()
        if line.startswith(prefix)
    ]


def test_delete_helper_releases_before_its_single_success_result(
    tmp_path: Path,
) -> None:
    _write_deletion_fixture(tmp_path)
    completed = _run_delete_helper(
        tmp_path,
        environment={"EVEJS_GAMESTORE_OWNER_ROLE": "reader"},
    )

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    results = _terminal_results(completed.stdout)
    assert results == [
        {
            "ok": True,
            "scope": "account",
            "username": "fixture-account",
            "accountId": 7,
            "accountDeleted": True,
            "deletedCharacters": [
                {"characterID": 140000007, "characterName": "Fixture One"}
            ],
        }
    ]
    assert lines.index("EVENT=acquire:false:maintenance") < lines.index(
        "EVENT=read:accounts"
    )
    assert "EVENT=acquire:true:maintenance" not in lines
    assert lines.index("EVENT=shutdown:launcher-character-deletion") < next(
        index
        for index, line in enumerate(lines)
        if line.startswith("EVEJS_LAUNCHER_RESULT=")
    )
    assert "EVENT=legacy-worker" not in lines
    assert "EVENT=legacy-close" not in lines


def test_delete_helper_preserves_work_and_shutdown_errors_without_prior_success(
    tmp_path: Path,
) -> None:
    _write_deletion_fixture(tmp_path)
    completed = _run_delete_helper(
        tmp_path,
        environment={
            "FIXTURE_ACCOUNT_PRESENT": "0",
            "FIXTURE_SHUTDOWN_FAIL": "1",
        },
    )

    assert completed.returncode == 1
    results = _terminal_results(completed.stdout)
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["error"] == "The selected account changed before deletion began."
    assert "CLEANUP_FAILURE" in results[0]["shutdownError"]
