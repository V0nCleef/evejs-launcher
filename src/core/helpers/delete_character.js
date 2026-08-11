"use strict";

// Runs only against an explicitly selected, offline Native game store. The
// Python launcher owns target validation, backup, rollback, process visibility,
// and post-operation verification. EveJS owns the actual character cleanup.
const fs = require("fs");
const path = require("path");

const RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT=";

function readPayload() {
  return JSON.parse(fs.readFileSync(0, "utf8"));
}

function escapePointer(value) {
  return String(value).replace(/~/g, "~0").replace(/\//g, "~1");
}

function emitResult(payload) {
  process.stdout.write(`${RESULT_PREFIX}${JSON.stringify(payload)}\n`);
}

function positiveInt(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && Math.trunc(numeric) > 0
    ? Math.trunc(numeric)
    : 0;
}

async function closeDatabase(database) {
  try {
    database.flushAllSync();
  } finally {
    await database._shutdownPersistenceWorkerForTests();
    database._closeSqliteForTests();
  }
}

async function main() {
  const payload = readPayload();
  const root = process.cwd();
  const serverSource = path.join(root, "server", "src");
  const database = require(path.join(serverSource, "gameStore"));
  try {
    const scope = String(payload.scope || "");
    const username = String(payload.username || "");
    const expectedAccountID = positiveInt(payload.accountId);
    const expectedCharacterID = positiveInt(payload.characterId);
    const expectedCharacterName = String(payload.characterName || "");
    if (!username || !expectedAccountID || !expectedCharacterID) {
      throw new Error("A verified account and character target are required.");
    }
    if (scope !== "character" && scope !== "account") {
      throw new Error("The deletion scope is invalid.");
    }

    const accountsResult = database.read("accounts", "/");
    const accounts = accountsResult.success && accountsResult.data
      ? accountsResult.data
      : {};
    const account = accounts[username];
    if (!account || positiveInt(account.id) !== expectedAccountID) {
      throw new Error("The selected account changed before deletion began.");
    }

    const charactersResult = database.read("characters", "/");
    const characters = charactersResult.success && charactersResult.data
      ? charactersResult.data
      : {};
    const selected = characters[String(expectedCharacterID)];
    if (
      !selected ||
      selected.isDeleted === true ||
      positiveInt(selected.accountId) !== expectedAccountID ||
      String(selected.characterName || "") !== expectedCharacterName
    ) {
      throw new Error("The selected character changed before deletion began.");
    }

    const accountCharacters = Object.entries(characters)
      .filter(([, record]) => (
        record &&
        record.isDeleted !== true &&
        positiveInt(record.accountId) === expectedAccountID
      ))
      .map(([characterID, record]) => ({
        characterID: positiveInt(characterID),
        characterName: String(record.characterName || ""),
      }))
      .filter((record) => record.characterID > 0)
      .sort((left, right) => left.characterID - right.characterID);

    if (scope === "character" && accountCharacters.length <= 1) {
      throw new Error(
        "This is the account's only character. Delete the account instead.",
      );
    }
    const targets = scope === "account"
      ? accountCharacters
      : accountCharacters.filter(
        (record) => record.characterID === expectedCharacterID,
      );
    if (targets.length === 0) {
      throw new Error("No active character matched the verified deletion target.");
    }

    const characterState = require(path.join(
      serverSource,
      "services",
      "character",
      "characterState",
    ));
    const {
      deleteCharacter,
    } = require(path.join(
      serverSource,
      "services",
      "character",
      "characterDeletionRuntime",
    ));

    for (const target of targets) {
      // The launcher already requires every client and EveJS service to be
      // offline, performs an explicit typed confirmation, and retains a full
      // affected-table backup. Marking the native biomass timer ready lets the
      // server's own comprehensive cleanup routine run immediately.
      const queued = characterState.updateCharacterRecord(
        target.characterID,
        (record) => ({
          ...record,
          deletePrepareDateTime: "1",
        }),
      );
      if (!queued || !queued.success) {
        throw new Error(
          `Unable to prepare ${target.characterName || target.characterID} for deletion.`,
        );
      }
      const deleted = deleteCharacter(target.characterID, expectedAccountID);
      if (!deleted || deleted.success !== true) {
        throw new Error(
          `EveJS did not confirm deletion of ${target.characterName || target.characterID}.`,
        );
      }
    }

    if (scope === "account") {
      const removed = database.remove(
        "accounts",
        `/${escapePointer(username)}`,
      );
      if (!removed || !removed.success) {
        throw new Error("The characters were deleted but the account could not be removed.");
      }
      const flushed = database.flushTablesSync(["accounts"]);
      if (!flushed || !flushed.success) {
        throw new Error("The deleted account could not be flushed safely to disk.");
      }
    }

    emitResult({
      ok: true,
      scope,
      username,
      accountId: expectedAccountID,
      accountDeleted: scope === "account",
      deletedCharacters: targets,
    });
  } finally {
    await closeDatabase(database);
  }
}

main().catch((error) => {
  emitResult({
    ok: false,
    error: String(error && error.message ? error.message : error),
  });
  process.exitCode = 1;
});
