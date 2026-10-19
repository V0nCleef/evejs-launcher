"use strict";
const fs = require("node:fs");
const path = require("node:path");
const {createHash, randomUUID} = require("node:crypto");
function option(name) {
  const index = process.argv.indexOf(name);
  if (index < 2 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}
const request = JSON.parse(fs.readFileSync(option("--request"), "utf8"));
let result;
function ordinary(target, directory = false) {
  const info = fs.lstatSync(target);
  if (info.isSymbolicLink() || !(directory ? info.isDirectory() : info.isFile())) {
    throw new Error("Example paths must be ordinary files and folders.");
  }
}
try {
  if (request.protocol !== "evejs_launcher_mod_v1" || request.action !== "prepare_server_start" ||
      request.runtime.backend !== "native" || request.profile !== null) {
    throw new Error("Unsupported Native startup request.");
  }
  const root = request.runtime.evejsRoot;
  const source = path.join(root, "package.json");
  ordinary(source);
  if (fs.statSync(source).size > 1024 * 1024) throw new Error("Package metadata is too large.");
  const bytes = fs.readFileSync(source);
  const metadata = JSON.parse(bytes.toString("utf8"));
  if (typeof metadata.version !== "string" || !metadata.version) throw new Error("Missing EveJS version.");
  const generation = createHash("sha256").update(bytes).digest("hex");
  // This demo writes ONLY its private derived record. A real native repair
  // additionally needs service-inactivity checks, reviewed hashes and rollback.
  const local = path.join(root, "_local");
  ordinary(local, true);
  const state = path.join(local, "native-startup-demo");
  if (!fs.existsSync(state)) fs.mkdirSync(state);
  ordinary(state, true);
  const receipt = path.join(state, "prepared.json");
  let previous = null;
  if (fs.existsSync(receipt)) {
    ordinary(receipt);
    if (fs.statSync(receipt).size > 4096) throw new Error("Example receipt is too large.");
    previous = JSON.parse(fs.readFileSync(receipt, "utf8"));
    if (previous.schemaVersion !== 1 || previous.owner !== request.mod.identity ||
        !/^[a-f0-9]{64}$/.test(previous.generation)) throw new Error("Unknown example receipt; review it first.");
  }
  if (!previous || previous.generation !== generation) {
    const temporary = `${receipt}.${randomUUID()}.tmp`;
    try {
      const fd = fs.openSync(temporary, "wx");
      try {
        fs.writeFileSync(fd, JSON.stringify({schemaVersion: 1, owner: request.mod.identity,
          generation, version: metadata.version}, null, 2) + "\n");
        fs.fsyncSync(fd);
      } finally { fs.closeSync(fd); }
      fs.renameSync(temporary, receipt);
    } finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  }
  result = {success: true, state: "ready", message: "Native startup example is ready."};
} catch (error) {
  result = {success: false, state: "failed", message: error.message};
}
fs.writeFileSync(option("--result"), JSON.stringify({protocol: "evejs_launcher_mod_v1",
  requestId: request.requestId, ...result, restartRequired: [], contributions: [],
  environment: {}, arguments: []}), {flag: "wx"});
