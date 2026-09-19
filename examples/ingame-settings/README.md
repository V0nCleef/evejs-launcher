# In-game settings window: author-owned example

This is a teaching example, **not an Add ZIP mod or a universal login API**.
It contains no vendor client code. The controls follow the authored AutoMining
window tested with client build 3396210, but this smaller example has not itself
been tested in a live client. Python 3 mocks check its logic; the intended client
syntax is Python 2.7-compatible. Review APIs against your supported build.

## Required delivery: login handshake

**Load this window through your supported EveJS login handshake. Do not patch
the client's script archive or shared command classes to install it.**
Keep the Python sources in your mod; the server delivers them at login. Wait for
the authenticated character, install the controller once, acknowledge readiness,
then open it from your own command. Loading the code does not automatically open
the window.

Follow [Load your window through the login handshake](LOGIN-HANDSHAKE.md) for
the server integration, bootstrap sequence, code sketch and cleanup steps.
If the required integration is unavailable, document the unsupported build
instead of inventing an API or silently adding a client-file patch. Existing
mods may retain a documented fallback while migrating safely.

## Wire it into your mod

- Register your own service named `exampleModSettings` using your reviewed EveJS
  service integration. Bind the functions returned by `createSettingsHandlers`
  to its `Handle_GetSettings` and `Handle_SaveSettings` handlers. These names are
  defined by this example; they are not built-in services or Launcher features.
- Pass your persistent per-character store with synchronous `read(id)` and
  `write(id, state)` methods. `read` returns a saved object or null; `write` must
  persist atomically or throw. Use server-owned storage outside the replaceable
  mod folder. With async/multi-worker storage, use a transaction/CAS for revisions.
- Deliver `window.py` through your reviewed login integration. After readiness,
  call `install_window(sm, session, is_ready)` and retain the returned controller.
  `is_ready` must validate the current server/session, as in your companion.
- From your mod's own chat command or menu, send
  `session.sendNotification("OnExampleModSettingsOpen", "clientID", [])` only
  after that client advertised readiness. Repeating the event opens the same
  window ID. Do not expose a command accepting arbitrary client code.
- Call the prior controller's `dispose()` before loading a replacement. Also
  dispose on disconnect or loss of companion ownership; fresh login must check
  readiness again. Do not patch the client's shared command-service class.
- Make your server feature consume the saved `enabled` and `interval` settings.
  Saving this example's checkbox does not implement mining or any other feature.

`window.py` creates a checkbox, interval field, Apply, Reload and status text.
Changes stay local until Apply. Closing without Apply discards the draft. Controls
are disabled while a request is in flight, server revisions reject stale writes,
and character changes close the window. No background polling is needed for this
small example. If adding status polling, stop it when the window closes and do
not overwrite the user's unsaved edits.

## Server binding sketch

```javascript
const { createSettingsHandlers } = require('./settings-handlers');
Object.assign(YourService.prototype, createSettingsHandlers(yourCharacterSettingsStore));
```

`YourService` and `yourCharacterSettingsStore` are integration points supplied by
your mod, not globals available in every EveJS build. Preserve existing service
handlers and reject name collisions. Never accept a character ID from the UI;
the supplied authenticated session determines which settings can be changed.

## Check before sharing

Test first open, reopening, Apply, Reload, close without Apply, server rejection,
stale revisions, missing service, disconnect, different characters, two clients,
and persistence after a server restart. Test both your supported delivery methods
without duplicate handlers. Package the example only after adding and verifying
the service, storage, login and command integrations described above.
