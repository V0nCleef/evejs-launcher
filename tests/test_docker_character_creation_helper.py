"""Daemonless Node-level tests for transactional Docker character creation."""
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
DOCKER_HELPER = HELPERS / "docker_create_character.js"
TERMINAL_HELPER = HELPERS / "terminal_result.js"
RESULT_PREFIX = "EVEJS_LAUNCHER_RESULT="


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


def _transaction_probe(tmp_path: Path, mode: str) -> dict:
    source = r"""
        "use strict";

        const crypto = require("crypto");
        const fs = require("fs");
        const path = require("path");
        const helper = require(__DOCKER_HELPER__);
        const mode = __MODE__;
        const tables = [
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
        const clone = (value) => JSON.parse(JSON.stringify(value));
        const initial = {
          accounts: {
            "legacy-account": { id: 7, role: "player", marker: "before:accounts" },
          },
          alliances: {
            "8001": { allianceID: 8001, marker: "before:alliances" },
          },
          characters: {
            "9001": { characterID: 9001, characterName: "Legacy Pilot" },
            "140000004": { characterID: 140000004, characterName: "GM ELYSIAN" },
          },
          corporations: {
            "7001": { corporationID: 7001, marker: "before:corporations" },
          },
          identityState: {
            version: 1,
            nextAccountID: 101,
            nextCharacterID: 202,
            nextItemID: 303,
          },
          items: {
            "6001": { itemID: 6001, ownerID: 9001, marker: "before:items" },
          },
          skills: {
            "9001": { starterSkill: 1, marker: "before:skills" },
          },
          mail: {
            _meta: { nextMessageID: 400, nextMailingListID: 12 },
            messages: { "399": { messageID: 399, marker: "before:mail" } },
            mailboxes: { "9001": { marker: "before:mailbox" } },
            mailingLists: { "11": { marker: "before:mailingLists" } },
          },
          notifications: {
            _meta: { nextNotificationID: 500 },
            boxes: { "9001": { marker: "before:notifications" } },
          },
        };
        const state = clone(initial);
        const events = [];
        let pendingOutbox = 0;

        const outboxConnection = {
          prepare(statement) {
            if (statement !== "SELECT COUNT(*) AS count FROM _persistence_outbox") {
              throw new Error("unexpected synthetic outbox query");
            }
            return {
              get() {
                events.push(`verify-outbox:${pendingOutbox}`);
                return { count: pendingOutbox };
              },
            };
          },
        };

        function pointerKey(pointer) {
          return String(pointer).slice(1).replace(/~1/g, "/").replace(/~0/g, "~");
        }

        const database = {
          acquirePersistenceOwnerLease(options) {
            events.push(`acquire:${options && options.recover === true}`);
            return { success: true, acquired: true };
          },
          renewPersistenceOwnerLease() {
            events.push("renew-lease");
            return { success: true, renewed: true };
          },
          ensureTable(table) {
            events.push(`ensure:${table}`);
            if (!Object.prototype.hasOwnProperty.call(state, table)) {
              state[table] = {};
            }
            return { success: true };
          },
          read(table) {
            events.push(`read:${table}`);
            return { success: true, data: clone(state[table] || {}) };
          },
          write(table, pointer, value) {
            events.push(`write:${table}`);
            if (pointer === "/") {
              state[table] = clone(value);
            } else {
              state[table][pointerKey(pointer)] = clone(value);
            }
            return { success: true };
          },
          remove(table, pointer) {
            events.push(`remove:${table}`);
            delete state[table][pointerKey(pointer)];
            return { success: true };
          },
          flushTablesSync(selected) {
            events.push(`flush:${selected.join(",")}`);
            return { success: true };
          },
          flushAllSync() {
            events.push("flush-all");
            return { success: true };
          },
          async shutdown(reason) {
            events.push(`shutdown:${reason}`);
            if (
              mode === "cleanup-failure" ||
              mode === "verified-commit-cleanup-failure"
            ) {
              return {
                success: false,
                released: false,
                errorMsg: "PRIVATE_CLEANUP_SENTINEL",
              };
            }
            return { success: true, released: true };
          },
        };

        const retainedRoot = path.join(process.cwd(), "retained-root");
        const sha256 = (filePath) => crypto
          .createHash("sha256")
          .update(fs.readFileSync(filePath))
          .digest("hex");
        let backupContext = null;

        function createRetainedBackup(preimage) {
          fs.mkdirSync(retainedRoot);
          const backupName = "fixture-backup";
          const backupPath = path.join(retainedRoot, backupName);
          const backupData = path.join(backupPath, "data");
          const backupStoreFiles = path.join(backupPath, "store-files");
          const backupTables = path.join(backupPath, "sqlite-tables.json");
          const metadataPath = path.join(backupPath, "metadata.json");
          fs.mkdirSync(backupPath);
          fs.mkdirSync(backupData);
          fs.mkdirSync(backupStoreFiles);

          const logicalRows = Object.fromEntries(
            tables.map((table) => [table, [["root", clone(preimage[table])]]]),
          );
          const sqliteDigests = Object.fromEntries(
            tables.map((table) => [table, helper.logicalRowsDigest(logicalRows[table])]),
          );
          fs.writeFileSync(
            backupTables,
            JSON.stringify({ version: 1, tables: logicalRows }),
          );

          const accountsData = path.join(backupData, "accounts");
          fs.mkdirSync(accountsData);
          fs.writeFileSync(path.join(accountsData, "preimage.json"), "fixture-data");
          const scopedDataDirectories = Object.fromEntries(
            tables.map((table) => [table, table === "accounts"]),
          );
          const scopedDataDigests = Object.fromEntries(
            tables.map((table) => [
              table,
              table === "accounts" ? helper.listFileDigests(accountsData) : {},
            ]),
          );

          const manifestPath = path.join(backupStoreFiles, "manifest.json");
          fs.writeFileSync(manifestPath, '{"version":1}');
          const metadata = {
            version: 1,
            backupName,
            sqliteDigests,
            fileDigests: {},
            manifestDigests: { "manifest.json": sha256(manifestPath) },
            scopedDataDirectories,
            scopedDataDigests,
          };
          fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2));
          const context = {
            backupName,
            name: backupName,
            backupCreated: true,
            created: true,
            backupPath,
            backupTables,
            backupData,
            backupStoreFiles,
            metadataPath,
            metadataSha256: sha256(metadataPath),
            metadata,
            preimage: clone(preimage),
            touchedTables: new Set(),
          };
          helper.verifyRetainedBackup(context, retainedRoot);
          return context;
        }

        function corruptRetainedBackup() {
          if (mode === "retained-delete-metadata") {
            fs.unlinkSync(backupContext.metadataPath);
          } else if (mode === "retained-corrupt-sqlite") {
            fs.appendFileSync(backupContext.backupTables, "corrupt");
          } else if (mode === "retained-delete-data") {
            fs.unlinkSync(path.join(backupContext.backupData, "accounts", "preimage.json"));
          } else if (mode === "retained-corrupt-manifest") {
            fs.appendFileSync(
              path.join(backupContext.backupStoreFiles, "manifest.json"),
              "corrupt",
            );
          }
        }

        const backupManager = {
          async create() {
            helper.assertPersistenceOutboxEmpty(outboxConnection);
            events.push("backup");
            backupContext = createRetainedBackup(state);
            return backupContext;
          },
          beforeMutation(context, table) {
            events.push(`before:${table}`);
            context.touchedTables.add(table);
          },
          async rollback(context, receivedDatabase) {
            events.push("rollback");
            if (receivedDatabase !== database) {
              throw new Error("rollback received the wrong database");
            }
            helper.verifyRetainedBackup(context, retainedRoot);
            for (const key of Object.keys(state)) {
              delete state[key];
            }
            Object.assign(state, clone(context.preimage));
            return { success: true };
          },
          verifyRestored(context) {
            events.push("verify-restored");
            helper.verifyRetainedBackup(context, retainedRoot);
            helper.assertPersistenceOutboxEmpty(outboxConnection);
            if (JSON.stringify(state) !== JSON.stringify(context.preimage)) {
              throw new Error("synthetic rollback verification failed");
            }
            return { success: true };
          },
          verifyCreation(context, expected) {
            events.push("verify-creation");
            helper.verifyRetainedBackup(context, retainedRoot);
            helper.assertPersistenceOutboxEmpty(outboxConnection);
            const account = state.accounts["fixture-account"];
            const character = state.characters[String(expected.characterId)];
            const ship = character && state.items[String(character.shipID)];
            if (
              !account ||
              Number(account.id) !== Number(expected.accountId) ||
              !character ||
              character.characterName !== "Fixture Pilot" ||
              !ship
            ) {
              throw new Error("synthetic creation verification failed");
            }
            helper.assertCreationLogicalContract(context.preimage, state, {
              ...expected,
              shipId: character.shipID,
              stationId: character.stationID,
            });
            return { success: true, rookieShipVerified: true };
          },
        };

        function loadCreationDependencies() {
          events.push("load-dependencies");
          return {
            reserveAccountID() {
              events.push("reserve-account-id");
              database.write(
                "identityState",
                "/",
                { ...state.identityState, nextAccountID: 102 },
                { force: true },
              );
              return 101;
            },
            buildPersistedAccountRoleRecord(record) {
              events.push("build-account");
              return {
                passwordhash: record.passwordhash,
                id: record.id,
                isGM: record.isGM,
                banned: record.banned,
                role: "player",
                chatRole: "player",
              };
            },
            CharService: class SyntheticCharService {
              Handle_CreateCharacterWithDoll(args, session) {
                events.push("create-character");
                const characterId = 202;
                const shipId = 303;
                const moduleId = 304;
                const stationId = 60003760;
                const sentDate = "2026-08-13T12:00:00.000Z";
                const senderId = 140000004;
                const senderName = "GM ELYSIAN";
                const title = "Welcome to EveJS Elysian";
                database.write(
                  "characters",
                  `/${characterId}`,
                  {
                    accountId: session.userid,
                    characterName: args[0],
                    shipID: shipId,
                    shipTypeID: 606,
                    stationID: stationId,
                  },
                  { force: true },
                );
                database.write(
                  "items",
                  `/${shipId}`,
                  {
                    itemID: shipId,
                    ownerID: characterId,
                    typeID: 606,
                    locationID: stationId,
                    flagID: 4,
                    singleton: 1,
                    groupID: 237,
                    categoryID: 6,
                  },
                  { force: true },
                );
                database.write(
                  "items",
                  `/${moduleId}`,
                  {
                    itemID: moduleId,
                    ownerID: characterId,
                    typeID: 100,
                    locationID: shipId,
                    flagID: 11,
                    singleton: 0,
                    groupID: 1,
                    categoryID: 7,
                  },
                  { force: true },
                );
                database.write(
                  "identityState",
                  "/",
                  {
                    ...state.identityState,
                    nextCharacterID: characterId + 1,
                    nextItemID: moduleId + 1,
                  },
                  { force: true },
                );
                const messageId = state.mail._meta.nextMessageID;
                const message = {
                  messageID: messageId,
                  senderID: senderId,
                  toCharacterIDs: [characterId],
                  toListID: null,
                  toCorpOrAllianceID: null,
                  title,
                  body: helper.expectedWelcomeMailBody(args[0]),
                  sentDate,
                  createdAt: sentDate,
                };
                database.write(
                  "mail",
                  "/",
                  {
                    ...state.mail,
                    _meta: { ...state.mail._meta, nextMessageID: messageId + 1 },
                    messages: { ...state.mail.messages, [messageId]: message },
                    mailboxes: {
                      ...state.mail.mailboxes,
                      [characterId]: {
                        statuses: { [messageId]: { statusMask: 0, labelMask: 1 } },
                        labels: {},
                        _meta: { nextLabelMask: 16 },
                      },
                    },
                  },
                  { force: true },
                );
                const notificationId = state.notifications._meta.nextNotificationID;
                const notification = {
                  notificationID: notificationId,
                  typeID: 1004,
                  senderID: senderId,
                  receiverID: characterId,
                  processed: false,
                  created: sentDate,
                  groupID: 4,
                  data: {
                    senderName,
                    subject: title,
                    msg: {
                      messageID: messageId,
                      senderID: senderId,
                      senderName,
                      sentDate,
                      toCharacterIDs: [characterId],
                      toListID: null,
                      toCorpOrAllianceID: null,
                      subject: title,
                      statusMask: 0,
                      labelMask: 1,
                      read: false,
                      trashed: false,
                      replied: false,
                      forwarded: false,
                    },
                  },
                };
                database.write(
                  "notifications",
                  "/",
                  {
                    _meta: { nextNotificationID: notificationId + 1 },
                    boxes: {
                      ...state.notifications.boxes,
                      [characterId]: {
                        byID: { [notificationId]: notification },
                        order: [notificationId],
                      },
                    },
                  },
                  { force: true },
                );
                if (
                  mode === "operation-failure-outbox" ||
                  mode === "verified-commit-outbox"
                ) {
                  pendingOutbox = 1;
                }
                const preexistingMatch = mode.match(
                  /^preexisting-(delete|modify)-(accounts|alliances|characters|corporations|identityState|items|skills|mail|notifications)$/,
                );
                if (preexistingMatch) {
                  const operation = preexistingMatch[1];
                  const table = preexistingMatch[2];
                  const targets = {
                    accounts: [state.accounts, "legacy-account"],
                    alliances: [state.alliances, "8001"],
                    characters: [state.characters, "9001"],
                    corporations: [state.corporations, "7001"],
                    items: [state.items, "6001"],
                    skills: [state.skills, "9001"],
                    mail: [state.mail.messages, "399"],
                    notifications: [state.notifications.boxes, "9001"],
                  };
                  if (table === "identityState") {
                    if (operation === "delete") delete state.identityState.version;
                    else state.identityState.version = 2;
                  } else {
                    const [container, key] = targets[table];
                    if (operation === "delete") delete container[key];
                    else container[key] = { ...container[key], unexpected: "changed" };
                  }
                }
                if (mode === "contract-new-item-below-watermark") {
                  state.items["302"] = {
                    itemID: 302,
                    ownerID: characterId,
                    locationID: shipId,
                  };
                } else if (mode === "contract-item-orphan") {
                  state.items[String(moduleId)].locationID = 999999;
                } else if (mode === "contract-mail-delta") {
                  state.mail.messages[String(messageId)].title = "unexpected";
                } else if (mode === "contract-notification-delta") {
                  state.notifications.boxes[String(characterId)]
                    .byID[String(notificationId)].typeID = 1;
                } else if (mode === "contract-skills-delta") {
                  state.skills[String(characterId)] = { unexpected: true };
                }
                if (mode.startsWith("retained-")) corruptRetainedBackup();
                const reachesVerification =
                  mode === "success" ||
                  mode === "verified-commit-cleanup-failure" ||
                  mode === "verified-commit-outbox" ||
                  mode.startsWith("preexisting-") ||
                  mode.startsWith("retained-") ||
                  mode.startsWith("contract-");
                if (
                  !reachesVerification
                ) {
                  throw new Error(
                    "fixture-account Fixture Pilot PRIVATE_PASSWORD_SENTINEL",
                  );
                }
                return characterId;
              }
            },
          };
        }

        (async () => {
          const result = await helper.executeDockerCharacterCreation(
            {
              characterName: "Fixture Pilot",
              isGM: false,
              password: "PRIVATE_PASSWORD_SENTINEL",
              username: "fixture-account",
            },
            { database, backupManager, loadCreationDependencies },
          );
          process.stdout.write(JSON.stringify({
            result,
            events,
            touched: backupContext
              ? [...backupContext.touchedTables].sort()
              : [],
            stateMatchesInitial:
              JSON.stringify(state) === JSON.stringify(initial),
          }));
        })().catch((error) => {
          process.stderr.write(error.stack || String(error));
          process.exitCode = 1;
        });
    """
    source = source.replace("__DOCKER_HELPER__", json.dumps(str(DOCKER_HELPER)))
    source = source.replace("__MODE__", json.dumps(mode))
    completed = _run_script(tmp_path, source)
    assert completed.returncode == 0, completed.stderr
    return _last_json(completed.stdout)


