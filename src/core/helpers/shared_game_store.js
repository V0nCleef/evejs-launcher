"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const versionMetadata = new Map([
  ["261125.3", "b5d079c0ad77cac247c7ffeae2237d542c0562dccc43b6f45d05ade34e79558a"],
  ["261126.2", "c9bec0de76c0b9909335b965b5f8a15f5bdcfba65c7b1bb3346fb1e54497e773"],
]);
const applicationId = 0x45564744;
const schemaVersion = 35;
const gameStore = "server/src/gameStore";
const definitions = Object.freeze({
  [gameStore + "/index.js"]: "359c8ce0dd2e6d0d8e0b9021f100567d6dcef6f33a1dc825cd5a61478be9f6bd",
  [gameStore + "/sqliteStore.js"]: "084f3d48c3e67884089305dd945baa68f4bdc6f323f42a3ded67fb61207f9fb4",
  [gameStore + "/storeRoot.js"]: "b1cff2aa081472d6b317ea243c14ba0ab10500d35408bac7a8b2817edde4cb2a",
  [gameStore + "/persistenceWorker.js"]: "50eba43727c8e16fd2e694da45640f17978c128f531c84160f3cfd0499129766",
  [gameStore + "/persistenceWorker.worker.js"]: "718a165ba709078e812714ec687c896d1a42222bacfdd91f98e5bcd9180df7e2",
  [gameStore + "/persistenceRoles.js"]: "906e930bb399c8cfb88f44851d738f715f67d6859825be4692ae2a0704da2d36",
  [gameStore + "/persistenceLeasePolicy.js"]: "4c9c8731c4ede6e19188c6c19fb405937d283716423b2351796edebe2c0a6f4d",
  [gameStore + "/tableOwnership.js"]: "e2f531ee050b8931b3e5a0ef294dd7d1d3533375d0bc5c098288e154aa4eabb0",
  "server/src/services/character/characterNameRuntime.js": "3751ca932f8f391e5758b17c8c47c793547dd4f12ae1ad476c8d8622cad3c715",
  "server/src/config/dataRoot.js": "d5b428a8554a5295267bcdd4ec36a695024aaf7b8311bd416c82f6837d7fac37",
});
const schemaSources = "7786e2566dd50ce969bd3ac90b010c6a560fa2dda0180ba35c3bb9c7196829df";
const schemaSql = "01230834449e5e2c1fa72917348bfdbe15dc405fd1015d152cd41ab45c3ca5de";
const runtimeTables = Object.freeze({
  _migrations: [
    "0b0bf94684f715006e2888df96041c8a3f05cabeda0439312b23063b50a49a9b",
    "292813840720caa7bf316f52b289ee766d229c6a60df07ce0e038ce8c08aa194",
  ],
  _persistence_owners: ["2520f76f9f6340bc5e3348848c9623fc576b43f5ee433d23ad09067efe2b04ef"],
  _persistence_outbox: ["8020e87d5a333cf7c54fa81d556d92f200190f31716b8f86cc6af44a89657020"],
});
const characterOperations = Object.freeze({
  "server/src/services/character/charService.js": "3ae19b17892fedbcc3aae210da011791a35636af8390b530972e92ef275d7183",
  "server/src/services/character/characterState.js": "52ebd8fc5c607a3149f62582e1b04ab039465d287a17d0d40209302216d42192",
  "server/src/services/character/characterDeletionRuntime.js": "c5d4469f5a0e64d4bde47c7f21a8d2f62006ec65e84ffa1950d054f462aa7f00",
  "server/src/services/_shared/identityAllocator.js": "1004a37500e5657bec50043d223923e84df08a09480808a026bd60745331269b",
  "server/src/services/account/accountRoleProfiles.js": "6dba306c68c6f7c0c780f9f55b0663cb8716150455b0a6a803cf3a4d8102a67f",
});

function problem(code, message) {
  return Object.assign(new Error(message), { sharedStoreCode: code });
}

