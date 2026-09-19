"use strict";

// Author-owned example, not an EveJS or Launcher API. Bind these handlers to
// your registered "exampleModSettings" service through your reviewed loader.
// store.read(characterID) -> saved state or null
// store.write(characterID, state) -> persist synchronously or throw
// Use your existing server settings store; do not save into the client archive.
function createSettingsHandlers(store) {
  const character = session => {
    const id = Number(session?.characterID || session?.charid);
    if (!Number.isSafeInteger(id) || id <= 0) throw Error("Log in with a character first.");
    return id;
  };
  const state = id => ({ enabled: false, interval: 60, revision: 0, ...store.read(id) });
  const reply = action => {
    try { return JSON.stringify({ success: true, ...action() }); }
    catch (error) { return JSON.stringify({ success: false, message: error.message }); }
  };
  const decode = value => {
    if (value && ["wstring", "token", "rawstr"].includes(value.type)) value = value.value;
    if (Buffer.isBuffer(value) && value.length <= 1024) value = value.toString("utf8");
    if (typeof value !== "string" || value.length > 1024) throw Error("Invalid settings request.");
    const request = JSON.parse(value);
    if (!request || Array.isArray(request) || typeof request !== "object" ||
        Object.keys(request).some(key => !["enabled", "interval", "revision"].includes(key)) ||
        typeof request.enabled !== "boolean" || !Number.isInteger(request.interval) ||
        request.interval < 6 || request.interval > 86400 ||
        !Number.isSafeInteger(request.revision) || request.revision < 0) {
      throw Error("Use an interval from 6 to 86400 seconds and reload stale settings.");
    }
    return request;
  };
  return {
    Handle_GetSettings(args, session) {
      return reply(() => ({ settings: state(character(session)) }));
    },
    Handle_SaveSettings(args, session) {
      return reply(() => {
        const id = character(session);
        const request = decode(args?.[0]);
        const previous = state(id);
        if (request.revision !== previous.revision) throw Error("Settings changed. Reload before saving.");
        const next = { enabled: request.enabled, interval: request.interval, revision: previous.revision + 1 };
        // No awaits between read and write. For async/multi-worker storage,
        // use a database transaction or compare-and-swap instead.
        store.write(id, next);
        return { settings: next };
      });
    },
  };
}

module.exports = { createSettingsHandlers };
