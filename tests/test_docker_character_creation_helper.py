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
REVIEWED_PROFILE_HASHES = {
    "0.12.5": {
        "gameStore/index.js": (
            "dc474d40f02e64db715361630c1a48ac3e3de30ed77ae300d2ffd82815178421"
        ),
        "gameStore/sqliteStore.js": (
            "c71e3a6fb0c2f3c51be2848f0842a5286775c978ad19d39c006747ef08fecc0a"
        ),
        "gameStore/tableRepository.js": (
            "f74665cd5de3a23bc4bf0f317ab6aa4186ba0a9b8443bcbdf70bbe784d99ac6e"
        ),
        "services/_shared/identityAllocator.js": (
            "f801b4cbc973451ea7ba561d6a1df9bae589ddd84b21874549864ebe1124ea8f"
        ),
        "services/account/accountRoleProfiles.js": (
            "b850b911ae3942f97211d6031c5e45bbf90f67764d97f59fb1c00b55b9c3494b"
        ),
        "services/character/charService.js": (
            "302bfbcc6abdb7125721d469a9a76dc5879393488d83ad2ddd710da92dff7524"
        ),
        "services/ship/rookieShipRuntime.js": (
            "54ea7e981c9938538c264079124c2cd6f47518946e734a5be91fb54f40272f2b"
        ),
        "space/runtime.js": (
            "485cb18c79e5f54d3973327fdcf4c3b686442cab0b1c67bbb85e02d853088097"
        ),
        "space/transitions.js": (
            "211c2074f82a6129bfa96da6e3150e55bf217c419bb7a733e75359f51d1a4307"
        ),
    },
    "0.12.6": {
        "gameStore/index.js": (
            "4007195df24a93f815a2f82b155bbfdfd78b56749b3a89d1f402abb33df61eac"
        ),
        "gameStore/sqliteStore.js": (
            "c181ef96354b5565baf457d9d40de0380654eb8e20f95008d1a8f3870d7c926f"
        ),
        "gameStore/tableRepository.js": (
            "ae7bdb55fd48e0d0e3fd3c72416e511804ee4414652e4e84a139b351a62c3008"
        ),
        "services/_shared/identityAllocator.js": (
            "f801b4cbc973451ea7ba561d6a1df9bae589ddd84b21874549864ebe1124ea8f"
        ),
        "services/account/accountRoleProfiles.js": (
            "b850b911ae3942f97211d6031c5e45bbf90f67764d97f59fb1c00b55b9c3494b"
        ),
        "services/character/charService.js": (
            "e48586fa08087bbcde9bf9ac15eaff2525b976c0900e7202b8e3c03d87c85aaa"
        ),
        "services/character/characterState.js": (
            "4292922f810901292c1915716998f62727d3cbcd62fae61f35a366dc92f28a9d"
        ),
        "services/character/characterCreationData.js": (
            "7ef9c0b71ac5a595c47baf39013390029cd32de794ed5c98227c74bfb7c0ac64"
        ),
        "services/skills/skillState.js": (
            "9194dd1c0184094a182aabd84b1236c5be2ea6410578edcc408a489099c55f88"
        ),
        "services/mail/mailState.js": (
            "47a656ca926aeed1621ed390cf4353e7b5c6bce69355587cbb123b732b6c47b9"
        ),
        "services/notifications/notificationState.js": (
            "535c108b63b5d4e769018017987af6e37eedbb5c30c5e29403ada2117700b53d"
        ),
        "services/ship/rookieShipRuntime.js": (
            "54ea7e981c9938538c264079124c2cd6f47518946e734a5be91fb54f40272f2b"
        ),
        "space/runtime.js": (
            "2103dced34a2fdcc99a4b174e851a1f009864fd20c15f9d0bc15093bb651a300"
        ),
        "space/transitions.js": (
            "70686c837c62530467f78d88757641f479a05f410c097e3df7a1f24cd674be19"
        ),
    },
}


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
        const reviewedVersion = mode.startsWith("v0126-") ? "0.12.6" : "0.12.5";
        const starterSkillEntries = [
          { typeID: 3300, level: 4 },
          { typeID: 3301, level: 1 },
        ];
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
        function syntheticSkillType(typeID) {
          return {
            typeID,
            categoryID: 16,
            groupID: 255,
            groupName: "Gunnery",
            name: `Synthetic Skill ${typeID}`,
            published: true,
            skillRank: 1,
          };
        }
        function buildSyntheticSkillRecord(characterId, entry, rawSkillType = null) {
          const skillType = rawSkillType || syntheticSkillType(entry.typeID);
          const skillRank = skillType.skillRank;
          const skillPoints = entry.level <= 0
            ? 0
            : Math.round(
              250 * skillRank * Math.pow(Math.sqrt(32), entry.level - 1),
            );
          return {
            itemID: characterId * 100000 + entry.typeID,
            typeID: entry.typeID,
            ownerID: characterId,
            locationID: characterId,
            flagID: 7,
            categoryID: skillType.categoryID || 16,
            groupID: skillType.groupID || 0,
            groupName: skillType.groupName || "",
            itemName: skillType.name,
            published: Boolean(skillType.published),
            skillLevel: entry.level,
            trainedSkillLevel: entry.level,
            effectiveSkillLevel: entry.level,
            virtualSkillLevel: null,
            skillRank,
            skillPoints,
            trainedSkillPoints: skillPoints,
            inTraining: false,
            trainingStartSP: skillPoints,
            trainingDestinationSP: skillPoints,
            trainingStartTime: null,
            trainingEndTime: null,
          };
        }
        const starterSkillPointTotal = starterSkillEntries.reduce(
          (total, entry) =>
            total + buildSyntheticSkillRecord(1, entry).skillPoints,
          0,
        );
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
        if (mode.startsWith("v0126-empty-prestate")) {
          initial.mail = {};
          initial.notifications = {};
        } else if (mode === "v0126-mail-nonempty-prestate") {
          initial.mail = { unexpected: true };
          initial.notifications = {};
        } else if (mode === "v0126-notifications-nonempty-prestate") {
          initial.mail = {};
          initial.notifications = { unexpected: true };
        }
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
                    ...(reviewedVersion === "0.12.6"
                      ? { raceID: 1, skillPoints: starterSkillPointTotal }
                      : {}),
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
                if (reviewedVersion === "0.12.6") {
                  database.write(
                    "skills",
                    "/",
                    {
                      ...state.skills,
                      [characterId]: Object.fromEntries(
                        starterSkillEntries.map((entry) => [
                          String(entry.typeID),
                          buildSyntheticSkillRecord(characterId, entry),
                        ]),
                      ),
                    },
                    { force: true },
                  );
                  if (!state.mail._meta || typeof state.mail._meta !== "object") {
                    state.mail._meta = {
                      nextMessageID: 1,
                      nextMailingListID: 500000000,
                    };
                  }
                  if (!Number.isSafeInteger(state.mail._meta.nextMessageID)) {
                    state.mail._meta.nextMessageID = 1;
                  }
                  if (!Number.isSafeInteger(state.mail._meta.nextMailingListID)) {
                    state.mail._meta.nextMailingListID = 500000000;
                  }
                  if (!state.mail.messages || typeof state.mail.messages !== "object") {
                    state.mail.messages = {};
                  }
                  if (!state.mail.mailboxes || typeof state.mail.mailboxes !== "object") {
                    state.mail.mailboxes = {};
                  }
                  if (!state.mail.mailingLists || typeof state.mail.mailingLists !== "object") {
                    state.mail.mailingLists = {};
                  }
                  if (
                    !state.notifications._meta ||
                    typeof state.notifications._meta !== "object"
                  ) {
                    state.notifications._meta = { nextNotificationID: 1 };
                  }
                  if (
                    !Number.isSafeInteger(
                      state.notifications._meta.nextNotificationID,
                    )
                  ) {
                    state.notifications._meta.nextNotificationID = 1;
                  }
                  if (
                    !state.notifications.boxes ||
                    typeof state.notifications.boxes !== "object"
                  ) {
                    state.notifications.boxes = {};
                  }
                }
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
                        statuses: {
                          [messageId]: {
                            ...(reviewedVersion === "0.12.6"
                              ? { messageID: messageId }
                              : {}),
                            statusMask: 0,
                            labelMask: 1,
                          },
                        },
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
                } else if (mode === "v0126-skill-extra-character") {
                  state.skills["777"] = {
                    "3300": buildSyntheticSkillRecord(777, starterSkillEntries[0]),
                  };
                } else if (mode === "v0126-skill-empty-map") {
                  state.skills[String(characterId)] = {};
                } else if (mode === "v0126-skill-unexpected-type") {
                  state.skills[String(characterId)]["9999"] =
                    buildSyntheticSkillRecord(characterId, {
                      typeID: 9999,
                      level: 1,
                    });
                } else if (mode === "v0126-skill-missing-type") {
                  delete state.skills[String(characterId)]["3301"];
                } else if (mode === "v0126-skill-level-mismatch") {
                  state.skills[String(characterId)]["3300"].skillLevel = 3;
                } else if (mode === "v0126-skill-extra-field") {
                  state.skills[String(characterId)]["3300"].unexpected = true;
                } else if (mode === "v0126-skill-item-id") {
                  state.skills[String(characterId)]["3300"].itemID += 1;
                } else if (mode === "v0126-skill-owner-location") {
                  state.skills[String(characterId)]["3300"].ownerID = 999;
                  state.skills[String(characterId)]["3300"].locationID = 999;
                } else if (mode === "v0126-skill-flag-category") {
                  state.skills[String(characterId)]["3300"].flagID = 8;
                  state.skills[String(characterId)]["3300"].categoryID = 17;
                } else if (mode === "v0126-skill-empty-name") {
                  state.skills[String(characterId)]["3300"].itemName = "";
                } else if (mode === "v0126-skill-rank") {
                  state.skills[String(characterId)]["3300"].skillRank = 2;
                } else if (mode === "v0126-skill-coherent-rank-points") {
                  const skill = state.skills[String(characterId)]["3300"];
                  const alteredRank = 2;
                  const alteredPoints = Math.round(
                    250 * alteredRank * Math.pow(
                      Math.sqrt(32),
                      skill.skillLevel - 1,
                    ),
                  );
                  skill.skillRank = alteredRank;
                  skill.skillPoints = alteredPoints;
                  skill.trainedSkillPoints = alteredPoints;
                  skill.trainingStartSP = alteredPoints;
                  skill.trainingDestinationSP = alteredPoints;
                } else if (mode === "v0126-skill-metadata-published") {
                  const skill = state.skills[String(characterId)]["3300"];
                  skill.groupID = 999;
                  skill.groupName = "Altered Group";
                  skill.itemName = "Altered Skill";
                  skill.published = false;
                } else if (mode === "v0126-skill-points-formula") {
                  const skill = state.skills[String(characterId)]["3300"];
                  const wrongPoints = skill.skillPoints + 1;
                  skill.skillPoints = wrongPoints;
                  skill.trainedSkillPoints = wrongPoints;
                  skill.trainingStartSP = wrongPoints;
                  skill.trainingDestinationSP = wrongPoints;
                } else if (mode === "v0126-skill-training") {
                  const skill = state.skills[String(characterId)]["3300"];
                  skill.inTraining = true;
                  skill.trainingStartTime = "2026-08-13T12:00:00.000Z";
                  skill.trainingEndTime = "2026-08-14T12:00:00.000Z";
                } else if (mode === "v0126-preexisting-skill-modify") {
                  state.skills["9001"] = {
                    ...state.skills["9001"],
                    unexpected: "changed",
                  };
                } else if (mode === "v0126-character-race-id") {
                  state.characters[String(characterId)].raceID = 2;
                } else if (mode === "v0126-character-skill-points") {
                  state.characters[String(characterId)].skillPoints += 1;
                } else if (mode === "v0126-mailbox-status-message-id") {
                  state.mail.mailboxes[String(characterId)]
                    .statuses[String(messageId)].messageID = messageId + 1;
                }
                if (mode.startsWith("retained-")) corruptRetainedBackup();
                const reachesVerification =
                  mode === "success" ||
                  mode === "verified-commit-cleanup-failure" ||
                  mode === "verified-commit-outbox" ||
                  mode.startsWith("preexisting-") ||
                  mode.startsWith("retained-") ||
                  mode.startsWith("contract-") ||
                  mode.startsWith("v0126-");
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
            ...(reviewedVersion === "0.12.6"
              ? {
                  getCharacterCreationRace(raceID) {
                    const skills = mode === "v0126-skill-overbound-profile"
                      ? Array.from({ length: 257 }, (_value, index) => ({
                          typeID: 10000 + index,
                          level: 1,
                        }))
                      : starterSkillEntries;
                    return raceID === 1
                      ? { raceID: 1, name: "Synthetic", skills: clone(skills) }
                      : null;
                  },
                  getSkillTypeByID(typeID) {
                    events.push(`get-skill-type:${typeID}`);
                    return starterSkillEntries.some(
                      (entry) => entry.typeID === typeID,
                    )
                      ? clone(syntheticSkillType(typeID))
                      : null;
                  },
                  buildSkillRecord(characterId, skillType, level) {
                    events.push(`build-skill-record:${skillType.typeID}`);
                    return buildSyntheticSkillRecord(
                      characterId,
                      { typeID: skillType.typeID, level },
                      skillType,
                    );
                  },
                }
              : {}),
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
            {
              database,
              backupManager,
              loadCreationDependencies,
              reviewedProfile: helper.reviewedImageProfile(reviewedVersion),
            },
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

    attestation = "const { serverSource, profile } = attestProductionImage(root);"
    game_store_import = (
        'const database = require(path.join(serverSource, "gameStore", "index.js"));'
    )
    assert source.index(attestation) < source.index(game_store_import)
    assert "const profile = reviewedImageProfile(serverPackage.version);" in source
    assert "profile.sourceContracts" in source
    assert "sqlite-tables.json" in source
    assert "source.backup(" not in source
    assert "copyTreeStrict(dataRoot, backupData)" not in source
    assert (
        "dc474d40f02e64db715361630c1a48ac3e3de30ed77ae300d2ffd82815178421"
        in source
    )


