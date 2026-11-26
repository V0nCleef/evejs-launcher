"use strict";

// An offline maintenance worker for the reviewed EveJS GameStore.
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { isDeepStrictEqual } = require("node:util");
const { shutdownDatabase } = require("./game_store_maintenance");
const sharedStore = require("./shared_game_store");
const { exitWithTerminalResult } = require("./terminal_result");

const reviewed = Object.freeze({
  "config/version.json": "ac8011ee1fae8723a18c0d1a5cf7b12c965a64616aa1dfdc63cd494399f82f6c",
  "server/src/gameStore/index.js": "a68770eb6f233087f7ec0a45ae2a90a2e354bffbd4ccd62f3fa828eb478e8e93",
  "server/src/gameStore/sqliteStore.js": "d08d91703797e6ff25d4da88bc57a6e07bdfc63a42cdb328cbe265f13f0475e8",
  "server/src/gameStore/storeRoot.js": "841a065f65f858e8d1cfc530ce79b66ab1b735baa418c429b2cfd96aa14e9a47",
  "server/src/gameStore/persistenceWorker.js": "82ece8ae5f08d5039fd2bc0168204443c91ac88cf17d26ce97d12cbfb0139e40",
  "server/src/gameStore/persistenceWorker.worker.js": "b0dac64b99fd0d7978d86e048a1b3886f3b336f6f12d787a930448b76f0d6b2c",
  "server/src/gameStore/persistenceRoles.js": "906e930bb399c8cfb88f44851d738f715f67d6859825be4692ae2a0704da2d36",
  "server/src/gameStore/persistenceLeasePolicy.js": "4c9c8731c4ede6e19188c6c19fb405937d283716423b2351796edebe2c0a6f4d",
  "server/src/gameStore/tableOwnership.js": "575f71e1851fdbd420e0c2f54f892f4ecaaa2c85e7622c1b38149c8298ea46c9",
  "server/src/services/character/characterNameRuntime.js": "3751ca932f8f391e5758b17c8c47c793547dd4f12ae1ad476c8d8622cad3c715",
});

const backupFolder = ".launcher-character-renames";
const maxRowBytes = 4 * 1024 * 1024;

function problem(code) {
  return Object.assign(new Error(code), { renameCode: code });
}

function hash(text) {
  return crypto.createHash("sha256").update(text).digest("hex");
}

function readRequest() {
  const buffer = Buffer.alloc(4097);
  let size = 0;
  while (size < buffer.length) {
    const count = fs.readSync(0, buffer, size, buffer.length - size);
    if (count === 0) break;
    size += count;
  }
  if (size === buffer.length) throw problem("INVALID_REQUEST");
  const request = JSON.parse(buffer.subarray(0, size).toString("utf8"));
  if (
    !request || Object.keys(request).length !== 6 ||
    !["username", "oldName", "name"].every((key) => (
      typeof request[key] === "string" && request[key].length > 0 &&
      request[key].length <= 256 && !/[\u0000-\u001f\u007f]/.test(request[key])
    )) ||
    !["accountId", "characterId"].every((key) => Number.isSafeInteger(request[key]) && request[key] > 0) ||
    typeof request.operationId !== "string" || !/^[0-9a-f]{32}$/.test(request.operationId)
  ) {
    throw problem("INVALID_REQUEST");
  }
  return request;
}

function ordinaryPath(file, kind) {
  const info = fs.lstatSync(file);
  if (kind === "file" ? !info.isFile() || info.nlink !== 1 : !info.isDirectory()) {
    throw problem("UNSUPPORTED_LAYOUT");
  }
  const resolved = fs.realpathSync.native(file);
  const expected = path.resolve(file);
  const matches = process.platform === "win32"
    ? resolved.toLowerCase() === expected.toLowerCase()
    : resolved === expected;
  if (!matches) throw problem("UNSUPPORTED_LAYOUT");
  return info;
}

