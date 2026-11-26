"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { verifyRelease, assertSchema, assertIdle, readStore } = require("./shared_game_store");
const { runMaintenanceOperation } = require("./game_store_maintenance");
const { createCharacter } = require("./create_character");
const { deleteCharacterOrAccount } = require("./delete_character");
const { exitWithTerminalResult } = require("./terminal_result");

async function main() {
  const payload = JSON.parse(fs.readFileSync(0, "utf8"));
  const root = process.cwd();
  const workspace = process.env.EVEJS_DATA_ROOT;
  const store = path.join(workspace, "gameStore");
  const file = path.join(store, "gamestore.sqlite");
  if (!path.isAbsolute(workspace) || path.join(store, "data") !== process.env.EVEJS_GAMESTORE_DATA_DIR ||
      path.join(workspace, "launcher-character-maintenance") !== process.argv[2] ||
      !fs.lstatSync(process.argv[2]).isFile()) {
    throw new Error("EveJS character maintenance requires a detached launcher workspace.");
  }
  const handlers = { create: createCharacter, delete: deleteCharacterOrAccount };
  if (!Object.hasOwn(handlers, payload.operation)) throw new Error("Unsupported character maintenance operation.");
  const { Sqlite, legacyNames } = verifyRelease(root, { characterMaintenance: true });
  readStore(Sqlite, file, connection => {
    assertIdle(connection, assertSchema(connection, legacyNames));
    if (payload.operation === "delete") {
      const market = connection.prepare(`
        SELECT 1 FROM market_escrows AS escrow
        JOIN character_records AS pilot ON pilot.character_id IN
          (escrow.actor_character_id, escrow.return_owner_id, escrow.wallet_owner_id)
        WHERE pilot.account_id = ? AND (? OR pilot.character_id = ?) LIMIT 1
      `).get(payload.accountId, payload.scope === "account" ? 1 : 0, payload.characterId);
      if (market) throw new Error("Settle this character's market orders and escrow before deletion.");
    }
  });
  const source = path.join(root, "server", "src");
  const database = require(path.join(source, "gameStore"));
  const result = await runMaintenanceOperation(
    database, "launcher-shared-character-maintenance", () => {},
    () => handlers[payload.operation](database, source, payload),
  );
  readStore(Sqlite, file, connection => {
    assertIdle(connection, assertSchema(connection, legacyNames));
    if (connection.pragma("integrity_check(1)", { simple: true }) !== "ok" ||
        connection.pragma("foreign_key_check").length) {
      throw new Error("The updated game save failed integrity verification.");
    }
  });
  return { ok: true, ...result };
}

main().then(
  result => exitWithTerminalResult(result, 0),
  error => exitWithTerminalResult({ ok: false, error: String(error.message).slice(0, 300) }, 1),
);