def test_payload_parser_is_bounded_strict_and_private_safe(tmp_path: Path) -> None:
    source = r"""
        "use strict";
        const helper = require(__DOCKER_HELPER__);
        const privateValues = [
          "PRIVATE_ACCOUNT_SENTINEL",
          "PRIVATE_CHARACTER_SENTINEL",
          "PRIVATE_PASSWORD_SENTINEL",
          "PRIVATE_EXTRA_SENTINEL",
        ];
        const canonical = Buffer.from(JSON.stringify({
          characterName: "Fixture Pilot",
          isGM: false,
          password: "fixture-password",
          username: "fixture-account",
        }), "utf8");
        const accepted = helper.parsePayloadBuffer(canonical);
        const rejected = [
          Buffer.from(JSON.stringify({
            characterName: "PRIVATE_CHARACTER_SENTINEL",
            isGM: false,
            password: "PRIVATE_PASSWORD_SENTINEL",
            username: "PRIVATE_ACCOUNT_SENTINEL",
            PRIVATE_EXTRA_SENTINEL: true,
          }), "utf8"),
          Buffer.from(JSON.stringify({
            characterName: "Fixture Pilot",
            isGM: "PRIVATE_CHARACTER_SENTINEL",
            password: "fixture-password",
            username: "fixture-account",
          }), "utf8"),
          Buffer.from(JSON.stringify([
            "PRIVATE_ACCOUNT_SENTINEL",
            "PRIVATE_CHARACTER_SENTINEL",
          ]), "utf8"),
          Buffer.from("{PRIVATE_ACCOUNT_SENTINEL", "utf8"),
          Buffer.alloc((16 * 1024) + 1, "x"),
        ];
        const failures = rejected.map((payload) => {
          try {
            helper.parsePayloadBuffer(payload);
            return { rejected: false, message: "" };
          } catch (error) {
            return {
              rejected: true,
              code: String(error && error.code || ""),
              message: String(error && error.message || error),
            };
          }
        });
        process.stdout.write(JSON.stringify({ accepted, failures, privateValues }));
    """
    source = source.replace("__DOCKER_HELPER__", json.dumps(str(DOCKER_HELPER)))
    completed = _run_script(tmp_path, source)

    assert completed.returncode == 0, completed.stderr
    report = _last_json(completed.stdout)
    assert report["accepted"] == {
        "characterName": "Fixture Pilot",
        "isGM": False,
        "password": "fixture-password",
        "username": "fixture-account",
    }
    assert all(failure["rejected"] for failure in report["failures"])
    diagnostic_text = json.dumps(report["failures"])
    for private_value in report["privateValues"]:
        assert private_value not in diagnostic_text


