"use strict";

// Retains EveJS's public maintenance lease while the Python launcher takes or
// restores its scoped backup. It acquires exclusive ownership without recovery
// so the backup still contains every unreplayed durable operation.
const path = require("path");
const readline = require("readline");
const {
  acquireMaintenance,
  failureResult,
  shutdownDatabase,
} = require("./game_store_maintenance");

const READY_PREFIX = "EVEJS_LAUNCHER_LEASE_READY=";
const RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT=";

function emit(prefix, payload) {
  process.stdout.write(`${prefix}${JSON.stringify(payload)}\n`);
}

function waitForRelease() {
  return new Promise((resolve, reject) => {
    const input = readline.createInterface({ input: process.stdin });
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      input.close();
      resolve();
    };
    input.on("line", (line) => {
      if (String(line).trim() === "release") finish();
    });
    input.on("close", finish);
    input.on("error", reject);
  });
}

async function main() {
  const root = process.cwd();
  let database = null;
  let workError = null;
  let shutdown = null;
  try {
    database = require(path.join(root, "server", "src", "gameStore"));
    acquireMaintenance(database, { recover: false });
    emit(READY_PREFIX, { ok: true });
    await waitForRelease();
  } catch (error) {
    error.operationStarted = false;
    workError = error;
  }

  if (database !== null) {
    try {
      shutdown = await shutdownDatabase(
        database,
        workError
          ? "launcher-maintenance-guard-failed"
          : "launcher-maintenance-guard-release",
      );
    } catch (shutdownError) {
      if (workError) {
        workError.shutdownError = shutdownError;
      } else {
        workError = shutdownError;
      }
    }
  }

  if (workError) {
    emit(RESULT_PREFIX, failureResult(workError));
    process.exitCode = 1;
    return;
  }

  emit(RESULT_PREFIX, {
    ok: true,
    released: shutdown && shutdown.released === true,
  });
}

main().catch((error) => {
  emit(RESULT_PREFIX, failureResult(error));
  process.exitCode = 1;
});