function verifyRelease(root) {
  for (const [relative, expected] of Object.entries(reviewed)) {
    const file = path.join(root, relative);
    const info = ordinaryPath(file, "file");
    if (info.size > 4 * 1024 * 1024 ||
        hash(fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n")) !== expected) {
      throw problem("UNSUPPORTED_RELEASE");
    }
  }
  // npm's supported native-dependency repair writes script approvals to the
  // server manifest. Check its runtime contract and the exact installed SQLite
  // release without assuming the manifest retains its original formatting.
  for (const [relative, check] of [
    ["server/package.json", (value) => (
      value.name === "eve.js" && value.type === "commonjs" &&
      value.dependencies?.["better-sqlite3"] === "^12.11.1"
    )],
    ["server/node_modules/better-sqlite3/package.json", (value) => (
      value.name === "better-sqlite3" && value.version === "12.11.1"
    )],
  ]) {
    const file = path.join(root, relative);
    if (ordinaryPath(file, "file").size > 64 * 1024 ||
        !check(JSON.parse(fs.readFileSync(file, "utf8")))) {
      throw problem("UNSUPPORTED_RELEASE");
    }
  }
}

function verifyLayout(store, databaseFile) {
  ordinaryPath(store, "directory");
  ordinaryPath(path.join(store, "data"), "directory");
  ordinaryPath(databaseFile, "file");
  for (const suffix of ["-wal", "-shm", "-journal"]) {
    try {
      ordinaryPath(databaseFile + suffix, "file");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
}

function nameError(validation, code) {
  if (validation === code.RESERVED) return "RESERVED_NAME";
  if (validation === code.UNAVAILABLE) return "NAME_UNAVAILABLE";
  return "INVALID_NAME";
}

function recordValue(raw, requireCanonical = true) {
  if (typeof raw !== "string" || Buffer.byteLength(raw) > maxRowBytes) {
    throw problem("UNSUPPORTED_LAYOUT");
  }
  const value = JSON.parse(raw);
  // The stock store serializes canonical JSON. A noncanonical record could
  // contain duplicated keys or numbers that Node would rewrite when parsed.
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      (requireCanonical && JSON.stringify(value) !== raw)) {
    throw problem("UNSUPPORTED_LAYOUT");
  }
  return value;
}

function saveBackup(store, request, raw, after) {
  const folder = path.join(store, backupFolder);
  try {
    fs.mkdirSync(folder, { mode: 0o700 });
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
  }
  ordinaryPath(folder, "directory");
  const file = path.join(folder, `${request.operationId}.json`);
  const fd = fs.openSync(file, "wx", 0o600);
  try {
    fs.writeFileSync(fd, JSON.stringify({
      schemaVersion: 1,
      operationId: request.operationId,
      characterId: request.characterId,
      accountId: request.accountId,
      originalRow: raw,
      expectedRowSha256: hash(after),
    }) + "\n");
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  if (process.platform !== "win32") {
    const directory = fs.openSync(folder, "r");
    try {
      fs.fsyncSync(directory);
    } finally {
      fs.closeSync(directory);
    }
  }
}

function readDisk(Sqlite, file, inspect) {
  const connection = new Sqlite(file, { readonly: true, fileMustExist: true, timeout: 1500 });
  try {
    return inspect(connection);
  } finally {
    connection.close();
  }
}

function inspectOwners(connection) {
  if (connection.prepare("SELECT 1 FROM _persistence_outbox LIMIT 1").get()) {
    throw problem("PENDING_WRITES");
  }
  return connection.prepare(
    "SELECT owner_role, instance_id, epoch, active, exclusive FROM _persistence_owners ORDER BY owner_role",
  ).all();
}

function assertReviewedStore(connection) {
  const migrated = connection.prepare("SELECT 1 FROM _migrations WHERE table_name = ?");
  for (const table of ["accounts", "characters"]) {
    const columns = connection.prepare(`PRAGMA table_info("${table}")`).all();
    if (columns.length !== 2 || columns[0].name !== "key" || columns[0].type !== "TEXT" ||
        columns[0].pk !== 1 || columns[1].name !== "json" || columns[1].type !== "TEXT" ||
        !migrated.get(table)) {
      throw problem("UNSUPPORTED_LAYOUT");
    }
  }
  if (connection.prepare(
    "SELECT 1 FROM sqlite_schema WHERE type = 'trigger' AND tbl_name IN ('accounts', 'characters')",
  ).get()) {
    throw problem("UNSUPPORTED_LAYOUT");
  }
}

function characterRow(connection, characterId) {
  return connection.prepare("SELECT json FROM characters WHERE key = ?").get(String(characterId))?.json;
}

function renameSharedRow(Sqlite, file, request, before, after, name, proof) {
  const connection = new Sqlite(file, { fileMustExist: true, timeout: 1500 });
  try {
    connection.transaction(() => {
      if (!isDeepStrictEqual(inspectOwners(connection), before.owners)) throw problem("OWNER_CHANGED");
      if (characterRow(connection, request.characterId) !== before.raw) throw problem("TARGET_CHANGED");
      proof.mutationStarted = true;
      const result = connection.prepare(`
        UPDATE character_records SET character_name = ?
        WHERE character_id = ? AND account_id = ? AND character_name = ?
      `).run(name, request.characterId, request.accountId, request.oldName);
      if (result.changes !== 1 || characterRow(connection, request.characterId) !== after) {
        throw problem("VERIFY_FAILED");
      }
    }).immediate();
  } catch (error) {
    proof.rollbackConfirmed = characterRow(connection, request.characterId) === before.raw;
    throw error;
  } finally {
    connection.close();
  }
}

function renameUnderLease(database, Sqlite, file, store, request, source, proof, shared) {
  const nameRuntime = require(path.join(source, "services", "character", "characterNameRuntime"));
  const key = `/${request.characterId}/characterName`;
  const before = readDisk(Sqlite, file, (connection) => {
    const owners = inspectOwners(connection);
    const owner = owners.find((row) => row.owner_role === "maintenance");
    if (!owner || owner.active !== 1 || owner.exclusive !== 1) throw problem("OWNER_CHANGED");
    const account = connection.prepare("SELECT json FROM accounts WHERE key = ?").get(request.username);
    const raw = characterRow(connection, request.characterId);
    if (!account || raw === undefined) throw problem("TARGET_CHANGED");
    const record = recordValue(raw, !shared);
    if (recordValue(account.json, !shared).id !== request.accountId || record.accountId !== request.accountId ||
        record.characterName !== request.oldName || record.isDeleted === true) {
      throw problem("TARGET_CHANGED");
    }
    return { owners, raw, record };
  });
  const name = nameRuntime.normalizeNameString(request.name).trim();
  if (name === before.record.characterName) return { name, raw: before.raw, changed: false };

  const validation = nameRuntime.validateCharacterName(name);
  if (validation !== nameRuntime.VALIDATION_CODE.VALID) {
    throw problem(nameError(validation, nameRuntime.VALIDATION_CODE));
  }
  // The typed view can contain integers and decimal text that JS cannot round-trip.
  // Change just the stock text column and compare the full projected row in SQLite.
  const after = shared
    ? readDisk(Sqlite, file, connection => connection.prepare(
      "SELECT json_set(?, '$.characterName', ?)",
    ).pluck().get(before.raw, name))
    : JSON.stringify({ ...before.record, characterName: name });
  saveBackup(store, request, before.raw, after);
  proof.backupCreated = true;
  if (shared) {
    renameSharedRow(Sqlite, file, request, before, after, name, proof);
    return { name, raw: after, changed: true };
  }

  function currentRow() {
    return readDisk(Sqlite, file, (connection) => {
      if (!isDeepStrictEqual(inspectOwners(connection), before.owners)) throw problem("OWNER_CHANGED");
      return characterRow(connection, request.characterId);
    });
  }
  function persist(value) {
    if (database.write("characters", key, value)?.success !== true ||
        database.flushTablesSync(["characters"])?.success !== true) {
      throw problem("WRITE_FAILED");
    }
  }
  if (currentRow() !== before.raw) throw problem("TARGET_CHANGED");
  try {
    proof.mutationStarted = true;
    // The game is offline. The stock GameStore writer changes only this
    // field, using the same owner fences and row flush as normal gameplay.
    persist(name);
    if (currentRow() !== after) throw problem("VERIFY_FAILED");
  } catch (error) {
    try {
      const current = currentRow();
      if (current === before.raw || current === after) {
        persist(before.record.characterName);
        proof.rollbackConfirmed = currentRow() === before.raw;
      }
    } catch (_) {
      // The owner changed, or the store has unresolved writes. Retain the
      // backup and surface an indeterminate result instead of guessing.
    }
    throw error;
  }
  return { name, raw: after, changed: true };
}

async function main(proof) {
  const request = readRequest();
  proof.operationId = request.operationId;
  const root = process.cwd();
  const store = path.dirname(process.env.EVEJS_GAMESTORE_DATA_DIR || "");
  const file = path.join(store, "gamestore.sqlite");
  const source = path.join(root, "server", "src");
  if (!path.isAbsolute(store) || path.join(store, "data") !== process.env.EVEJS_GAMESTORE_DATA_DIR) {
    throw problem("UNSUPPORTED_LAYOUT");
  }
  let shared;
  try {
    const versionFile = path.join(root, "config/version.json");
    if (ordinaryPath(versionFile, "file").size > 4096) throw problem("UNSUPPORTED_RELEASE");
    if (sharedStore.supportsRelease(JSON.parse(fs.readFileSync(versionFile, "utf8")).evejsVersion)) {
      shared = sharedStore.verifyRelease(root);
    } else {
      verifyRelease(root);
    }
  } catch (_) {
    throw problem("UNSUPPORTED_RELEASE");
  }
  verifyLayout(store, file);
  const Sqlite = shared?.Sqlite || require(path.join(root, "server", "node_modules", "better-sqlite3"));
  readDisk(Sqlite, file, (connection) => {
    if (shared) {
      try {
        sharedStore.assertIdle(connection, sharedStore.assertSchema(connection, shared.legacyNames));
      } catch (error) {
        throw problem(error.sharedStoreCode || "UNSUPPORTED_LAYOUT");
      }
    } else {
      assertReviewedStore(connection);
      // Confirm existing owner and journal schema before the stock writer can initialize.
      inspectOwners(connection);
    }
  });
  const database = require(path.join(source, "gameStore"));
  try {
    try {
      database.acquirePersistenceOwnerLease({ recover: false });
    } catch (_) {
      throw problem("OWNER_BUSY");
    }
    proof.leaseAcquired = true;
    const result = renameUnderLease(database, Sqlite, file, store, request, source, proof, Boolean(shared));
    await shutdownDatabase(database, "launcher-character-rename");
    proof.leaseAcquired = false;
    readDisk(Sqlite, file, (connection) => {
      const owners = inspectOwners(connection);
      if (owners.some((row) => row.owner_role === "maintenance" && row.active === 1) ||
          characterRow(connection, request.characterId) !== result.raw) {
        throw problem("VERIFY_FAILED");
      }
    });
    return { ok: true, operationId: request.operationId, characterId: request.characterId,
      name: result.name, changed: result.changed, backupCreated: proof.backupCreated };
  } catch (error) {
    if (proof.leaseAcquired) {
      try {
        await shutdownDatabase(database, "launcher-character-rename-error");
        proof.leaseAcquired = false;
      } catch (_) {
        // A backup or current result cannot be trusted until the lease is released.
      }
    }
    throw error;
  }
}

const proof = { operationId: null, backupCreated: false, leaseAcquired: false,
  mutationStarted: false, rollbackConfirmed: false };
main(proof).then(
  (result) => exitWithTerminalResult(result, 0),
  (error) => exitWithTerminalResult({
    ok: false,
    operationId: proof.operationId,
    code: error.renameCode || "RENAME_FAILED",
    backupCreated: proof.backupCreated,
    needsReview: proof.leaseAcquired || (proof.mutationStarted && !proof.rollbackConfirmed),
  }, 1),
);
