"use strict";
const fs = require("node:fs");
const PROTOCOL = "evejs_launcher_mod_v1";
function option(name) {
  const index = process.argv.indexOf(name);
  if (index < 2 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}
const request = JSON.parse(fs.readFileSync(option("--request"), "utf8"));
let result;
try {
  if (request.protocol !== PROTOCOL || !["install", "prepare_disable", "prepare_remove"].includes(request.action)) {
    throw new Error("Unsupported overlay action or protocol.");
  }
  const edits = [];
  if (request.action === "install") {
    const {region, replacement} = request.settings.global;
    if (!["first", "second"].includes(region) || typeof replacement !== "string") {
      throw new Error("Choose a demo region and replacement value in Configure.");
    }
    edits.push({base: "evejs", path: "server/launcher-overlay-demo.js", format: "text",
      key: [`// <${region}>`, `// </${region}>`],
      value: `\nexports.${region} = ${JSON.stringify(replacement)};\n`});
  }
  // All shared edits are proposals. The host owns their commit and restoration.
  result = {protocol: PROTOCOL, requestId: request.requestId, success: true,
    state: "ready", message: "Overlay action prepared.", restartRequired: [],
    contributions: edits, environment: {}, arguments: []};
} catch (error) {
  result = {protocol: PROTOCOL, requestId: request.requestId, success: false,
    state: "failed", message: String(error.message), restartRequired: [],
    contributions: [], environment: {}, arguments: []};
}
fs.writeFileSync(option("--result"), JSON.stringify(result));
