"use strict";
// Authored Launcher bridge. Participating mods never borrow another mod's loader.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const Module = require("module");
const frozen = /*__FROZEN_PAYLOAD__*/;
// The common ABI is embedded in this immutable file for Native and Docker.
const deliveryApi = (() => {
  const module = { exports: {} };
  /*__LOGIN_DELIVERY_RUNTIME__*/
  return module.exports;
})();
const root = path.resolve(__dirname, "../../..");
const target = path.join(root, "server/src/network/tcp/handshake.js");
const baseline = fs.readFileSync(target, "utf8");
if (crypto.createHash("sha256").update(Buffer.from(baseline, "utf8")).digest("hex") !== frozen.handshakeSha256) {
  throw new Error("Launcher shared Mods menu: login builder drifted after planning.");
}
const bootstrap = frozen.bootstrap;
try {
  const runtime = deliveryApi.getRuntime(root);
  if (runtime) {
    runtime.register({ id: "launcher.shared-menu", source: bootstrap });
    console.log("EVEJS_MOD_MENU:REGISTERED:v1");
  }
} catch (_) {
  console.error("[LoginDelivery] launcher.shared-menu registration failed; native startup preserved.");
}
