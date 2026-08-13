"use strict";

// This helper is mounted read-only into one reviewed Managed Compose one-off
// container. The launcher proves the exact target and stopped service matrix;
// this process then owns backup, mutation, verification, rollback, and lease
// release as one indivisible semantic operation.
process.env.EVEJS_GAMESTORE_OWNER_ROLE = "maintenance";
// Integrity scans are synchronous inside better-sqlite3 and the reviewed live
// store is large enough that the 30-second server default is not a safe upper
// bound. Match the launcher's five-minute one-off timeout; normal shutdown
// releases this lease immediately, while a hard-killed helper remains fenced.
process.env.EVEJS_PERSISTENCE_OWNER_LEASE_MS = "300000";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { exitWithTerminalResult } = require("./terminal_result");

const MAX_INPUT_BYTES = 16 * 1024;
const EXPECTED_APP_ROOT = "/app";
const EXPECTED_EVEJS_VERSION = "0.12.5";
const BACKUP_ROOT = "/run/evejs-launcher/backup";
const GAMESTORE_ROOT = "/var/lib/evejs/gameStore";
const GAMESTORE_DATA = `${GAMESTORE_ROOT}/data`;
const GAMESTORE_SQLITE = `${GAMESTORE_ROOT}/gamestore.sqlite`;
const PAYLOAD_KEYS = ["characterName", "isGM", "password", "username"];
const MUTATED_TABLES = [
  "accounts",
  "alliances",
  "characters",
  "corporations",
  "identityState",
  "items",
  "skills",
  "mail",
  "notifications",
];
const SAFE_TABLE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SAFE_BACKUP_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const ACCOUNT_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$/;
const MANIFEST_PATTERN = /^(?:manifest(?:[._-][A-Za-z0-9_.-]+)?|database-manifest)\.json$/i;
const WELCOME_MAIL_SENDER_ID = 140000004;
const WELCOME_MAIL_SENDER_NAME = "GM ELYSIAN";
const WELCOME_MAIL_TITLE = "Welcome to EveJS Elysian";
const REVIEWED_SOURCE_CONTRACTS = Object.freeze([
  ["gameStore/index.js", "dc474d40f02e64db715361630c1a48ac3e3de30ed77ae300d2ffd82815178421", [
    "function acquirePersistenceOwnerLease(options = {})",
    "function shutdown(reason = \"shutdown\")",
    "_sqliteTables: SQLITE_TABLES",
  ]],
  ["gameStore/sqliteStore.js", "c71e3a6fb0c2f3c51be2848f0842a5286775c978ad19d39c006747ef08fecc0a", [
    "function assembleFromRows(table, rows = {})",
    "assembleFromRows,",
    "ROW_KEY_SEP",
  ]],
  ["gameStore/tableRepository.js", "f74665cd5de3a23bc4bf0f317ab6aa4186ba0a9b8443bcbdf70bbe784d99ac6e", [
    "return store.write(table, pathArg, value, opts)",
    "return store.remove(table, pathArg)",
    "return store.ensureTable(table)",
  ]],
  ["services/_shared/identityAllocator.js", "f801b4cbc973451ea7ba561d6a1df9bae589ddd84b21874549864ebe1124ea8f", [
    "function reserveAccountID()",
    "database.flushTablesSync([IDENTITY_TABLE])",
  ]],
  ["services/account/accountRoleProfiles.js", "b850b911ae3942f97211d6031c5e45bbf90f67764d97f59fb1c00b55b9c3494b", [
    "function buildPersistedAccountRoleRecord(account = {})",
    "buildPersistedAccountRoleRecord,",
  ]],
  ["services/character/charService.js", "302bfbcc6abdb7125721d469a9a76dc5879393488d83ad2ddd710da92dff7524", [
    "Handle_CreateCharacterWithDoll(args, session)",
    "spawnRookieShipForCharacter(",
    "repo.flushTablesSync([",
  ]],
  ["services/ship/rookieShipRuntime.js", "54ea7e981c9938538c264079124c2cd6f47518946e734a5be91fb54f40272f2b", [
    "function spawnRookieShipForCharacter(",
    "spawnRookieShipForCharacter,",
  ]],
  ["space/runtime.js", "485cb18c79e5f54d3973327fdcf4c3b686442cab0b1c67bbb85e02d853088097", [
    "const runtimeExports = module.exports",
  ]],
  ["space/transitions.js", "211c2074f82a6129bfa96da6e3150e55bf217c419bb7a733e75359f51d1a4307", [
    "module.exports = {",
  ]],
]);

function fixedError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function validatePayloadObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw fixedError("INVALID_REQUEST", "Invalid Docker character request.");
  }
  if (
    Object.keys(value).length !== PAYLOAD_KEYS.length ||
    !PAYLOAD_KEYS.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  ) {
    throw fixedError("INVALID_REQUEST", "Invalid Docker character request.");
  }
  const { characterName, isGM, password, username } = value;
  if (
    typeof characterName !== "string" ||
    typeof isGM !== "boolean" ||
    typeof password !== "string" ||
    typeof username !== "string" ||
    !ACCOUNT_PATTERN.test(username) ||
    characterName.length < 3 ||
    characterName.length > 37 ||
    characterName !== characterName.trim().replace(/\s+/g, " ") ||
    password.length < 1 ||
    password.length > 128 ||
    /[\u0000-\u001f\u007f]/.test(characterName) ||
    /[\u0000-\u001f\u007f]/.test(password)
  ) {
    throw fixedError("INVALID_REQUEST", "Invalid Docker character request.");
  }
  return { characterName, isGM, password, username };
}

function parsePayloadBuffer(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length === 0 || buffer.length > MAX_INPUT_BYTES) {
    throw fixedError("INVALID_REQUEST", "Invalid Docker character request.");
  }
  let parsed;
  try {
    parsed = JSON.parse(buffer.toString("utf8"));
  } catch (_error) {
    throw fixedError("INVALID_REQUEST", "Invalid Docker character request.");
  }
  const value = validatePayloadObject(parsed);
  if (!Buffer.from(JSON.stringify(value), "utf8").equals(buffer)) {
    throw fixedError("NON_CANONICAL_REQUEST", "Invalid Docker character request.");
  }
  return value;
}

function requireSuccess(result, code) {
  if (!result || result.success !== true) {
    throw fixedError(code, "Docker character maintenance failed.");
  }
  return result;
}

function positiveInt(value) {
  const numeric = Number(value);
  return Number.isSafeInteger(numeric) && numeric > 0 ? numeric : 0;
}

function escapePointer(value) {
  return String(value).replace(/~/g, "~0").replace(/\//g, "~1");
}

function safeTableName(table) {
  if (typeof table !== "string" || !SAFE_TABLE.test(table)) {
    throw fixedError("UNSAFE_TABLE", "Docker character maintenance failed.");
  }
  return table;
}

function safeBackupName(context) {
  const value = context && (context.backupName || context.name);
  return typeof value === "string" && SAFE_BACKUP_NAME.test(value) ? value : null;
}

function quoteTable(table) {
  return `"${safeTableName(table)}"`;
}

function sameStringMap(left, right) {
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every((key, index) => key === rightKeys[index] && left[key] === right[key])
  );
}

