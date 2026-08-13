"use strict";

// Runs only against an explicitly selected, offline Native game store. The
// Python launcher owns backup, rollback, process visibility, and verification.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const {
  failureResult,
  requireSuccess,
  runMaintenanceOperation,
} = require("./game_store_maintenance");
const {
  exitWithTerminalResult,
} = require("./terminal_result");

function readPayload() {
  return JSON.parse(fs.readFileSync(0, "utf8"));
}

function escapePointer(value) {
  return String(value).replace(/~/g, "~0").replace(/\//g, "~1");
}

async function main() {
  const payload = readPayload();
  const root = process.cwd();
  const serverSource = path.join(root, "server", "src");
  const database = require(path.join(serverSource, "gameStore"));
  const result = await runMaintenanceOperation(
    database,
    "launcher-character-creation",
    async () => {
    const username = String(payload.username || "").trim();
    const characterName = String(payload.characterName || "")
      .trim()
      .replace(/\s+/g, " ");
    const isGM = payload.isGM === true;
    const password = String(payload.password || "");
    if (!username || !characterName) {
      throw new Error("Account and character names are required.");
    }

    const accountsResult = database.read("accounts", "/");
    const accounts = accountsResult.success && accountsResult.data
      ? accountsResult.data
      : {};
    if (Object.prototype.hasOwnProperty.call(accounts, username)) {
      throw new Error(`An account named "${username}" already exists.`);
    }
    const charactersResult = database.read("characters", "/");
    const characters = charactersResult.success && charactersResult.data
      ? charactersResult.data
      : {};
    const duplicateCharacter = Object.values(characters).some(
      (entry) =>
        entry &&
        String(entry.characterName || "").toLocaleLowerCase() ===
          characterName.toLocaleLowerCase(),
    );
    if (duplicateCharacter) {
      throw new Error(`A character named "${characterName}" already exists.`);
    }

    const { reserveAccountID } = require(path.join(
      serverSource,
      "services",
      "_shared",
      "identityAllocator",
    ));
    const { buildPersistedAccountRoleRecord } = require(path.join(
      serverSource,
      "services",
      "account",
      "accountRoleProfiles",
    ));
    const accountId = reserveAccountID();
    const account = buildPersistedAccountRoleRecord({
      passwordhash: crypto.createHash("sha1").update(password, "utf8").digest("hex"),
      id: accountId,
      isGM,
      banned: false,
    });
    const accountWrite = database.write(
      "accounts",
      `/${escapePointer(username)}`,
      account,
      { force: true },
    );
    if (!accountWrite.success) {
      throw new Error(accountWrite.errorMsg || "Unable to persist the account.");
    }
    const accountFlush = database.flushTablesSync(["accounts"]);
    if (!accountFlush.success) {
      throw new Error("Unable to flush the new account.");
    }

    const CharService = require(path.join(
      serverSource,
      "services",
      "character",
      "charService",
    ));
    const service = new CharService();
    const characterId = service.Handle_CreateCharacterWithDoll(
      [characterName, 1, 1, 1, null, null, 11, 1],
      { userid: accountId },
    );
    requireSuccess(
      database.flushAllSync(),
      "Unable to flush the complete starter character",
    );

    const storedCharacters = database.read("characters", "/").data || {};
    const storedCharacter = storedCharacters[String(characterId)];
    const items = database.read("items", "/").data || {};
    const shipId = Number(storedCharacter && storedCharacter.shipID) || 0;
    const rookieShipVerified = Boolean(shipId && items[String(shipId)]);
    if (
      !storedCharacter ||
      Number(storedCharacter.accountId) !== accountId ||
      storedCharacter.characterName !== characterName ||
      !rookieShipVerified
    ) {
      throw new Error("EveJS did not persist the complete starter character.");
    }

    return {
      accountId,
      characterId: Number(characterId),
      isGM: account.isGM === true,
      rookieShipVerified,
    };
  });
  return { ok: true, ...result };
}

main().then(
  (result) => exitWithTerminalResult(result, 0),
  (error) => exitWithTerminalResult(failureResult(error), 1),
);