def test_production_contract_attests_before_import_and_keeps_backup_scoped() -> None:
    source = DOCKER_HELPER.read_text(encoding="utf-8")

    attestation = "const serverSource = attestProductionImage(root);"
    game_store_import = (
        'const database = require(path.join(serverSource, "gameStore", "index.js"));'
    )
    assert source.index(attestation) < source.index(game_store_import)
    assert "sqlite-tables.json" in source
    assert "source.backup(" not in source
    assert "copyTreeStrict(dataRoot, backupData)" not in source
    assert (
        "dc474d40f02e64db715361630c1a48ac3e3de30ed77ae300d2ffd82815178421"
        in source
    )


def test_success_orders_lease_backup_mutation_shutdown_and_verification(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "success")
    result = report["result"]
    events = report["events"]

    assert result == {
        "ok": True,
        "accountId": 101,
        "characterId": 202,
        "rookieShipVerified": True,
        "backupCreated": True,
        "backupName": "fixture-backup",
        "cleanupConfirmed": True,
        "restartSafe": True,
    }
    assert events.count("verify-outbox:0") == 2
    assert events.index("verify-outbox:0") < events.index("backup")
    first_write = next(index for index, event in enumerate(events) if event.startswith("write:"))
    assert events.index("acquire:false") < events.index("backup") < first_write
    assert events.index("backup") < events.index("load-dependencies")
    for table in (
        "accounts",
            "characters",
            "identityState",
            "items",
            "mail",
        "notifications",
    ):
        assert events.index(f"before:{table}") < events.index(f"write:{table}")
    shutdown_index = next(
        index for index, event in enumerate(events) if event.startswith("shutdown:")
    )
    last_write = max(
        index for index, event in enumerate(events) if event.startswith("write:")
    )
    assert last_write < events.index("verify-creation") < shutdown_index
    assert "rollback" not in events
    assert report["stateMatchesInitial"] is False
    serialized = json.dumps(result)
    assert "fixture-account" not in serialized
    assert "Fixture Pilot" not in serialized
    assert "PRIVATE_PASSWORD_SENTINEL" not in serialized