function sha256File(filePath, renewLease = () => {}) {
  const hash = crypto.createHash("sha256");
  const descriptor = fs.openSync(filePath, "r");
  const buffer = Buffer.allocUnsafe(64 * 1024);
  let nextRenewal = Date.now() + 5_000;
  try {
    while (true) {
      const count = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (count === 0) break;
      hash.update(buffer.subarray(0, count));
      if (Date.now() >= nextRenewal) {
        renewLease();
        nextRenewal = Date.now() + 5_000;
      }
    }
  } finally {
    fs.closeSync(descriptor);
  }
  renewLease();
  return hash.digest("hex");
}

function requireOrdinaryFile(filePath) {
  const resolved = path.resolve(filePath);
  if (!fs.existsSync(resolved)) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  const stat = fs.lstatSync(resolved);
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.size <= 0 ||
    stat.size > 2 * 1024 * 1024 ||
    fs.realpathSync(resolved) !== resolved
  ) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  return resolved;
}

function readPackage(filePath) {
  let document;
  try {
    document = JSON.parse(fs.readFileSync(requireOrdinaryFile(filePath), "utf8"));
  } catch (_error) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  return document;
}

function attestProductionImage(root) {
  const resolvedRoot = path.resolve(root);
  if (
    resolvedRoot !== EXPECTED_APP_ROOT ||
    !fs.existsSync(resolvedRoot) ||
    fs.realpathSync(resolvedRoot) !== resolvedRoot
  ) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  // The reviewed image deliberately has no root package; accepting one would
  // permit a different image layout to masquerade behind the same tag.
  if (fs.existsSync(path.join(resolvedRoot, "package.json"))) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  const serverPackage = readPackage(path.join(resolvedRoot, "server", "package.json"));
  if (
    serverPackage.name !== "eve.js" ||
    serverPackage.version !== EXPECTED_EVEJS_VERSION ||
    serverPackage.type !== "commonjs" ||
    !serverPackage.dependencies ||
    serverPackage.dependencies["better-sqlite3"] !== "^12.11.1"
  ) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  const sqlitePackage = readPackage(
    path.join(resolvedRoot, "server", "node_modules", "better-sqlite3", "package.json"),
  );
  if (sqlitePackage.name !== "better-sqlite3" || sqlitePackage.version !== "12.11.1") {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  const serverSource = path.join(resolvedRoot, "server", "src");
  if (fs.existsSync(path.join(serverSource, "gameStore", "package.json"))) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  for (const [relativePath, expectedHash, tokens] of REVIEWED_SOURCE_CONTRACTS) {
    const source = fs.readFileSync(
      requireOrdinaryFile(path.join(serverSource, ...relativePath.split("/"))),
      "utf8",
    );
    const normalizedHash = crypto
      .createHash("sha256")
      .update(source.replace(/\r\n/g, "\n"), "utf8")
      .digest("hex");
    if (
      (expectedHash && normalizedHash !== expectedHash) ||
      !tokens.every((token) => source.includes(token))
    ) {
      throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
    }
  }
  return serverSource;
}

function requirePlainDirectory(directory, code = "UNSAFE_PATH") {
  const resolved = path.resolve(directory);
  if (!fs.existsSync(resolved)) {
    throw fixedError(code, "Docker character data is not initialized.");
  }
  const stat = fs.lstatSync(resolved);
  if (!stat.isDirectory() || stat.isSymbolicLink() || fs.realpathSync(resolved) !== resolved) {
    throw fixedError("UNSAFE_PATH", "Docker character backup path is unsafe.");
  }
  return resolved;
}

function assertDirectChild(candidate, parent) {
  const resolvedParent = path.resolve(parent);
  const resolvedCandidate = path.resolve(candidate);
  if (path.dirname(resolvedCandidate) !== resolvedParent) {
    throw fixedError("UNSAFE_PATH", "Docker character backup path is unsafe.");
  }
  return resolvedCandidate;
}

function listFileDigests(root, renewLease = () => {}) {
  const result = Object.create(null);
  const resolvedRoot = path.resolve(root);
  if (!fs.existsSync(resolvedRoot)) return result;

  function visit(current, relative) {
    const stat = fs.lstatSync(current);
    if (stat.isSymbolicLink()) {
      throw fixedError("UNSAFE_PATH", "Docker character data contains a symlink.");
    }
    if (stat.isFile()) {
      result[relative.split(path.sep).join("/")] = sha256File(current, renewLease);
      return;
    }
    if (!stat.isDirectory()) {
      throw fixedError("UNSAFE_PATH", "Docker character data contains an unsafe entry.");
    }
    if (relative) {
      result[`${relative.split(path.sep).join("/")}/`] = "directory";
    }
    for (const entry of fs.readdirSync(current).sort()) {
      visit(path.join(current, entry), relative ? path.join(relative, entry) : entry);
    }
  }

  visit(resolvedRoot, "");
  delete result[""];
  return result;
}

function copyTreeStrict(source, destination, renewLease = () => {}) {
  const sourceStat = fs.lstatSync(source);
  if (sourceStat.isSymbolicLink()) {
    throw fixedError("UNSAFE_PATH", "Docker character data contains a symlink.");
  }
  if (sourceStat.isFile()) {
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    renewLease();
    return;
  }
  if (!sourceStat.isDirectory()) {
    throw fixedError("UNSAFE_PATH", "Docker character data contains an unsafe entry.");
  }
  fs.mkdirSync(destination, { recursive: false, mode: 0o700 });
  for (const entry of fs.readdirSync(source).sort()) {
    copyTreeStrict(
      path.join(source, entry),
      path.join(destination, entry),
      renewLease,
    );
  }
}

function replaceTreeStrict(source, destination, allowedParent, renewLease = () => {}) {
  const safeDestination = assertDirectChild(destination, allowedParent);
  if (fs.existsSync(safeDestination)) {
    const stat = fs.lstatSync(safeDestination);
    if (stat.isSymbolicLink()) {
      throw fixedError("UNSAFE_PATH", "Docker character rollback path is unsafe.");
    }
    fs.rmSync(safeDestination, { recursive: true, force: false });
  }
  if (source && fs.existsSync(source)) {
    copyTreeStrict(source, safeDestination, renewLease);
  }
}

function manifestNames(storeRoot) {
  if (!fs.existsSync(storeRoot)) return [];
  const names = [];
  for (const entry of fs.readdirSync(storeRoot, { withFileTypes: true })) {
    if (!MANIFEST_PATTERN.test(entry.name)) continue;
    if (!entry.isFile() || entry.isSymbolicLink()) {
      throw fixedError("UNSAFE_PATH", "Docker character manifest path is unsafe.");
    }
    names.push(entry.name);
  }
  return names.sort();
}

function assertIntegrity(connection, renewLease = () => {}) {
  for (const pragma of ["quick_check", "integrity_check"]) {
    renewLease();
    const rows = connection.pragma(pragma);
    if (
      !Array.isArray(rows) ||
      rows.length === 0 ||
      !rows.every((row) => Object.values(row).every((value) => value === "ok"))
    ) {
      throw fixedError("SQLITE_INTEGRITY", "Docker character database verification failed.");
    }
    renewLease();
  }
}

function assertPersistenceOutboxEmpty(connection) {
  const pendingOutbox = connection
    .prepare("SELECT COUNT(*) AS count FROM _persistence_outbox")
    .get();
  if (!pendingOutbox || Number(pendingOutbox.count) !== 0) {
    // Recovery applies and acknowledges journal rows. A scoped logical backup
    // cannot restore that internal protocol state, so never declare a backup,
    // commit, or rollback verified while an outbox record remains pending.
    throw fixedError("PENDING_OUTBOX", "Docker character data requires recovery first.");
  }
}

function logicalTables(connection) {
  const rows = connection
    .prepare(
      "SELECT name FROM sqlite_master " +
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    )
    .all();
  const tables = [];
  for (const row of rows) {
    const table = safeTableName(row.name);
    if (table.startsWith("_")) continue;
    const columns = new Set(
      connection
        .prepare(`PRAGMA table_info(${quoteTable(table)})`)
        .all()
        .map((column) => column.name),
    );
    if (columns.has("key") && columns.has("json")) tables.push(table);
  }
  return tables;
}

function tableDigest(connection, table, renewLease = () => {}) {
  const hash = crypto.createHash("sha256");
  let rowsRead = 0;
  let nextRenewal = Date.now() + 5_000;
  for (const row of connection
    .prepare(`SELECT key, json FROM ${quoteTable(table)} ORDER BY key`)
    .iterate()) {
    const key = String(row.key);
    let value;
    try {
      // Rollback reconstructs and serializes the logical value through
      // GameStore. Normalize irrelevant legacy whitespace before comparing.
      value = JSON.parse(String(row.json));
    } catch (_error) {
      throw fixedError("SQLITE_DATA", "Docker character database verification failed.");
    }
    updateLogicalRowDigest(hash, key, value);
    rowsRead += 1;
    if ((rowsRead & 1023) === 0 && Date.now() >= nextRenewal) {
      renewLease();
      nextRenewal = Date.now() + 5_000;
    }
  }
  renewLease();
  return hash.digest("hex");
}

function updateLogicalRowDigest(hash, keyValue, value) {
  const key = String(keyValue);
  const json = JSON.stringify(value);
  if (typeof json !== "string") {
    throw fixedError("SQLITE_DATA", "Docker character database verification failed.");
  }
  hash.update(`${Buffer.byteLength(key, "utf8")}:`);
  hash.update(key, "utf8");
  hash.update(`${Buffer.byteLength(json, "utf8")}:`);
  hash.update(json, "utf8");
}

function readLogicalRows(connection, table, renewLease = () => {}) {
  renewLease();
  const rows = connection
    .prepare(`SELECT key, json FROM ${quoteTable(table)} ORDER BY key`)
    .all()
    .map((row) => {
      try {
        return [String(row.key), JSON.parse(String(row.json))];
      } catch (_error) {
        throw fixedError("SQLITE_DATA", "Docker character database verification failed.");
      }
    });
  renewLease();
  return rows;
}

function logicalRowsDigest(rows) {
  if (!Array.isArray(rows)) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  const hash = crypto.createHash("sha256");
  const seenKeys = new Set();
  for (const row of rows) {
    if (!Array.isArray(row) || row.length !== 2 || typeof row[0] !== "string") {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
    if (seenKeys.has(row[0])) {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
    seenKeys.add(row[0]);
    updateLogicalRowDigest(hash, row[0], row[1]);
  }
  return hash.digest("hex");
}

function logicalEqual(left, right) {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left) &&
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((value, index) => logicalEqual(value, right[index]))
    );
  }
  if (
    !left || typeof left !== "object" ||
    !right || typeof right !== "object"
  ) {
    return false;
  }
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] && logicalEqual(left[key], right[key]),
    )
  );
}

function requireObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
  return value;
}

function requireExactKeys(value, expected) {
  const keys = Object.keys(requireObject(value)).sort();
  const wanted = [...expected].sort();
  if (!logicalEqual(keys, wanted)) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
}

function requirePreservedEntries(beforeValue, afterValue, allowedNewKeys = new Set()) {
  const before = requireObject(beforeValue);
  const after = requireObject(afterValue);
  for (const [key, value] of Object.entries(before)) {
    if (!Object.prototype.hasOwnProperty.call(after, key) || !logicalEqual(value, after[key])) {
      throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
    }
  }
  const newKeys = Object.keys(after).filter(
    (key) => !Object.prototype.hasOwnProperty.call(before, key),
  );
  if (
    newKeys.length !== allowedNewKeys.size ||
    newKeys.some((key) => !allowedNewKeys.has(key))
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
}

function withoutKeys(value, omitted) {
  return Object.fromEntries(
    Object.entries(requireObject(value)).filter(([key]) => !omitted.has(key)),
  );
}

function expectedWelcomeMailBody(characterName) {
  const pilot = String(characterName).trim() || "pilot";
  return [
    `${WELCOME_MAIL_TITLE}, ${pilot}.`,
    "",
    "You are stepping into a live, evolving open-source New Eden. A surprising amount already works, and a surprising amount still bites back, so expect rough edges, unfinished systems, and the occasional spectacular bug.",
    "",
    "A few good habits will help a lot:",
    "- If something feels wrong, trust that instinct.",
    "- If you find a bug, report it through the Discord linked on the EveJS Elysian GitHub.",
    "- If you report it, include the exact steps so we can reproduce it fast.",
    "",
    "Enjoy the cluster, push it hard, and let us know what breaks.",
    "",
    WELCOME_MAIL_SENDER_NAME,
  ].join("<br>");
}

function expectedWelcomeSender(characters) {
  const records = requireObject(characters);
  const fixed = records[String(WELCOME_MAIL_SENDER_ID)];
  if (fixed && typeof fixed === "object" && !Array.isArray(fixed)) {
    const senderName = String(fixed.characterName || "").trim();
    return {
      senderId: WELCOME_MAIL_SENDER_ID,
      senderName: senderName || WELCOME_MAIL_SENDER_NAME,
    };
  }
  for (const [key, record] of Object.entries(records)) {
    if (
      record && typeof record === "object" && !Array.isArray(record) &&
      record.characterName === WELCOME_MAIL_SENDER_NAME && positiveInt(key)
    ) {
      return { senderId: positiveInt(key), senderName: WELCOME_MAIL_SENDER_NAME };
    }
  }
  return {
    senderId: WELCOME_MAIL_SENDER_ID,
    senderName: WELCOME_MAIL_SENDER_NAME,
  };
}

function assertCreationLogicalContract(beforeTables, afterTables, expected) {
  const before = requireObject(beforeTables);
  const after = requireObject(afterTables);
  requireExactKeys(before, MUTATED_TABLES);
  requireExactKeys(after, MUTATED_TABLES);

  for (const table of ["alliances", "corporations", "skills"]) {
    if (!logicalEqual(before[table], after[table])) {
      throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
    }
  }

  const username = String(expected.username);
  const characterKey = String(expected.characterId);
  requirePreservedEntries(before.accounts, after.accounts, new Set([username]));
  requirePreservedEntries(before.characters, after.characters, new Set([characterKey]));
  if (!logicalEqual(after.accounts[username], expected.accountRecord)) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }

  const beforeIdentity = requireObject(before.identityState);
  const afterIdentity = requireObject(after.identityState);
  requireExactKeys(beforeIdentity, [
    "version", "nextAccountID", "nextCharacterID", "nextItemID",
  ]);
  const allocatorFields = new Set(["nextAccountID", "nextCharacterID", "nextItemID"]);
  requireExactKeys(afterIdentity, Object.keys(beforeIdentity));
  if (
    !logicalEqual(
      withoutKeys(beforeIdentity, allocatorFields),
      withoutKeys(afterIdentity, allocatorFields),
    ) ||
    positiveInt(afterIdentity.nextAccountID) !== expected.accountId + 1 ||
    positiveInt(afterIdentity.nextCharacterID) !== expected.characterId + 1
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }

  const beforeItems = requireObject(before.items);
  const afterItems = requireObject(after.items);
  requirePreservedEntries(beforeItems, afterItems, new Set(
    Object.keys(afterItems).filter((key) => !Object.prototype.hasOwnProperty.call(beforeItems, key)),
  ));
  const newItemKeys = Object.keys(afterItems).filter(
    (key) => !Object.prototype.hasOwnProperty.call(beforeItems, key),
  );
  if (!newItemKeys.includes(String(expected.shipId)) || newItemKeys.length === 0) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
  const newItems = Object.fromEntries(newItemKeys.map((key) => [key, requireObject(afterItems[key])]));
  for (const [key, item] of Object.entries(newItems)) {
    if (
      positiveInt(item.itemID) !== positiveInt(Number(key)) ||
      positiveInt(item.ownerID) !== expected.characterId
    ) {
      throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
    }
    const seen = new Set([key]);
    let location = positiveInt(item.locationID);
    const isRookieShip = key === String(expected.shipId);
    if (isRookieShip && location !== expected.stationId) {
      throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
    }
    if (!isRookieShip && location !== expected.stationId) {
      while (location !== expected.shipId) {
        const parentKey = String(location);
        if (!location || seen.has(parentKey) || !Object.prototype.hasOwnProperty.call(newItems, parentKey)) {
          throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
        }
        seen.add(parentKey);
        location = positiveInt(newItems[parentKey].locationID);
      }
    }
  }
  const oldNextItem = positiveInt(beforeIdentity.nextItemID);
  const newNextItem = positiveInt(afterIdentity.nextItemID);
  const highestNewItem = Math.max(...newItemKeys.map((key) => positiveInt(Number(key))));
  if (
    !oldNextItem ||
    newItemKeys.some((key) => positiveInt(Number(key)) < oldNextItem) ||
    newNextItem <= oldNextItem ||
    newNextItem <= highestNewItem
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }

  const beforeMail = requireObject(before.mail);
  const afterMail = requireObject(after.mail);
  requireExactKeys(beforeMail, ["_meta", "messages", "mailboxes", "mailingLists"]);
  requireExactKeys(afterMail, Object.keys(beforeMail));
  const beforeMailMeta = requireObject(beforeMail._meta);
  const afterMailMeta = requireObject(afterMail._meta);
  requireExactKeys(beforeMailMeta, ["nextMessageID", "nextMailingListID"]);
  const messageId = positiveInt(beforeMailMeta.nextMessageID);
  if (
    !messageId ||
    positiveInt(afterMailMeta.nextMessageID) !== messageId + 1 ||
    !logicalEqual(
      withoutKeys(beforeMailMeta, new Set(["nextMessageID"])),
      withoutKeys(afterMailMeta, new Set(["nextMessageID"])),
    ) ||
    !logicalEqual(beforeMail.mailingLists, afterMail.mailingLists)
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
  requirePreservedEntries(beforeMail.messages, afterMail.messages, new Set([String(messageId)]));
  requirePreservedEntries(beforeMail.mailboxes, afterMail.mailboxes, new Set([characterKey]));
  const message = requireObject(afterMail.messages[String(messageId)]);
  requireExactKeys(message, [
    "messageID", "senderID", "toCharacterIDs", "toListID",
    "toCorpOrAllianceID", "title", "body", "sentDate", "createdAt",
  ]);
  const welcomeSender = expectedWelcomeSender(before.characters);
  if (
    positiveInt(message.messageID) !== messageId ||
    positiveInt(message.senderID) !== welcomeSender.senderId ||
    !logicalEqual(message.toCharacterIDs, [expected.characterId]) ||
    message.toListID !== null || message.toCorpOrAllianceID !== null ||
    message.title !== WELCOME_MAIL_TITLE ||
    message.body !== expectedWelcomeMailBody(expected.characterName) ||
    !logicalEqual(message.sentDate, message.createdAt)
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
  const mailbox = requireObject(afterMail.mailboxes[characterKey]);
  requireExactKeys(mailbox, ["statuses", "labels", "_meta"]);
  requireExactKeys(mailbox.statuses, [String(messageId)]);
  requireExactKeys(mailbox.statuses[String(messageId)], ["statusMask", "labelMask"]);
  if (
    Number(mailbox.statuses[String(messageId)].statusMask) !== 0 ||
    Number(mailbox.statuses[String(messageId)].labelMask) !== 1 ||
    !logicalEqual(mailbox.labels, {}) ||
    !logicalEqual(mailbox._meta, { nextLabelMask: 16 })
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }

  const beforeNotifications = requireObject(before.notifications);
  const afterNotifications = requireObject(after.notifications);
  requireExactKeys(beforeNotifications, ["_meta", "boxes"]);
  requireExactKeys(afterNotifications, Object.keys(beforeNotifications));
  const beforeNotificationMeta = requireObject(beforeNotifications._meta);
  const afterNotificationMeta = requireObject(afterNotifications._meta);
  requireExactKeys(beforeNotificationMeta, ["nextNotificationID"]);
  const notificationId = positiveInt(beforeNotificationMeta.nextNotificationID);
  if (
    !notificationId ||
    positiveInt(afterNotificationMeta.nextNotificationID) !== notificationId + 1 ||
    !logicalEqual(
      withoutKeys(beforeNotificationMeta, new Set(["nextNotificationID"])),
      withoutKeys(afterNotificationMeta, new Set(["nextNotificationID"])),
    )
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
  requirePreservedEntries(
    beforeNotifications.boxes,
    afterNotifications.boxes,
    new Set([characterKey]),
  );
  const box = requireObject(afterNotifications.boxes[characterKey]);
  requireExactKeys(box, ["byID", "order"]);
  requireExactKeys(box.byID, [String(notificationId)]);
  if (!logicalEqual(box.order, [notificationId])) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
  const notification = requireObject(box.byID[String(notificationId)]);
  requireExactKeys(notification, [
    "notificationID", "typeID", "senderID", "receiverID", "processed",
    "created", "groupID", "data",
  ]);
  const notificationData = requireObject(notification.data);
  requireExactKeys(notificationData, ["senderName", "subject", "msg"]);
  const notificationMessage = requireObject(notificationData.msg);
  requireExactKeys(notificationMessage, [
    "messageID", "senderID", "senderName", "sentDate", "toCharacterIDs",
    "toListID", "toCorpOrAllianceID", "subject", "statusMask", "labelMask",
    "read", "trashed", "replied", "forwarded",
  ]);
  if (
    positiveInt(notification.notificationID) !== notificationId ||
    Number(notification.typeID) !== 1004 ||
    positiveInt(notification.senderID) !== positiveInt(message.senderID) ||
    positiveInt(notification.receiverID) !== expected.characterId ||
    notification.processed !== false ||
    !logicalEqual(notification.created, message.sentDate) ||
    Number(notification.groupID) !== 4 ||
    notificationData.senderName !== welcomeSender.senderName ||
    notificationData.subject !== WELCOME_MAIL_TITLE ||
    positiveInt(notificationMessage.messageID) !== messageId ||
    positiveInt(notificationMessage.senderID) !== welcomeSender.senderId ||
    notificationMessage.senderName !== welcomeSender.senderName ||
    !logicalEqual(notificationMessage.sentDate, message.sentDate) ||
    !logicalEqual(notificationMessage.toCharacterIDs, [expected.characterId]) ||
    notificationMessage.toListID !== null ||
    notificationMessage.toCorpOrAllianceID !== null ||
    notificationMessage.subject !== WELCOME_MAIL_TITLE ||
    Number(notificationMessage.statusMask) !== 0 ||
    Number(notificationMessage.labelMask) !== 1 ||
    notificationMessage.read !== false ||
    notificationMessage.trashed !== false ||
    notificationMessage.replied !== false ||
    notificationMessage.forwarded !== false
  ) {
    throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
  }
}

function readLogicalTableBackup(backupFile, expectedDigests) {
  const stat = fs.lstatSync(backupFile);
  if (!stat.isFile() || stat.isSymbolicLink()) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  let document;
  try {
    document = JSON.parse(fs.readFileSync(backupFile, "utf8"));
  } catch (_error) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  if (
    !document ||
    document.version !== 1 ||
    !document.tables ||
    typeof document.tables !== "object" ||
    Array.isArray(document.tables)
  ) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  const expectedTables = [...MUTATED_TABLES].sort();
  const actualTables = Object.keys(document.tables).sort();
  if (
    expectedTables.length !== actualTables.length ||
    expectedTables.some((table, index) => table !== actualTables[index])
  ) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  for (const table of expectedTables) {
    if (logicalRowsDigest(document.tables[table]) !== expectedDigests[table]) {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
  }
  return document.tables;
}

function requireBackupRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  return value;
}

function requireBackupFile(filePath) {
  const resolved = path.resolve(filePath);
  if (!fs.existsSync(resolved)) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  const stat = fs.lstatSync(resolved);
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    fs.realpathSync(resolved) !== resolved
  ) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  return resolved;
}

function requireExactDirectoryEntries(directory, expectedEntries) {
  const entries = fs.readdirSync(requirePlainDirectory(directory)).sort();
  const expected = [...expectedEntries].sort();
  if (!logicalEqual(entries, expected)) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
}

function verifyRetainedBackup(context, backupRoot, renewLease = () => {}) {
  const root = requirePlainDirectory(backupRoot);
  const backupName = safeBackupName(context);
  if (!backupName) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  const backupPath = requirePlainDirectory(
    assertDirectChild(path.join(root, backupName), root),
  );
  const backupTables = assertDirectChild(path.join(backupPath, "sqlite-tables.json"), backupPath);
  const backupData = assertDirectChild(path.join(backupPath, "data"), backupPath);
  const backupStoreFiles = assertDirectChild(path.join(backupPath, "store-files"), backupPath);
  const metadataPath = assertDirectChild(path.join(backupPath, "metadata.json"), backupPath);
  if (
    path.resolve(context.backupPath) !== backupPath ||
    path.resolve(context.backupTables) !== backupTables ||
    path.resolve(context.backupData) !== backupData ||
    path.resolve(context.backupStoreFiles) !== backupStoreFiles ||
    path.resolve(context.metadataPath) !== metadataPath
  ) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  requireExactDirectoryEntries(backupPath, [
    "data", "metadata.json", "sqlite-tables.json", "store-files",
  ]);
  requireBackupFile(metadataPath);
  requireBackupFile(backupTables);
  requirePlainDirectory(backupData);
  requirePlainDirectory(backupStoreFiles);
  if (
    typeof context.metadataSha256 !== "string" ||
    sha256File(metadataPath, renewLease) !== context.metadataSha256
  ) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }

  let metadata;
  try {
    metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  } catch (_error) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  requireBackupRecord(metadata);
  requireExactKeys(metadata, [
    "backupName",
    "fileDigests",
    "manifestDigests",
    "scopedDataDigests",
    "scopedDataDirectories",
    "sqliteDigests",
    "version",
  ]);
  if (
    metadata.version !== 1 ||
    metadata.backupName !== backupName ||
    !logicalEqual(metadata, context.metadata)
  ) {
    throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
  }
  const sqliteDigestsValue = requireBackupRecord(metadata.sqliteDigests);
  requireBackupRecord(metadata.fileDigests);
  const manifestDigests = requireBackupRecord(metadata.manifestDigests);
  const scopedDirectories = requireBackupRecord(metadata.scopedDataDirectories);
  const scopedDigests = requireBackupRecord(metadata.scopedDataDigests);
  requireExactKeys(scopedDirectories, MUTATED_TABLES);
  requireExactKeys(scopedDigests, MUTATED_TABLES);

  const logicalPreimages = readLogicalTableBackup(backupTables, sqliteDigestsValue);
  const expectedDataEntries = [];
  for (const table of MUTATED_TABLES) {
    if (typeof scopedDirectories[table] !== "boolean") {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
    const expectedDigests = requireBackupRecord(scopedDigests[table]);
    const backupDirectory = assertDirectChild(path.join(backupData, table), backupData);
    if (scopedDirectories[table]) {
      expectedDataEntries.push(table);
      requirePlainDirectory(backupDirectory);
      if (!sameStringMap(expectedDigests, listFileDigests(backupDirectory, renewLease))) {
        throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
      }
    } else if (Object.keys(expectedDigests).length !== 0 || fs.existsSync(backupDirectory)) {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
  }
  requireExactDirectoryEntries(backupData, expectedDataEntries);

  const manifestNamesExpected = Object.keys(manifestDigests).sort();
  for (const name of manifestNamesExpected) {
    if (!MANIFEST_PATTERN.test(name) || typeof manifestDigests[name] !== "string") {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
    const backupManifest = requireBackupFile(
      assertDirectChild(path.join(backupStoreFiles, name), backupStoreFiles),
    );
    if (sha256File(backupManifest, renewLease) !== manifestDigests[name]) {
      throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
    }
  }
  requireExactDirectoryEntries(backupStoreFiles, manifestNamesExpected);
  renewLease();
  return logicalPreimages;
}

function sqliteDigests(connection, renewLease = () => {}) {
  return Object.fromEntries(
    logicalTables(connection).map((table) => [
      table,
      tableDigest(connection, table, renewLease),
    ]),
  );
}

function openReadonly(BetterSqlite3, databasePath) {
  const connection = new BetterSqlite3(databasePath, {
    readonly: true,
    fileMustExist: true,
  });
  connection.pragma("query_only = ON");
  connection.pragma("busy_timeout = 5000");
  return connection;
}

function makeProductionBackupManager({ database, BetterSqlite3, sqliteStore }) {
  const backupRoot = path.resolve(BACKUP_ROOT);
  const storeRoot = path.resolve(GAMESTORE_ROOT);
  const dataRoot = path.resolve(GAMESTORE_DATA);
  const sqlitePath = path.resolve(GAMESTORE_SQLITE);
  const renewLease = () => {
    if (typeof database.renewPersistenceOwnerLease !== "function") {
      throw fixedError("UNSUPPORTED_STORE", "Docker character data layout is unsupported.");
    }
    const renewed = database.renewPersistenceOwnerLease();
    if (!renewed || typeof renewed !== "object") {
      throw fixedError("LEASE_FAILED", "Docker character maintenance lease failed.");
    }
  };

  function assembleLogicalTables(rowsByTable) {
    return Object.fromEntries(
      MUTATED_TABLES.map((table) => {
        const rowMap = Object.create(null);
        for (const [key, value] of rowsByTable[table]) rowMap[key] = value;
        return [table, sqliteStore.assembleFromRows(table, rowMap)];
      }),
    );
  }

  async function create() {
    // The Docker volume mounts already exist.  Requiring them instead of
    // creating them keeps every filesystem mutation behind the acquired
    // persistence-owner lease and fails closed on an uninitialized store.
    requirePlainDirectory(backupRoot);
    requirePlainDirectory(storeRoot, "STORE_UNINITIALIZED");
    requirePlainDirectory(dataRoot, "STORE_UNINITIALIZED");
    if (!fs.existsSync(sqlitePath) || fs.lstatSync(sqlitePath).isSymbolicLink()) {
      throw fixedError("STORE_UNINITIALIZED", "Docker character data is not initialized.");
    }
    const backupName = `character-${Date.now()}-${crypto.randomBytes(8).toString("hex")}`;
    const backupPath = assertDirectChild(path.join(backupRoot, backupName), backupRoot);
    fs.mkdirSync(backupPath, { recursive: false, mode: 0o700 });
    const backupTables = path.join(backupPath, "sqlite-tables.json");
    const backupData = path.join(backupPath, "data");
    const backupStoreFiles = path.join(backupPath, "store-files");

    let source = null;
    try {
      source = openReadonly(BetterSqlite3, sqlitePath);
      assertIntegrity(source, renewLease);
      assertPersistenceOutboxEmpty(source);
      let sourceSqliteDigests;
      let logicalPreimages;
      source.exec("BEGIN");
      try {
        // Both the whole-store guard digests and the scoped rollback pre-images
        // come from one SQLite snapshot. The retained artifact therefore stays
        // small without weakening unexpected-mutation detection.
        sourceSqliteDigests = sqliteDigests(source, renewLease);
        if (!(database._sqliteTables instanceof Set)) {
          throw fixedError("UNSUPPORTED_STORE", "Docker character data layout is unsupported.");
        }
        const migratedTables = new Set(
          source
            .prepare("SELECT table_name FROM _migrations")
            .all()
            .map((row) => String(row.table_name)),
        );
        for (const table of database._sqliteTables) {
          if (
            !Object.prototype.hasOwnProperty.call(sourceSqliteDigests, table) ||
            !migratedTables.has(table)
          ) {
            throw fixedError("UNMIGRATED_STORE", "Docker character data migration is incomplete.");
          }
        }
        logicalPreimages = Object.fromEntries(
          MUTATED_TABLES.map((table) => {
            if (!Object.prototype.hasOwnProperty.call(sourceSqliteDigests, table)) {
              throw fixedError("UNMIGRATED_STORE", "Docker character data migration is incomplete.");
            }
            return [table, readLogicalRows(source, table, renewLease)];
          }),
        );
        source.exec("COMMIT");
      } catch (error) {
        if (source.inTransaction) source.exec("ROLLBACK");
        throw error;
      }
      source.close();
      source = null;

      fs.writeFileSync(
        backupTables,
        JSON.stringify({ version: 1, tables: logicalPreimages }),
        { encoding: "utf8", flag: "wx", mode: 0o600 },
      );
      readLogicalTableBackup(backupTables, sourceSqliteDigests);

      const sourceFileDigests = listFileDigests(dataRoot, renewLease);
      fs.mkdirSync(backupData, { recursive: false, mode: 0o700 });
      const scopedDataDirectories = {};
      const scopedDataDigests = {};
      for (const table of MUTATED_TABLES) {
        const sourceDirectory = assertDirectChild(path.join(dataRoot, table), dataRoot);
        const backupDirectory = assertDirectChild(path.join(backupData, table), backupData);
        const present = fs.existsSync(sourceDirectory);
        scopedDataDirectories[table] = present;
        scopedDataDigests[table] = present
          ? listFileDigests(sourceDirectory, renewLease)
          : {};
        if (!present) continue;
        copyTreeStrict(sourceDirectory, backupDirectory, renewLease);
        if (!sameStringMap(
          scopedDataDigests[table],
          listFileDigests(backupDirectory, renewLease),
        )) {
          throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
        }
      }

      fs.mkdirSync(backupStoreFiles, { recursive: false, mode: 0o700 });
      const manifests = manifestNames(storeRoot);
      const manifestDigests = {};
      for (const name of manifests) {
        const sourceManifest = assertDirectChild(path.join(storeRoot, name), storeRoot);
        const backupManifest = assertDirectChild(
          path.join(backupStoreFiles, name),
          backupStoreFiles,
        );
        fs.copyFileSync(sourceManifest, backupManifest, fs.constants.COPYFILE_EXCL);
        manifestDigests[name] = sha256File(sourceManifest, renewLease);
        if (sha256File(backupManifest, renewLease) !== manifestDigests[name]) {
          throw fixedError("BACKUP_MISMATCH", "Docker character backup verification failed.");
        }
      }

      const metadata = {
        version: 1,
        backupName,
        sqliteDigests: sourceSqliteDigests,
        fileDigests: sourceFileDigests,
        manifestDigests,
        scopedDataDirectories,
        scopedDataDigests,
      };
      const metadataPath = path.join(backupPath, "metadata.json");
      fs.writeFileSync(
        metadataPath,
        JSON.stringify(metadata, null, 2),
        { encoding: "utf8", flag: "wx", mode: 0o600 },
      );
      const context = {
        backupCreated: true,
        created: true,
        backupName,
        name: backupName,
        backupPath,
        backupTables,
        backupData,
        backupStoreFiles,
        metadataPath,
        metadataSha256: sha256File(metadataPath, renewLease),
        sqliteTables: new Set(MUTATED_TABLES),
        touchedTables: new Set(),
        metadata,
      };
      verifyRetainedBackup(context, backupRoot, renewLease);
      return context;
    } finally {
      if (source) source.close();
    }
  }

  function beforeMutation(context, table) {
    const normalized = safeTableName(table);
    if (!context.sqliteTables.has(normalized)) {
      throw fixedError(
        "UNBACKED_TABLE",
        "Docker character mutation was not backed up.",
      );
    }
    context.touchedTables.add(normalized);
  }

  async function rollback(context, targetDatabase) {
    // Prove every retained artifact is still the pre-image before changing any
    // live record. A partial/corrupt backup must never start a partial restore.
    const logicalPreimages = verifyRetainedBackup(context, backupRoot, renewLease);
    for (const table of [...context.touchedTables].sort()) {
      if (!context.sqliteTables.has(table)) {
        throw fixedError("UNBACKED_TABLE", "Docker character mutation was not backed up.");
      }
      const rowMap = Object.create(null);
      for (const [key, value] of logicalPreimages[table]) {
        rowMap[key] = value;
      }
      requireSuccess(
        targetDatabase.write(
          table,
          "/",
          sqliteStore.assembleFromRows(table, rowMap),
          { force: true },
        ),
        "ROLLBACK_WRITE_FAILED",
      );
    }
    requireSuccess(
      targetDatabase.flushTablesSync([...context.touchedTables].sort()),
      "ROLLBACK_FLUSH_FAILED",
    );
    requireSuccess(targetDatabase.flushAllSync(), "ROLLBACK_FLUSH_FAILED");

    for (const table of [...context.touchedTables].sort()) {
      replaceTreeStrict(
        context.metadata.scopedDataDirectories[table]
          ? path.join(context.backupData, table)
          : null,
        path.join(dataRoot, table),
        dataRoot,
        renewLease,
      );
    }

    const expectedManifests = new Set(Object.keys(context.metadata.manifestDigests));
    for (const name of manifestNames(storeRoot)) {
      if (!expectedManifests.has(name)) {
        fs.unlinkSync(assertDirectChild(path.join(storeRoot, name), storeRoot));
      }
    }
    for (const name of expectedManifests) {
      const destination = assertDirectChild(path.join(storeRoot, name), storeRoot);
      if (fs.existsSync(destination)) fs.unlinkSync(destination);
      fs.copyFileSync(
        assertDirectChild(path.join(context.backupStoreFiles, name), context.backupStoreFiles),
        destination,
        fs.constants.COPYFILE_EXCL,
      );
    }
    return { success: true };
  }

  function verifyFiles(context) {
    if (!sameStringMap(
      context.metadata.fileDigests,
      listFileDigests(dataRoot, renewLease),
    )) {
      throw fixedError("FILE_VERIFY_FAILED", "Docker character data verification failed.");
    }
    const currentManifestDigests = Object.fromEntries(
      manifestNames(storeRoot).map((name) => [
        name,
        sha256File(path.join(storeRoot, name), renewLease),
      ]),
    );
    if (!sameStringMap(context.metadata.manifestDigests, currentManifestDigests)) {
      throw fixedError("FILE_VERIFY_FAILED", "Docker character data verification failed.");
    }
  }

  function verifyRestored(context) {
    verifyRetainedBackup(context, backupRoot, renewLease);
    const live = openReadonly(BetterSqlite3, sqlitePath);
    try {
      assertIntegrity(live, renewLease);
      assertPersistenceOutboxEmpty(live);
      if (!sameStringMap(
        context.metadata.sqliteDigests,
        sqliteDigests(live, renewLease),
      )) {
        throw fixedError("ROLLBACK_VERIFY_FAILED", "Docker character rollback verification failed.");
      }
    } finally {
      live.close();
    }
    verifyFiles(context);
    verifyRetainedBackup(context, backupRoot, renewLease);
    return { success: true };
  }

  function verifyCreation(context, expected) {
    const logicalPreimages = verifyRetainedBackup(context, backupRoot, renewLease);
    const live = openReadonly(BetterSqlite3, sqlitePath);
    try {
      assertIntegrity(live, renewLease);
      assertPersistenceOutboxEmpty(live);
      const accountRow = live
        .prepare(`SELECT json FROM ${quoteTable("accounts")} WHERE key = ?`)
        .get(expected.username);
      const characterRow = live
        .prepare(`SELECT json FROM ${quoteTable("characters")} WHERE key = ?`)
        .get(String(expected.characterId));
      if (!accountRow || !characterRow) {
        throw fixedError("CREATION_VERIFY_FAILED", "Docker character creation was not persisted.");
      }
      const account = JSON.parse(String(accountRow.json));
      const character = JSON.parse(String(characterRow.json));
      const shipId = positiveInt(character.shipID);
      const shipTypeId = positiveInt(character.shipTypeID);
      const stationId = positiveInt(character.stationID);
      const itemRow = shipId
        ? live
            .prepare(`SELECT json FROM ${quoteTable("items")} WHERE key = ?`)
            .get(String(shipId))
        : null;
      const item = itemRow ? JSON.parse(String(itemRow.json)) : null;
      const expectedAccount = expected.accountRecord;
      if (
        positiveInt(account.id) !== expected.accountId ||
        !expectedAccount ||
        account.passwordhash !== expectedAccount.passwordhash ||
        account.isGM !== expectedAccount.isGM ||
        account.banned !== expectedAccount.banned ||
        account.role !== expectedAccount.role ||
        account.chatRole !== expectedAccount.chatRole ||
        positiveInt(character.accountId) !== expected.accountId ||
        character.characterName !== expected.characterName ||
        !shipTypeId ||
        !stationId ||
        !item ||
        positiveInt(item.itemID) !== shipId ||
        positiveInt(item.typeID) !== shipTypeId ||
        positiveInt(item.ownerID) !== expected.characterId ||
        positiveInt(item.locationID) !== stationId ||
        Number(item.flagID) !== 4 ||
        Number(item.singleton) !== 1 ||
        Number(item.groupID) !== 237 ||
        Number(item.categoryID) !== 6
      ) {
        throw fixedError("CREATION_VERIFY_FAILED", "Docker character creation was not persisted.");
      }

      const currentDigests = sqliteDigests(live, renewLease);
      if (
        Object.keys(currentDigests).length !==
        Object.keys(context.metadata.sqliteDigests).length
      ) {
        throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
      }
      for (const [table, digest] of Object.entries(context.metadata.sqliteDigests)) {
        if (!context.touchedTables.has(table) && currentDigests[table] !== digest) {
          throw fixedError("UNEXPECTED_MUTATION", "Docker character verification found an unexpected change.");
        }
      }
      const currentLogicalRows = Object.fromEntries(
        MUTATED_TABLES.map((table) => [table, readLogicalRows(live, table, renewLease)]),
      );
      assertCreationLogicalContract(
        assembleLogicalTables(logicalPreimages),
        assembleLogicalTables(currentLogicalRows),
        { ...expected, shipId, stationId },
      );
    } finally {
      live.close();
    }
    verifyFiles(context);
    verifyRetainedBackup(context, backupRoot, renewLease);
    return { success: true, rookieShipVerified: true };
  }

  return { create, beforeMutation, rollback, verifyCreation, verifyRestored };
}

function loadProductionRuntime() {
  const root = process.cwd();
  // Attest the exact reviewed image in this same one-off process before any
  // EveJS module can initialize GameStore or obtain persistence authority.
  const serverSource = attestProductionImage(root);
  const database = require(path.join(serverSource, "gameStore", "index.js"));
  const BetterSqlite3 = require(
    path.join(root, "server", "node_modules", "better-sqlite3"),
  );
  const sqliteStore = require(path.join(serverSource, "gameStore", "sqliteStore"));
  if (
    !database ||
    typeof database.acquirePersistenceOwnerLease !== "function" ||
    typeof database.renewPersistenceOwnerLease !== "function" ||
    typeof database.shutdown !== "function" ||
    !(database._sqliteTables instanceof Set) ||
    !MUTATED_TABLES.every((table) => database._sqliteTables.has(table)) ||
    !sqliteStore ||
    typeof sqliteStore.assembleFromRows !== "function" ||
    typeof BetterSqlite3 !== "function"
  ) {
    throw fixedError("UNSUPPORTED_IMAGE", "Docker character image is unsupported.");
  }
  return {
    database,
    backupManager: makeProductionBackupManager({ database, BetterSqlite3, sqliteStore }),
    loadCreationDependencies() {
      return {
        reserveAccountID: require(
          path.join(serverSource, "services", "_shared", "identityAllocator"),
        ).reserveAccountID,
        buildPersistedAccountRoleRecord: require(
          path.join(serverSource, "services", "account", "accountRoleProfiles"),
        ).buildPersistedAccountRoleRecord,
        CharService: require(
          path.join(serverSource, "services", "character", "charService"),
        ),
      };
    },
  };
}

function installMutationGuards(database, backupManager, context, onMutation) {
  const originals = {
    write: database.write,
    remove: database.remove,
    ensureTable: database.ensureTable,
  };
  if (!Object.values(originals).every((candidate) => typeof candidate === "function")) {
    throw fixedError("UNSUPPORTED_STORE", "Docker character data layout is unsupported.");
  }
  database.write = function guardedWrite(table, ...args) {
    backupManager.beforeMutation(context, table);
    onMutation();
    return originals.write.call(database, table, ...args);
  };
  database.remove = function guardedRemove(table, ...args) {
    backupManager.beforeMutation(context, table);
    onMutation();
    return originals.remove.call(database, table, ...args);
  };
  database.ensureTable = function guardedEnsure(table, ...args) {
    backupManager.beforeMutation(context, table);
    onMutation();
    return originals.ensureTable.call(database, table, ...args);
  };
  return () => {
    database.write = originals.write;
    database.remove = originals.remove;
    database.ensureTable = originals.ensureTable;
  };
}

async function executeDockerCharacterCreation(payload, overrides = {}) {
  const request = validatePayloadObject(payload);
  const runtime =
    overrides.database && overrides.backupManager && overrides.loadCreationDependencies
      ? overrides
      : loadProductionRuntime();
  const { database, backupManager, loadCreationDependencies } = runtime;
  let leaseAcquired = false;
  let context = null;
  let mutationStarted = false;
  let shutdownAttempted = false;
  let restoreGuards = null;

  try {
    if (
      !database ||
      typeof database.acquirePersistenceOwnerLease !== "function" ||
      typeof database.shutdown !== "function"
    ) {
      throw fixedError("UNSUPPORTED_STORE", "Docker character data layout is unsupported.");
    }
    const lease = database.acquirePersistenceOwnerLease({ recover: false });
    if (!lease || typeof lease !== "object") {
      throw fixedError("LEASE_FAILED", "Docker character maintenance lease failed.");
    }
    leaseAcquired = true;
    context = await backupManager.create(database);
    if (
      !context ||
      (context.backupCreated !== true && context.created !== true) ||
      !safeBackupName(context)
    ) {
      throw fixedError("BACKUP_FAILED", "Docker character backup failed.");
    }

    restoreGuards = installMutationGuards(
      database,
      backupManager,
      context,
      () => {
        mutationStarted = true;
      },
    );

    const { reserveAccountID, buildPersistedAccountRoleRecord, CharService } =
      loadCreationDependencies();
    if (
      typeof reserveAccountID !== "function" ||
      typeof buildPersistedAccountRoleRecord !== "function" ||
      typeof CharService !== "function" ||
      !CharService.prototype ||
      typeof CharService.prototype.Handle_CreateCharacterWithDoll !== "function"
    ) {
      throw fixedError("DEPENDENCY_FAILED", "Docker character dependencies are unavailable.");
    }

    const accountsResult = requireSuccess(
      database.read("accounts", "/"),
      "ACCOUNT_READ_FAILED",
    );
    const accounts = accountsResult.data || {};
    if (Object.prototype.hasOwnProperty.call(accounts, request.username)) {
      throw fixedError("ACCOUNT_EXISTS", "Docker character request conflicts with existing data.");
    }
    const charactersResult = requireSuccess(
      database.read("characters", "/"),
      "CHARACTER_READ_FAILED",
    );
    const duplicate = Object.values(charactersResult.data || {}).some(
      (entry) =>
        entry &&
        String(entry.characterName || "").toLocaleLowerCase() ===
          request.characterName.toLocaleLowerCase(),
    );
    if (duplicate) {
      throw fixedError("CHARACTER_EXISTS", "Docker character request conflicts with existing data.");
    }

    const accountId = positiveInt(reserveAccountID());
    if (!accountId) {
      throw fixedError("ACCOUNT_ID_FAILED", "Docker character account allocation failed.");
    }
    const account = buildPersistedAccountRoleRecord({
      passwordhash: crypto
        .createHash("sha1")
        .update(request.password, "utf8")
        .digest("hex"),
      id: accountId,
      isGM: request.isGM,
      banned: false,
    });
    requireSuccess(
      database.write(
        "accounts",
        `/${escapePointer(request.username)}`,
        account,
        { force: true },
      ),
      "ACCOUNT_WRITE_FAILED",
    );
    requireSuccess(database.flushTablesSync(["accounts"]), "ACCOUNT_FLUSH_FAILED");

    const service = new CharService();
    const characterId = positiveInt(
      service.Handle_CreateCharacterWithDoll(
        [request.characterName, 1, 1, 1, null, null, 11, 1],
        { userid: accountId },
      ),
    );
    if (!characterId) {
      throw fixedError("CHARACTER_ID_FAILED", "Docker character allocation failed.");
    }
    requireSuccess(database.flushAllSync(), "CREATION_FLUSH_FAILED");
    const verified = await backupManager.verifyCreation(context, {
      accountId,
      characterId,
      characterName: request.characterName,
      username: request.username,
      accountRecord: account,
    });
    if (!verified || verified.rookieShipVerified !== true) {
      throw fixedError("CREATION_VERIFY_FAILED", "Docker character creation was not persisted.");
    }

    shutdownAttempted = true;
    let cleanupConfirmed = false;
    try {
      const shutdown = await database.shutdown("launcher-docker-character-creation");
      cleanupConfirmed = Boolean(
        shutdown && shutdown.success === true && shutdown.released === true,
      );
    } catch (_shutdownError) {
      // Creation was flushed and independently verified before shutdown began.
      // EveJS fences all further GameStore writes once shutdown starts, so a
      // worker/release failure cannot be rolled back safely in this process.
      cleanupConfirmed = false;
    }
    if (restoreGuards) restoreGuards();
    return {
      ok: true,
      accountId,
      characterId,
      rookieShipVerified: true,
      backupCreated: true,
      backupName: safeBackupName(context),
      cleanupConfirmed,
      restartSafe: cleanupConfirmed,
    };
  } catch (_error) {
    let rollbackSucceeded = false;
    let restartSafe = false;
    const backupName = safeBackupName(context);
    const backupCreated = Boolean(
      backupName && context &&
        (context.backupCreated === true || context.created === true),
    );

    if (!shutdownAttempted && leaseAcquired) {
      if (mutationStarted && context) {
        try {
          await backupManager.rollback(context, database);
          await backupManager.verifyRestored(context);
          rollbackSucceeded = true;
        } catch (_rollbackError) {
          rollbackSucceeded = false;
        }
      } else {
        rollbackSucceeded = true;
      }
      try {
        shutdownAttempted = true;
        const shutdown = await database.shutdown(
          "launcher-docker-character-creation-rollback",
        );
        restartSafe = Boolean(
          rollbackSucceeded &&
            shutdown &&
            shutdown.success === true &&
            shutdown.released === true,
        );
      } catch (_shutdownError) {
        restartSafe = false;
      }
    }
    if (restoreGuards) restoreGuards();
    return {
      ok: false,
      error: "Docker character creation failed",
      code: "CHARACTER_CREATION_FAILED",
      backupCreated,
      ...(backupCreated ? { backupName } : {}),
      rollbackSucceeded,
      restartSafe,
    };
  }
}

async function main() {
  // Leave one sentinel byte so oversized input is rejected without ever
  // allocating or buffering more than the fixed protocol bound.
  const input = Buffer.allocUnsafe(MAX_INPUT_BYTES + 1);
  let length = 0;
  while (length < input.length) {
    const count = fs.readSync(0, input, length, input.length - length, null);
    if (count === 0) break;
    length += count;
    if (length > MAX_INPUT_BYTES) {
      throw fixedError("INVALID_REQUEST", "Invalid Docker character request.");
    }
  }
  const payload = parsePayloadBuffer(input.subarray(0, length));
  return executeDockerCharacterCreation(payload);
}

if (require.main === module) {
  main().then(
    (result) => exitWithTerminalResult(result, 0),
    (error) =>
      exitWithTerminalResult(
        {
          ok: false,
          error: "Docker character creation failed",
          code: "CHARACTER_CREATION_FAILED",
          backupCreated: false,
          rollbackSucceeded: false,
          restartSafe: false,
        },
        0,
      ),
  );
}

module.exports = {
  assertCreationLogicalContract,
  assertPersistenceOutboxEmpty,
  executeDockerCharacterCreation,
  expectedWelcomeMailBody,
  listFileDigests,
  logicalRowsDigest,
  parsePayloadBuffer,
  verifyRetainedBackup,
};
