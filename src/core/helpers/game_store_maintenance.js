"use strict";

const path = require("path");

// EveJS v0.12.5 makes unlabelled GameStore imports passive readers. Launcher
// helpers are allowed to write only after the UI has proved every service and
// client offline, so declare the explicit offline-writer role before any EveJS
// module can import GameStore and freeze the process role.
process.env.EVEJS_GAMESTORE_OWNER_ROLE = "maintenance";

function normalizeError(value, fallback) {
  return value instanceof Error
    ? value
    : new Error(String(value || fallback));
}

function resultError(result, fallback) {
  const detail = result && (result.errorMsg || result.error);
  return new Error(detail ? `${fallback}: ${detail}` : fallback);
}

function requireSuccess(result, fallback) {
  if (!result || result.success !== true) {
    throw resultError(result, fallback);
  }
  return result;
}

function workerShutdownSucceeded(result) {
  return Boolean(
    result &&
      !result.error &&
      (!Array.isArray(result.errors) || result.errors.length === 0) &&
      (!Array.isArray(result.writeErrors) || result.writeErrors.length === 0) &&
      (result.active !== true || result.terminated === true),
  );
}

async function shutdownLegacyDatabase(database) {
  const failures = [];

  try {
    requireSuccess(
      database.flushAllSync(),
      "GameStore legacy flush failed",
    );
  } catch (error) {
    failures.push(normalizeError(error, "GameStore legacy flush failed"));
  }

  try {
    if (typeof database._shutdownPersistenceWorkerForTests !== "function") {
      throw new Error("GameStore legacy persistence shutdown is unavailable");
    }
    const worker = await database._shutdownPersistenceWorkerForTests();
    if (!workerShutdownSucceeded(worker)) {
      const error = resultError(
        worker,
        "GameStore legacy persistence worker shutdown failed",
      );
      error.workerResult = worker;
      throw error;
    }
  } catch (error) {
    failures.push(
      normalizeError(
        error,
        "GameStore legacy persistence worker shutdown failed",
      ),
    );
  }

  try {
    if (typeof database._closeSqliteForTests !== "function") {
      throw new Error("GameStore legacy SQLite close is unavailable");
    }
    database._closeSqliteForTests();
  } catch (error) {
    failures.push(normalizeError(error, "GameStore legacy SQLite close failed"));
  }

  if (failures.length > 0) {
    const primary = failures[0];
    primary.cleanupErrors = failures.slice(1);
    throw primary;
  }

  return { success: true, legacy: true };
}

async function shutdownDatabase(database, reason) {
  // EveJS v0.12.5+ owns a durable maintenance lease. Only the public shutdown
  // path flushes, drains the worker, and releases that lease for the world
  // server that the launcher may restart immediately afterward.
  if (typeof database.shutdown === "function") {
    const result = await database.shutdown(reason);
    if (
      !result ||
      result.success !== true ||
      (
        typeof database.acquirePersistenceOwnerLease === "function" &&
        result.released !== true
      )
    ) {
      const error = resultError(result, "GameStore maintenance shutdown failed");
      error.code =
        (result && result.code) || "GAMESTORE_SHUTDOWN_FAILED";
      error.shutdownResult = result;
      throw error;
    }
    return result;
  }

  // EveJS v0.12.4 has no public shutdown or owner lease. Retain its legacy
  // hooks, but verify every phase and always attempt the final SQLite close.
  return shutdownLegacyDatabase(database);
}

function acquireMaintenance(database, options = {}) {
  if (typeof database.acquirePersistenceOwnerLease === "function") {
    return database.acquirePersistenceOwnerLease({
      recover: options.recover === true,
    });
  }
  return null;
}

