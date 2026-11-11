"use strict";
// Shared wire-level login delivery ABI. No handshake source transformations.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const Module = require("node:module");
const { isMainThread } = require("node:worker_threads");
const KEY = Symbol.for("evejs.loginDelivery.v1");
const MAX_SOURCE = 256 * 1024;
const MAX_PROVIDERS = 64;
const MAX_TOTAL_SOURCE = 1024 * 1024;
const MAX_EXPRESSION = 1024 * 1024;
const MAX_PACKET = 2 * 1024 * 1024;
const ID = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const canonical = file => {
  const name = path.resolve(file);
  return process.platform === "win32" ? name.toLowerCase() : name;
};
const digest = value => crypto.createHash("sha256").update(value).digest("hex");

function expressionBuffer(expression) {
  const bytes = Buffer.from(expression, "latin1"), packet = Buffer.alloc(bytes.length + 5);
  packet[0] = 0x74; packet.writeUInt32LE(bytes.length, 1); bytes.copy(packet, 5);
  return packet;
}

function readExpression(packet) {
  if (!Buffer.isBuffer(packet) || packet.length < 5 || packet[0] !== 0x74 ||
      packet.readUInt32LE(1) !== packet.length - 5 || packet.length - 5 > MAX_EXPRESSION) {
    throw Error("unsupported signed-function envelope");
  }
  return packet.toString("latin1", 5);
}

function receiptText(value) {
  // Retail Python 2 WriteBuffer becomes Unicode when a provider writes a
  // Unicode receipt. The native marshal codec represents that as wstring.
  // Accept only the codec's explicit text shapes, never arbitrary coercion.
  if (Buffer.isBuffer(value)) return value.length <= MAX_EXPRESSION ? value.toString("utf8") : null;
  if (value && typeof value === "object" && !Array.isArray(value) &&
      ["wstring", "token", "rawstr"].includes(value.type)) value = value.value;
  return typeof value === "string" && Buffer.byteLength(value, "utf8") <= MAX_EXPRESSION ? value : null;
}

function ordered(providers) {
  const result = [], visiting = [], visited = new Set(), blocked = new Map();
  function visit(provider) {
    const cycle = visiting.indexOf(provider.id);
    if (cycle >= 0) {
      for (const id of visiting.slice(cycle)) blocked.set(id, "provider dependency cycle");
      return;
    }
    if (visited.has(provider.id)) return;
    visiting.push(provider.id);
    for (const dependency of provider.after) if (providers.has(dependency)) visit(providers.get(dependency));
    for (const dependency of provider.requires) {
      if (!providers.has(dependency)) blocked.set(provider.id, "missing prerequisite " + dependency);
      else {
        visit(providers.get(dependency));
        if (blocked.has(dependency)) blocked.set(provider.id, "unavailable prerequisite " + dependency);
      }
    }
    visiting.pop(); visited.add(provider.id);
    if (!blocked.has(provider.id)) result.push(provider);
  }
  [...providers.values()].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0).forEach(visit);
  return { order: result, blocked };
}

function bootstrap(providers, nonce) {
  const payload = providers.map(p => ({ id: p.id, source: p.encoded, requires: p.requires }));
  const data = Buffer.from(JSON.stringify(payload)).toString("base64");
  // One namespace per provider; the native expression runs outside this handler.
  return [
    "import sys, json, base64, zlib",
    "_completed = set()",
    "for _p in json.loads(base64.b64decode('" + data + "')):",
    " try:",
    "  if any(_dependency not in _completed for _dependency in _p['requires']):",
    "   raise RuntimeError('login prerequisite unavailable')",
    "  _ns = dict(_evejs_context)",
    "  _ns.update(__name__='evejs_login_' + _p['id'], _evejs_context=_evejs_context)",
    "  eval(compile(zlib.decompress(base64.b64decode(_p['source'])), '<evejs-login:' + _p['id'] + '>', 'exec'), _ns)",
    "  _completed.add(_p['id'])",
    "  sys.stdout.write('\\nEVEJS_LOGIN:v1:" + nonce + ":' + _p['id'] + ':OK\\n')",
    " except BaseException:",
    "  sys.stdout.write('\\nEVEJS_LOGIN:v1:" + nonce + ":' + _p['id'] + ':FAILED\\n')",
  ].join("\n") + "\n";
}