function digest(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function ordinary(file, directory = false) {
  const info = fs.lstatSync(file);
  const real = fs.realpathSync.native(file);
  const expected = path.resolve(file);
  const equal = process.platform === "win32" ? real.toLowerCase() === expected.toLowerCase() : real === expected;
  if (!equal || (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1)) {
    throw problem("UNSUPPORTED_LAYOUT", "A shared GameStore path is redirected.");
  }
  return info;
}

function sourceText(file) {
  if (ordinary(file).size > 4 * 1024 * 1024) {
    throw problem("UNSUPPORTED_RELEASE", "The shared GameStore definitions could not be verified.");
  }
  return fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
}

function supportsRelease(version) {
  return versionMetadata.has(version);
}

function verifyRelease(root, { characterMaintenance = false } = {}) {
  const pkg = JSON.parse(sourceText(path.join(root, "server/package.json")));
  const metadataHash = versionMetadata.get(pkg.version);
  if (!metadataHash || digest(sourceText(path.join(root, "config/version.json"))) !== metadataHash) {
    throw problem("UNSUPPORTED_RELEASE", "The shared GameStore release metadata could not be verified.");
  }
  const expected = characterMaintenance ? { ...definitions, ...characterOperations } : definitions;
  for (const [relative, hash] of Object.entries(expected)) {
    if (digest(sourceText(path.join(root, relative))) !== hash) {
      throw problem("UNSUPPORTED_RELEASE", `The shared GameStore definitions differ from EveJS ${pkg.version}.`);
    }
  }
  const schemaPath = path.join(root, gameStore, "shared-schema");
  ordinary(schemaPath, true);
  const members = fs.readdirSync(schemaPath).sort();
  const hashes = Object.fromEntries(members.map(name => [name, digest(sourceText(path.join(schemaPath, name)))]));
  if (digest(JSON.stringify(hashes)) !== schemaSources) {
    throw problem("UNSUPPORTED_RELEASE", `The shared GameStore schema code differs from EveJS ${pkg.version}.`);
  }
  const sqlite = path.join(root, "server/node_modules/better-sqlite3");
  const dependency = JSON.parse(sourceText(path.join(sqlite, "package.json")));
  if (pkg.name !== "eve.js" || pkg.type !== "commonjs" ||
      pkg.dependencies?.["better-sqlite3"] !== "^13.2.0" ||
      dependency.name !== "better-sqlite3" || dependency.version !== "13.2.0") {
    throw problem("UNSUPPORTED_RELEASE", "The reviewed shared GameStore dependencies could not be verified.");
  }
  const source = sourceText(path.join(root, gameStore, "index.js"));
  const tableDefinition = /const SQLITE_TABLES = new Set\(\[([\s\S]*?)\]\);/.exec(source)[1];
  const legacyNames = new Set([...tableDefinition.matchAll(/^\s*"([A-Za-z0-9_]+)",/gm)].map(m => m[1]));
  // Stock conversion keeps retired JSON tables from older saves. The exact
  // table shape is still checked below, and related triggers remain disallowed.
  legacyNames.add("industrialHirelingContracts");
  return { Sqlite: require(sqlite), legacyNames };
}

function assertSchema(connection, legacyNames) {
  const invalid = () => problem("UNSUPPORTED_LAYOUT", "The save is not a verified EveJS schema-35 database.");
  if (connection.pragma("application_id", { simple: true }) !== applicationId ||
      connection.pragma("user_version", { simple: true }) !== schemaVersion) {
    throw invalid();
  }
  const rows = connection.prepare(
    "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'",
  ).raw().all();
  // The schema's trigger SQL is large enough to spill SQLite's ORDER BY into
  // Docker's small temporary filesystem. Sort the already-read rows in memory.
  rows.sort(([type, name], [otherType, otherName]) => {
    if (type !== otherType) return type < otherType ? -1 : 1;
    return name < otherName ? -1 : name > otherName ? 1 : 0;
  });
  const core = [];
  let hasMigrations = false;
  for (const row of rows) {
    const [type, name, table, sql] = row;
    if (Object.hasOwn(runtimeTables, name)) {
      if (!runtimeTables[name].includes(digest(JSON.stringify(row)))) throw invalid();
      if (name === "_migrations") hasMigrations = true;
    } else if (type !== "table" || table !== name || !legacyNames.has(name) ||
        sql !== `CREATE TABLE "${name}" (key TEXT PRIMARY KEY, json TEXT NOT NULL)`) {
      core.push(row);
    }
  }
  if (!hasMigrations || digest(JSON.stringify(core)) !== schemaSql) throw invalid();
  return new Set(rows.filter(row => row[0] === "table").map(row => row[1]));
}

function readStore(Sqlite, file, inspect) {
  ordinary(file);
  const connection = new Sqlite(file, { readonly: true, fileMustExist: true, timeout: 1500 });
  try {
    return inspect(connection);
  } finally {
    connection.close();
  }
}

function assertIdle(connection, tables) {
  if (tables.has("_persistence_outbox") && connection.prepare("SELECT 1 FROM _persistence_outbox LIMIT 1").get()) {
    throw problem("PENDING_WRITES", "The game save has unfinished writes. Start and cleanly stop Game before retrying.");
  }
  if (tables.has("_persistence_owners") && connection.prepare(
    "SELECT 1 FROM _persistence_owners WHERE active=1 AND lease_expires_at>? LIMIT 1",
  ).get(Date.now())) {
    throw problem("OWNER_BUSY", "Game still owns this save. Stop it and let its last write lease expire.");
  }
}

function reviewTransfer(root, file, market, freshMarket) {
  const { Sqlite, legacyNames } = verifyRelease(root);
  if (!file) return; // An unused destination may have its runtime ready and no saved database.
  readStore(Sqlite, file, connection => {
    const tables = assertSchema(connection, legacyNames);
    assertIdle(connection, tables);
    if (!market) return;
    if (freshMarket && (
      connection.prepare("SELECT 1 FROM market_escrows LIMIT 1").get() ||
      connection.prepare("SELECT 1 FROM market_event_cursors WHERE event_id<>0 LIMIT 1").get() ||
      connection.prepare("SELECT 1 FROM inventory_items WHERE location_id>9200000000 AND location_id<9300000000 LIMIT 1").get()
    )) {
      throw problem("MARKET_PAIR", "This save has market activity tied to its market database. Choose Bring market.");
    }
    ordinary(market);
    try {
      require(path.join(root, gameStore, "shared-schema/verifyPairedMarket")).verifyPairedMarket(connection, market);
    } catch (_) {
      throw problem("MARKET_PAIR", "The market database does not match the game save's orders, escrow, or event progress.");
    }
  });
}

module.exports = { supportsRelease, verifyRelease, assertSchema, assertIdle, readStore, reviewTransfer };
