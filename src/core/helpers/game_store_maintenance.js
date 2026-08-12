"use strict";

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
    if (!result || result.success !== true) {
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

function acquireMaintenance(database) {
  if (typeof database.acquirePersistenceOwnerLease === "function") {
    return database.acquirePersistenceOwnerLease({ recover: true });
  }
  return null;
}

async function runMaintenanceOperation(database, reason, operation) {
  let workError = null;
  let value;

  try {
    acquireMaintenance(database);
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
  if (normalized.shutdownError) {
    result.shutdownError = normalizeError(
      normalized.shutdownError,
      "GameStore maintenance shutdown failed",
    ).message;
  }
  return result;
}

module.exports = {
  failureResult,
  requireSuccess,
  runMaintenanceOperation,
};