function compose(original, providers, nonce) {
  const previous = readExpression(original);
  const encoded = Buffer.from(bootstrap(providers, nonce), "utf8").toString("base64");
  const execute = 'eval(compile(__import__("base64").b64decode("' + encoded +
    '"), "<evejs-login-delivery-v1>", "exec"), {"__builtins__": __builtins__, "_evejs_context": globals()})';
  const result = expressionBuffer('(lambda _evejs_result: (' + execute + ', _evejs_result)[1])(' + previous + ')');
  if (result.length - 5 > MAX_EXPRESSION) throw Error("combined login expression exceeds 1 MiB");
  return result;
}

function createRuntime(root, { logger = console, moduleHost = Module, codec: suppliedCodec } = {}) {
  root = canonical(root);
  const target = canonical(path.join(root, "server/src/network/tcp/handshake.js"));
  const providers = new Map(), sessions = new WeakMap(), attached = new WeakSet(), warnings = new Set();
  let codec = suppliedCodec, frozen = false, sourceBytes = 0, adapterFailure = null;
  function report(id, phase, reason) {
    const message = "[LoginDelivery] " + id + " " + phase + ": " + reason;
    if (!warnings.has(message) && warnings.size < 100) {
      warnings.add(message); (logger.error || logger.log).call(logger, message);
    }
  }
  function notify(provider, session, ok, reason) {
    const status = Object.freeze({ id: provider.id, ok, reason });
    try {
      if (ok) provider.onReceipt?.(session, status);
      else provider.onFailure?.(session, status);
    } catch (_) { report(provider.id, "callback", "provider callback failed"); return false; }
    return ok;
  }
  function adapterError(reason) {
    const error = Error("Required LoginDelivery activation failed: " + reason);
    error.code = "EVEJS_LOGIN_ADAPTER_UNAVAILABLE";
    return error;
  }
  function unavailable(reason) {
    adapterFailure = reason;
    report("adapter-v1", "unavailable", reason);
    if ([...providers.values()].some(p => p.required)) throw adapterError(reason);
    return false;
  }
  function reject(session, reason) {
    const state = sessions.get(session) || {};
    if (state.rejected) return { done: false, loginDeliveryFailed: true };
    state.rejected = true; state.replied = true; state.closing = true;
    sessions.set(session, state);
    report("required-provider", "login-rejected", reason);
    try {
      if (typeof session._sendTransportClose === "function") {
        session._sendTransportClose("LoginAuthFailed", {}, "Required client initialization failed");
      } else session.socket?.destroy?.();
    } finally { state.closing = false; }
    return { done: false, loginDeliveryFailed: true };
  }
  function failure(session, list, phase, reason) {
    for (const provider of list) {
      report(provider.id, phase, reason); notify(provider, session, false, phase);
    }
    return list.some(p => p.required);
  }
  function replaceSignedFunction(packet, value, replacement) {
    // No shared-object map: replacing a field must not invalidate saved offsets.
    if (packet[4] !== codec.MARSHAL_HEADER || packet.readUInt32LE(5) !== 0) {
      throw Error("unsupported login shared-object map");
    }
    const before = codec.marshalEncode(value), after = codec.marshalEncode(replacement);
    if (before[0] !== codec.MARSHAL_HEADER || after[0] !== codec.MARSHAL_HEADER ||
        before.readUInt32LE(1) !== 0 || after.readUInt32LE(1) !== 0) {
      throw Error("unsupported codec envelope");
    }
    const old = before.subarray(5), at = packet.indexOf(old, 9);
    if (at < 0 || packet.indexOf(old, at + 1) >= 0) throw Error("signed-function field is not uniquely encoded");
    const body = Buffer.concat([packet.subarray(4, at), after.subarray(5), packet.subarray(at + old.length)]);
    if (body.length + 4 > MAX_PACKET) throw Error("combined login packet exceeds 2 MiB");
    return codec.wrapPacket(body);
  }
  function attach(Service) {
    if (typeof Service !== "function" || typeof Service.prototype?._sendPacket !== "function" ||
        typeof Service.prototype?._handleFuncResult !== "function") {
      return unavailable("compiled handshake lacks packet/reply capabilities");
    }
    if (attached.has(Service.prototype)) return true;
    const prototype = Service.prototype;
    const descriptors = new Map();
    for (const name of ["_sendPacket", "_handleFuncResult"]) {
      const descriptor = Object.getOwnPropertyDescriptor(prototype, name);
      if (descriptor ? !("value" in descriptor) || descriptor.writable === false : !Object.isExtensible(prototype)) {
        return unavailable("compiled handshake methods are read-only");
      }
      descriptors.set(name, descriptor);
    }
    try {
      codec ||= require(path.join(root, "server/src/network/tcp/utils/marshal.js"));
      for (const name of ["marshalDecodeExact", "marshalEncode", "wrapPacket", "dictGet", "strVal"]) {
        if (typeof codec[name] !== "function") throw Error("missing native codec capability " + name);
      }
      if (!Number.isInteger(codec.MARSHAL_HEADER) || codec.MARSHAL_HEADER < 0 || codec.MARSHAL_HEADER > 255) {
        throw Error("missing native codec header");
      }
    } catch (error) { return unavailable(error.message); }
    const previousSend = Service.prototype._sendPacket;
    const previousReply = Service.prototype._handleFuncResult;
    const send = function(packet) {
      const state = sessions.get(this);
      if (state?.rejected) return state.closing ? previousSend.apply(this, arguments) : undefined;
      if (!state?.sent && providers.size && Number(this.userId) > 0 && Buffer.isBuffer(packet) &&
          packet.length >= 9 && packet.length <= MAX_PACKET && packet.readUInt32LE(0) === packet.length - 4) {
        let value;
        try { value = codec.marshalDecodeExact(packet.subarray(4)); }
        catch (_) { /* Other native packets are outside this adapter's ownership. */ }
        if (Array.isArray(value) && value.length === 4 && codec.strVal(value[0]) === "" &&
            Array.isArray(value[1]) && value[1].length === 2 &&
            value[2]?.type === "dict" && value[3]?.type === "dict" &&
            codec.dictGet(value[3], "challenge_responsehash") !== undefined) {
          frozen = true;
          const list = [...providers.values()];
          try {
            if (value[1][1] !== false && value[1][1] !== 0) throw Error("unsupported verification encoding");
            const { order, blocked } = ordered(providers);
            let requiredBlocked = false;
            for (const [id, reason] of blocked) {
              if (failure(this, [providers.get(id)], "dependency-failed", reason)) requiredBlocked = true;
            }
            if (requiredBlocked) return reject(this, "required provider dependency unavailable");
            if (!order.length) {
              sessions.set(this, { sent: true, replied: true });
              return previousSend.apply(this, arguments);
            }
            const nonce = crypto.randomBytes(16).toString("hex");
            const next = replaceSignedFunction(packet, value[1][0], compose(value[1][0], order, nonce));
            sessions.set(this, { sent: true, replied: false, order, nonce });
            packet = next;
          } catch (error) {
            sessions.set(this, { sent: true, replied: true });
            if (failure(this, list, "delivery-failed", error.message)) return reject(this, "delivery unavailable");
          }
        }
      }
      return previousSend.apply(this, [packet, ...Array.prototype.slice.call(arguments, 1)]);
    };
    const reply = function(decoded) {
      const state = sessions.get(this);
      if (state?.rejected) return { done: false, loginDeliveryFailed: true };
      if (providers.size && !state) {
        if (failure(this, [...providers.values()], "not-delivered", "no supported signed-function packet observed")) {
          return reject(this, "no supported login payload");
        }
      } else if (state?.sent && !state.replied) {
        state.replied = true;
        const text = Array.isArray(decoded) && decoded.length >= 2 ? decoded[1] : null;
        const output = receiptText(text);
        const lines = output === null ? [] : output.split(/\r?\n/);
        let requiredFailure = false;
        const completed = new Set();
        for (const provider of state.order) {
          const prefix = "EVEJS_LOGIN:v1:" + state.nonce + ":" + provider.id + ":";
          const hits = lines.filter(line => line.startsWith(prefix));
          const dependencyFailed = provider.requires.some(id => !completed.has(id));
          const ok = !dependencyFailed && hits.length === 1 && hits[0] === prefix + "OK";
          const reason = dependencyFailed ? "dependency-failed" : ok ? "client-executed" : "client-failed";
          if (!ok) report(provider.id, reason, dependencyFailed ? "prerequisite did not initialize" : hits.length ? "invalid or failed receipt" : "missing client receipt");
          if (notify(provider, this, ok, reason)) completed.add(provider.id);
          else if (provider.required) requiredFailure = true;
        }
        if (requiredFailure) return reject(this, "required client receipt failed");
      }
      // Native validation, acknowledgements and other reply observers retain ownership.
      return previousReply.apply(this, arguments);
    };
    // Avoid invoking inherited setters or leaving one half attached on failure.
    try {
      for (const [name, method] of [["_sendPacket", send], ["_handleFuncResult", reply]]) {
        Object.defineProperty(prototype, name, descriptors.get(name) ? { ...descriptors.get(name), value: method } :
          { value: method, writable: true, enumerable: false, configurable: true });
      }
    } catch (_) {
      for (const [name, descriptor] of descriptors) {
        try { if (descriptor) Object.defineProperty(prototype, name, descriptor); else delete prototype[name]; } catch (_) {}
      }
      return unavailable("compiled handshake methods could not be attached");
    }
    attached.add(Service.prototype);
    adapterFailure = null;
    return true;
  }
  function register(spec) {
    if (!spec || typeof spec.id !== "string" || !ID.test(spec.id) || typeof spec.source !== "string" ||
        !spec.source.length || Buffer.byteLength(spec.source, "utf8") > MAX_SOURCE) {
      throw Error("LoginDelivery requires a stable ID and bounded UTF-8 source");
    }
    for (const name of ["after", "requires"]) if (spec[name] !== undefined &&
        (!Array.isArray(spec[name]) || spec[name].some(id => typeof id !== "string" || !ID.test(id) || id === spec.id))) {
      throw Error("Invalid LoginDelivery " + name + " dependency");
    }
    if (spec.required !== undefined && typeof spec.required !== "boolean") throw Error("Invalid required delivery flag");
    for (const name of ["onReceipt", "onFailure"]) if (spec[name] !== undefined && typeof spec[name] !== "function") {
      throw Error("Invalid LoginDelivery callback");
    }
    const after = [...new Set(spec.after || [])].sort(), requires = [...new Set(spec.requires || [])].sort();
    const identity = digest(JSON.stringify([spec.source, after, requires, spec.required === true]));
    const old = providers.get(spec.id);
    if (old) {
      if (old.identity !== identity || old.onReceipt !== spec.onReceipt || old.onFailure !== spec.onFailure) {
        throw Error("Conflicting LoginDelivery provider " + spec.id);
      }
      return old.handle;
    }
    if (frozen) throw Error("LoginDelivery registration is frozen after the first login");
    if (spec.required === true && adapterFailure) throw adapterError(adapterFailure);
    if (providers.size >= MAX_PROVIDERS) throw Error("LoginDelivery supports at most 64 providers");
    const bytes = Buffer.byteLength(spec.source, "utf8");
    if (sourceBytes + bytes > MAX_TOTAL_SOURCE) throw Error("LoginDelivery aggregate raw source exceeds 1 MiB");
    const provider = { id: spec.id, identity, after, requires, required: spec.required === true,
      encoded: zlib.deflateSync(Buffer.from(spec.source, "utf8")).toString("base64"),
      onReceipt: spec.onReceipt, onFailure: spec.onFailure };
    provider.handle = Object.freeze({ id: spec.id, abi: 1 });
    providers.set(spec.id, provider); sourceBytes += bytes; return provider.handle;
  }
  const cached = Object.values(moduleHost._cache || {}).find(item => canonical(item.filename || ".") === target);
  if (cached) {
    report("adapter-v1", "late-preload", "handshake was already loaded; restart before accepting logins");
    throw Error("LoginDelivery must preload before the native handshake");
  }
  const previousCompile = moduleHost.prototype._compile;
  moduleHost.prototype._compile = function(source, filename) {
    const result = previousCompile.call(this, source, filename);
    if (canonical(filename) === target) attach(this.exports);
    return result;
  };
  return Object.freeze({ abi: 1, root, register, attach });
}

function getRuntime(root, options = {}) {
  if (!isMainThread) return null;
  const host = options.host || globalThis, existing = host[KEY];
  if (existing) {
    if (existing.abi !== 1 || existing.root !== canonical(root)) throw Error("Conflicting LoginDelivery runtime owner");
    return existing;
  }
  const runtime = createRuntime(root, options); host[KEY] = runtime; return runtime;
}

function supportsRoot(root) {
  return fs.existsSync(path.join(root, "server/src/network/tcp/handshake.js")) &&
    fs.existsSync(path.join(root, "server/src/network/tcp/utils/marshal.js"));
}

module.exports = { KEY, getRuntime, createRuntime, supportsRoot, MAX_SOURCE, MAX_EXPRESSION, MAX_PROVIDERS, MAX_TOTAL_SOURCE };
