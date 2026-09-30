"use strict";
// Authored Launcher bridge. Participating mods never borrow another mod's loader.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const Module = require("module");
const frozen = /*__FROZEN_PAYLOAD__*/;
const root = path.resolve(__dirname, "../../..");
const target = path.join(root, "server/src/network/tcp/handshake.js");
const baseline = fs.readFileSync(target, "utf8");
if (crypto.createHash("sha256").update(Buffer.from(baseline, "utf8")).digest("hex") !== frozen.handshakeSha256) {
  throw new Error("Launcher shared Mods menu: login builder drifted after planning.");
}
const bootstrap = [
  "try:",
  " import sys, types, base64, json",
  " _delivery = json.loads(base64.b64decode('" + Buffer.from(JSON.stringify(frozen)).toString("base64") + "'))",
  " _module = sys.modules.get('evejs_mod_menu')",
  " if _module is not None and getattr(_module, '_build_digest', None) != _delivery['frameworkDigest']:",
  "  _module.shutdown()",
  "  _module = None",
  " if _module is None:",
  "  _module = types.ModuleType('evejs_mod_menu')",
  "  _module.__dict__.update(sm=sm, session=session)",
  "  sys.modules['evejs_mod_menu'] = _module",
  "  eval(compile(base64.b64decode(_delivery['framework']), '<evejs-mod-menu-v1>', 'exec'), _module.__dict__)",
  "  _module._build_digest = _delivery['frameworkDigest']",
  " _module.bootstrap(_delivery['entries'])",
  "except:",
  " import traceback",
  " traceback.print_exc()",
  " print 'EVEJS_MOD_MENU:BOOTSTRAP_FAILED'",
].join("\n") + "\n";
const encoded = Buffer.from(bootstrap, "utf8").toString("base64");
const append = `
// Launcher owns only this local builder wrapper. Preserve the entire input.
const _evejsSharedMenuPreviousBuilder = buildTidiSignedFunc;
buildTidiSignedFunc = function () {
  const original = _evejsSharedMenuPreviousBuilder.apply(this, arguments);
  if (!Buffer.isBuffer(original) || original[0] !== 0x74 || original.readUInt32LE(1) !== original.length - 5) {
    throw new Error("Unsupported shared Mods menu signed function envelope.");
  }
  // Latin-1 round-trips every original byte, including another mod's UTF-8.
  const expression = '(lambda _result: (eval(compile(__import__("base64").b64decode("${encoded}"), "<evejs-shared-mod-menu>", "exec")), _result)[1])(' + original.toString("latin1", 5) + ')';
  const data = Buffer.from(expression, "latin1");
  const packet = Buffer.alloc(data.length + 5);
  packet[0] = 0x74;
  packet.writeUInt32LE(data.length, 1);
  data.copy(packet, 5);
  return packet;
};
console.log("EVEJS_MOD_MENU:DELIVERY_READY:v1");
`;
const previousCompile = Module.prototype._compile;
let applied = false;
Module.prototype._compile = function (source, filename) {
  if (path.resolve(filename) === target) {
    if (applied) throw new Error("Shared Mods menu handshake compiled twice.");
    // Other preloads may append to this module. Never discard their additions.
    if (!source.replace(/\r\n/g, "\n").startsWith(baseline.replace(/\r\n/g, "\n"))) {
      throw new Error("Shared Mods menu cannot compose a changed login module.");
    }
    applied = true;
    source += append;
  }
  return previousCompile.call(this, source, filename);
};
