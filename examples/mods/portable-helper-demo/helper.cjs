"use strict";
// This is the container helper, not a Windows client helper.
const fs = require("node:fs");
const path = require("node:path");
const actions = new Set(["install", "verify", "prepare_server_start", "prepare_disable", "prepare_remove", "recover"]);
const chunks = [];
let size = 0;
process.stdin.on("data", chunk => {
  size += chunk.length;
  if (size > 16 * 1024) { process.stderr.write("Request exceeds 16 KiB\n"); process.exit(1); }
  chunks.push(chunk);
});
process.stdin.on("end", () => {
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (request.protocol !== "evejs_launcher_mod_v1" || typeof request.requestId !== "string" ||
      request.mod?.id !== "portable-helper-demo" || !actions.has(request.action) ||
      request.runtime?.backend !== "docker" || request.runtime.platform !== "linux" ||
      request.runtime.modDataRoot !== "/var/lib/evejs/mods/portable-helper-demo") {
    throw Error("Unsupported portable helper request");
  }
  const file = path.join(request.runtime.modDataRoot, "installed.json");
  if (request.action === "install" || request.action === "recover") {
    fs.mkdirSync(request.runtime.modDataRoot, { recursive: true });
    if (!fs.existsSync(file)) fs.writeFileSync(file, JSON.stringify({ schemaVersion: 1 }) + "\n", { flag: "wx" });
  }
  let ready = false;
  try { ready = JSON.parse(fs.readFileSync(file, "utf8")).schemaVersion === 1; } catch (_) {}
  // Verify, startup, Disable and Remove perform no writes. Durable state survives removal.
  process.stdout.write("EVEJS_MOD_HELPER_RESULT=" + JSON.stringify({
    protocol: request.protocol, requestId: request.requestId, success: ready,
    state: ready ? "ready" : "failed", message: ready ? "Owned receipt verified." : "Run install or recover first.",
    restartRequired: [], contributions: [], environment: {}, arguments: []
  }) + "\n");
});