def test_exact_reviewed_image_profiles_are_immutable_and_fail_closed(
    tmp_path: Path,
) -> None:
    source = r"""
        "use strict";
        const helper = require(__DOCKER_HELPER__);
        const versions = ["0.12.5", "0.12.6"];
        const profiles = Object.fromEntries(versions.map((version) => {
          const profile = helper.reviewedImageProfile(version);
          const immutable = Boolean(
            profile &&
            Object.isFrozen(profile) &&
            Object.isFrozen(profile.serverPackage) &&
            Object.isFrozen(profile.serverPackage.dependencies) &&
            Object.isFrozen(profile.sqlitePackage) &&
            Object.isFrozen(profile.sourceContracts) &&
            profile.sourceContracts.every((contract) =>
              Object.isFrozen(contract) && Object.isFrozen(contract[2])
            )
          );
          return [version, {
            version: profile && profile.version,
            serverPackage: profile && profile.serverPackage,
            sqlitePackage: profile && profile.sqlitePackage,
            sourceHashes: profile && Object.fromEntries(
              profile.sourceContracts.map(([relativePath, expectedHash]) =>
                [relativePath, expectedHash]
              )
            ),
            immutable,
          }];
        }));
        const rejectedVersions = [
          "0.12.4",
          "0.12.7",
          "0.12.6+local",
          "v0.12.6",
          "",
          "__proto__",
          "constructor",
          null,
        ];
        const rejected = rejectedVersions.map((version) =>
          helper.reviewedImageProfile(version) === null
        );
        process.stdout.write(JSON.stringify({ profiles, rejected }));
    """
    source = source.replace("__DOCKER_HELPER__", json.dumps(str(DOCKER_HELPER)))
    completed = _run_script(tmp_path, source)

    assert completed.returncode == 0, completed.stderr
    report = _last_json(completed.stdout)
    assert all(report["rejected"])
    assert set(report["profiles"]) == set(REVIEWED_PROFILE_HASHES)
    for version, expected_hashes in REVIEWED_PROFILE_HASHES.items():
        profile = report["profiles"][version]
        assert profile["version"] == version
        assert profile["serverPackage"] == {
            "name": "eve.js",
            "type": "commonjs",
            "dependencies": {"better-sqlite3": "^12.11.1"},
        }
        assert profile["sqlitePackage"] == {
            "name": "better-sqlite3",
            "version": "12.11.1",
        }
        assert profile["sourceHashes"] == expected_hashes
        assert profile["immutable"] is True


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