function assertOwnerCheckpoint(database, checkpoint) {
  const ownerLeaseSupported =
    typeof database.acquirePersistenceOwnerLease === "function";
  if (checkpoint === null || checkpoint === undefined) {
    if (!ownerLeaseSupported) return;
    const error = new Error(
      "A maintenance owner checkpoint is required for this EveJS store.",
    );
    error.code = "PERSISTENCE_OWNER_CHECKPOINT_REQUIRED";
    error.operationStarted = false;
    throw error;
  }
  if (!ownerLeaseSupported) {
    const error = new Error(
      "The maintenance owner checkpoint does not match this EveJS store.",
    );
    error.code = "PERSISTENCE_OWNER_CHECKPOINT_INVALID";
    error.operationStarted = false;
    throw error;
  }
  if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint)) {
    const error = new Error("The maintenance owner checkpoint is invalid.");
    error.code = "PERSISTENCE_OWNER_CHECKPOINT_INVALID";
    error.operationStarted = false;
    throw error;
  }
  const expected = Object.fromEntries(
    Object.entries(checkpoint).map(([role, epoch]) => [role, Number(epoch)]),
  );
  if (
    !Number.isInteger(expected.maintenance) ||
    expected.maintenance < 1 ||
    Object.values(expected).some((epoch) => !Number.isInteger(epoch) || epoch < 1)
  ) {
    const error = new Error("The maintenance owner checkpoint is invalid.");
    error.code = "PERSISTENCE_OWNER_CHECKPOINT_INVALID";
    error.operationStarted = false;
    throw error;
  }

  const BetterSqlite3 = require(path.join(
    process.cwd(),
    "server",
    "node_modules",
    "better-sqlite3",
  ));
  const sqlite = new BetterSqlite3(
    process.env.EVEJS_GAMESTORE_SQLITE_PATH,
    { readonly: true, fileMustExist: true },
  );
  let rows;
  try {
    const outbox = sqlite.prepare(
      "SELECT COUNT(*) AS count FROM _persistence_outbox",
    ).get();
    if (!outbox || Number(outbox.count) !== 0) {
      const error = new Error(
        "EveJS character data requires persistence recovery first. " +
          "Start the game service, let it finish loading, stop it cleanly, and try again.",
      );
      error.code = "PERSISTENCE_OUTBOX_PENDING";
      error.operationStarted = false;
      throw error;
    }
    rows = sqlite.prepare(
      "SELECT owner_role, epoch FROM _persistence_owners ORDER BY owner_role",
    ).all();
  } finally {
    sqlite.close();
  }
  const actual = Object.fromEntries(
    rows.map((row) => [String(row.owner_role), Number(row.epoch)]),
  );
  const expectedRoles = Object.keys(expected).sort();
  const actualRoles = Object.keys(actual).sort();
  const unchanged =
    JSON.stringify(expectedRoles) === JSON.stringify(actualRoles) &&
    expectedRoles.every((role) => (
      role === "maintenance"
        ? actual[role] === expected[role] + 1
        : actual[role] === expected[role]
    ));
  if (!unchanged) {
    const error = new Error(
      "EveJS persistence ownership changed after the maintenance backup. " +
        "No character changes were made; try again with the game service stopped.",
    );
    error.code = "PERSISTENCE_OWNER_CHECKPOINT_STALE";
    error.operationStarted = false;
    throw error;
  }
}

async function runMaintenanceOperation(database, reason, prepare, operation) {
  let workError = null;
  let value;
  let operationStarted = false;

  try {
    if (typeof prepare !== "function" || typeof operation !== "function") {
      const error = new Error(
        "GameStore maintenance preparation and operation are required.",
      );
      error.code = "PERSISTENCE_MAINTENANCE_INTERFACE_INVALID";
      error.operationStarted = false;
      throw error;
    }
    // Prove exclusivity without replaying the durable outbox. A scoped backup
    // cannot restore internal journal acknowledgements, so prepare also rejects
    // a non-empty outbox before application mutation begins.
    acquireMaintenance(database, { recover: false });
    await prepare();
    operationStarted = true;
    value = await operation();
  } catch (error) {
    workError = normalizeError(error, "GameStore maintenance operation failed");
  }

  try {
    await shutdownDatabase(database, reason);
  } catch (error) {
    const shutdownError = normalizeError(
      error,
      "GameStore maintenance shutdown failed",
    );
    if (workError) {
      workError.shutdownError = shutdownError;
    } else {
      workError = shutdownError;
    }
  }

  if (workError) {
    // The lifecycle owns this phase flag. Never let a lower-level error's
    // stale metadata suppress rollback after application mutation began.
    workError.operationStarted = operationStarted;
    throw workError;
  }
  return value;
}

function failureResult(error) {
  const normalized = normalizeError(
    error,
    "GameStore maintenance operation failed",
  );
  const result = {
    ok: false,
    error: normalized.message,
  };
  if (normalized.code) {
    result.code = String(normalized.code);
  }
  if (typeof normalized.operationStarted === "boolean") {
    result.operationStarted = normalized.operationStarted;
  }
  if (normalized.shutdownError) {
    result.shutdownError = normalizeError(
      normalized.shutdownError,
      "GameStore maintenance shutdown failed",
    ).message;
  }
  return result;
}

module.exports = {
  acquireMaintenance,
  assertOwnerCheckpoint,
  failureResult,
  requireSuccess,
  runMaintenanceOperation,
  shutdownDatabase,
};
