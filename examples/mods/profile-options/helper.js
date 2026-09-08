"use strict";

const fs = require("node:fs");
const path = require("node:path");
const PROTOCOL = "evejs_launcher_mod_v1";

function option(name) {
  const index = process.argv.indexOf(name);
  if (index < 2 || !process.argv[index + 1]) throw new Error(`Missing ${name}`);
  return process.argv[index + 1];
}

const resultPath = option("--result");
let request;
let result;
try {
  request = JSON.parse(fs.readFileSync(option("--request"), "utf8"));
  if (request.protocol !== PROTOCOL || request.action !== "prepare_profile" || !request.profile) {
    throw new Error("This helper supports API 1 profile preparation only.");
  }
  const values = request.settings.profile;
  if (typeof values.label !== "string" || !Number.isInteger(values.intensity) || typeof values.enabled !== "boolean") {
    throw new Error("Typed profile preferences are missing.");
  }
  result = {
    protocol: PROTOCOL, requestId: request.requestId,
    success: true, state: "ready", message: "Example profile preferences prepared.", restartRequired: [],
    contributions: [
      {base: "profile", path: "preferences.ini", format: "ini", key: ["Example", "Label"], value: values.label},
      {base: "profile", path: "preferences.ini", format: "ini", key: ["Example", "Intensity"], value: values.intensity},
      {base: "profile", path: "preferences.ini", format: "ini", key: ["Example", "Enabled"], value: values.enabled ? 1 : 0},
      {base: "profile_settings", path: "prefs.ini", format: "ini", key: ["LauncherExample", "Label"], value: values.label}
    ],
    environment: {LAUNCHER_PROFILE_OPTIONS: path.join(request.profile.modDataRoot, "preferences.ini")},
    arguments: []
  };
} catch (error) {
  result = {protocol: PROTOCOL, requestId: request ? request.requestId : "invalid-request",
    success: false, state: "failed", message: String(error.message).replace(/[\r\n]/g, " ").slice(0, 4096),
    restartRequired: [], contributions: [], environment: {}, arguments: []};
  process.exitCode = 1;
}
// Write only the host-supplied result. The host owns all proposed config edits.
fs.writeFileSync(resultPath, JSON.stringify(result), {encoding: "utf8", flag: "wx"});
