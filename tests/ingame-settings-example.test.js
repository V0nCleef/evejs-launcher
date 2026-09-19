"use strict";
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createSettingsHandlers } = require('../examples/ingame-settings/settings-handlers');
const session = { characterID: 101 };
const request = (overrides = {}) => JSON.stringify({ enabled: true, interval: 120, revision: 0, ...overrides });
function setup() {
  const saved = new Map();
  const store = { read: id => saved.get(id), write: (id, value) => saved.set(id, value) };
  const handlers = createSettingsHandlers(store);
  const save = (payload, who = session) => JSON.parse(handlers.Handle_SaveSettings([payload], who));
  const get = (who = session) => JSON.parse(handlers.Handle_GetSettings([], who));
  return { saved, store, handlers, save, get };
}
test('per-character defaults, save, stale revision and fresh store reader', () => {
  const { save, get, store } = setup();
  assert.deepEqual(get().settings, { enabled: false, interval: 60, revision: 0 });
  assert.equal(save(request()).settings.revision, 1);
  assert.equal(save(request()).success, false);
  assert.equal(get({ characterID: 102 }).settings.enabled, false);
  const reloaded = createSettingsHandlers(store);
  assert.equal(JSON.parse(reloaded.Handle_GetSettings([], session)).settings.interval, 120);
});
test('reject absent identity, impersonation, wrong types, ranges and oversized payloads', () => {
  const { save, saved } = setup();
  assert.equal(save(request(), {}).success, false);
  for (const payload of ['{', 'null', '[]', 'x'.repeat(1025),
    request({ characterID: 102 }), request({ interval: 5 }), request({ interval: 86401 }),
    request({ interval: 6.5 }), request({ enabled: 'true' }), request({ revision: -1 })]) {
    assert.equal(save(payload).success, false);
  }
  assert.equal(saved.size, 0);
});
test('accept bounded client byte and tagged text encodings', () => {
  for (const wrap of [s => s, s => Buffer.from(s),
                     s => ({ type: 'wstring', value: s }),
                     s => ({ type: 'rawstr', value: Buffer.from(s) })]) {
    assert.equal(setup().save(wrap(request())).success, true);
  }
});
test('failed atomic storage write does not claim success or change settings', () => {
  const { save, get, store } = setup();
  store.write = () => { throw Error('Unavailable'); };
  assert.equal(save(request()).success, false);
  assert.deepEqual(get().settings, { enabled: false, interval: 60, revision: 0 });
});