@pytest.mark.parametrize(
    "mode",
    ["v0126-success", "v0126-empty-prestate-success"],
)
def test_v0126_exact_starter_skills_and_supported_mail_prestates_commit(
    tmp_path: Path,
    mode: str,
) -> None:
    report = _transaction_probe(tmp_path, mode)

    assert report["result"] == {
        "ok": True,
        "accountId": 101,
        "characterId": 202,
        "rookieShipVerified": True,
        "backupCreated": True,
        "backupName": "fixture-backup",
        "cleanupConfirmed": True,
        "restartSafe": True,
    }
    assert report["stateMatchesInitial"] is False
    assert "skills" in report["touched"]
    assert "mail" in report["touched"]
    assert "notifications" in report["touched"]
    assert "verify-creation" in report["events"]
    assert "rollback" not in report["events"]
    first_write = next(
        index
        for index, event in enumerate(report["events"])
        if event.startswith("write:")
    )
    assert report["events"].index("get-skill-type:3300") < first_write
    assert report["events"].index("get-skill-type:3301") < first_write


@pytest.mark.parametrize(
    "mode",
    [
        "v0126-skill-extra-character",
        "v0126-skill-empty-map",
        "v0126-skill-unexpected-type",
        "v0126-skill-missing-type",
        "v0126-skill-level-mismatch",
        "v0126-skill-extra-field",
        "v0126-skill-item-id",
        "v0126-skill-owner-location",
        "v0126-skill-flag-category",
        "v0126-skill-empty-name",
        "v0126-skill-rank",
        "v0126-skill-coherent-rank-points",
        "v0126-skill-metadata-published",
        "v0126-skill-points-formula",
        "v0126-skill-training",
        "v0126-preexisting-skill-modify",
    ],
)
def test_v0126_malformed_or_unexpected_skill_mutation_rolls_back(
    tmp_path: Path,
    mode: str,
) -> None:
    report = _transaction_probe(tmp_path, mode)
    result = report["result"]

    assert result["ok"] is False
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert report["events"].index("verify-creation") < report["events"].index(
        "rollback"
    )