def test_verified_commit_survives_unconfirmed_final_cleanup_without_retry(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "verified-commit-cleanup-failure")
    result = report["result"]
    events = report["events"]

    assert result == {
        "ok": True,
        "accountId": 101,
        "characterId": 202,
        "rookieShipVerified": True,
        "backupCreated": True,
        "backupName": "fixture-backup",
        "cleanupConfirmed": False,
        "restartSafe": False,
    }
    assert report["stateMatchesInitial"] is False
    assert "verify-creation" in events
    assert "rollback" not in events
    shutdown_index = next(
        index for index, event in enumerate(events) if event.startswith("shutdown:")
    )
    assert events.index("verify-creation") < shutdown_index
    serialized = json.dumps(result)
    assert "fixture-account" not in serialized
    assert "Fixture Pilot" not in serialized
    assert "PRIVATE_PASSWORD_SENTINEL" not in serialized
    assert "PRIVATE_CLEANUP_SENTINEL" not in serialized


def test_partial_mutation_restores_every_touched_table_before_release(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "operation-failure")
    result = report["result"]
    events = report["events"]

    assert result["ok"] is False
    assert result["backupCreated"] is True
    assert result["backupName"] == "fixture-backup"
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert set(report["touched"]) == {
        "accounts",
        "characters",
        "identityState",
        "items",
        "mail",
        "notifications",
    }
    shutdown_index = next(
        index for index, event in enumerate(events) if event.startswith("shutdown:")
    )
    assert events.index("rollback") < events.index("verify-restored") < shutdown_index
    serialized = json.dumps(result)
    assert "fixture-account" not in serialized
    assert "Fixture Pilot" not in serialized
    assert "PRIVATE_PASSWORD_SENTINEL" not in serialized


