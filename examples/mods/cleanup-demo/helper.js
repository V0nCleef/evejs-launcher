"use strict";
const fs = require("node:fs");
const path = require("node:path");
const {randomUUID} = require("node:crypto");
const protocol = "evejs_launcher_mod_v1";
function option(name) {
  const index = process.argv.indexOf(name);
  if (index < 2 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}
const request = JSON.parse(fs.readFileSync(option("--request"), "utf8"));
const statePath = path.join(__dirname, "demo-state.json");
function persist(document) {
  const temporary = `${statePath}.${randomUUID()}.tmp`;
  try {
    const descriptor = fs.openSync(temporary, "wx");
    try {
      fs.writeFileSync(descriptor, JSON.stringify(document, null, 2));
      fs.fsyncSync(descriptor);
    } finally {
      fs.closeSync(descriptor);
    }
    fs.renameSync(temporary, statePath);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}
let response;
try {
  if (request.protocol !== protocol || !["install", "prepare_disable", "prepare_remove"].includes(request.action)) {
    throw new Error("Unsupported cleanup demonstration request.");
  }
  const owner = request.mod.identity;
  if (typeof owner !== "string" || !owner) throw new Error("Missing owner identity.");
  let document = {schemaVersion: 1, phase: "restored", records: []};
  if (fs.existsSync(statePath)) {
    if (!fs.lstatSync(statePath).isFile() || fs.lstatSync(statePath).isSymbolicLink()) {
      throw new Error("Demo state must be an ordinary file.");
    }
    document = JSON.parse(fs.readFileSync(statePath, "utf8"));
  }
  if (document.schemaVersion !== 1 || !Array.isArray(document.records) ||
      !document.records.every(row => row && typeof row.owner === "string" && typeof row.id === "string") ||
      !["active", "retiring", "restored"].includes(document.phase)) {
    throw new Error("Invalid demo state; preserve it for recovery.");
  }
  let state = "ready";
  if (request.action === "install") {
    if (document.phase === "retiring") throw new Error("Finish pending cleanup before reinstalling.");
    if (document.phase === "restored") {
      for (const id of ["first", "second"]) {
        if (!document.records.some(row => row.owner === owner && row.id === id)) {
          document.records.push({owner, id});
        }
      }
    }
    document.phase = "active";
  } else {
    // One bounded unit of work per invocation; exact ownership, never a family-wide delete.
    const index = document.records.findIndex(row => row.owner === owner);
    if (index !== -1) document.records.splice(index, 1);
    const remaining = document.records.filter(row => row.owner === owner).length;
    document.phase = remaining ? "retiring" : "restored";
    state = remaining ? "pending" : "ready";
  }
  // Commit before replying. A lost reply can safely be retried in a new process.
  persist(document);
  response = {protocol, requestId: request.requestId, success: true, state,
    message: state === "pending" ? "Demo cleanup is pending. Retry to finish; keep this provider installed." : "Demo action completed.",
    restartRequired: [], contributions: [], environment: {}, arguments: []};
} catch (error) {
  response = {protocol, requestId: request.requestId, success: false, state: "failed",
    message: String(error.message), restartRequired: [], contributions: [], environment: {}, arguments: []};
  process.exitCode = 1;
}
fs.writeFileSync(option("--result"), JSON.stringify(response), {flag: "wx"});