@pytest.mark.parametrize(
    "mode",
    ["v0126-character-race-id", "v0126-character-skill-points"],
)
def test_v0126_character_race_and_skill_total_are_exact(
    tmp_path: Path,
    mode: str,
) -> None:
    report = _transaction_probe(tmp_path, mode)
    result = report["result"]

    assert result["ok"] is False
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert "verify-creation" in report["events"]
    assert "rollback" in report["events"]


def test_v0126_overbound_starter_skill_profile_fails_before_mutation(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "v0126-skill-overbound-profile")
    result = report["result"]

    assert result["ok"] is False
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert report["touched"] == []
    assert "verify-creation" not in report["events"]
    assert "rollback" not in report["events"]


@pytest.mark.parametrize(
    "mode",
    [
        "v0126-mail-nonempty-prestate",
        "v0126-notifications-nonempty-prestate",
    ],
)
def test_v0126_default_state_exception_requires_an_exact_empty_prestate(
    tmp_path: Path,
    mode: str,
) -> None:
    report = _transaction_probe(tmp_path, mode)
    result = report["result"]

    assert result["ok"] is False
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert "verify-creation" in report["events"]
    assert "rollback" in report["events"]


def test_v0126_mailbox_status_requires_the_allocated_message_id(
    tmp_path: Path,
) -> None:
    report = _transaction_probe(tmp_path, "v0126-mailbox-status-message-id")
    result = report["result"]

    assert result["ok"] is False
    assert result["rollbackSucceeded"] is True
    assert result["restartSafe"] is True
    assert report["stateMatchesInitial"] is True
    assert "verify-creation" in report["events"]
    assert "rollback" in report["events"]


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