def test_pending_outbox_after_partial_failure_blocks_verified_rollback(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "operation-failure-outbox")
    result = report["result"]
    events = report["events"]

    assert result["ok"] is False
    assert result["backupCreated"] is True
    assert result["rollbackSucceeded"] is False
    assert result["restartSafe"] is False
    assert report["stateMatchesInitial"] is True
    assert events.count("verify-outbox:0") == 1
    assert events.count("verify-outbox:1") == 1
    assert events.index("rollback") < events.index("verify-restored")
    assert events.index("verify-restored") < events.index("verify-outbox:1")


def test_pending_outbox_invalidates_success_verification_and_rollback(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "verified-commit-outbox")
    result = report["result"]
    events = report["events"]

    assert result["ok"] is False
    assert result["backupCreated"] is True
    assert result["rollbackSucceeded"] is False
    assert result["restartSafe"] is False
    assert report["stateMatchesInitial"] is True
    assert events.count("verify-outbox:1") == 2
    assert events.index("verify-creation") < events.index("rollback")
    assert events.index("rollback") < events.index("verify-restored")


@pytest.mark.parametrize(
    "mode",
    [
        "retained-delete-metadata",
        "retained-corrupt-sqlite",
        "retained-delete-data",
        "retained-corrupt-manifest",
    ],
)
def test_corrupt_retained_artifact_blocks_commit_and_verified_rollback(
    tmp_path: Path,
    mode: str,
) -> None:
    report = _transaction_probe(tmp_path, mode)
    result = report["result"]

    assert result["ok"] is False
    assert result["backupCreated"] is True
    assert result["rollbackSucceeded"] is False
    assert result["restartSafe"] is False
    assert report["stateMatchesInitial"] is False
    assert "verify-creation" in report["events"]
    assert "rollback" in report["events"]
    assert "verify-restored" not in report["events"]


