# Load your window through the login handshake

**Build new windows/HUDs around login-handshake delivery. Do not install this
example by editing the client's script archive or shared command classes.**
The server delivers your authored client code during login; the code then waits
for the character before registering the window. Loading the code and opening
the window are separate steps.

This tutorial requires a reviewed login integration for your supported EveJS
build. The Launcher does not supply a universal `installWindow` or handshake
injection API. If your build lacks that integration, report the limitation;
do not silently add a new archive patch to make the tutorial work. Existing mods
can keep a documented legacy fallback while migrating safely.

## 1. Keep the files in your mod

Package `window.py` with your mod's own client bootstrap. Register the settings
service and persistent store on the server as described in [README](README.md).
Do not copy `window.py` into the physical game client. Only your authored
bootstrap and window code belong in the login payload; the server handlers stay
on the server.

## 2. Compose with the supported server login path

Identify the EveJS-authored login payload builder in the exact server build you
support. Check its interface and compatibility before installing your hook.
Use your loader's reviewed runtime integration to extend that builder while
preserving its original behavior and return value. Encode the authored source
as data, run it in a private namespace, and bound the payload size. A failure in
your companion must not prevent the normal login from completing.

These are responsibilities of your mod's version-specific adapter, not methods
available in every EveJS release. For an authored implementation to study, see
the pinned [server composition example](https://github.com/V0nCleef/EveJS-Automining/blob/05a2924b3bf2fe4baa1caf5cdc1413a9d3128b0b/lib/loginDelivery.js)
and [client bootstrap](https://github.com/V0nCleef/EveJS-Automining/blob/05a2924b3bf2fe4baa1caf5cdc1413a9d3128b0b/client/login.py).
They target reviewed builds and contain mod-specific services and identities;
do not copy their service names, compatibility hashes or legacy detection into
your own mod unchanged.

## 3. Wait for the character, then install the controller

The handshake can run before `sm`, the character session or remote services are
ready. Start a cooperative, bounded readiness task; do not block login. Register
the window only after your bootstrap has validated the current connection,
authenticated character and matching server-side mod version/generation.

The following belongs **inside your bootstrap's successful readiness callback**.
The callback variables come from your own integration; this is not a complete
bootstrap or a Launcher API:

```python
# Python 2.7-compatible integration sketch.
# window_source is the authored window.py delivered in the login payload.
# previous_controller, sm, session and is_ready are supplied by your bootstrap.
if previous_controller is not None:
    previous_controller.dispose()

namespace = {'__builtins__': __builtins__, '__name__': 'my_mod_window'}
eval(compile(window_source, '<my-mod-window>', 'exec'), namespace)
controller = namespace['install_window'](sm, session, is_ready)
# Retain controller in your bootstrap; acknowledge readiness only after success.
```

`is_ready()` must become false when the connection, character or payload owner
changes. A character ID alone is insufficient. Cancel pending readiness attempts
on replacement/disconnect, retry only within a bounded policy, and dispose partial
registration if installation fails. Use a unique identity for your mod.

## 4. Open it from your own command

After the controller is installed, acknowledge readiness to your own server
service. Validate that acknowledgement against the authenticated session and the
current payload generation. Clear readiness on disconnect or character change.
Only then should your registered command send:

```javascript
// Only for the authenticated session that acknowledged this mod's readiness.
session.sendNotification('OnExampleModSettingsOpen', 'clientID', []);
```

`window.py` handles that event and opens its existing window ID. Rename the
example's event, service and window identities together. Do not patch the shared
client command class, accept arbitrary code through chat, or show the window
before login finishes. Apply/Reload call the settings service; they do not need
the server to resend the window's source.

## 5. Clean up and prove delivery works

On disconnect, character change or replacement, invalidate readiness, stop pending
tasks and call `controller.dispose()` to close the window and unregister its
handlers. Reconnect must perform a fresh handshake/readiness sequence. Turning
the server mod off must not leave a usable old controller in a reused client.

Test login before character selection, repeated delivery, reconnect, switching
characters, two clients, failed acknowledgement, disable/remove and opening the
window repeatedly. Verify the window can be delivered without changing client
script files. Check Apply/Reload and persistence after restart separately.

When migrating an existing mod, restore only its verified owned changes through
the normal update lifecycle. Preserve shared-client compatibility and rollback;
follow the [migration guide](../../docs/how-to-make-a-mod/12-client-files.md).
If you report delivery to the Launcher, use its negotiated helper contract and
report `login-handshake` only for a setup that actually uses it.

This document is an integration walkthrough. The supplied smaller window example
has logic tests, but its complete integration still needs your supported build's
live verification.