@pytest.mark.parametrize(
    "table",
    [
        "accounts",
        "alliances",
        "characters",
        "corporations",
        "identityState",
        "items",
        "skills",
        "mail",
        "notifications",
    ],
)
@pytest.mark.parametrize("operation", ["delete", "modify"])
def test_preexisting_logical_content_change_rejects_success_and_rolls_back(
    tmp_path: Path,
    table: str,
    operation: str,
) -> None:
    report = _transaction_probe(tmp_path, f"preexisting-{operation}-{table}")
    result = report["result"]

    assert result["ok"] is False
    assert result["backupCreated"] is True
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert report["events"].index("verify-creation") < report["events"].index("rollback")
    assert report["events"].index("rollback") < report["events"].index("verify-restored")


@pytest.mark.parametrize(
    "mode",
    [
        "contract-new-item-below-watermark",
        "contract-item-orphan",
        "contract-mail-delta",
        "contract-notification-delta",
        "contract-skills-delta",
    ],
)
def test_unattributable_new_content_rejects_success_and_rolls_back(
    tmp_path: Path,
    mode: str,
) -> None:
    report = _transaction_probe(tmp_path, mode)
    result = report["result"]

    assert result["ok"] is False
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True


def test_cleanup_failure_never_claims_restart_safety_or_leaks_details(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "cleanup-failure")
    result = report["result"]
    events = report["events"]

    assert result["ok"] is False
    assert isinstance(result["rollbackSucceeded"], bool)
    assert result["restartSafe"] is False
    assert report["stateMatchesInitial"] is True
    shutdown_index = next(
        index for index, event in enumerate(events) if event.startswith("shutdown:")
    )
    assert events.index("rollback") < shutdown_index
    serialized = json.dumps(result)
    assert "fixture-account" not in serialized
    assert "Fixture Pilot" not in serialized
    assert "PRIVATE_PASSWORD_SENTINEL" not in serialized
    assert "PRIVATE_CLEANUP_SENTINEL" not in serialized


def test_terminal_result_completes_partial_sync_writes_exactly_once(
    tmp_path: Path,
) -> None:
    source = r"""
        "use strict";
        const terminal = require(__TERMINAL_HELPER__);
        terminal._resetForTests();
        const chunks = [];
        let calls = 0;
        const fsImpl = {
          writeSync(_fd, buffer, offset, length) {
            calls += 1;
            const count = Math.min(3, length);
            chunks.push(Buffer.from(buffer.subarray(offset, offset + count)));
            return count;
          },
        };
        terminal.writeTerminalResultSync(
          { ok: true, phase: "fixture" },
          { fd: 9, fsImpl },
        );
        let duplicateRejected = false;
        try {
          terminal.writeTerminalResultSync(
            { ok: false },
            { fd: 9, fsImpl },
          );
        } catch (_error) {
          duplicateRejected = true;
        }
        process.stdout.write(JSON.stringify({
          calls,
          duplicateRejected,
          output: Buffer.concat(chunks).toString("utf8"),
        }));
    """
    source = source.replace("__TERMINAL_HELPER__", json.dumps(str(TERMINAL_HELPER)))
    completed = _run_script(tmp_path, source)

    assert completed.returncode == 0, completed.stderr
    report = _last_json(completed.stdout)
    assert report["calls"] > 1
    assert report["duplicateRejected"] is True
    assert report["output"] == (
        'EVEJS_LAUNCHER_RESULT={"ok":true,"phase":"fixture"}\n'
    )


def test_terminal_result_exits_before_queued_work_can_run(tmp_path: Path) -> None:
    source = r"""
        "use strict";
        const terminal = require(__TERMINAL_HELPER__);
        setImmediate(() => process.stdout.write("LATE_WORK_RAN\n"));
        terminal.exitWithTerminalResult({ ok: true, phase: "fixture" }, 0);
        process.stdout.write("RETURNED_AFTER_EXIT\n");
    """
    source = source.replace("__TERMINAL_HELPER__", json.dumps(str(TERMINAL_HELPER)))
    completed = _run_script(tmp_path, source)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == (
        'EVEJS_LAUNCHER_RESULT={"ok":true,"phase":"fixture"}\n'
    )
    assert "LATE_WORK_RAN" not in completed.stdout
    assert "RETURNED_AFTER_EXIT" not in completed.stdout


def test_cli_semantic_failure_keeps_trusted_marker_on_exit_zero(
    tmp_path: Path,
) -> None:
    private_value = "PRIVATE_CLI_REQUEST_SENTINEL"
    completed = subprocess.run(
        [str(NODE), str(DOCKER_HELPER)],
        cwd=tmp_path,
        input=json.dumps({"username": private_value}),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(RESULT_PREFIX)
    result = json.loads(lines[0][len(RESULT_PREFIX) :])
    assert result == {
        "ok": False,
        "error": "Docker character creation failed",
        "code": "CHARACTER_CREATION_FAILED",
        "backupCreated": False,
        "rollbackSucceeded": False,
        "restartSafe": False,
    }
    assert private_value not in completed.stdout
    assert private_value not in completed.stderr
